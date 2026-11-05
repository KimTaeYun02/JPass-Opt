"""기존 최근접 역 클라이언트를 재사용하며 경로 크롤링은 실행하지 않는다."""

import logging

from pydantic import ValidationError
from requests import RequestException, Timeout

from app.schemas.station import (
    ResolvedStation,
    StationDayResult,
    StationLocation,
    StationPlaceResult,
    StationResolveRequest,
    StationResolveResponse,
)
from app.services import route_service

logger = logging.getLogger(__name__)


def resolve_stations(request: StationResolveRequest) -> StationResolveResponse:
    cache = {}
    days = []
    for day in request.days:
        places = []
        for place in day.places:
            key = (place.location.lat, place.location.lng)
            if key not in cache:
                cache[key] = _lookup(*key)
            status, station, reason = cache[key]
            places.append(
                StationPlaceResult(
                    googlePlaceId=place.googlePlaceId,
                    status=status,
                    station=station,
                    reason=reason,
                )
            )
        days.append(StationDayResult(dayIndex=day.dayIndex, places=places))
    return StationResolveResponse(days=days)


def _lookup(lat: float, lng: float):
    try:
        rows = route_service.get_nearest_stations(lat, lng, limit=1)
        if not rows:
            return "not_found", None, None
        raw = rows[0]
        if any(isinstance(raw[key], bool) for key in ("latitude", "longitude")):
            raise ValueError("잘못된 역 좌표")
        station = ResolvedStation(
            name=raw["name"],
            location=StationLocation(
                lat=float(raw["latitude"]), lng=float(raw["longitude"])
            ),
            line=raw.get("line"),
        )
        return "ok", station, None
    except Timeout:
        return "error", None, "timeout"
    except RequestException:
        return "error", None, "unavailable"
    except (
        ValidationError,
        KeyError,
        TypeError,
        ValueError,
        IndexError,
        OverflowError,
    ) as exc:
        logger.warning("기준역 응답 형식 오류: %s", type(exc).__name__)
        return "error", None, "invalid_response"
    except Exception as exc:
        # 외부 클라이언트의 예기치 않은 오류도 다른 관광지의 조회를 막지 않는다.
        logger.warning("기준역 외부 조회 실패: %s", type(exc).__name__)
        return "error", None, "unavailable"
