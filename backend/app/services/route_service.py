"""근처 역 조회 → 경로 수집 → 방문 순서 최적화 → 환승역 좌표 보완."""

import asyncio
import logging
from copy import deepcopy
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.clients.heartrails import get_nearest_stations, get_stations
from app.crawling import crawl_route
from app.optimizer import optimize_visit_order, quotes_from_segments
from app.schemas.route import (
    Leg,
    Location,
    OptimizedDay,
    RouteRequest,
    RouteResponse,
    Station,
    TransportStep,
    Visit,
)

JAPAN_TIMEZONE = ZoneInfo("Asia/Tokyo")
logger = logging.getLogger(__name__)


class RouteDataError(ValueError):
    """입력한 여행지로 유효한 동선을 만들 수 없다."""


def find_stations(day):
    """1. 관광지마다 가장 가까운 역 하나를 연결한다. 방문 ID는 역과 별개다."""
    stations = {}
    for index, place in enumerate(day.places, start=1):
        visit_id = f"day-{day.dayIndex}-place-{index}"
        results = get_nearest_stations(place.location.lat, place.location.lng, limit=1)
        if not results:
            raise RouteDataError(
                f"{day.dayIndex}일차 {place.name}: 근처 역이 없습니다."
            )
        raw = results[0]
        stations[visit_id] = Station(
            name=raw["name"],
            location=Location(lat=raw["latitude"], lng=raw["longitude"]),
            prefecture=raw.get("prefecture"),
        )

    # 크롤러가 역 이름만 받으므로, 동명이역을 같은 역으로 취급하지 않는다.
    by_name = {}
    for station in stations.values():
        if station.name in by_name and station != by_name[station.name]:
            raise RouteDataError(
                f"{station.name}: 동명이역 구분이 필요합니다. 크롤러 입력 확장 TODO"
            )
        by_name[station.name] = station
    return stations


async def collect_quotes(stations, start_at):
    """2. 모든 방향의 역 간 후보를 조회하고 구간마다 가장 빠른 후보를 고른다."""
    quotes, cache = [], {}
    for from_id, departure in stations.items():
        for to_id, arrival in stations.items():
            if from_id == to_id:
                continue
            key = departure.name, arrival.name
            if key not in cache:
                if departure.name == arrival.name:
                    # 서로 다른 관광지라도 같은 역이면 역 간 비용만 0이다.
                    # TODO: 실제 관광지 사이 도보 경로/시간은 별도 조회해야 한다.
                    cache[key] = {
                        "time": 0,
                        "fee": 0,
                        "route": [
                            {"station": departure.name, "line": "동일 역"},
                            {"station": arrival.name, "line": None},
                        ],
                    }
                else:
                    logger.info("경로 조회: %s → %s (%s)", *key, start_at.isoformat())
                    # 이 환경은 Xvfb에서 화면 모드 Chromium으로 크롤링한다.
                    result = await crawl_route(
                        *key, departure_time=start_at, headless=False
                    )
                    try:
                        selected, _ = quotes_from_segments(
                            [
                                {
                                    "departure": key[0],
                                    "arrival": key[1],
                                    "routes": result["routes"],
                                }
                            ],
                            selection="fastest",
                        )
                    except ValueError as exc:
                        raise RouteDataError(
                            f"{key[0]} → {key[1]}: 유효한 이동 경로가 없습니다."
                        ) from exc
                    cache[key] = selected[0]["result"]
            quotes.append(
                {
                    "from": from_id,
                    "to": to_id,
                    "stations": {"from": departure.name, "to": arrival.name},
                    "source": "snapshot",
                    "result": deepcopy(cache[key]),
                }
            )
    return quotes


def find_route_station(name, line, known_stations, cache):
    """관광지 근처 역을 재사용하고, 환승역은 노선 → 역명 순으로 조회한다."""
    if name in known_stations:
        return known_stations[name]
    key = name, line
    if key not in cache:
        logger.info("환승역 좌표 조회: %s (%s)", name, line)
        results = get_stations(name, line=line)
        if not results:
            raise RouteDataError(
                f"{name} ({line}): HeartRails에서 역 좌표를 찾을 수 없습니다."
            )
        raw = results[0]
        cache[key] = Station(
            name=name,
            location=Location(lat=raw["latitude"], lng=raw["longitude"]),
            prefecture=raw.get("prefecture"),
        )
    return cache[key]


def build_day_output(day, day_date, start_at, stations, plan):
    """4. 최적 순서에 원래 관광지 정보를 붙이고 이동 기준 일시/요금을 정리한다."""
    places = {
        f"day-{day.dayIndex}-place-{index}": place
        for index, place in enumerate(day.places, start=1)
    }
    station_by_name = {station.name: station for station in stations.values()}
    transfer_stations = {}
    visits, legs = [], []
    current = start_at
    for order, visit_id in enumerate(plan["order"], start=1):
        place = places[visit_id]
        visits.append(
            Visit(
                visitId=visit_id,
                order=order,
                name=place.name,
                location=place.location,
                googlePlaceId=place.googlePlaceId,
                station=stations[visit_id],
                estimatedArrivalAt=current,
                estimatedDepartureAt=current if order < len(plan["order"]) else None,
            )
        )
        if order == len(plan["order"]):
            break
        quote = plan["legs"][order - 1]
        result = quote["result"]
        arrival_at = current + timedelta(minutes=result["time"])
        route = []
        if quote["stations"]["from"] != quote["stations"]["to"]:
            for origin, destination in zip(result["route"], result["route"][1:]):
                route.append(
                    TransportStep(
                        fromStation=find_route_station(
                            origin["station"],
                            origin["line"],
                            station_by_name,
                            transfer_stations,
                        ),
                        toStation=find_route_station(
                            destination["station"],
                            origin["line"],
                            station_by_name,
                            transfer_stations,
                        ),
                        line=origin["line"],
                    )
                )
        legs.append(
            Leg(
                fromVisitId=quote["from"],
                toVisitId=quote["to"],
                departureAt=current,
                arrivalAt=arrival_at,
                durationMinutes=result["time"],
                fare=result["fee"],
                route=route,
            )
        )
        current = arrival_at
    return OptimizedDay(
        dayIndex=day.dayIndex,
        date=day_date,
        startAt=start_at,
        endAt=current,
        visits=visits,
        legs=legs,
        totalTravelMinutes=plan["time"],
        totalTransportFare=plan["fee"],
    )


async def optimize_route(request: RouteRequest) -> RouteResponse:
    """근처 역 → 경로 수집 → 최적화 → 응답. 일차별로 독립 실행한다."""
    days = []
    for day in sorted(request.days, key=lambda item: item.dayIndex):
        day_date = request.startDate + timedelta(days=day.dayIndex - 1)
        start_at = datetime.combine(
            day_date, time.fromisoformat(day.dailyStartTime), tzinfo=JAPAN_TIMEZONE
        )
        logger.info("%s일차: 근처 역 조회", day.dayIndex)
        stations = await asyncio.to_thread(find_stations, day)
        quotes = await collect_quotes(stations, start_at)

        # 3. 역 이름이 아닌 장소 ID를 최적화하므로 같은 역의 관광지도 유지된다.
        if len(stations) == 1:
            plan = {"order": list(stations), "legs": [], "time": 0, "fee": 0}
        else:
            try:
                plan = optimize_visit_order(list(stations), quotes)
            except ValueError as exc:
                raise RouteDataError(
                    "입력한 관광지의 동선을 계산할 수 없습니다."
                ) from exc
        days.append(
            await asyncio.to_thread(
                build_day_output, day, day_date, start_at, stations, plan
            )
        )

    return RouteResponse(
        startDate=request.startDate,
        dayCount=request.dayCount,
        days=days,
        totalTravelMinutes=sum(day.totalTravelMinutes for day in days),
        totalTransportFare=sum(day.totalTransportFare for day in days),
    )
