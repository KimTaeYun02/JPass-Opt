"""역 주변 관광지 추천 HTTP 요청과 서비스 연결."""

import logging
from time import perf_counter

from fastapi import APIRouter, HTTPException

from app.errors import ProviderError, ProviderRateLimitError, ProviderTimeoutError
from app.schemas.nearby import NearbyRequest, NearbyResponse
from app.services.nearby_service import recommend_nearby

router = APIRouter(prefix="/api/nearby", tags=["주변 관광지 추천"])
logger = logging.getLogger(__name__)


@router.post(
    "/recommend",
    response_model=NearbyResponse,
    summary="선택한 역 주변 관광지 추천",
    description=(
        "사용자 요청사항과 취향 태그를 바탕으로 LLM이 기준 역을 선택하고, "
        "각 역의 검색 반경 내 관광지를 최대 placeCount개 반환합니다. "
        "단일 태그는 검색 순서를 유지하고, 복수 태그의 후보가 2개 이상이면 "
        "LLM이 사용자 요청에 맞춰 최종 장소와 순서를 선택합니다. "
        "후보가 placeCount개 이하여도 순위를 정하며, 0~1개이면 순위 선정을 생략합니다."
    ),
    responses={
        422: {"description": "입력이 올바르지 않음"},
        429: {"description": "외부 추천 서비스의 요청 한도 초과"},
        502: {"description": "외부 추천 서비스 조회 오류"},
        504: {"description": "외부 추천 서비스 응답 시간 초과"},
    },
)
def recommend(payload: NearbyRequest) -> NearbyResponse:
    # 동기 외부 클라이언트 호출은 FastAPI의 작업 스레드에서 실행한다.
    started_at = perf_counter()
    logger.info(
        "주변 관광지 추천 요청: 역=%d개, 태그=%s, 역당=%d개, 반경=%sm, 앵커 상한=%s",
        len(payload.stations),
        ",".join(payload.travelTypes),
        payload.placeCount,
        payload.radius,
        payload.maxAnchorCount if payload.maxAnchorCount is not None else "기본값",
    )
    try:
        result = recommend_nearby(payload)
        logger.info(
            "주변 관광지 추천 완료: 앵커=%d개, 추천=%d개, 소요=%.2f초",
            len(result.results),
            sum(item.returnedCount for item in result.results),
            perf_counter() - started_at,
        )
        return result
    except ProviderRateLimitError as exc:
        logger.warning(
            "주변 관광지 추천 실패: status=429, 오류=%s, 소요=%.2f초",
            exc,
            perf_counter() - started_at,
        )
        raise HTTPException(
            status_code=429,
            detail="외부 추천 서비스의 요청 한도를 초과했습니다. 잠시 후 다시 시도해주세요.",
        ) from exc
    except ProviderTimeoutError as exc:
        logger.warning(
            "주변 관광지 추천 실패: status=504, 오류=%s, 소요=%.2f초",
            exc,
            perf_counter() - started_at,
        )
        raise HTTPException(
            status_code=504, detail="외부 추천 서비스 응답 시간이 초과되었습니다."
        ) from exc
    except ProviderError as exc:
        logger.warning(
            "주변 관광지 추천 실패: status=502, 오류=%s, 소요=%.2f초",
            exc,
            perf_counter() - started_at,
        )
        raise HTTPException(
            status_code=502,
            detail="외부 추천 서비스에서 정상적인 응답을 받지 못했습니다.",
        ) from exc
