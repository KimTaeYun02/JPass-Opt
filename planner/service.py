"""V2 orchestration: local orders, one baseline, bounded repairs, one pass batch."""

from copy import deepcopy
import time

from .evaluator import constraint, evaluate, finish_route
from .gateway import Context, Gateway
from .ledger import Ledger, PlanError, Stopped
from .mobility_data import MobilityData, MobilityFile
from .models import PlanRequest, StationCandidate, digest, instant, rail_budget
from .multimodal import LegSelector
from .order_alternates import can_select, deadline_targets, feasible, select_alternate
from .ordering import distance, local_candidates
from .pass_order_hints import PassOrderHints
from .pricing import compare_passes


class Planner:
    def __init__(self, ledger: Ledger, provider, *, timeout_seconds=300, monotonic=time.monotonic, retries=1,
                 cluster_diameter_m=800, pass_order_hints: PassOrderHints | None = None, mobility=None):
        if not 0 < timeout_seconds <= 300:
            raise ValueError("작업 기한은 0~300초 범위입니다.")
        self.ledger, self.provider = ledger, provider
        self.timeout_seconds, self.monotonic = timeout_seconds, monotonic
        self.cluster_diameter_m = cluster_diameter_m
        self.pass_order_hints = pass_order_hints
        self.gateway = Gateway(provider, retries=retries, monotonic=monotonic)
        self.mobility = mobility if mobility is not None else MobilityData(MobilityFile(source=provider.dataSource))

    async def plan(self, request: PlanRequest, echo_input: dict | None = None) -> dict:
        started = self.monotonic()
        if request.dataSource == "live":
            raise PlanError("LIVE_UNSUPPORTED", 501)
        if request.dataSource != self.provider.dataSource:
            raise PlanError("SOURCE_MISMATCH")
        if request.fixtureId != self.provider.fixture_id:
            raise PlanError("UNKNOWN_FIXTURE")
        if request.options.allowMultimodal and self.mobility.source != request.dataSource:
            raise PlanError("MOBILITY_SOURCE_MISMATCH")
        n = len(request.places)
        rail_limit = request.budget.railAttempts if request.budget.railAttempts is not None else rail_budget(n)
        if rail_limit < n-1 or rail_limit > rail_budget(n):
            raise PlanError("INVALID_RAIL_BUDGET")
        original = deepcopy(echo_input if echo_input is not None else request.model_dump(mode="json"))
        if echo_input is None and "allowMultimodal" not in request.options.model_fields_set:
            original["options"].pop("allowMultimodal", None)
        self.ledger.begin(request.sessionId, request.operationId, digest(original), {"rail": rail_limit, "station": n, "walk": 0, "rag": 1})
        ctx = Context(self.ledger, request.sessionId, request.operationId, started + self.timeout_seconds,
                      monotonic=self.monotonic, cache_allowed=request.options.allowCache)
        try:
            result = await self._plan(request, ctx)
            result.update(inputVersion="2", dataSource=self.provider.dataSource, fixtureId=self.provider.fixture_id,
                          fixtureLabel="저장된 크롤러 응답 재생 (실시간 아님)" if self.provider.dataSource == "replay" else "가상 시간표·운임 시험 결과", echoInput=original)
            if self.provider.dataSource == "replay":
                result["replayProvenance"] = deepcopy(self.provider.provenance)
                if self.provider.provenance["kind"] == "test-only":
                    result["fixtureLabel"] = "합성 자료 재생 (실데이터 검증 아님)"
            result["ledger"] = self.ledger.snapshot(request.sessionId, request.operationId)
            result["ledger"]["measurementBasis"] = self.provider.dataSource + "-no-network"
            if request.options.allowMultimodal:
                result["ledger"]["busAttempts"] = ctx.bus_attempts
                result["ledger"]["railOnlyAttempts"] = result["ledger"]["railAttempts"] - ctx.bus_attempts
            result["ledger"].update(cacheHits=ctx.cache_hits, sharedRequests=0,
                                    budgetExhausted=any("budget_exhausted" in s for s in ctx.stop_reasons + result["stopReasons"]),
                                    stopReasons=list(dict.fromkeys(ctx.stop_reasons + result["stopReasons"])))
            return result
        finally:
            ctx.cache.clear()
            self.ledger.finish(request.sessionId, request.operationId)

    async def _plan(self, request, ctx):
        options = request.options
        candidates = local_candidates(request, self.cluster_diameter_m,
                                      rank_limit=None if options.allowOrderAlternates else 3)
        ranked = candidates["orders"]
        candidates["orders"] = ranked[:3]
        # Even a preflight failure returns all visits with their declared anchors.
        fallback_order = [p.clientPlaceId for p in sorted(request.places, key=lambda p: {"start": -1, "end": 1}.get(p.anchorRole, 0))]
        if request.mode == "strict" and any(p.openingWindows is None for p in request.places):
            empty = finish_route(fallback_order, [], [],
                                 [constraint("opening", "unsupported", "strict_opening_hours_missing")], {}, False,
                                 "strict_opening_hours_missing", False)
            return self.response(empty, empty, candidates, "not_attempted", [], None)
        if not candidates["orders"]:
            empty = finish_route(fallback_order, [], [],
                                 [constraint("time_only_bounds", "violated", "mandatory_time_constraints_infeasible")], {}, False,
                                 "mandatory_time_constraints_infeasible", False)
            return self.response(empty, empty, candidates, "not_attempted", [], None)
        if request.candidateIndex >= len(candidates["orders"]):
            raise PlanError("CANDIDATE_INDEX_UNAVAILABLE")
        order = candidates["orders"][request.candidateIndex]["order"]
        station_lists = {pid: [] for pid in order} if options.allowMultimodal else {}
        by_id = {p.clientPlaceId: p for p in request.places}
        leg_selector = LegSelector(self.mobility, request, station_lists, self.gateway, ctx) if options.allowMultimodal else None
        all_walk = leg_selector and not options.allowOrderAlternates and leg_selector.short_walk_order(order, by_id)
        try:
            for pid in order if len(order) > 1 and not all_walk else []:
                place = by_id[pid]

                async def fetch(place=place):
                    return [StationCandidate.model_validate(c) for c in await self.provider.stations(place)]

                try:
                    stations = await self.gateway.call(ctx, "station", fetch)
                except Stopped as stop:
                    if not options.allowMultimodal or stop.reason not in ("fixture_place_unsupported", "replay_place_miss"):
                        raise
                    continue
                stations.sort(key=lambda c: (distance(place, c.station), c.station.identity()))
                unique = {}
                for station in stations:
                    unique.setdefault(station.station.identity(), station)
                station_lists[pid] = list(unique.values())[:2]
                if not station_lists[pid] and not options.allowMultimodal:
                    raise Stopped("no_station_candidate")
        except Stopped as stop:
            empty = finish_route(order, [], [], [constraint("station_lookup", "unknown", stop.reason)], {}, False, stop.reason, False)
            return self.response(empty, empty, candidates, "not_attempted", [], None)
        choices = {pid: stations[0] for pid, stations in station_lists.items() if stations}
        baseline = await evaluate(request, order, choices, self.gateway, ctx, leg_selector=leg_selector)
        immutable_baseline = deepcopy(baseline)
        selected = deepcopy(baseline)
        improvement = "not_attempted"
        repair_log = []
        if options.allowOrderAlternates:
            selected, audit = await self._compare_order(request, ctx, ranked, baseline, choices, leg_selector)
            audit["selectedWithoutKnownViolations"] = feasible(selected) if selected["completion"] == "complete" else None
            if not feasible(selected):
                audit["feasibilityStatus"] = "not_found_in_evaluated_orders"
                audit["feasibilityMessage"] = "평가한 후보에서 가능한 일정을 확인하지 못했습니다."
            else:
                audit["feasibilityStatus"] = (
                    "confirmed" if selected["timing"] == "timetable" and selected["accessWalking"] == "routed"
                    else "conditional"
                )
            candidates["orderEvaluation"] = audit
            if options.allowRepair and options.allowStationAlternates and len(order) > 1:
                improvement = "disabled_order_alternates"
        elif not leg_selector and baseline["completion"] == "complete" and options.allowRepair and options.allowStationAlternates and len(order) > 1:
            if not options.allowPrefixReuse or not self.provider.capabilities.reusePrefixInRequest:
                improvement = "disabled_reuse"
            else:
                improvement = "skipped_budget"
                for i in range(len(order)-1, -1, -1):
                    pid = order[i]
                    if len(station_lists[pid]) < 2:
                        continue
                    start = max(0, i-1)
                    needed = len(order)-1-start
                    if needed > ctx.remaining():
                        repair_log.append({"placeId": pid, "status": "skipped_budget", "requiredRailUpperBound": needed})
                        continue
                    alternate = deepcopy(choices)
                    alternate[pid] = station_lists[pid][1]
                    ctx.phase = "repair"
                    patched = await evaluate(request, order, alternate, self.gateway, ctx, prefix=baseline, start_index=start)
                    state = "incomplete" if patched["completion"] != "complete" else "completed" if self.better(patched, baseline) else "rejected_worse"
                    repair_log.append({"placeId": pid, "status": state, "digest": patched["digest"], "requiredRailUpperBound": needed, "stopReason": patched["stopReason"]})
                    improvement = state
                    if state == "completed":
                        selected = patched
                    # Bound the first prototype to one repair evaluation and two complete routes.
                    break
        assert baseline == immutable_baseline
        comparisons = None
        if options.allowPassEvaluation and selected["completion"] == "complete":
            order_alternate = candidates.get("orderEvaluation", {}).get("plan")
            routes = {route["digest"]: route for route in (baseline, selected, order_alternate)
                      if route is not None and feasible(route)}
            routes = list(routes.values())
            if not routes:
                comparisons = {"status": "unknown", "recommendation": None, "evaluations": [], "reason": "no_feasible_completed_candidate"}
            elif self.provider.capabilities.ragDeliveryAllowed:
                ctx.phase = "pricing"
                try:
                    async def fetch_passes():
                        rows = await self.provider.passes(deepcopy(routes))
                        if not isinstance(rows, list):
                            raise ValueError("RAG 배치 응답은 배열이어야 합니다.")
                        return rows

                    raw = await self.gateway.call(ctx, "rag", fetch_passes)
                    comparisons = compare_passes(routes, raw)
                    if options.allowOrderAlternates and len(routes) > 1:
                        evaluated = {row["routeDigest"] for row in comparisons["evaluations"] if row["status"] == "known"}
                        unpriced = [route["digest"] for route in routes if route["digest"] not in evaluated]
                        comparisons["unconfirmedPassRouteDigests"] = unpriced
                        if unpriced and comparisons["status"] == "known":
                            comparisons["status"] = "partial"
                except Stopped as stop:
                    comparisons = {"status": "unknown", "recommendation": None, "evaluations": [], "reason": stop.reason}
            else:
                comparisons = {"status": "unknown", "recommendation": None, "evaluations": [], "reason": "rag_delivery_not_allowed"}
        return self.response(immutable_baseline, selected, candidates, improvement, repair_log, comparisons)

    async def _compare_order(self, request, ctx, ranked, baseline, choices, leg_selector=None):
        # Both orders use the same immutable request and the same station bindings.
        allow_reuse = (baseline["completion"] == "complete" and request.options.allowPrefixReuse
                       and self.provider.capabilities.reusePrefixInRequest)
        requested_policy = request.options.orderAlternatePolicy
        policy = requested_policy
        pass_matches = None
        hint = {}
        targets = deadline_targets(request.places, baseline) if requested_policy in ("deadline_first", "pass_hint") else []
        if requested_policy == "pass_hint":
            if leg_selector:
                policy = "deadline_first" if targets else "diverse"
                hint = {"reason": "multimodal_station_bindings"}
            else:
                policy, pass_matches, hint = self._pass_order_policy(request, baseline, choices, targets)
        if policy != "deadline_first":
            targets = []
        alternate, skip = select_alternate(ranked, baseline["order"], choices,
                                          remaining=ctx.remaining(), reuse_prefix=allow_reuse,
                                          policy=policy, targets=targets, pass_matches=pass_matches,
                                          conservative=leg_selector is not None)
        audit = {"objective": "earliest-finish-then-known-fare", "status": skip, "alternate": alternate,
                 "policy": requested_policy, "priorityVisitIds": targets}
        if requested_policy == "pass_hint":
            if policy == "pass_hint" and (alternate is None or "passHintMatchedLegs" not in alternate):
                policy = "diverse"
                hint["reason"] = skip or "no_discriminating_hint"
            audit.update(effectivePolicy=policy, passHint=hint)
        if alternate is None:
            return deepcopy(baseline), audit
        start = alternate["reusedLegs"]
        previous_phase = ctx.phase
        ctx.phase = "repair"
        try:
            compared = await evaluate(request, alternate["order"], choices, self.gateway, ctx,
                                      prefix=baseline if start else None, start_index=start, leg_selector=leg_selector)
        finally:
            ctx.phase = previous_phase
        accepted = can_select(compared, baseline)
        audit.update(status="selected" if accepted else "incomplete" if compared["completion"] != "complete"
                     else "infeasible" if not feasible(compared) else "baseline_kept",
                     stopReason=compared["stopReason"], plan=compared)
        return compared if accepted else deepcopy(baseline), audit

    def _pass_order_policy(self, request, baseline, choices, targets):
        snapshot = self.pass_order_hints
        hint = {"passId": snapshot.passId, "evidence": snapshot.evidence.model_dump()} if snapshot else {}
        if any(check["visitId"] in targets and check["kind"] in ("fixed_visit", "opening") and check["status"] == "violated"
               for check in baseline["constraints"]):
            return "deadline_first", None, {**hint, "reason": "known_visit_deadline_missed"}
        if snapshot is None:
            return "diverse", None, {"reason": "snapshot_unavailable"}
        matches, reason = snapshot.match_visit_pairs(request, choices)
        if not matches:
            return "diverse", None, {**hint, "reason": reason}
        return "pass_hint", matches, hint

    @staticmethod
    def better(candidate, baseline):
        if candidate["completion"] != "complete" or candidate["timing"] != baseline["timing"] or candidate["accessWalking"] != baseline["accessWalking"]:
            return False
        if any(c["status"] in ("violated", "unsupported") for c in candidate["constraints"]):
            return False
        states = lambda route: {(c["kind"], c["visitId"]): c["status"] for c in route["constraints"]}
        old, new = states(baseline), states(candidate)
        rank = {"satisfied": 0, "unknown": 1, "violated": 2, "unsupported": 3}
        if old.keys() != new.keys() or any(rank[new[k]] > rank[old[k]] for k in old):
            return False
        if any(old[k] in ("violated", "unsupported") and new[k] == "satisfied" for k in old):
            return True
        if candidate["fare"]["noPassTotal"] is None or baseline["fare"]["noPassTotal"] is None:
            return False
        score = lambda r: (r["fare"]["noPassTotal"], instant(r["finishAt"]), r["transfers"] if r["transfers"] is not None else float("inf"))
        return score(candidate) < score(baseline)

    @staticmethod
    def response(baseline, selected, candidates, improvement, repair_log, comparisons):
        reasons = [r for r in [baseline["stopReason"], selected["stopReason"]] if r]
        reasons.extend(r["stopReason"] for r in repair_log if r.get("stopReason"))
        alternate = candidates.get("orderEvaluation", {})
        if alternate.get("stopReason"):
            reasons.append(alternate["stopReason"])
        return {"completion": selected["completion"], "timing": selected["timing"], "accessWalking": selected["accessWalking"],
                "pricing": comparisons["status"] if comparisons else selected["fare"]["status"],
                "constraints": deepcopy(selected["constraints"]), "improvement": improvement, "repairLog": repair_log,
                "candidates": candidates, "baseline": {"planId": baseline["planId"], "digest": baseline["digest"],
                    "preserved": baseline["completion"] == "complete", "reason": "immutable_completed_baseline" if baseline["completion"] == "complete" else "baseline_incomplete", "plan": baseline},
                "selected": selected, "passComparison": comparisons, "stopReasons": reasons,
                "comparisonScope": "completed-candidates-only", "optimality": "not-guaranteed"}
