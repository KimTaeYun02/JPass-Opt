"""Offline comparison of fixed-time ordering and recorded sequential schedules."""

from copy import deepcopy
from datetime import timedelta
from itertools import permutations
from pathlib import Path
import tempfile
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from .adapters.ekispert import RawSearch
from .evaluator import evaluate
from .gateway import Context, Gateway
from .ledger import Ledger
from .models import DTO, PlanRequest, Text, digest, instant
from .ordering import local_candidates
from .replay import Binding, ReplayProvider
from .service import Planner
import time


class Scenario(DTO):
    schemaVersion: Literal["sequential-scenario/1"]
    request: PlanRequest
    bindings: list[Binding] = Field(min_length=3, max_length=3)
    assumptions: list[Text] = Field(min_length=1, max_length=16)
    sources: list[Text] = Field(max_length=16)

    @model_validator(mode="after")
    def comparable_scope(self):
        request = self.request
        if (len(request.places) != 3 or request.dataSource != "replay" or request.mode != "reference"
                or request.endBy is not None or request.candidateIndex != 0):
            raise ValueError("첫 비교는 종료 제한 없는 관광지 3개 참고 모드입니다.")
        options = request.options
        if options.allowRepair or options.allowPassEvaluation or options.allowCache or options.allowPrefixReuse:
            raise ValueError("비교 시험은 개선·패스·캐시·접두부 재사용을 사용하지 않습니다.")
        if any(p.anchorRole or p.openingWindows is not None or p.fixedVisitAt for p in request.places):
            raise ValueError("기존 방식과 같은 자유 시작·종료 및 미확인 영업시간 조건이 필요합니다.")
        prepared = instant(request.startAt).astimezone(ZoneInfo("Asia/Tokyo"))
        if (prepared.hour, prepared.minute, prepared.second, prepared.microsecond) != (9, 0, 0, 0):
            raise ValueError("고정 행렬의 기준은 같은 날 일본 시간 09:00입니다.")
        places = {p.clientPlaceId: p for p in request.places}
        if len({b.place.clientPlaceId for b in self.bindings}) != 3:
            raise ValueError("중복 장소 바인딩입니다.")
        identities = set()
        for binding in self.bindings:
            if binding.place != places.get(binding.place.clientPlaceId) or len(binding.candidates) != 1:
                raise ValueError("입력과 동일한 장소별 고정 역 1개가 필요합니다.")
            candidate = binding.candidates[0]
            if candidate.access.status != "estimated" or candidate.egress.status != "estimated":
                raise ValueError("이번 비교의 도보는 명시적 추정값이어야 합니다.")
            identities.add(candidate.station.identity())
        if len(identities) != 3:
            raise ValueError("이 시험은 서로 다른 역 3개를 비교합니다.")
        return self


def geometric_order(scenario: Scenario) -> list[str]:
    """Select before observing any rail matrix."""
    return local_candidates(scenario.request)["orders"][0]["order"]


def fastest_course(payload: dict) -> tuple[int, dict]:
    parsed = RawSearch.model_validate(payload)
    valid = [(course.time, course.fee, index, course) for index, course in enumerate(parsed.routes)
             if course.time is not None and course.fee is not None]
    if any(any(point.line is None for point in course.route[:-1]) or course.route[-1].line is not None
           for _,_,_,course in valid):
        raise ValueError("legacy_invalid_line_fields")
    if not valid:
        raise ValueError("legacy_no_valid_time_and_fare")
    _, _, index, course = min(valid, key=lambda row: row[:3])
    return index, course.model_dump(mode="json")


def fixed_time_order(ids: list[str], edges: list[dict]) -> dict:
    """Exact n=3 equivalent of v1: duration, fare, UTF-16 ID order."""
    if len(ids) != 3 or len(set(ids)) != 3:
        raise ValueError("three_unique_visit_ids_required")
    costs = {}
    for edge in edges:
        key = edge["from"], edge["to"]
        if key in costs or key[0] not in ids or key[1] not in ids or key[0] == key[1]:
            raise ValueError("duplicate_or_invalid_snapshot_edge")
        index, result = fastest_course(edge["payload"])
        costs[key] = {"from":key[0], "to":key[1], "routeIndex":index, "result":result}
    expected = {(a,b) for a in ids for b in ids if a != b}
    if set(costs) != expected:
        raise ValueError("incomplete_snapshot_matrix")
    scored = []
    for order in permutations(sorted(ids, key=lambda value:value.encode("utf-16-be"))):
        legs = [costs[a,b] for a,b in zip(order,order[1:])]
        scored.append((sum(l["result"]["time"] for l in legs), sum(l["result"]["fee"] for l in legs),
                       tuple(value.encode("utf-16-be") for value in order), list(order), legs))
    duration, fare, _, order, legs = min(scored, key=lambda row:row[:3])
    return {"order":order, "displayedTravelMinutes":duration, "grossFare":fare, "legs":deepcopy(legs),
            "evaluatedOrders":len(scored), "objective":"displayed-duration-then-fare", "queryBasis":"fixed-09:00"}


def schedule_metrics(route: dict | None) -> dict:
    if route is None or route["completion"] != "complete":
        return {"completion":"unknown" if route is None else route["completion"], "elapsedMinutes":None,
                "initialWaitMinutes":None, "rideMinutes":None, "walkingMinutes":None, "stayMinutes":None,
                "grossFare":None, "finishAt":None}
    legs = route["legs"]
    return {"completion":"complete", "order":route["order"], "elapsedMinutes":route["elapsedMinutes"],
            "initialWaitMinutes":sum(l["initialWaitMinutes"] for l in legs),
            "rideMinutes":sum(l["rideMinutes"] for l in legs),
            "walkingMinutes":sum(l[side]["minutes"] for l in legs for side in ("accessWalking","egressWalking")),
            "stayMinutes":sum(v["stayMinutes"] for v in route["visits"]),
            "grossFare":route["fare"]["noPassTotal"], "finishAt":route["finishAt"],
            "timing":route["timing"], "optimality":"not-guaranteed"}


async def replay_v2(scenario: Scenario, dataset_path: Path) -> dict:
    raw = scenario.request.model_dump(mode="json")
    with tempfile.TemporaryDirectory() as folder:
        ledger = Ledger(Path(folder)/"ledger.sqlite3")
        raw["sessionId"] = ledger.create_session()["sessionId"]
        return await Planner(ledger, ReplayProvider(dataset_path), retries=0).plan(PlanRequest.model_validate(raw), raw)


async def replay_selected_order(scenario: Scenario, dataset_path: Path, order: list[str], *, timeout_seconds=300) -> dict:
    """Re-evaluate the old chosen order with the same v2 timing rules."""
    if sorted(order) != sorted(p.clientPlaceId for p in scenario.request.places):
        raise ValueError("replay_order_must_preserve_all_visits")
    if not 0 < timeout_seconds <= 300:
        raise ValueError("재생 기한은 최대 300초입니다.")
    provider = ReplayProvider(dataset_path)
    with tempfile.TemporaryDirectory() as folder:
        ledger = Ledger(Path(folder)/"ledger.sqlite3")
        session = ledger.create_session()["sessionId"]
        operation = "legacy-order-validation"
        ledger.begin(session, operation, digest(order), {"rail":len(order)-1,"station":len(order),"walk":0,"rag":0})
        context = Context(ledger, session, operation, time.monotonic()+timeout_seconds)
        gateway = Gateway(provider, retries=0)
        choices = {}
        try:
            for place in scenario.request.places:
                choices[place.clientPlaceId] = (await gateway.call(context, "station", lambda place=place:provider.stations(place)))[0]
            route = await evaluate(scenario.request, order, choices, gateway, context)
            result = {"dataSource":"replay", "selected":route, "ledger":ledger.snapshot(session, operation)}
            result["ledger"]["measurementBasis"] = "replay-no-network"
            return result
        finally:
            ledger.finish(session, operation)


def compare_schedules(fixed: dict | None, old_route: dict | None, new_route: dict | None) -> dict:
    old, new = schedule_metrics(old_route), schedule_metrics(new_route)
    complete = old["completion"] == new["completion"] == "complete"
    minutes = old["elapsedMinutes"]-new["elapsedMinutes"] if complete else None
    fares_known = complete and old["grossFare"] is not None and new["grossFare"] is not None
    return {"status":"complete" if complete and fixed is not None else "partial",
            "legacyFixed0900":fixed, "legacyOrderAtActualTimes":old, "v2AtActualTimes":new,
            "v2ElapsedMinutesSaved":minutes,
            "v2GrossFareSaved":old["grossFare"]-new["grossFare"] if fares_known else None,
            "timingOutcome":"unknown" if minutes is None else "same" if minutes == 0 else "shorter" if minutes > 0 else "longer",
            "scope":"one-three-place-case-with-estimated-walks-and-fixed-station-bindings",
            "optimality":"not-guaranteed", "passSavingsVerified":False}
