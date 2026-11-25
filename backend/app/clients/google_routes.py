"""선택된 두 좌표 사이 WALK 경로. 자동 재시도나 추정값 대체는 하지 않는다."""

import math
import re
from dataclasses import dataclass
from decimal import Decimal

from app.clients.http_client import request_json
from app.errors import ProviderResponseError


@dataclass(frozen=True)
class WalkingRoute:
    minutes: int
    distance: int
    path: list[dict]


def decode_polyline(encoded: str) -> list[dict]:
    """Google의 1e5 좌표 인코딩을 제한된 크기로 검증·복원한다."""
    if not isinstance(encoded, str) or not 1 <= len(encoded) <= 50000:
        raise ValueError("Invalid polyline")
    numbers, value, shift = [], 0, 0
    for char in encoded:
        byte = ord(char) - 63
        if not 0 <= byte <= 63 or shift > 30:
            raise ValueError("Invalid polyline")
        value |= (byte & 31) << shift
        if byte < 32:
            numbers.append(~(value >> 1) if value & 1 else value >> 1)
            value, shift = 0, 0
        else:
            shift += 5
    if shift or len(numbers) % 2:
        raise ValueError("Incomplete polyline")
    lat, lng, path = 0, 0, []
    for index in range(0, len(numbers), 2):
        lat += numbers[index]
        lng += numbers[index + 1]
        if abs(lat) > 9000000 or abs(lng) > 18000000:
            raise ValueError("Invalid coordinates")
        path.append({"lat": lat / 100000, "lng": lng / 100000})
    if not 2 <= len(path) <= 10000:
        raise ValueError("Invalid path length")
    return path


def walking_route(origin: dict, destination: dict, api_key: str) -> WalkingRoute | None:
    """정상적인 빈 경로만 None이다. 인증·통신·응답 오류는 예외로 전달한다."""
    if origin == destination:
        return WalkingRoute(0, 0, [origin, destination])

    def waypoint(point):
        return {
            "location": {
                "latLng": {
                    "latitude": point["lat"],
                    "longitude": point["lng"],
                }
            }
        }

    data = request_json(
        "POST",
        "https://routes.googleapis.com/directions/v2:computeRoutes",
        provider="Google 도보 경로",
        timeout=15,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline",
        },
        json={
            "origin": waypoint(origin),
            "destination": waypoint(destination),
            "travelMode": "WALK",
            "languageCode": "ko",
            "units": "METRIC",
            "computeAlternativeRoutes": False,
        },
    )
    if not isinstance(data, dict) or "error" in data:
        raise ProviderResponseError("Google 도보 경로 응답이 올바르지 않습니다.")
    routes = data.get("routes", [])
    if not isinstance(routes, list):
        raise ProviderResponseError("Google 도보 경로 목록이 올바르지 않습니다.")
    if not routes:
        return None
    try:
        row = routes[0]
        duration = row["duration"]
        if not isinstance(duration, str) or not re.fullmatch(
            r"\d+(?:\.\d{1,9})?s", duration
        ):
            raise ValueError("Invalid duration")
        seconds = Decimal(duration[:-1])
        distance = row["distanceMeters"]
        if (
            type(distance) is not int
            or not 0 <= distance <= 2**53 - 1
            or seconds > (2**53 - 1) * 60
        ):
            raise ValueError("Invalid distance/duration")
        path = decode_polyline(row["polyline"]["encodedPolyline"])
        return WalkingRoute(math.ceil(seconds / 60), distance, path)
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderResponseError(
            "Google 도보 경로 형식이 올바르지 않습니다."
        ) from exc
