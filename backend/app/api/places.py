"""자연어 장소 검색 HTTP 요청과 서비스 연결."""

import logging
from time import perf_counter

from fastapi import APIRouter, HTTPException

from app.schemas.place_search import PlaceSearchRequest, PlaceSearchResponse
from app.services.place_search_service import (
    PlaceSearchConfigurationError,
    PlaceSearchProviderError,
    PlaceSearchProviderTimeout,
    PlaceSearchRateLimitError,
    search_places,
)

router = APIRouter(prefix="/api/places", tags=["장소 검색"])
logger = logging.getLogger(__name__)


@router.post(
    "/search",
    response_model=PlaceSearchResponse,
    summary="자연어로 일본 장소 검색",
    description=(
        "사용자 요청을 일본어 검색어로 증강하고 검색어별 Google Places 첫 페이지를 반환합니다. "
        "Google 응답 언어는 한국어(ko), 지역 코드는 JP로 고정합니다. "
        "검색어는 최대 세 개이며 요청이 모호해 검색어를 만들 수 없으면 "
        "장소 검색 없이 textQueries는 null, results는 빈 배열로 반환합니다. "
        "다음 페이지는 자동으로 조회하지 않습니다."
    ),
    responses={
        422: {"description": "입력이 올바르지 않음"},
        429: {"description": "외부 검색 서비스의 요청 한도 초과"},
        502: {"description": "외부 검색 서비스 조회 오류"},
        503: {"description": "검색 서비스 설정 누락"},
        504: {"description": "외부 검색 서비스 응답 시간 초과"},
    },
)
def search(payload: PlaceSearchRequest) -> PlaceSearchResponse:
    # 동기 외부 클라이언트 호출은 FastAPI의 작업 스레드에서 실행한다.
    started_at = perf_counter()
    logger.info("장소 검색 요청: 검색어당 최대=%d개", payload.pageSize)
    try:
        result = search_places(payload)
        logger.info(
            "장소 검색 완료: 검색어=%d개, 장소=%d개, 검색 생략=%s, 소요=%.2f초",
            len(result.textQueries or []),
            sum(len(item.response.get("places", [])) for item in result.results),
            result.textQueries is None,
            perf_counter() - started_at,
        )
        return result
    except PlaceSearchConfigurationError as exc:
        logger.warning(
            "장소 검색 실패: status=503, 설정 누락, 소요=%.2f초",
            perf_counter() - started_at,
        )
        raise HTTPException(
            status_code=503, detail="장소 검색 서비스를 사용할 수 없습니다."
        ) from exc
    except PlaceSearchRateLimitError as exc:
        logger.warning(
            "장소 검색 실패: status=429, 요청 한도 초과, 소요=%.2f초",
            perf_counter() - started_at,
        )
        raise HTTPException(
            status_code=429,
            detail="외부 검색 서비스의 요청 한도를 초과했습니다. 잠시 후 다시 시도해주세요.",
        ) from exc
    except PlaceSearchProviderTimeout as exc:
        logger.warning(
            "장소 검색 실패: status=504, 응답 시간 초과, 소요=%.2f초",
            perf_counter() - started_at,
        )
        raise HTTPException(
            status_code=504, detail="외부 검색 서비스 응답 시간이 초과되었습니다."
        ) from exc
    except PlaceSearchProviderError as exc:
        logger.warning(
            "장소 검색 실패: status=502, 외부 서비스 오류, 소요=%.2f초",
            perf_counter() - started_at,
        )
        raise HTTPException(
            status_code=502,
            detail="외부 검색 서비스에서 정상적인 응답을 받지 못했습니다.",
        ) from exc
