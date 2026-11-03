from typing import TypedDict, cast

import requests

from app.constants.place_types import PLACE_TYPES_BY_TRAVEL_TYPE, TRAVEL_TYPE
from app.errors import (
    ProviderError,
    ProviderResponseError,
    ProviderTimeoutError,
    provider_error_for_status,
)


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


def _request_places(
    endpoint: str,
    *,
    api_key: str,
    field_mask: str,
    payload: dict,
    validate_place_objects: bool = False,
) -> dict:
    """Places 요청과 응답 형식 검증을 한 곳에서 처리한다."""
    try:
        response = requests.post(
            f"https://places.googleapis.com/v1/places:{endpoint}",
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": field_mask,
            },
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
    except requests.Timeout as exc:
        raise ProviderTimeoutError("Google 장소 검색 응답 시간 초과") from exc
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        raise provider_error_for_status(status, "Google 장소 검색 실패") from exc
    except requests.RequestException as exc:
        raise ProviderError("Google 장소 검색 연결 실패") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise ProviderResponseError(
            "Google 장소 검색 응답 JSON 형식이 올바르지 않습니다."
        ) from exc

    if not isinstance(data, dict) or "error" in data:
        raise ProviderResponseError("Google 장소 검색 응답이 올바르지 않습니다.")
    places = data.get("places", [])
    if not isinstance(places, list) or (
        validate_place_objects and any(not isinstance(place, dict) for place in places)
    ):
        raise ProviderResponseError("Google 장소 목록 형식이 올바르지 않습니다.")
    if "nextPageToken" in data and not isinstance(data["nextPageToken"], str):
        raise ProviderResponseError("Google 다음 페이지 토큰이 올바르지 않습니다.")
    return data


def search_text(
    text_query: str,
    api_key: str,
    *,
    language_code: str = "ko",
    region_code: str = "JP",
    page_size: int = 20,
    rank_preference: str = "RELEVANCE",
    location_bias: dict | None = None,
    page_token: str | None = None,
) -> dict:
    """Text Search를 한 번 호출한다. 페이지 추가 조회나 재시도는 하지 않는다."""
    FIELD_MASK = (
        "places.id,places.displayName,places.formattedAddress,places.location,"
        "places.primaryType,places.types,places.googleMapsUri,nextPageToken"
    )
    if not isinstance(text_query, str) or not text_query.strip():
        raise ValueError("text_query에 검색어를 입력해주세요.")
    if not api_key.strip():
        raise ValueError("Google Places API 키가 필요합니다.")

    payload = {
        "textQuery": text_query,
        "languageCode": language_code,
        "regionCode": region_code,
        "pageSize": page_size,
        "rankPreference": rank_preference,
        "openNow": False,
    }
    if location_bias is not None:
        payload["locationBias"] = location_bias
    if page_token is not None:
        if not isinstance(page_token, str) or not page_token.strip():
            raise ValueError("page_token에 이전 응답의 nextPageToken을 입력해주세요.")
        payload["pageToken"] = page_token

    return _request_places(
        "searchText",
        api_key=api_key.strip(),
        field_mask=FIELD_MASK,
        payload=payload,
        validate_place_objects=True,
    )


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

    data = _request_places(
        "searchNearby",
        api_key=api_key,
        field_mask=(
            "places.id,"
            "places.displayName,"
            "places.location,"
            "places.formattedAddress,"
            "places.primaryType,"
            "places.types"
        ),
        payload={
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
    )
    return cast(list[NearbyPlace], data.get("places", []))
