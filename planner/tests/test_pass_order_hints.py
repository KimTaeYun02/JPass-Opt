from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from planner.app import create_app
from planner.ledger import Ledger
from planner.order_benchmark import prepare_case
from planner.pass_order_hints import PassOrderHints
from planner.policy_benchmark import PassTrialProvider, holdout


class ReversePassProvider(PassTrialProvider):
    covered_edges = {"s3>s2", "s2>s1"}


def hints(**overrides):
    raw = {
        "schemaVersion": "pass-order-hints/1",
        "passId": "synthetic-line-pass",
        "evidence": {"source": "synthetic-station-links", "version": "hint-v1"},
        "stationNamespace": "fixture",
        "stationSourceVersion": "2026-11-test-1",
        "effectiveFrom": "2026-11-17T00:00:00+09:00",
        "effectiveUntil": "2026-11-18T00:00:00+09:00",
        "stationPairs": (("s3", "s2"), ("s2", "s1")),
    }
    return PassOrderHints.model_validate({**raw, **overrides})


class PassOrderHintsTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.ledger = Ledger(self.folder/"ledger.sqlite3")

    def plan(self, raw, provider, snapshot, policy="pass_hint"):
        with TestClient(create_app(ledger=self.ledger, provider=provider, pass_order_hints=snapshot)) as client:
            body = {**raw, "sessionId": client.post("/api/v2/sessions").json()["sessionId"],
                    "options": {**raw["options"], "allowOrderAlternates": True, "orderAlternatePolicy": policy}}
            reply = client.post("/api/v2/routes/plan", json=body)
            self.assertEqual(reply.status_code, 200, reply.text)
            return reply.json()

    def test_hint_changes_only_alternate_and_verified_pricing_finds_lower_total(self):
        raw, _ = prepare_case("scaling", 3)
        raw["options"]["allowPassEvaluation"] = True
        provider = ReversePassProvider()
        ordinary = self.plan(raw, provider, hints(), policy="diverse")
        hinted = self.plan(raw, provider, hints())
        self.assertEqual(ordinary["baseline"], hinted["baseline"])
        self.assertEqual(ordinary["selected"], hinted["selected"])
        old_audit = ordinary["candidates"]["orderEvaluation"]
        new_audit = hinted["candidates"]["orderEvaluation"]
        self.assertEqual(old_audit["alternate"]["order"], ["p2", "p1", "p3"])
        self.assertEqual(new_audit["alternate"]["order"], ["p3", "p2", "p1"])
        self.assertEqual(new_audit["effectivePolicy"], "pass_hint")
        self.assertEqual(new_audit["alternate"]["passHintMatchedLegs"], 2)
        self.assertEqual(new_audit["passHint"]["evidence"]["version"], "hint-v1")
        for result in (ordinary, hinted):
            self.assertEqual(result["ledger"]["railAttempts"], 4)
            self.assertEqual(result["ledger"]["stationAttempts"], 3)
            self.assertEqual(result["ledger"]["ragRequests"], 1)
            self.assertEqual(result["ledger"]["httpRequestsObserved"], 0)
        self.assertEqual(ordinary["passComparison"]["recommendation"]["total"], 390)
        self.assertEqual(hinted["passComparison"]["recommendation"]["total"], 190)
        self.assertEqual(hinted["passComparison"]["recommendation"]["routeDigest"], new_audit["plan"]["digest"])

    def test_unavailable_stale_or_indistinguishable_hints_fall_back_to_diverse(self):
        raw, provider = prepare_case("scaling", 3)
        ordinary = self.plan(raw, provider, None, policy="diverse")
        all_pairs = tuple((a, b) for a in ("s1", "s2", "s3") for b in ("s1", "s2", "s3") if a != b)
        cases = (
            (None, "snapshot_unavailable"),
            (hints(effectiveUntil="2026-11-17T08:00:00+09:00"), "outside_evidence_window"),
            (hints(stationSourceVersion="different-station-data"), "station_dataset_mismatch"),
            (hints(stationNamespace="different-namespace"), "station_dataset_mismatch"),
            (hints(stationPairs=(("s7", "s8"),)), "no_matching_station_pairs"),
            (hints(stationPairs=all_pairs), "no_discriminating_hint"),
        )
        for snapshot, reason in cases:
            with self.subTest(reason=reason):
                result = self.plan(raw, provider, snapshot)
                audit = result["candidates"]["orderEvaluation"]
                self.assertEqual(audit["effectivePolicy"], "diverse")
                self.assertEqual(audit["passHint"]["reason"], reason)
                self.assertEqual(audit["alternate"], ordinary["candidates"]["orderEvaluation"]["alternate"])
                self.assertEqual(result["selected"], ordinary["selected"])

    def test_direction_is_exact_and_hint_does_not_expand_budget(self):
        raw, provider = prepare_case("scaling", 3)
        forward = hints(stationPairs=(("s1", "s2"), ("s2", "s3")))
        result = self.plan(raw, provider, forward)
        audit = result["candidates"]["orderEvaluation"]
        self.assertNotEqual(audit["alternate"]["order"], ["p3", "p2", "p1"])
        self.assertEqual(audit["alternate"]["passHintMatchedLegs"], 1)
        raw["budget"] = {"railAttempts": 3}
        limited = self.plan(raw, provider, hints())
        self.assertEqual(limited["ledger"]["railAttempts"], 2)
        self.assertEqual(limited["candidates"]["orderEvaluation"]["status"], "skipped_budget")

    def test_known_reservation_miss_beats_pass_signal(self):
        raw, provider = holdout("reservation_holdout", 4)
        result = self.plan(raw, provider, hints(stationPairs=(("s1", "s2"), ("s2", "s4"), ("s4", "s3"))))
        audit = result["candidates"]["orderEvaluation"]
        self.assertEqual(audit["effectivePolicy"], "deadline_first")
        self.assertEqual(audit["passHint"]["reason"], "known_visit_deadline_missed")
        self.assertEqual(result["selected"]["order"][:2], ["p1", "p3"])
        self.assertEqual(result["ledger"]["railAttempts"], 6)

    def test_affordability_filters_better_match_and_reordering_input_has_no_effect(self):
        raw, provider = prepare_case("scaling", 5)
        raw["options"]["allowPrefixReuse"] = True
        pairs = (("s5", "s4"), ("s4", "s3"), ("s3", "s2"), ("s2", "s1"))
        plan = self.plan(raw, provider, hints(stationPairs=pairs))
        audit = plan["candidates"]["orderEvaluation"]
        self.assertEqual(audit["alternate"]["order"][:2], ["p1", "p2"])
        self.assertEqual(audit["alternate"]["passHintMatchedLegs"], 2)
        self.assertEqual(audit["alternate"]["requiredRailUpperBound"], 3)
        self.assertEqual(plan["ledger"]["railAttempts"], 7)
        raw["places"].reverse()
        repeated = self.plan(raw, provider, hints(stationPairs=pairs[::-1]))
        self.assertEqual(repeated["candidates"]["orderEvaluation"], audit)

    def test_normal_server_loads_configured_json_and_rejects_invalid_version(self):
        raw, provider = prepare_case("scaling", 3)
        path = self.folder/"pass-hints.json"
        path.write_text(hints().model_dump_json(), encoding="utf-8")
        with patch.dict("os.environ", {"JPASS_PASS_ORDER_HINTS": str(path)}):
            result = self.plan(raw, provider, None)
            self.assertEqual(result["candidates"]["orderEvaluation"]["effectivePolicy"], "pass_hint")
            path.write_text(hints().model_dump_json().replace("pass-order-hints/1", "pass-order-hints/99"),
                            encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schemaVersion"):
                create_app(ledger=self.ledger, provider=provider)

    def test_hint_never_prices_a_total_only_quote(self):
        raw, _ = prepare_case("scaling", 3)
        raw["options"]["allowPassEvaluation"] = True
        result = self.plan(raw, ReversePassProvider(total_only=True), hints())
        self.assertEqual(result["candidates"]["orderEvaluation"]["effectivePolicy"], "pass_hint")
        self.assertEqual(result["passComparison"]["status"], "partial")
        self.assertTrue(all(row["total"] is None for row in result["passComparison"]["evaluations"]))
        self.assertIsNone(result["passComparison"]["recommendation"]["passId"])
