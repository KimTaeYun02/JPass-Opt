from math import isfinite
from unicodedata import normalize

from app.clients.http_client import request_json
from app.errors import ProviderResponseError


def _request_stations(params: dict[str, str | float]) -> list[dict]:
    """HeartRails 응답 구조를 검증하고 역 목록을 반환한다."""
    data = request_json(
        "GET",
        "https://www.heartrails-express.com/api/json",
        provider="HeartRails 역 조회",
        params=params,
        timeout=10,
    )

    if not isinstance(data, dict) or "error" in data:
        raise ProviderResponseError("HeartRails 역 조회 응답이 올바르지 않습니다.")
    result = data.get("response")
    if not isinstance(result, dict) or "error" in result:
        raise ProviderResponseError("HeartRails 역 조회 응답이 올바르지 않습니다.")
    stations = result.get("station", [])
    if not isinstance(stations, list) or any(
        not isinstance(station, dict) for station in stations
    ):
        raise ProviderResponseError("HeartRails 역 목록 형식이 올바르지 않습니다.")
    return stations


def _has_valid_coordinates(station: dict) -> bool:
    latitude, longitude = station.get("y"), station.get("x")
    return (
        all(
            not isinstance(value, bool) and isinstance(value, (int, float))
            for value in (latitude, longitude)
        )
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
        and isfinite(latitude)
        and isfinite(longitude)
    )


def _station_result(station: dict) -> dict:
    """서비스가 사용하는 역 이름·좌표·지역의 형식을 보장한다."""
    name, prefecture = station.get("name"), station.get("prefecture")
    if (
        not isinstance(name, str)
        or not name.strip()
        or not _has_valid_coordinates(station)
        or (prefecture is not None and not isinstance(prefecture, str))
    ):
        raise ProviderResponseError("HeartRails 역 정보 형식이 올바르지 않습니다.")
    return {
        "name": name,
        "line": station.get("line"),
        "prefecture": prefecture,
        "latitude": station["y"],
        "longitude": station["x"],
    }


def get_stations(name: str, line: str | None = None) -> list[dict]:
    """노선으로 먼저 조회하고, 역 이름이 일치하는 좌표가 없으면 역명으로 조회한다."""
    normalized_name = normalize("NFKC", name).strip()
    if not normalized_name:
        return []

    normalized_line = normalize("NFKC", line or "").split("・", 1)[0].strip()
    if normalized_line:
        try:
            stations = _get_matching_stations(
                normalized_name, {"method": "getStations", "line": normalized_line}
            )
        except ProviderResponseError:
            # 크롤러의 노선 표시명이 인식되지 않아도 역명으로 다시 조회한다.
            stations = []
        if stations:
            return stations

    return _get_matching_stations(
        normalized_name, {"method": "getStations", "name": normalized_name}
    )


def _get_matching_stations(name: str, params: dict[str, str]) -> list[dict]:
    matches = []
    for station in _request_stations(params):
        station_name = station.get("name")
        if not isinstance(station_name, str):
            continue
        if normalize("NFKC", station_name).strip() != name:
            continue
        if not _has_valid_coordinates(station):
            continue
        matches.append(_station_result(station))
    return matches


def get_nearest_stations(
    latitude: float,
    longitude: float,
    limit: int = 3,
) -> list[dict]:
    stations = _request_stations(
        {
            "method": "getStations",
            "x": longitude,
            "y": latitude,
        }
    )
    return [
        {
            **_station_result(station),
            "distance": station.get("distance"),
        }
        for station in stations[:limit]
    ]
