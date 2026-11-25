import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from pydantic import ValidationError

from planner.evaluator import choose_ride
from planner.fixture_provider import FixtureProvider
from planner.gateway import Context, Gateway
from planner.ledger import Ledger, PlanError, Stopped
from planner.models import PlanRequest, RailQuery, StationRef, digest, instant, rail_budget
from planner.ordering import distance, groups, local_candidates
from planner.pricing import compare_passes
from planner.provider import Capabilities
from planner.service import Planner

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "planner/fixtures/tokyo-demo.json"
INPUT = ROOT / "sample/low-call/eight-node-input.json"


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class PlannerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "ledger.sqlite3"
        self.ledger = Ledger(self.path)
        self.provider = FixtureProvider(FIXTURE)
        self.clock = FakeClock()

    def request(self, n=8, **kwargs):
        raw = json.loads(INPUT.read_text(encoding="utf-8"))
        raw["places"] = raw["places"][:n]
        for i, place in enumerate(raw["places"]):
            place.pop("anchorRole", None)
            if i == 0:
                place["anchorRole"] = "start"
            elif i == n-1:
                place["anchorRole"] = "end"
        raw["sessionId"] = self.ledger.create_session()["sessionId"]
        raw["operationId"] = str(uuid4())
        raw["options"] = {"allowRepair": False, "allowPassEvaluation": False}
        raw.update(kwargs)
        return raw

    async def run_plan(self, raw, **kwargs):
        return await Planner(self.ledger, self.provider, monotonic=self.clock, **kwargs).plan(PlanRequest.model_validate(raw), raw)

    async def test_eight_node_baseline_counts_and_echo(self):
        raw = self.request()
        before = deepcopy(raw)
        result = await self.run_plan(raw)
        self.assertEqual(raw, before)
        self.assertEqual(result["echoInput"], raw)
        self.assertTrue(result["echoInput"]["startAt"].endswith("+09:00"))
        self.assertEqual(result["completion"], "complete")
        self.assertEqual(result["timing"], "reference")
        self.assertEqual(result["ledger"]["railAttempts"], 7)
        self.assertEqual(result["ledger"]["stationAttempts"], 8)
        self.assertEqual(result["ledger"]["totalRoutingOperationAttempts"], 15)
        self.assertEqual(result["ledger"]["httpRequestsObserved"], 0)
        self.assertEqual(result["ledger"]["providerCalls"], 0)
        self.assertEqual(result["selected"]["fare"]["noPassTotal"], 1540)
        self.assertEqual(result["selected"]["order"], [f"p{i}" for i in range(1, 9)])
        self.assertEqual(len(result["selected"]["visits"]), 8)

    async def test_preflight_failure_preserves_anchors_with_unsorted_input(self):
        for reason in ("unknown_hours", "closed"):
            raw = self.request(n=3)
            if reason == "unknown_hours":
                raw["mode"] = "strict"
                raw["places"][1]["openingWindows"] = None
            else:
                raw["places"][1]["openingWindows"] = []
            raw["places"].reverse()
            result = await self.run_plan(raw)
            self.assertEqual(result["selected"]["order"], ["p1", "p2", "p3"])
            self.assertEqual(result["echoInput"]["places"], raw["places"])
            self.assertEqual(result["completion"], "failed")
            self.assertEqual(result["ledger"]["totalRoutingOperationAttempts"], 0)

    async def test_retry_is_charged_and_cap_stops_eleventh_dispatch(self):
        self.provider.failures = {f"rail:s{i}>s{i+1}": 1 for i in range(1, 5)}
        raw = self.request()
        result = await self.run_plan(raw)
        self.assertEqual(result["ledger"]["railAttempts"], 10)
        self.assertEqual(sum(e[0] == "rail" for e in self.provider.events), 10)
        self.assertTrue(result["ledger"]["budgetExhausted"])
        self.assertEqual(result["completion"], "partial")
        self.assertIsNone(result["selected"]["fare"]["noPassTotal"])
        self.assertEqual(result["ledger"]["failedAttempts"], 4)

    async def test_one_retry_can_finish_within_budget(self):
        self.provider.failures = {"rail:s1>s2": 1}
        result = await self.run_plan(self.request())
        self.assertEqual(result["completion"], "complete")
        self.assertEqual(result["ledger"]["railAttempts"], 8)
        self.assertEqual(result["ledger"]["failedAttempts"], 1)

    async def test_invalid_budget_has_zero_provider_dispatch(self):
        for limit in (0, 6):
            raw = self.request(budget={"railAttempts": limit})
            with self.assertRaisesRegex(PlanError, "INVALID_RAIL_BUDGET"):
                await self.run_plan(raw)
        raw = self.request(2, budget={"railAttempts": 3})
        with self.assertRaises(PlanError):
            await self.run_plan(raw)
        self.assertEqual(self.provider.events, [])
        self.assertEqual([rail_budget(i) for i in range(1, 9)], [0, 2, 4, 6, 7, 8, 9, 10])

    async def test_no_time_feasible_order_stops_before_station_calls(self):
        raw = self.request(endBy="2026-11-17T10:00:00+09:00")
        result = await self.run_plan(raw)
        self.assertEqual(result["completion"], "failed")
        self.assertEqual(result["constraints"][0]["status"], "violated")
        self.assertEqual(result["ledger"]["totalRoutingOperationAttempts"], 0)
        self.assertEqual(self.provider.events, [])

    async def test_single_visit_has_no_station_or_rail_calls(self):
        result = await self.run_plan(self.request(1))
        self.assertEqual(result["completion"], "complete")
        self.assertEqual(result["selected"]["elapsedMinutes"], 30)
        self.assertEqual(result["selected"]["fare"]["noPassTotal"], 0)
        self.assertEqual(result["ledger"]["totalRoutingOperationAttempts"], 0)

    async def test_earliest_arrival_not_shortest_ride_and_wait_once(self):
        self.provider.data["edges"]["s1>s2"] = {"choices": [
            {"headwayMinutes": 60, "rideMinutes": 5, "fare": 100},
            {"headwayMinutes": 10, "rideMinutes": 20, "fare": 300}]}
        result = await self.run_plan(self.request(2))
        leg = result["selected"]["legs"][0]
        self.assertEqual(leg["preparedAt"], "2026-11-17T00:33:00Z")
        self.assertEqual(leg["rideMinutes"], 20)
        self.assertEqual(leg["initialWaitMinutes"], 7)
        self.assertEqual(leg["arrivalAt"], "2026-11-17T01:00:00Z")
        self.assertEqual(result["selected"]["elapsedMinutes"], 93)

    async def test_midnight_has_real_dates_and_expired_pass_not_ranked(self):
        raw = self.request(2, startAt="2026-11-17T23:30:00+09:00", endBy="2026-11-18T02:00:00+09:00",
                           options={"allowRepair": False, "allowPassEvaluation": True})
        result = await self.run_plan(raw)
        leg = result["selected"]["legs"][0]
        self.assertGreaterEqual(instant(leg["departureAt"]), instant("2026-11-18T00:00:00+09:00"))
        row = result["passComparison"]["evaluations"][0]
        self.assertEqual(row["reason"], "journey_outside_reported_validity")
        self.assertEqual(row["status"], "unknown")
        self.assertIsNone(row["total"])

    async def test_missing_dates_never_propagated_from_duration(self):
        self.provider.data["defaultEdge"]["missingDates"] = True
        result = await self.run_plan(self.request(3))
        self.assertEqual(result["completion"], "partial")
        self.assertEqual(result["timing"], "unknown")
        self.assertEqual(result["selected"]["stopReason"], "missing_dated_trip")
        self.assertEqual(result["ledger"]["railAttempts"], 1)
        self.assertIsNone(result["selected"]["finishAt"])
        self.assertIsNone(result["selected"]["fare"]["noPassTotal"])

    async def test_unknown_access_or_egress_not_zero(self):
        for pid, direction, rail_calls in [("p1", "access", 0), ("p2", "egress", 1)]:
            self.provider = FixtureProvider(FIXTURE)
            walk = self.provider.data["stations"][pid][0][direction]
            walk.update(status="unknown", minutes=None)
            result = await self.run_plan(self.request(2))
            self.assertEqual(result["completion"], "partial")
            self.assertEqual(result["accessWalking"], "unknown")
            self.assertEqual(result["ledger"]["railAttempts"], rail_calls)
            self.assertIsNone(result["selected"]["fare"]["noPassTotal"])

    async def test_unknown_hours_and_estimates_do_not_certify_fixed_arrival(self):
        raw = self.request(2)
        raw["places"][0]["openingWindows"] = None
        raw["places"][1]["fixedVisitAt"] = "2026-11-17T11:00:00+09:00"
        result = await self.run_plan(raw)
        self.assertEqual(result["completion"], "complete")
        self.assertEqual(result["timing"], "reference")
        fixed = next(c for c in result["constraints"] if c["kind"] == "fixed_visit")
        self.assertEqual(fixed["status"], "unknown")
        self.assertTrue(any(c["reason"] == "opening_hours_missing" for c in result["constraints"]))

    async def test_strict_does_not_enable_walking_queries(self):
        result = await self.run_plan(self.request(2, mode="strict"))
        self.assertEqual(result["completion"], "partial")
        self.assertEqual(result["selected"]["stopReason"], "strict_walk_unavailable")
        self.assertEqual(result["ledger"]["walkAttempts"], 0)

    async def test_verified_same_station_keeps_walks_and_stays(self):
        self.provider.data["stations"]["p2"] = deepcopy(self.provider.data["stations"]["p1"][:1])
        result = await self.run_plan(self.request(2))
        self.assertEqual(result["ledger"]["railAttempts"], 0)
        self.assertEqual(result["selected"]["elapsedMinutes"], 66)
        self.assertEqual(len(result["selected"]["visits"]), 2)
        self.assertTrue(result["selected"]["legs"][0]["samePhysicalStation"])

    async def test_same_name_or_coordinates_are_not_physical_identity(self):
        first = self.provider.data["stations"]["p1"][0]["station"]
        second = self.provider.data["stations"]["p2"][0]["station"]
        second.update(name=first["name"], location=deepcopy(first["location"]))
        result = await self.run_plan(self.request(2))
        self.assertEqual(result["ledger"]["railAttempts"], 1)
        a, b = StationRef.model_validate(first), StationRef.model_validate(second)
        self.assertFalse(a.same_physical(b))
        a = a.model_copy(update={"providerStationId": None})
        b = b.model_copy(update={"providerStationId": None})
        self.assertEqual(a.identity(), b.identity())
        self.assertFalse(a.same_physical(b))

    async def test_missing_fare_is_not_free(self):
        self.provider.data["defaultEdge"]["fare"] = None
        result = await self.run_plan(self.request(2, options={"allowRepair": False, "allowPassEvaluation": True}))
        self.assertEqual(result["completion"], "complete")
        self.assertEqual(result["selected"]["fare"]["status"], "partial")
        self.assertIsNone(result["selected"]["fare"]["noPassTotal"])
        self.assertIsNone(result["passComparison"]["recommendation"])

    async def test_repair_preserves_baseline_and_prefix_without_cache(self):
        raw = self.request(options={"allowPrefixReuse": True, "allowCache": False, "allowRepair": True, "allowPassEvaluation": False})
        result = await self.run_plan(raw)
        self.assertEqual(result["improvement"], "completed")
        self.assertEqual(result["ledger"]["railAttempts"], 8)
        self.assertEqual(result["ledger"]["repairAttempts"], 1)
        self.assertTrue(result["baseline"]["preserved"])
        self.assertEqual(result["baseline"]["plan"]["fare"]["noPassTotal"], 1540)
        self.assertEqual(result["selected"]["fare"]["noPassTotal"], 1480)
        self.assertNotEqual(result["selected"]["digest"], result["baseline"]["digest"])
        prefix = [e for e in self.provider.events if e[0] == "rail" and e[1] == "s1>s2"]
        self.assertEqual(len(prefix), 1)
        self.assertEqual(result["ledger"]["cacheHits"], 0)

    async def test_repair_failure_keeps_completed_baseline(self):
        self.provider.data["edges"]["s7>s8-alt"] = {"unsupported": True}
        result = await self.run_plan(self.request(options={"allowPrefixReuse": True, "allowRepair": True, "allowPassEvaluation": False}))
        self.assertEqual(result["completion"], "complete")
        self.assertEqual(result["improvement"], "incomplete")
        self.assertEqual(result["baseline"]["plan"], result["selected"])
        self.assertEqual(result["baseline"]["digest"], result["selected"]["digest"])
        self.assertEqual(result["ledger"]["failedAttempts"], 1)

    async def test_worse_repair_not_adopted_and_option_gates(self):
        raw = self.request(options={"allowPrefixReuse": True, "allowRepair": True, "allowPassEvaluation": False})
        self.provider.data["edges"]["s7>s8-alt"]["fare"] = 1000
        result = await self.run_plan(raw)
        self.assertEqual(result["improvement"], "rejected_worse")
        self.assertEqual(result["baseline"]["plan"], result["selected"])
        for flags, expected in [({"allowRepair": False}, "not_attempted"),
                                ({"allowRepair": True, "allowStationAlternates": False}, "not_attempted"),
                                ({"allowRepair": True}, "disabled_reuse")]:
            result = await self.run_plan(self.request(options={**flags, "allowPassEvaluation": False}))
            self.assertEqual(result["improvement"], expected)
            self.assertEqual(result["ledger"]["repairAttempts"], 0)
        self.provider.capabilities = Capabilities(reusePrefixInRequest=False)
        result = await self.run_plan(self.request(options={"allowPrefixReuse": True, "allowRepair": True, "allowPassEvaluation": False}))
        self.assertEqual(result["improvement"], "disabled_reuse")

    async def test_suffix_is_requeried_at_changed_time(self):
        self.provider.data["stations"]["p8"] = self.provider.data["stations"]["p8"][:1]
        self.provider.data["edges"]["s6>s7-alt"] = {"headwayMinutes": 10, "rideMinutes": 25, "fare": 100}
        result = await self.run_plan(self.request(options={"allowPrefixReuse": True, "allowRepair": True, "allowPassEvaluation": False}))
        self.assertEqual(result["ledger"]["railAttempts"], 9)
        old = next(e for e in self.provider.events if e[0] == "rail" and e[1] == "s7>s8")
        new = next(e for e in self.provider.events if e[0] == "rail" and e[1] == "s7-alt>s8")
        self.assertNotEqual(old[2], new[2])

    async def test_cache_uses_exact_time_direction_options_and_returns_copies(self):
        raw = self.request(2)
        session, operation = raw["sessionId"], raw["operationId"]
        self.ledger.begin(session, operation, "hash", {"rail": 10, "station": 0, "walk": 0, "rag": 0})
        ctx = Context(self.ledger, session, operation, 300, monotonic=self.clock, cache_allowed=True)
        gateway = Gateway(self.provider)
        a = StationRef.model_validate(self.provider.data["stations"]["p1"][0]["station"])
        b = StationRef.model_validate(self.provider.data["stations"]["p2"][0]["station"])
        query = RailQuery(origin=a, destination=b, preparedAt="2026-11-17T09:00:00+09:00")
        first = await gateway.rail(ctx, query)
        first[0].fareUnits.clear()
        same = await gateway.rail(ctx, query.model_copy(update={"preparedAt": "2026-11-17T00:00:00Z"}))
        self.assertTrue(same[0].fareUnits)
        self.assertEqual(ctx.cache_hits, 1)
        for changed in [{"preparedAt": "2026-11-17T09:01:00+09:00"}, {"origin": b, "destination": a}, {"options": {"seat": "reserved"}}]:
            await gateway.rail(ctx, query.model_copy(update=changed))
        self.assertEqual(self.ledger.snapshot(session, operation)["railAttempts"], 4)
        self.provider.capabilities = Capabilities(reuseExactQuotes=False)
        await gateway.rail(ctx, query)
        self.assertEqual(self.ledger.snapshot(session, operation)["railAttempts"], 5)

    async def test_deadline_mid_rail_stops_new_attempts(self):
        original = self.provider.rail

        async def delayed(query):
            value = await original(query)
            self.clock.advance(4)
            return value

        self.provider.rail = delayed
        result = await self.run_plan(self.request(), timeout_seconds=3)
        self.assertEqual(result["selected"]["stopReason"], "deadline_exceeded")
        self.assertEqual(result["ledger"]["railAttempts"], 1)
        self.assertEqual(result["ledger"]["failedAttempts"], 1)
        self.assertFalse(result["ledger"]["budgetExhausted"])

    async def test_queue_time_counts_deadline_without_charging_dispatch(self):
        raw = self.request(2)
        planner = Planner(self.ledger, self.provider, monotonic=self.clock, timeout_seconds=1)
        await planner.gateway.semaphore.acquire()
        task = asyncio.create_task(planner.plan(PlanRequest.model_validate(raw), raw))
        await asyncio.sleep(0)
        self.clock.advance(2)
        planner.gateway.semaphore.release()
        result = await task
        self.assertEqual(result["ledger"]["stationAttempts"], 0)
        self.assertEqual(result["selected"]["stopReason"], "deadline_exceeded")

    async def test_cancelled_dispatched_attempt_not_refunded_and_id_not_reused(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.provider.rail

        async def pending(query):
            entered.set()
            await release.wait()
            return await original(query)

        self.provider.rail = pending
        raw = self.request(2)
        planner = Planner(self.ledger, self.provider, monotonic=self.clock)
        task = asyncio.create_task(planner.plan(PlanRequest.model_validate(raw), raw))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        snapshot = self.ledger.snapshot(raw["sessionId"], raw["operationId"])
        self.assertEqual(snapshot["railAttempts"], 1)
        self.assertEqual(snapshot["failedAttempts"], 1)
        with self.assertRaisesRegex(PlanError, "OPERATION_ALREADY_USED"):
            await planner.plan(PlanRequest.model_validate(raw), raw)

    async def test_two_sessions_share_provider_serialization_and_duplicate_409(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.provider.stations
        active = maximum = 0

        async def blocked(place):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                entered.set()
                await release.wait()
                return await original(place)
            finally:
                active -= 1

        self.provider.stations = blocked
        planner = Planner(self.ledger, self.provider, monotonic=self.clock)
        first, second = self.request(2), self.request(2)
        task1 = asyncio.create_task(planner.plan(PlanRequest.model_validate(first), first))
        await entered.wait()
        task2 = asyncio.create_task(planner.plan(PlanRequest.model_validate(second), second))
        with self.assertRaisesRegex(PlanError, "ALREADY_RUNNING") as error:
            await planner.plan(PlanRequest.model_validate(first), first)
        self.assertEqual(error.exception.status, 409)
        release.set()
        a, b = await asyncio.gather(task1, task2)
        self.assertEqual(maximum, 1)
        self.assertEqual(a["completion"], b["completion"])
        self.assertEqual(a["ledger"]["stationAttempts"], 2)

    async def test_pass_purchase_once_no_pass_wins_and_unknown_eligibility(self):
        result = await self.run_plan(self.request(options={"allowRepair": False, "allowPassEvaluation": True}))
        row = result["passComparison"]["evaluations"][0]
        self.assertEqual(row["total"], 900)
        self.assertEqual(row["savings"], 640)
        self.assertEqual(result["ledger"]["ragRequests"], 1)
        short = await self.run_plan(self.request(2, options={"allowRepair": False, "allowPassEvaluation": True}))
        self.assertIsNone(short["passComparison"]["recommendation"]["passId"])
        self.provider.data["passResponses"][0]["eligibility"] = "unknown"
        unknown = await self.run_plan(self.request(options={"allowRepair": False, "allowPassEvaluation": True}))
        self.assertEqual(unknown["passComparison"]["evaluations"][0]["status"], "unknown")
        self.assertIsNone(unknown["passComparison"]["recommendation"]["passId"])

    async def test_pass_bad_arithmetic_duplicate_missing_allocation_unranked(self):
        result = await self.run_plan(self.request())
        route = result["selected"]
        rows = [r.model_dump(mode="json") for r in await self.provider.passes([route])]
        for mutate in (lambda r: r.update(reportedTotal=1), lambda r: r["allocations"].pop(),
                       lambda r: r["allocations"].append(deepcopy(r["allocations"][0])), lambda r: r.update(purchasePrice=None)):
            raw = deepcopy(rows)
            mutate(raw[0])
            comparison = compare_passes([route], raw)
            self.assertEqual(comparison["evaluations"][0]["status"], "unknown")
            self.assertIsNone(comparison["recommendation"]["passId"])
        duplicated = compare_passes([route], rows + rows)
        self.assertTrue(all(r["status"] == "unknown" for r in duplicated["evaluations"]))

    async def test_rag_permission_and_failure_cannot_destroy_route(self):
        self.provider.capabilities = Capabilities(ragDeliveryAllowed=False)
        result = await self.run_plan(self.request(2, options={"allowRepair": False, "allowPassEvaluation": True}))
        self.assertEqual(result["ledger"]["ragRequests"], 0)
        self.assertEqual(result["passComparison"]["reason"], "rag_delivery_not_allowed")
        self.provider.capabilities = Capabilities(ragDeliveryAllowed=True)
        self.provider.failures["rag"] = 1
        failed = await self.run_plan(self.request(2, options={"allowRepair": False, "allowPassEvaluation": True}))
        self.assertEqual(failed["completion"], "complete")
        self.assertEqual(failed["ledger"]["ragRequests"], 1)
        self.assertEqual(failed["passComparison"]["status"], "unknown")

    async def test_one_connected_fare_unit_covers_multiple_rides_without_double_charge(self):
        original = self.provider.rail

        async def connected(query):
            rows = [r.model_dump(mode="json") for r in await original(query)]
            segment = deepcopy(rows[0]["segments"][0])
            segment["segmentId"] += ":transfer"
            rows[0]["segments"].append(segment)
            rows[0]["fareUnits"][0]["rideSegmentIds"].append(segment["segmentId"])
            rows[0]["initialWaitIncluded"] = "yes"
            rows[0]["displayedDurationMinutes"] = 999
            return rows

        self.provider.rail = connected
        result = await self.run_plan(self.request(2))
        self.assertEqual(len(result["selected"]["legs"][0]["rideSegmentIds"]), 2)
        self.assertEqual(result["selected"]["fare"]["noPassTotal"], 220)
        self.assertEqual(result["selected"]["elapsedMinutes"], 85)

    async def test_overlapping_provider_fares_are_rejected_in_counted_attempt(self):
        original = self.provider.rail

        async def overlapping(query):
            rows = [r.model_dump(mode="json") for r in await original(query)]
            duplicate = deepcopy(rows[0]["fareUnits"][0])
            duplicate["fareUnitId"] += ":duplicate"
            rows[0]["fareUnits"].append(duplicate)
            return rows

        self.provider.rail = overlapping
        result = await self.run_plan(self.request(2))
        self.assertEqual(result["completion"], "partial")
        self.assertEqual(result["ledger"]["railAttempts"], 1)
        self.assertEqual(result["ledger"]["failedAttempts"], 1)
        self.assertIsNone(result["selected"]["fare"]["noPassTotal"])

    async def test_dated_trip_before_ready_is_not_selected_and_fare_breaks_arrival_tie(self):
        a = StationRef.model_validate(self.provider.data["stations"]["p1"][0]["station"])
        b = StationRef.model_validate(self.provider.data["stations"]["p2"][0]["station"])
        query = RailQuery(origin=a, destination=b, preparedAt="2026-11-17T09:33:00+09:00")
        ride = (await self.provider.rail(query))[0]
        earlier = ride.model_copy(update={"rideId": "past", "departureAt": "2026-11-17T09:00:00+09:00"})
        cheaper_unit = ride.fareUnits[0].model_copy(update={"baseFare": 10})
        cheaper = ride.model_copy(update={"rideId": "cheap", "fareUnits": [cheaper_unit]})
        self.assertEqual(choose_ride([earlier, ride, cheaper], instant(query.preparedAt)).rideId, "cheap")

    async def test_strict_missing_hours_and_actual_constraint_violation_do_not_sell_pass(self):
        raw = self.request(2, mode="strict")
        raw["places"][0]["openingWindows"] = None
        missing = await self.run_plan(raw)
        self.assertEqual(missing["ledger"]["stationAttempts"], 0)
        self.assertEqual(missing["constraints"][0]["status"], "unsupported")
        for choices in self.provider.data["stations"].values():
            for candidate in choices:
                candidate["access"]["status"] = candidate["egress"]["status"] = "routed"
        self.provider.data["defaultEdge"]["rideMinutes"] = 60
        raw = self.request(2, mode="strict", options={"allowRepair": False, "allowPassEvaluation": True})
        raw["places"][1]["fixedVisitAt"] = "2026-11-17T10:00:00+09:00"
        missed = await self.run_plan(raw)
        self.assertEqual(missed["completion"], "complete")
        self.assertTrue(any(c["status"] == "violated" for c in missed["constraints"]))
        self.assertEqual(missed["ledger"]["ragRequests"], 0)
        self.assertIsNone(missed["passComparison"]["recommendation"])

    async def test_separate_planners_share_provider_gate_and_malformed_rag_is_partial(self):
        first = Planner(self.ledger, self.provider, monotonic=self.clock)
        second = Planner(self.ledger, FixtureProvider(FIXTURE), monotonic=self.clock)
        self.assertIs(first.gateway.semaphore, second.gateway.semaphore)

        async def malformed(routes):
            return None

        self.provider.passes = malformed
        result = await self.run_plan(self.request(2, options={"allowRepair": False, "allowPassEvaluation": True}))
        self.assertEqual(result["completion"], "complete")
        self.assertEqual(result["ledger"]["ragRequests"], 1)
        self.assertEqual(result["passComparison"]["status"], "unknown")

    async def test_completed_route_digest_is_reproducible(self):
        result = await self.run_plan(self.request())
        route = deepcopy(result["selected"])
        expected = route.pop("digest")
        route.pop("planId")
        self.assertEqual(digest(route), expected)


class LedgerAndOrderingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "ledger.sqlite3"

    def test_atomic_reservations_across_connections(self):
        ledger = Ledger(self.path, {"rail": 2, "station": 2, "walk": 0, "rag": 0})
        session = ledger.create_session()["sessionId"]
        ledger.begin(session, "one", "fingerprint", {"rail": 2, "station": 2, "walk": 0, "rag": 0})

        def reserve(_):
            try:
                ledger.reserve(session, "one", "rail", "baseline")
                return True
            except Stopped:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(reserve, range(12))), 2)
        self.assertEqual(ledger.snapshot(session, "one")["railAttempts"], 2)

    def test_restart_and_cumulative_station_cap_survive_new_operations(self):
        ledger = Ledger(self.path, {"rail": 3, "station": 1, "walk": 0, "rag": 0})
        session = ledger.create_session()["sessionId"]
        caps = {"rail": 2, "station": 2, "walk": 0, "rag": 0}
        ledger.begin(session, "one", "a", caps)
        event = ledger.reserve(session, "one", "station", "baseline")
        ledger.record(event, "failed", "provider_failure")
        restarted = Ledger(self.path)
        restarted.abandon_running()
        with self.assertRaisesRegex(PlanError, "OPERATION_ALREADY_USED"):
            restarted.begin(session, "one", "a", caps)
        restarted.begin(session, "two", "b", caps)
        with self.assertRaisesRegex(Stopped, "station_budget_exhausted"):
            restarted.reserve(session, "two", "station", "baseline")
        self.assertEqual(restarted.snapshot(session, "two")["sessionUsage"]["station"]["used"], 1)
        with self.assertRaisesRegex(PlanError, "UNKNOWN_SESSION"):
            restarted.begin("forged-id", "three", "c", caps)
        with restarted.connect() as db:
            columns = {r[1] for r in db.execute("PRAGMA table_info(operations)")}
        self.assertFalse({"request", "response", "location", "name"} & columns)

    def request(self):
        return PlanRequest.model_validate(json.loads(INPUT.read_text(encoding="utf-8")))

    def test_geometric_candidates_preserve_anchors_outliers_and_all_nodes(self):
        request = self.request()
        raw = request.model_dump(mode="json")
        raw["places"][4]["location"] = {"lat": 43.0, "lng": 141.0}
        candidate = local_candidates(PlanRequest.model_validate(raw), 800)
        self.assertLessEqual(len(candidate["orders"]), 3)
        for order in candidate["orders"]:
            self.assertEqual(order["order"][0], "p1")
            self.assertEqual(order["order"][-1], "p8")
            self.assertEqual(set(order["order"]), {p.clientPlaceId for p in request.places})
        self.assertIn(["p5"], candidate["groups"])
        self.assertEqual(candidate["routeProviderAttempts"], 0)

    def test_dense_identical_coordinates_keep_ids_and_deterministic_order(self):
        raw = self.request().model_dump(mode="json")
        for place in raw["places"]:
            place["location"] = {"lat": 35.0, "lng": 139.0}
            place.pop("anchorRole", None)
        original = local_candidates(PlanRequest.model_validate(raw))
        raw["places"].reverse()
        reversed_result = local_candidates(PlanRequest.model_validate(raw))
        self.assertEqual(original, reversed_result)
        self.assertEqual(len(original["orders"][0]["order"]), 8)
        self.assertEqual(len(original["groups"][0]), 8)

    def test_chain_cannot_expand_group_diameter_and_windows_override_geometry(self):
        raw = self.request().model_dump(mode="json")
        raw["places"] = raw["places"][:3]
        for i, place in enumerate(raw["places"]):
            place.pop("anchorRole", None)
            place["location"] = {"lat": 35.0, "lng": 139 + i * 0.004}
        request = PlanRequest.model_validate(raw)
        by_id = {p.clientPlaceId: p for p in request.places}
        for group in groups(request.places, 500):
            for a in group:
                for b in group:
                    self.assertLessEqual(distance(by_id[a], by_id[b]), 500)
        raw["places"][0]["fixedVisitAt"] = "2026-11-17T11:00:00+09:00"
        raw["places"][2]["fixedVisitAt"] = "2026-11-17T09:00:00+09:00"
        result = local_candidates(PlanRequest.model_validate(raw), 500)
        for row in result["orders"]:
            self.assertLess(row["order"].index("p3"), row["order"].index("p1"))

    def test_invalid_input_id_missing_stay_and_date_rejected(self):
        original = self.request().model_dump(mode="json")
        for mutate in (lambda r: r["places"][0].pop("stayMinutes"),
                       lambda r: r["places"][1].update(clientPlaceId="p1"),
                       lambda r: r.update(startAt="2026-02-30T09:00:00+09:00"),
                       lambda r: r.update(startAt="2026-11-17T09:00:00"),
                       lambda r: r["places"][0].update(stayMinutes=True)):
            value = deepcopy(original)
            mutate(value)
            with self.assertRaises(ValidationError):
                PlanRequest.model_validate(value)

    def test_unrepresentable_finish_time_is_pruned_without_queries(self):
        raw = self.request().model_dump(mode="json")
        raw.update(startAt="9999-12-31T23:59:00Z", endBy=None)
        raw["places"] = raw["places"][:1]
        raw["places"][0].update(openingWindows=None, stayMinutes=30)
        result = local_candidates(PlanRequest.model_validate(raw))
        self.assertEqual(result["orders"], [])
        self.assertEqual(result["routeProviderAttempts"], 0)


if __name__ == "__main__":
    unittest.main()
