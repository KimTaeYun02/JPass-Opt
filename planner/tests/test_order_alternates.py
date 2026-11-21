from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from planner.evaluator import evaluate
from planner.gateway import Context
from planner.ledger import Ledger
from planner.models import PlanRequest, StationCandidate
from planner.order_benchmark import compare_replay, prepare_case, run_policy
from planner.provider import Capabilities
from planner.service import Planner


class OrderAlternateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.ledger = Ledger(self.folder/"plans.sqlite3")

    async def test_replay_selects_distinct_rank_three_and_preserves_baseline_on_missing_record(self):
        base = Path(__file__).resolve().parents[2]/"sample/low-call"
        with patch("socket.socket.connect", side_effect=AssertionError("offline only")):
            result = await compare_replay(self.ledger, self.folder/"different", "different_orders",
                                          base/"divergent-revalidation/run-20261119")
            missing = await compare_replay(self.ledger, self.folder/"same", "baseline_only",
                                           base/"sequential/run-20261119-compat")
        self.assertEqual((result["first"]["elapsedMinutes"], result["first"]["grossFare"]), (379, 1246))
        chosen = result["diverse"]
        self.assertEqual((chosen["elapsedMinutes"], chosen["grossFare"], chosen["railAttempts"]), (373, 1047, 4))
        self.assertEqual(chosen["alternate"]["geometricRank"], 3)
        self.assertEqual(chosen["alternate"]["reusedLegs"], 0)
        self.assertEqual(chosen["httpRequestsObserved"], 0)
        self.assertEqual(missing["diverse"]["order"], missing["first"]["order"])
        self.assertEqual(missing["diverse"]["alternateMetrics"]["stopReason"], "replay_query_miss")
        self.assertIsNone(missing["diverse"]["alternateMetrics"]["grossFare"])

    async def test_suffix_matches_full_route_and_requires_both_permissions(self):
        raw, provider = prepare_case("scaling", 8)
        skipped = await run_policy(self.ledger, provider, raw, "without", alternate=True)
        self.assertEqual(skipped["candidates"]["orderEvaluation"]["status"], "skipped_budget")
        result = await run_policy(self.ledger, provider, raw, "with", alternate=True, prefix=True)
        audit = result["candidates"]["orderEvaluation"]
        self.assertEqual(audit["alternate"]["reusedLegs"], 5)
        self.assertEqual(result["ledger"]["railAttempts"], 9)
        self.assertEqual(result["ledger"]["stationAttempts"], 8)
        req = PlanRequest.model_validate(result["echoInput"])
        choices = {p.clientPlaceId: StationCandidate.model_validate(provider.data["stations"][p.clientPlaceId][0])
                   for p in req.places}
        self.ledger.begin(req.sessionId, "full-proof", "full-proof", {"rail":7,"station":0,"walk":0,"rag":0})
        ctx = Context(self.ledger, req.sessionId, "full-proof", time.monotonic()+30)
        try:
            full = await evaluate(req, audit["alternate"]["order"], choices, Planner(self.ledger, provider).gateway, ctx)
        finally:
            self.ledger.finish(req.sessionId, "full-proof")
        self.assertEqual(audit["plan"], full)
        provider.capabilities = Capabilities(reusePrefixInRequest=False)
        blocked = await run_policy(self.ledger, provider, raw, "provider-blocks", alternate=True, prefix=True)
        self.assertEqual(blocked["ledger"]["railAttempts"], 7)
        self.assertEqual(blocked["candidates"]["orderEvaluation"]["status"], "skipped_budget")

    async def test_infeasible_route_is_not_promoted_and_feasible_alternate_can_recover(self):
        for name, n, status, feasible in (("no_feasible", 3, "infeasible", False),
                                          ("opening_window", 4, "selected", True)):
            raw, provider = prepare_case(name, n)
            plan = await run_policy(self.ledger, provider, raw, name, alternate=True)
            audit = plan["candidates"]["orderEvaluation"]
            self.assertEqual(audit["status"], status)
            self.assertIs(audit["selectedWithoutKnownViolations"], feasible)
            self.assertTrue(any(check["status"] == "violated" for check in plan["baseline"]["plan"]["constraints"]))

    async def test_complete_alternate_that_misses_closing_time_is_rejected(self):
        raw, provider = prepare_case("scaling", 3)
        raw["places"][0]["openingWindows"][0]["endAt"] = "2026-11-17T10:02:00+09:00"
        result = await run_policy(self.ledger, provider, raw, "rejected", alternate=True)
        audit = result["candidates"]["orderEvaluation"]
        self.assertEqual(audit["status"], "infeasible")
        self.assertEqual(audit["plan"]["completion"], "complete")
        self.assertTrue(audit["selectedWithoutKnownViolations"])
        self.assertEqual(result["selected"], result["baseline"]["plan"])

    async def test_reversal_only_and_anchored_no_alternative(self):
        raw, provider = prepare_case("scaling", 2)
        provider.data["edges"]["s1>s2"] = {"headwayMinutes": 10, "rideMinutes": 55, "fare": 900}
        plan = await run_policy(self.ledger, provider, raw, "reverse", alternate=True)
        audit = plan["candidates"]["orderEvaluation"]
        self.assertEqual(audit["alternate"]["kind"], "reversal")
        self.assertEqual(plan["selected"]["order"], ["p2", "p1"])
        self.assertEqual(plan["ledger"]["railAttempts"], 2)
        raw["places"][0]["anchorRole"] = "start"
        anchored = await run_policy(self.ledger, provider, raw, "no-alternative", alternate=True)
        self.assertEqual(anchored["candidates"]["orderEvaluation"]["status"], "no_alternative")
        self.assertEqual(anchored["ledger"]["railAttempts"], 1)

    async def test_retry_and_session_caps_count_before_alternate_dispatch(self):
        raw, provider = prepare_case("rail_detour", 3)
        provider.failures["rail:s1>s2"] = 1
        raw.update(sessionId=self.ledger.create_session()["sessionId"], operationId="retry")
        raw["options"]["allowOrderAlternates"] = True
        result = await Planner(self.ledger, provider).plan(PlanRequest.model_validate(raw), raw)
        self.assertEqual(result["ledger"]["railAttempts"], 3)
        self.assertEqual(result["ledger"]["failedAttempts"], 1)
        self.assertEqual(result["candidates"]["orderEvaluation"]["status"], "skipped_budget")
        self.assertEqual(sum(event[0] == "rail" for event in provider.events), 3)

        limited = Ledger(self.folder/"limited.sqlite3", {"rail":3,"station":3,"walk":0,"rag":0})
        plan = await run_policy(limited, provider, raw, "session-cap", alternate=True)
        self.assertEqual(plan["ledger"]["limits"]["rail"], 4)
        self.assertEqual(plan["ledger"]["sessionUsage"]["rail"], {"used":2,"limit":3})
        self.assertEqual(plan["candidates"]["orderEvaluation"]["status"], "skipped_budget")
