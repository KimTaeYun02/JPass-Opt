from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from planner.app import create_app
from planner.fixture_provider import FixtureProvider
from planner.ledger import Ledger

ROOT = Path(__file__).resolve().parents[2]


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.ledger = Ledger(Path(self.folder.name) / "budget.sqlite3")
        self.provider = FixtureProvider(ROOT / "planner/fixtures/tokyo-demo.json")
        self.client = TestClient(create_app(ledger=self.ledger, provider=self.provider))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def request(self):
        request = json.loads((ROOT / "sample/low-call/eight-node-input.json").read_text(encoding="utf-8"))
        created = self.client.post("/api/v2/sessions")
        self.assertEqual(created.status_code, 201)
        request["sessionId"] = created.json()["sessionId"]
        return request

    def test_http_api_issues_session_echoes_request_and_does_not_replay_used_operation(self):
        raw = self.request()
        response = self.client.post("/api/v2/routes/plan", json=raw)
        self.assertEqual(response.status_code, 200, response.text)
        value = response.json()
        self.assertEqual(value["dataSource"], "fixture")
        self.assertEqual(value["echoInput"], raw)
        self.assertLessEqual(value["ledger"]["railAttempts"], 10)
        self.assertEqual(value["ledger"]["ragRequests"], 1)
        prior = len(self.provider.events)
        replay = self.client.post("/api/v2/routes/plan", json=raw)
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(replay.json()["error"], "OPERATION_ALREADY_USED")
        self.assertEqual(len(self.provider.events), prior)
        raw["places"][0]["stayMinutes"] = 31
        changed = self.client.post("/api/v2/routes/plan", json=raw)
        self.assertEqual(changed.json()["error"], "OPERATION_ID_CONFLICT")

    def test_live_invalid_json_invalid_dates_and_unknown_session_dispatch_nothing(self):
        raw = self.request()
        attempts = [({**raw, "dataSource": "live"}, 501), ({**raw, "sessionId": "made-up"}, 400),
                    ({**raw, "budget": {"railAttempts": 1}}, 400), ({**raw, "startAt": "2026-02-30T09:00:00+09:00"}, 400),
                    ({**raw, "unexpected": True}, 400)]
        for body, status in attempts:
            reply = self.client.post("/api/v2/routes/plan", json=body)
            self.assertEqual(reply.status_code, status, reply.text)
        broken = self.client.post("/api/v2/routes/plan", content="{", headers={"content-type": "application/json"})
        self.assertEqual(broken.status_code, 400)
        self.assertEqual(self.provider.events, [])
        self.assertFalse(self.client.get("/api/v2/health").json()["liveSupported"])

    def test_opt_in_order_comparison_uses_shared_station_lookup(self):
        raw = self.request()
        raw["places"] = raw["places"][:3]
        raw["places"][0].pop("anchorRole")
        raw["options"] = {"allowOrderAlternates": True, "allowPassEvaluation": False}
        response = self.client.post("/api/v2/routes/plan", json=raw)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        audit = result["candidates"]["orderEvaluation"]
        self.assertEqual(audit["alternate"]["kind"], "different_path")
        self.assertEqual(audit["alternate"]["geometricRank"], 3)
        self.assertEqual(result["ledger"]["railAttempts"], 4)
        self.assertEqual(result["ledger"]["stationAttempts"], 3)
        self.assertEqual(result["repairLog"], [])
        self.assertEqual(result["improvement"], "disabled_order_alternates")

    def test_session_usage_persists_new_operations_and_station_only_failures(self):
        self.client.__exit__(None, None, None)
        ledger = Ledger(Path(self.folder.name) / "small.sqlite3", {"rail": 2, "station": 1, "walk": 0, "rag": 1})
        provider = FixtureProvider(ROOT / "planner/fixtures/tokyo-demo.json")
        provider.failures["station:p1"] = 10
        with TestClient(create_app(ledger=ledger, provider=provider)) as client:
            raw = json.loads((ROOT / "sample/low-call/eight-node-input.json").read_text(encoding="utf-8"))
            raw["sessionId"] = client.post("/api/v2/sessions").json()["sessionId"]
            first = client.post("/api/v2/routes/plan", json=raw).json()
            self.assertEqual(first["ledger"]["stationAttempts"], 1)
            self.assertEqual(first["ledger"]["railAttempts"], 0)
            raw["operationId"] = "next-op"
            second = client.post("/api/v2/routes/plan", json=raw).json()
            self.assertEqual(second["ledger"]["stationAttempts"], 0)
            self.assertEqual(second["ledger"]["sessionUsage"]["station"]["used"], 1)
            self.assertTrue(second["ledger"]["budgetExhausted"])

    def test_v1_endpoint_is_not_shadowed_by_fixture_api(self):
        self.assertEqual(self.client.post("/api/routes/optimize", json={}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
