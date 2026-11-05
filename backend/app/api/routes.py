"""동선 최적화 HTTP 요청과 서비스 연결."""

import logging

from fastapi import APIRouter, HTTPException, Request

from app.schemas.route import RouteRequest, RouteResponse
from app.services.route_service import (
    RouteDataError,
    RouteProviderError,
    RouteProviderTimeout,
    optimize_route,
)

router = APIRouter(prefix="/api/routes", tags=["동선 최적화"])
logger = logging.getLogger(__name__)


@router.post(
    "/optimize",
    response_model=RouteResponse,
    summary="일차별 최적 방문 순서와 역 위치 조회",
    description=(
        "관광지별 실제 Google Place ID와 좌표를 받아 방문 순서, 역·환승역 좌표, "
        "이동 시간과 교통비를 반환합니다. 시각은 체류·도보를 제외한 역 간 이동 기준입니다."
    ),
    responses={
        422: {"description": "입력이 올바르지 않거나 유효한 역·경로를 찾지 못함"},
        502: {"description": "외부 역·경로 정보 서비스 조회 오류"},
        504: {"description": "외부 역·경로 정보 서비스 응답 시간 초과"},
    },
)
async def optimize(payload: RouteRequest, request: Request) -> RouteResponse:
    try:
        # 브라우저가 과도하게 실행되지 않도록 프로세스당 한 요청씩 처리한다.
        async with request.app.state.route_lock:
            return await optimize_route(payload)
    except RouteProviderTimeout as exc:
        logger.warning("동선 조회 시간 초과", exc_info=True)
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except RouteProviderError as exc:
        logger.warning("동선 제공 서비스 오류", exc_info=True)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RouteDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
