from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from planner.ledger import Ledger
from planner.order_benchmark import run_policy
from planner.policy_benchmark import block_network, compare_policies, holdout, pass_trial, replay_trial


class PolicyStudyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)
        self.ledger = Ledger(self.path/"ledger.sqlite3")

    async def test_four_attempts_find_the_reverse_under_tight_finish(self):
        raw, provider = holdout("direction_tight", 3)
        policies = await compare_policies(self.ledger, provider, raw)
        self.assertEqual({row["railAttempts"] for row in policies.values()}, {4})
        self.assertEqual(policies["reverse_first"]["order"], ["p3", "p2", "p1"])
        self.assertTrue(policies["reverse_first"]["feasible"])
        self.assertEqual(policies["diverse"]["feasibilityStatus"], "not_found_in_evaluated_orders")

    async def test_deadline_recovers_fixed_visit_but_budget_can_rule_out_second_order(self):
        raw, provider = holdout("reservation_holdout", 4)
        policies = await compare_policies(self.ledger, provider, raw)
        self.assertFalse(policies["diverse"]["feasible"])
        fixed = policies["deadline_first"]
        self.assertEqual(fixed["priorityVisitIds"][0], "p3")
        self.assertEqual(fixed["order"][:2], ["p1", "p3"])
        self.assertEqual(fixed["feasibilityStatus"], "confirmed")
        self.assertEqual(fixed["railAttempts"], policies["diverse"]["railAttempts"])
        raw, provider = holdout("reservation_anchored", 5)
        raw["options"]["orderAlternatePolicy"] = "deadline_first"
        plan = await run_policy(self.ledger, provider, raw, "limited", alternate=True, prefix=True)
        audit = plan["candidates"]["orderEvaluation"]
        self.assertEqual(audit["status"], "skipped_budget")
        self.assertEqual(audit["feasibilityStatus"], "not_found_in_evaluated_orders")
        self.assertIn("평가한 후보", audit["feasibilityMessage"])
        self.assertTrue(any(check["status"] == "violated" for check in plan["constraints"]))

    async def test_one_pass_batch_can_recommend_a_different_schedule(self):
        one = await pass_trial(self.ledger, alternate=False)
        two = await pass_trial(self.ledger)
        recommendation = two["passComparison"]["recommendation"]
        alternate = two["candidates"]["orderEvaluation"]["plan"]
        self.assertEqual(one["selected"]["digest"], two["selected"]["digest"])
        self.assertEqual(one["passComparison"]["recommendation"]["total"], 440)
        self.assertEqual(two["ledger"]["ragRequests"], 1)
        self.assertEqual(two["ledger"]["railAttempts"], 4)
        self.assertEqual(recommendation, {"routeDigest": alternate["digest"],
                                         "passId": "synthetic-line-pass", "total": 190})
        self.assertGreater(alternate["elapsedMinutes"], two["selected"]["elapsedMinutes"])
        self.assertEqual({r["routeDigest"] for r in two["passComparison"]["evaluations"]},
                         {alternate["digest"], two["selected"]["digest"]})

    async def test_total_only_and_real_replay_do_not_claim_pass_cost(self):
        result = await pass_trial(self.ledger, total_only=True)
        comparison = result["passComparison"]
        self.assertEqual(comparison["status"], "partial")
        self.assertEqual(len(comparison["unconfirmedPassRouteDigests"]), 2)
        for item in comparison["evaluations"]:
            self.assertEqual(item["reason"], "fare_components_unconfirmed")
            self.assertIsNone(item["total"])
            self.assertNotIn("decision", item)
        self.assertEqual(comparison["recommendation"]["total"], 440)
        self.assertIsNone(comparison["recommendation"]["passId"])
        single = await pass_trial(self.ledger, total_only=True, alternate=False)
        single_comparison = single["passComparison"]
        self.assertEqual(single_comparison["status"], "partial")
        self.assertIsNone(single_comparison["evaluations"][0]["total"])
        self.assertIsNone(single_comparison["recommendation"]["passId"])
        with block_network():
            replay = await replay_trial(self.ledger, self.path/"recordings.json")
        self.assertEqual(replay["ragRequests"], 0)
        self.assertIsNone(replay["passComparison"]["recommendation"])
        self.assertEqual(replay["policies"]["reverse_first"]["alternateMetrics"]["stopReason"], "replay_query_miss")

    async def test_policy_is_inactive_when_opted_out_and_short_window_has_a_preflight_proof(self):
        raw, provider = holdout("reservation_holdout", 4)
        first = None
        for policy in ("diverse", "reverse_first", "deadline_first"):
            raw["options"]["orderAlternatePolicy"] = policy
            plan = await run_policy(self.ledger, provider, raw, "disabled", alternate=False)
            self.assertNotIn("orderEvaluation", plan["candidates"])
            del plan["echoInput"]
            for attempt in plan["ledger"]["attempts"]:
                del attempt["id"]  # Row IDs differ across independent runs.
            if first is None:
                first = plan
            else:
                self.assertEqual(plan, first)
        raw["places"][0]["openingWindows"][0]["endAt"] = "2026-11-17T08:10:00+09:00"
        impossible = await run_policy(self.ledger, provider, raw, "preflight", alternate=True)
        self.assertIn("mandatory_time_constraints_infeasible", impossible["stopReasons"])
        self.assertEqual(impossible["ledger"]["railAttempts"], 0)
        self.assertNotIn("orderEvaluation", impossible["candidates"])
