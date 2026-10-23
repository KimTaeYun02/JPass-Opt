from math import isfinite
from unicodedata import normalize

import requests


def get_stations(name: str, line: str | None = None) -> list[dict]:
    """노선으로 먼저 조회하고, 역 이름이 일치하는 좌표가 없으면 역명으로 조회한다."""
    normalized_name = normalize("NFKC", name).strip()
    if not normalized_name:
        return []

    normalized_line = normalize("NFKC", line or "").split("・", 1)[0].strip()
    if normalized_line:
        stations = _get_matching_stations(
            normalized_name, {"method": "getStations", "line": normalized_line}
        )
        if stations:
            return stations

    return _get_matching_stations(
        normalized_name, {"method": "getStations", "name": normalized_name}
    )


def _get_matching_stations(name: str, params: dict[str, str]) -> list[dict]:
    response = requests.get(
        "https://www.heartrails-express.com/api/json", params=params, timeout=10
    )
    response.raise_for_status()
    stations = response.json().get("response", {}).get("station", []) or []

    matches = []
    for station in stations:
        station_name = station.get("name")
        if not isinstance(station_name, str):
            continue
        if normalize("NFKC", station_name).strip() != name:
            continue
        latitude, longitude = station.get("y"), station.get("x")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            for value in (latitude, longitude)
        ):
            continue
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue
        matches.append(
            {
                "name": station_name,
                "line": station.get("line"),
                "prefecture": station.get("prefecture"),
                "latitude": latitude,
                "longitude": longitude,
            }
        )
    return matches


def get_nearest_stations(
    latitude: float,
    longitude: float,
    limit: int = 3,
) -> list[dict]:
    url = "https://www.heartrails-express.com/api/json"

    response = requests.get(
        url,
        params={
            "method": "getStations",
            "x": longitude,
            "y": latitude,
        },
        timeout=10,
    )
    response.raise_for_status()

    stations = response.json().get("response", {}).get("station", [])

    return [
        {
            "name": station["name"],
            "line": station["line"],
            "prefecture": station["prefecture"],
            "latitude": station["y"],
            "longitude": station["x"],
            "distance": station["distance"],
        }
        for station in stations[:limit]
    ]
