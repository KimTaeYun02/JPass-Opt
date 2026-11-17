"""LLM 앵커 역 선정 → 역별 주변 검색 → 관광지 추천 응답 조립."""

import json
import logging
import math
import os
import random
from time import perf_counter
from typing import cast

from pydantic import ValidationError

from app.clients.google_maps import search_nearby
from app.errors import ProviderResponseError
from app.llm import LLMWrapper
from app.prompts.nearby_anchor import ANCHOR_SYSTEM_PROMPT
from app.prompts.nearby_ranking import RERANK_SYSTEM_PROMPT
from app.schemas.nearby import (
    AnchorStationSelection,
    NearbyRequest,
    NearbyResponse,
    RecommendedPlace,
    RecommendedPlaceRanking,
    StationInput,
    StationOutput,
    StationRecommendations,
)
from app.schemas.route import Location

logger = logging.getLogger(__name__)


def select_anchor_station(
    request: NearbyRequest,
    *,
    llm: LLMWrapper,
    max_anchor_count: int | None = None,
) -> list[StationInput]:
    """1. 기본적으로 입력 역 수의 절반까지 LLM으로 앵커 역을 선택한다."""
    if max_anchor_count is None:
        max_anchor_count = max(1, len(request.stations) // 2)
    if (
        isinstance(max_anchor_count, bool)
        or not isinstance(max_anchor_count, int)
        or max_anchor_count < 1
    ):
        raise ValueError("max_anchor_count는 1 이상의 정수여야 합니다.")
    max_anchor_count = min(max_anchor_count, len(request.stations))
    candidates = {
        f"candidate-{index}": station
        for index, station in enumerate(request.stations, start=1)
    }
    user_prompt = json.dumps(
        {
            "userRequest": request.userRequest,
            "travelTypes": request.travelTypes,
            "minAnchorCount": 1,
            "maxAnchorCount": max_anchor_count,
            "stations": [
                {"candidateId": candidate_id, **station.model_dump(mode="json")}
                for candidate_id, station in candidates.items()
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
    response_schema = AnchorStationSelection.model_json_schema()
    ids_schema = response_schema["properties"]["candidateIds"]
    ids_schema["maxItems"] = max_anchor_count
    ids_schema["items"]["enum"] = list(candidates)
    started_at = perf_counter()
    logger.info(
        "LLM 앵커 선정 시작: 후보=%d개, 선택 범위=1~%d개",
        len(candidates),
        max_anchor_count,
    )
    selection = cast(
        AnchorStationSelection,
        llm.request(
            system_prompt=ANCHOR_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_schema=response_schema,
            response_validator=AnchorStationSelection,
            model_env="LLM_NEARBY_MODEL",
        ),
    )
    if len(selection.candidateIds) > max_anchor_count:
        logger.warning(
            "LLM 앵커 선택 상한 초과: 선택=%d개, 상한=%d개",
            len(selection.candidateIds),
            max_anchor_count,
        )
        raise ProviderResponseError(
            f"LLM은 앵커 역을 최대 {max_anchor_count}개 선택할 수 있습니다."
        )
    unknown_ids = [value for value in selection.candidateIds if value not in candidates]
    if unknown_ids:
        logger.warning(
            "LLM 앵커 응답에 입력에 없는 후보 ID가 %d개 있음", len(unknown_ids)
        )
        raise ProviderResponseError(
            f"LLM이 입력에 없는 역을 선택했습니다: {', '.join(unknown_ids)}"
        )
    logger.info(
        "LLM 앵커 선정 완료: 선택=%d개, 후보=%s, 소요=%.2f초",
        len(selection.candidateIds),
        ",".join(selection.candidateIds),
        perf_counter() - started_at,
    )
    return [candidates[value].model_copy(deep=True) for value in selection.candidateIds]


def distance_meters(origin: Location, destination: Location) -> float:
    """구면 위 두 좌표의 직선거리. 도보 경로나 이동 시간은 계산하지 않는다."""
    lat1, lat2 = math.radians(origin.lat), math.radians(destination.lat)
    lat_delta = lat2 - lat1
    lng_delta = math.radians(destination.lng - origin.lng)
    value = (
        math.sin(lat_delta / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(lng_delta / 2) ** 2
    )
    return 2 * 6371008.8 * math.asin(math.sqrt(min(1.0, max(0.0, value))))


def normalize_place(
    raw: dict, tag: str, station: StationInput, radius: float
) -> RecommendedPlace | None:
    """3. Google 응답을 지도에 사용할 장소 정보로 정리한다."""
    if not isinstance(raw, dict):
        return None
    place_id = raw.get("id")
    display_name = raw.get("displayName")
    location = raw.get("location")
    if (
        not isinstance(place_id, str)
        or not place_id.strip()
        or not isinstance(display_name, dict)
        or not isinstance(display_name.get("text"), str)
        or not display_name["text"].strip()
        or not isinstance(location, dict)
    ):
        return None
    coordinates = location.get("latitude"), location.get("longitude")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in coordinates
    ):
        return None
    try:
        point = Location(lat=coordinates[0], lng=coordinates[1])
    except ValidationError:
        return None
    distance = distance_meters(station.location, point)
    if distance > radius:
        return None
    address, primary_type = raw.get("formattedAddress"), raw.get("primaryType")
    return RecommendedPlace(
        googlePlaceId=place_id,
        name=display_name["text"],
        location=point,
        address=address if isinstance(address, str) else None,
        primaryType=primary_type if isinstance(primary_type, str) else None,
        searchTags=[tag],
        distanceMeters=round(distance, 1),
    )


def rank_places_with_llm(
    request: NearbyRequest,
    candidates: list[RecommendedPlace],
    *,
    llm: LLMWrapper,
) -> list[RecommendedPlace]:
    """LLM으로 장소 후보를 정렬하고 선택한 인덱스를 장소로 복원한다."""
    selection_count = min(request.placeCount, len(candidates))
    user_prompt = json.dumps(
        {
            "userRequest": request.userRequest,
            "travelTypes": request.travelTypes,
            "placeCount": selection_count,
            "candidates": [
                {
                    "index": index,
                    "name": place.name,
                    "primaryType": place.primaryType,
                    "searchTags": place.searchTags,
                    "distanceMeters": place.distanceMeters,
                }
                for index, place in enumerate(candidates)
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    response_schema = RecommendedPlaceRanking.model_json_schema()
    indices_schema = response_schema["properties"]["indices"]
    indices_schema["minItems"] = selection_count
    indices_schema["maxItems"] = selection_count
    indices_schema["items"]["enum"] = list(range(len(candidates)))

    started_at = perf_counter()
    logger.info(
        "LLM 장소 재순위화 시작: 후보=%d개, 선택=%d개",
        len(candidates),
        selection_count,
    )
    ranking = cast(
        RecommendedPlaceRanking,
        llm.request(
            system_prompt=RERANK_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_schema=response_schema,
            response_validator=RecommendedPlaceRanking,
            model_env="LLM_NEARBY_MODEL",
        ),
    )

    if len(ranking.indices) != selection_count:
        raise ProviderResponseError(
            "LLM이 요청한 개수의 추천 인덱스를 반환하지 않았습니다."
        )
    if any(index >= len(candidates) for index in ranking.indices):
        raise ProviderResponseError("LLM이 후보에 없는 추천 인덱스를 반환했습니다.")

    logger.info(
        "LLM 장소 재순위화 완료: 선택=%d개, 소요=%.2f초",
        len(ranking.indices),
        perf_counter() - started_at,
    )
    return [candidates[index] for index in ranking.indices]


def recommend_for_station(
    request: NearbyRequest,
    station: StationInput,
    *,
    api_key: str,
    llm: LLMWrapper,
) -> StationRecommendations:
    """선택한 역 하나를 기준으로 최대 placeCount개의 장소를 추천한다."""
    started_at = perf_counter()
    logger.info(
        "역별 관광지 검색 시작: stationId=%s, 역=%s",
        station.stationId,
        station.name,
    )
    single_tag = len(request.travelTypes) == 1
    if single_tag:
        search_plan = [(request.travelTypes[0], request.placeCount)]
    else:
        budget = 2 * request.placeCount
        # 태그 수가 2N보다 많으면 이번 실행에서 조회할 태그도 무작위로 고른다.
        tags = random.sample(
            request.travelTypes, k=min(len(request.travelTypes), budget)
        )
        quota, remainder = divmod(budget, len(tags))
        search_plan = [
            (tag, quota + (index < remainder)) for index, tag in enumerate(tags)
        ]

    candidates: dict[str, RecommendedPlace] = {}
    for tag, count in search_plan:
        logger.info(
            "주변 검색 시작: stationId=%s, 태그=%s, 최대=%d개, 반경=%sm",
            station.stationId,
            tag,
            count,
            request.radius,
        )
        search_started_at = perf_counter()
        places = search_nearby(
            latitude=station.location.lat,
            longitude=station.location.lng,
            api_key=api_key,
            travel_type=tag,
            radius=request.radius,
            max_results=count,
        )
        logger.info(
            "주변 검색 완료: stationId=%s, 태그=%s, 수신=%d개, 소요=%.2f초",
            station.stationId,
            tag,
            len(places),
            perf_counter() - search_started_at,
        )
        for raw in places[:count]:
            place = normalize_place(raw, tag, station, request.radius)
            if place is None:
                continue
            existing = candidates.get(place.googlePlaceId)
            if existing is None:
                candidates[place.googlePlaceId] = place
            elif tag not in existing.searchTags:
                existing.searchTags.append(tag)

    candidate_places = list(candidates.values())
    if single_tag or len(candidate_places) <= 1:
        selected = candidate_places[: request.placeCount]
        selection_method = "거리순" if single_tag else "후보 순서"
    else:
        selected = rank_places_with_llm(request, candidate_places, llm=llm)
        selection_method = "LLM 재순위화"
    logger.info(
        "역별 추천 완료: stationId=%s, 유효한 중복 제거 후보=%d개, 추천=%d개, 방식=%s, 소요=%.2f초",
        station.stationId,
        len(candidates),
        len(selected),
        selection_method,
        perf_counter() - started_at,
    )
    return StationRecommendations(
        station=StationOutput(
            name=station.name,
            stationId=station.stationId,
            location=station.location,
        ),
        radius=request.radius,
        requestedCount=request.placeCount,
        returnedCount=len(selected),
        recommendedPlaces=selected,
    )


def recommend_nearby(request: NearbyRequest) -> NearbyResponse:
    """서버 설정으로 LLM 앵커 선정·역별 주변 검색·추천 응답 조립을 실행한다."""
    places_key = os.environ["GOOGLE_PLACE_API_KEY"].strip()
    with LLMWrapper() as llm:
        stations = select_anchor_station(
            request, llm=llm, max_anchor_count=request.maxAnchorCount
        )
        return NearbyResponse(
            results=[
                recommend_for_station(request, station, api_key=places_key, llm=llm)
                for station in stations
            ]
        )
