"""관광지 좌표만으로 동선 계산 전 기준역을 조회한다."""

from fastapi import APIRouter

from app.schemas.station import StationResolveRequest, StationResolveResponse
from app.services.station_service import resolve_stations

router = APIRouter(prefix="/api/stations", tags=["기준역 조회"])


@router.post(
    "/resolve",
    response_model=StationResolveResponse,
    summary="일차별 관광지의 최근접 기준역 조회",
    description=(
        "관광지별 Google Place ID와 좌표를 받아 최근접 역을 반환합니다. "
        "dayIndex는 1~7, 일차별 장소는 1~8개이며 일부 일차만 요청할 수 있습니다. "
        "동선 계산·경로 크롤링·Google Places 호출은 실행하지 않습니다. "
        "조회 실패는 장소별 status로 반환하며 다른 장소의 성공 결과를 유지합니다."
    ),
)
def resolve(payload: StationResolveRequest) -> StationResolveResponse:
    return resolve_stations(payload)
