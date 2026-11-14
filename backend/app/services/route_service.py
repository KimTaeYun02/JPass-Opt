"""근처 역 조회 → 경로 수집 → 방문 순서 최적화 → 좌표 보완 → 응답 조립."""

import asyncio
import logging
from contextlib import AsyncExitStack
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from playwright.async_api import Page

from app.clients.heartrails import get_nearest_stations, get_stations
from app.crawling import browser_page, crawl_route
from app.errors import ProviderResponseError
from app.optimizer import optimize_visit_orders, quotes_from_segments
from app.optimizer.types import Plan, Quote, RouteResult, StationPair
from app.schemas.route import (
    DayInput,
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


def find_stations(day: DayInput) -> dict[str, Station]:
    """1. 관광지마다 가장 가까운 역 하나를 연결한다. 방문 ID는 역과 별개다."""
    stations: dict[str, Station] = {}
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
    by_name: dict[str, Station] = {}
    for station in stations.values():
        if station.name in by_name and station != by_name[station.name]:
            raise RouteDataError(
                f"{station.name}: 동명이역 구분이 필요합니다. 크롤러 입력 확장 TODO"
            )
        by_name[station.name] = station
    return stations


async def collect_quotes(
    stations: dict[str, Station], start_at: datetime
) -> list[Quote]:
    """2. 필요한 경우 페이지 하나를 열어 모든 방향의 역 간 후보를 조회한다."""
    quotes: list[Quote] = []
    cache: dict[tuple[str, str], RouteResult] = {}
    async with AsyncExitStack() as stack:
        page: Page | None = None
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
                        logger.info(
                            "경로 조회: %s → %s (%s)", *key, start_at.isoformat()
                        )
                        if page is None:
                            # 첫 실제 조회에서만 열고, 수집이 끝나면 정리한다.
                            page = await stack.enter_async_context(
                                browser_page(headless=False)
                            )
                        result = await crawl_route(page, *key, departure_time=start_at)
                        if not result["routes"]:
                            raise RouteDataError(
                                f"{key[0]} → {key[1]}: 이동 경로가 없습니다."
                            )
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


def find_route_stations(
    stations: dict[str, Station], plan: Plan
) -> dict[tuple[str, str], Station]:
    """4. 선택 경로의 역 좌표를 준비한다. 관광지 근처 역은 재사용한다."""
    known_stations = {station.name: station for station in stations.values()}
    route_stations: dict[tuple[str, str], Station] = {}
    for quote in plan["legs"]:
        # collect_quotes()에서 채운 역 정보는 최적화 이후에도 유지된다.
        pair = cast(StationPair, quote.get("stations"))
        if pair["from"] == pair["to"]:
            continue
        route = quote["result"]["route"]
        for origin, destination in zip(route, route[1:]):
            # 마지막 역은 출발점에서 제외되며, 나머지 노선은 이미 검증됐다.
            line = cast(str, origin["line"])
            for name in (origin["station"], destination["station"]):
                key = name, line
                if key in route_stations:
                    continue
                if name in known_stations:
                    route_stations[key] = known_stations[name]
                    continue
                logger.info("환승역 좌표 조회: %s (%s)", name, line)
                results = get_stations(name, line=line)
                if not results:
                    raise ProviderResponseError(
                        f"{name} ({line}): HeartRails에서 역 좌표를 찾을 수 없습니다."
                    )
                raw = results[0]
                route_stations[key] = Station(
                    name=name,
                    location=Location(lat=raw["latitude"], lng=raw["longitude"]),
                    prefecture=raw.get("prefecture"),
                )
    return route_stations


def build_day_output(
    day: DayInput,
    day_date: date,
    start_at: datetime,
    stations: dict[str, Station],
    plan: Plan,
    route_stations: dict[tuple[str, str], Station],
) -> OptimizedDay:
    """5. 준비된 좌표와 최적 순서로 방문·이동 일시와 요금 응답을 조립한다."""
    places = {
        f"day-{day.dayIndex}-place-{index}": place
        for index, place in enumerate(day.places, start=1)
    }
    visits: list[Visit] = []
    legs: list[Leg] = []
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
        try:
            arrival_at = current + timedelta(minutes=result["time"])
        except OverflowError as exc:
            raise RouteDataError(
                f"{day.dayIndex}일차: 이동 시간을 더한 도착 일시가 지원하는 날짜 범위를 초과했습니다."
            ) from exc
        route: list[TransportStep] = []
        pair = cast(StationPair, quote.get("stations"))
        if pair["from"] != pair["to"]:
            for origin, destination in zip(result["route"], result["route"][1:]):
                line = cast(str, origin["line"])
                route.append(
                    TransportStep(
                        fromStation=route_stations[origin["station"], line],
                        toStation=route_stations[destination["station"], line],
                        line=line,
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
    """근처 역 → 경로 수집 → 최적화 → 좌표 보완 → 응답. 일차별로 독립 실행한다."""
    days: list[OptimizedDay] = []
    for day in sorted(request.days, key=lambda item: item.dayIndex):
        day_date = request.startDate + timedelta(days=day.dayIndex - 1)
        start_at = datetime.combine(
            day_date, time.fromisoformat(day.dailyStartTime), tzinfo=JAPAN_TIMEZONE
        )
        logger.info("%s일차: 근처 역 조회", day.dayIndex)
        # 1. 관광지마다 가장 가까운 역 하나를 연결한다. 방문 ID는 역과 별개다.
        stations = await asyncio.to_thread(find_stations, day)
        # 2. 모든 방향의 역 간 후보를 조회하고 구간마다 가장 빠른 후보를 고른다.
        quotes = await collect_quotes(stations, start_at)

        # 3. 역 이름이 아닌 장소 ID를 최적화하므로 같은 역의 관광지도 유지된다.
        plan: Plan
        if len(stations) == 1:
            plan = {"order": list(stations), "legs": [], "time": 0, "fee": 0}
        else:
            plan = optimize_visit_orders(list(stations), quotes, limit=1)[0]
        # 4. 선택한 경로의 역과 환승역 좌표를 조회한다.
        route_stations = await asyncio.to_thread(find_route_stations, stations, plan)
        # 5. 외부 조회 없이 준비된 데이터로 응답을 조립한다.
        days.append(
            build_day_output(day, day_date, start_at, stations, plan, route_stations)
        )

    return RouteResponse(
        startDate=request.startDate,
        dayCount=request.dayCount,
        days=days,
        totalTravelMinutes=sum(day.totalTravelMinutes for day in days),
        totalTransportFare=sum(day.totalTransportFare for day in days),
    )
