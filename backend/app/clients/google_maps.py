from typing import TypedDict, cast

import requests

from app.constants.place_types import PLACE_TYPES_BY_TRAVEL_TYPE, TRAVEL_TYPE


class PlaceLocation(TypedDict, total=False):
    latitude: float
    longitude: float


class PlaceDisplayName(TypedDict, total=False):
    text: str
    languageCode: str


class NearbyPlace(TypedDict, total=False):
    """주변 검색에서 요청하는 필드. 응답에서 키가 누락될 수 있다."""

    id: str
    displayName: PlaceDisplayName
    location: PlaceLocation
    formattedAddress: str
    primaryType: str
    types: list[str]


def get_coordinates(
    place_name: str,
    api_key: str,
    use_geocoding: bool = True,
) -> PlaceLocation | None:
    """장소의 위도·경도를 반환한다. 검색 결과가 없으면 None을 반환한다."""
    if use_geocoding:
        url = "https://maps.googleapis.com/maps/api/geocode/json"

        response = requests.get(
            url,
            params={
                "address": place_name,
                "key": api_key,
                "language": "ko",
                "region": "jp",
                "components": "country:JP",
            },
            timeout=10,
        )
    else:
        url = "https://places.googleapis.com/v1/places:searchText"

        response = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": "places.location",
            },
            json={
                "textQuery": place_name,
                "languageCode": "ko",
                "regionCode": "JP",
                "pageSize": 1,
            },
            timeout=10,
        )

    response.raise_for_status()
    data = response.json()

    if use_geocoding:
        status = data.get("status")

        if status == "ZERO_RESULTS":
            return None

        if status != "OK":
            raise RuntimeError(
                f"Geocoding API Error: {status} {data.get('error_message', '')}"
            )

        location = data["results"][0]["geometry"]["location"]

        return {
            "latitude": location["lat"],
            "longitude": location["lng"],
        }

    places = data.get("places", [])

    if not places:
        return None

    return cast(PlaceLocation, places[0]["location"])


def search_nearby(
    latitude: float,
    longitude: float,
    api_key: str,
    *,
    travel_type: str,
    radius: float = 1000.0,
    max_results: int = 20,
    rank_preference: str = "DISTANCE",
) -> list[NearbyPlace]:
    """지정한 좌표 주변에서 여행 유형에 해당하는 장소를 검색한다.

    travel_type은 constants/place_types.py의 TRAVEL_TYPE 중 하나를 전달한다.
    """
    if travel_type not in TRAVEL_TYPE:
        raise ValueError(f"지원하지 않는 여행 유형입니다: {travel_type}")

    included_types = PLACE_TYPES_BY_TRAVEL_TYPE.get(travel_type)
    if not included_types:
        raise ValueError(
            f"검색할 장소 타입이 설정되지 않은 여행 유형입니다: {travel_type}"
        )

    response = requests.post(
        "https://places.googleapis.com/v1/places:searchNearby",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": (
                "places.id,"
                "places.displayName,"
                "places.location,"
                "places.formattedAddress,"
                "places.primaryType,"
                "places.types"
            ),
        },
        json={
            "includedTypes": list(included_types),
            "maxResultCount": max_results,
            "rankPreference": rank_preference,
            "locationRestriction": {
                "circle": {
                    "center": {
                        "latitude": latitude,
                        "longitude": longitude,
                    },
                    "radius": radius,
                }
            },
            "languageCode": "ko",
            "regionCode": "JP",
        },
        timeout=30,
    )
    response.raise_for_status()
    return cast(list[NearbyPlace], response.json().get("places", []))
