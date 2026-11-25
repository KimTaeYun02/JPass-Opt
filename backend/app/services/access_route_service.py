"""유향 접근 도보를 비교한 역 선택과 관광지 간 고정 비용 최적화.

역 조합 전체를 탐색하지 않는다. 방향별 접근 시간이 가장 짧은 후보를 택한다.
직접 도보 비교는 가까운 12방향으로 제한하고 범위를 응답에 표시한다.
"""

import asyncio
import os
from contextlib import AsyncExitStack
from datetime import datetime, time, timedelta
from itertools import permutations

from app.clients.google_routes import walking_route
from app.clients.heartrails import get_station_candidates
from app.crawling import browser_page, crawl_route
from app.errors import (
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from app.optimizer import optimize_visit_orders, quotes_from_segments
from app.schemas.route import Leg, OptimizedDay, RouteResponse, Station, Visit
from app.services.nearby_service import distance_meters
from app.services.route_service import (
    JAPAN_TIMEZONE,
    RouteDataError,
    find_route_station,
)

MAX_STATION_CANDIDATES = 3
MAX_DIRECT_WALKS = 12
DIRECT_WALK_METERS = 1200


def station_key(station):
    return (
        station.name,
        station.location.lat,
        station.location.lng,
        station.prefecture,
    )


def point(item, kind):
    return {"kind": kind, "name": item.name, "location": item.location.model_dump()}


def walk_segment(origin, destination, route):
    return {
        "fromPoint": origin,
        "toPoint": destination,
        "mode": "walking",
        "durationMinutes": route.minutes,
        "distanceMeters": route.distance,
        "path": route.path,
    }


def candidates_for_day(day):
    result, cache, names = {}, {}, {}
    for index, place in enumerate(day.places, 1):
        key = (place.location.lat, place.location.lng)
        if key not in cache:
            cache[key] = [
                Station(
                    name=row["name"],
                    location={"lat": row["latitude"], "lng": row["longitude"]},
                    prefecture=row.get("prefecture"),
                )
                for row in get_station_candidates(*key, limit=MAX_STATION_CANDIDATES)
            ]
        if not cache[key]:
            raise RouteDataError(f"{place.name}: 근처 역이 없습니다.")
        for station in cache[key]:
            identity = station_key(station)
            if station.name in names and names[station.name] != identity:
                raise RouteDataError(
                    f"{station.name}: 동명이역 구분이 필요한 경로입니다."
                )
            names[station.name] = identity
        result[f"day-{day.dayIndex}-place-{index}"] = cache[key]
    return result


async def build_access_day(day, day_date, start_at, api_key, browser_stack):
    candidates = await asyncio.to_thread(candidates_for_day, day)
    places = dict(zip(candidates, day.places))
    if len(places) == 1:
        visit_id, place = next(iter(places.items()))
        return OptimizedDay(
            dayIndex=day.dayIndex,
            date=day_date,
            startAt=start_at,
            endAt=start_at,
            visits=[
                Visit(
                    visitId=visit_id,
                    order=1,
                    name=place.name,
                    location=place.location,
                    googlePlaceId=place.googlePlaceId,
                    station=candidates[visit_id][0],
                    estimatedArrivalAt=start_at,
                    estimatedDepartureAt=None,
                )
            ],
            legs=[],
            totalTravelMinutes=0,
            totalTransportFare=0,
            candidateSearchTruncated=False,
        )

    semaphore = asyncio.Semaphore(4)
    walk_cache = {}

    async def lookup_walk(origin, destination):
        async with semaphore:
            return await asyncio.to_thread(walking_route, origin, destination, api_key)

    async def walk(origin, destination):
        a, b = origin.location.model_dump(), destination.location.model_dump()
        key = (a["lat"], a["lng"], b["lat"], b["lng"])
        if key not in walk_cache:
            walk_cache[key] = asyncio.create_task(lookup_walk(a, b))
        return await walk_cache[key]

    async def access_candidate(visit_id, station):
        outbound, inbound = await asyncio.gather(
            walk(places[visit_id], station), walk(station, places[visit_id])
        )
        return station, outbound, inbound

    try:
        choices = await asyncio.gather(
            *(
                access_candidate(visit_id, station)
                for visit_id, rows in candidates.items()
                for station in rows
            )
        )
        departures, arrivals = {}, {}
        offset = 0
        for visit_id, rows in candidates.items():
            options = choices[offset : offset + len(rows)]
            offset += len(rows)
            outgoing = [row for row in options if row[1] is not None]
            incoming = [row for row in options if row[2] is not None]
            if not outgoing or not incoming:
                raise RouteDataError(
                    f"{places[visit_id].name}: 역까지 유효한 도보 경로를 찾지 못했습니다."
                )
            departures[visit_id] = min(
                outgoing,
                key=lambda row: (
                    row[1].minutes,
                    row[1].distance,
                    station_key(row[0])[:3],
                ),
            )[:2]
            chosen = min(
                incoming,
                key=lambda row: (
                    row[2].minutes,
                    row[2].distance,
                    station_key(row[0])[:3],
                ),
            )
            arrivals[visit_id] = (chosen[0], chosen[2])

        # 동일 역 공유 쌍을 먼저, 나머지는 가까운 쌍부터 제한적으로 비교한다.
        pairs = list(permutations(places, 2))
        direct_pairs = []
        for a, b in pairs:
            distance = distance_meters(places[a].location, places[b].location)
            shared = station_key(departures[a][0]) == station_key(arrivals[b][0])
            if shared or distance <= DIRECT_WALK_METERS:
                direct_pairs.append((not shared, distance, a, b))
        direct_pairs.sort()
        selected_pairs = direct_pairs[:MAX_DIRECT_WALKS]
        direct_routes = await asyncio.gather(
            *(walk(places[a], places[b]) for _, _, a, b in selected_pairs)
        )
        direct = {
            (a, b): route for (_, _, a, b), route in zip(selected_pairs, direct_routes)
        }

        station_cache, quotes = {}, []
        known_stations = {
            station.name: station for rows in candidates.values() for station in rows
        }
        coordinate_cache = {}
        page = None
        for a, b in pairs:
            origin, outward = departures[a]
            destination, inward = arrivals[b]
            candidate = direct.get((a, b))
            key = (station_key(origin), station_key(destination))
            transit, route = None, []
            access_minutes = outward.minutes + inward.minutes
            # 음수가 아닌 역 간 비용이 직접 도보보다 나을 수 없으면 조회하지 않는다.
            if candidate is None or candidate.minutes > access_minutes:
                if key[0] == key[1]:
                    transit = {"time": 0, "fee": 0, "route": []}
                else:
                    if key not in station_cache:
                        if page is None:
                            page = await browser_stack.enter_async_context(
                                browser_page(headless=False)
                            )
                        raw = await crawl_route(
                            page,
                            origin.name,
                            destination.name,
                            departure_time=start_at,
                        )
                        if not isinstance(raw, dict) or not isinstance(
                            raw.get("routes"), list
                        ):
                            raise ProviderResponseError(
                                "역 간 경로 응답 형식이 올바르지 않습니다."
                            )
                        try:
                            selected, _ = quotes_from_segments(
                                [
                                    {
                                        "departure": origin.name,
                                        "arrival": destination.name,
                                        "routes": raw["routes"],
                                    }
                                ]
                                if raw["routes"]
                                else [],
                                selection="fastest",
                            )
                            station_cache[key] = (
                                selected[0]["result"] if selected else None
                            )
                        except ValueError as exc:
                            raise RouteDataError(
                                f"{origin.name} → {destination.name}: 유효한 역 간 경로가 없습니다."
                            ) from exc
                    transit = station_cache[key]
                    if transit is None and candidate is None:
                        raise RouteDataError(
                            f"{origin.name} → {destination.name}: 유효한 이동 경로가 없습니다."
                        )
            use_direct = candidate is not None and (
                transit is None
                or (candidate.minutes, 0)
                <= (access_minutes + transit["time"], transit["fee"])
            )
            if use_direct:
                segments = [
                    walk_segment(
                        point(places[a], "place"), point(places[b], "place"), candidate
                    )
                ]
                minutes, fare = candidate.minutes, 0
            else:
                segments = [
                    walk_segment(
                        point(places[a], "place"), point(origin, "station"), outward
                    )
                ]
                if transit["time"] or key[0] != key[1]:
                    segments.append(
                        {
                            "fromPoint": point(origin, "station"),
                            "toPoint": point(destination, "station"),
                            "mode": "transit",
                            "durationMinutes": transit["time"],
                            "distanceMeters": None,
                            "path": None,
                        }
                    )
                segments.append(
                    walk_segment(
                        point(destination, "station"), point(places[b], "place"), inward
                    )
                )
                minutes, fare = access_minutes + transit["time"], transit["fee"]
                route = transit["route"]
            quotes.append(
                {
                    "from": a,
                    "to": b,
                    "stations": {"from": origin.name, "to": destination.name},
                    "source": "snapshot",
                    "result": {"time": minutes, "fee": fare, "route": []},
                    "segments": segments,
                    "transportRoute": route,
                }
            )
        plan = optimize_visit_orders(list(places), quotes, limit=1)[0]
        visits, legs, current = [], [], start_at
        for index, visit_id in enumerate(plan["order"]):
            place = places[visit_id]
            visits.append(
                Visit(
                    visitId=visit_id,
                    order=index + 1,
                    name=place.name,
                    location=place.location,
                    googlePlaceId=place.googlePlaceId,
                    station=candidates[visit_id][0],
                    estimatedArrivalAt=current,
                    estimatedDepartureAt=current if index < len(plan["legs"]) else None,
                )
            )
            if index < len(plan["legs"]):
                quote = plan["legs"][index]
                try:
                    end_at = current + timedelta(minutes=quote["result"]["time"])
                except OverflowError as exc:
                    raise RouteDataError(
                        "이동 후 시각이 지원하는 날짜 범위를 초과합니다."
                    ) from exc
                transport_route = []
                raw_route = quote["transportRoute"]
                for start, end in zip(raw_route, raw_route[1:]):
                    start_station = await asyncio.to_thread(
                        find_route_station,
                        start["station"],
                        start["line"],
                        known_stations,
                        coordinate_cache,
                    )
                    end_station = await asyncio.to_thread(
                        find_route_station,
                        end["station"],
                        start["line"],
                        known_stations,
                        coordinate_cache,
                    )
                    transport_route.append(
                        {
                            "fromStation": start_station,
                            "toStation": end_station,
                            "line": start["line"],
                        }
                    )
                legs.append(
                    Leg(
                        fromVisitId=quote["from"],
                        toVisitId=quote["to"],
                        departureAt=current,
                        arrivalAt=end_at,
                        durationMinutes=quote["result"]["time"],
                        fare=quote["result"]["fee"],
                        route=transport_route,
                        segments=quote["segments"],
                    )
                )
                current = end_at
        return OptimizedDay(
            dayIndex=day.dayIndex,
            date=day_date,
            startAt=start_at,
            endAt=current,
            visits=visits,
            legs=legs,
            totalTravelMinutes=plan["time"],
            totalTransportFare=plan["fee"],
            candidateSearchTruncated=len(direct_pairs) > MAX_DIRECT_WALKS,
        )
    finally:
        # 실패 후 대기 중인 추가 조회를 계속 실행하지 않는다.
        for task in walk_cache.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*walk_cache.values(), return_exceptions=True)


async def optimize_with_access(request):
    api_key = os.getenv("GOOGLE_ROUTES_API_KEY", "").strip()
    if not api_key and any(len(day.places) > 1 for day in request.days):
        raise ProviderConfigurationError("Google 도보 경로 설정이 필요합니다.")
    days = []
    try:
        async with asyncio.timeout(540):
            for day in sorted(request.days, key=lambda row: row.dayIndex):
                date = request.startDate + timedelta(days=day.dayIndex - 1)
                start = datetime.combine(
                    date, time.fromisoformat(day.dailyStartTime), tzinfo=JAPAN_TIMEZONE
                )
                async with AsyncExitStack() as browser_stack:
                    days.append(
                        await build_access_day(day, date, start, api_key, browser_stack)
                    )
    except TimeoutError as exc:
        raise ProviderTimeoutError(
            "도보 포함 동선 계산 시간이 초과되었습니다."
        ) from exc
    return RouteResponse(
        startDate=request.startDate,
        dayCount=request.dayCount,
        timingBasis="placeTravelOnly",
        days=days,
        totalTravelMinutes=sum(day.totalTravelMinutes for day in days),
        totalTransportFare=sum(day.totalTransportFare for day in days),
    )
