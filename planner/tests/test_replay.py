"""Recorded supplier data stays distinct from synthetic and live data."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient
from pydantic import ValidationError

from planner.adapters.ekispert import AbsoluteTimes, audit_matrix, normalize_routes, parse_absolute_time
from planner.app import create_app
from planner.capture import collect
from planner.evaluator import known_fare
from planner.gateway import Context, Gateway
from planner.ledger import Ledger
from planner.models import FareUnit, PlanRequest, RailQuery, digest, instant, iso
from planner.pricing import fare_summary, unit_total
from planner.provider import ProviderFailure
from planner.replay import ReplayProvider, record_from_capture
from planner.service import Planner

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "sample/low-call/replay"


def load(name):
    return json.loads((SAMPLE / name).read_text(encoding="utf-8"))


class ReplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        self.data = load("legacy-recording.json")
        self.input = load("legacy-input.json")

    def provider(self, dated=False):
        raw = deepcopy(self.data)
        if dated:
            raw["provenance"]["kind"] = "test-only"
            raw["provenance"]["notice"] = "합성 시각이 포함된 테스트 전용 자료. 실제 시간표 검증이 아님."
            record = raw["records"][0]
            prepared = instant(record["query"]["preparedAt"])
            record["absoluteTimes"] = [{"departureAt": iso(prepared + timedelta(minutes=1)),
                "arrivalAt": iso(prepared + timedelta(minutes=3+i*10)), "evidence": "dom-time-datetime"}
                for i in range(len(record["payload"]["routes"]))]
        path = self.path / "recording.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return ReplayProvider(path)

    async def plan(self, provider):
        ledger = Ledger(self.path / "ledger.sqlite3")
        raw = deepcopy(self.input)
        raw["sessionId"] = ledger.create_session()["sessionId"]
        return await Planner(ledger, provider).plan(PlanRequest.model_validate(raw), raw)

    async def test_real_legacy_response_stops_missing_dates_and_never_calls_network(self):
        result = await self.plan(self.provider())
        self.assertEqual(result["dataSource"], "replay")
        self.assertEqual(result["completion"], "partial")
        self.assertEqual(result["timing"], "unknown")
        self.assertEqual(result["selected"]["stopReason"], "missing_dated_trip")
        self.assertIsNone(result["selected"]["fare"]["noPassTotal"])
        self.assertEqual(result["ledger"]["railAttempts"], 1)
        self.assertEqual(result["ledger"]["stationAttempts"], 2)
        self.assertEqual(result["ledger"]["httpRequestsObserved"], 0)
        self.assertEqual(result["ledger"]["measurementBasis"], "replay-no-network")
        self.assertEqual(result["ledger"]["ragRequests"], 0)
        self.assertEqual(result["replayProvenance"]["kind"], "legacy-team-crawler")

    async def test_explicit_test_dates_propagate_wait_and_gross_without_invented_components(self):
        result = await self.plan(self.provider(dated=True))
        self.assertEqual(result["completion"], "complete")
        self.assertEqual(result["timing"], "reference")
        route = result["selected"]
        self.assertEqual(route["elapsedMinutes"], 5)
        self.assertEqual(route["legs"][0]["initialWaitMinutes"], 1)
        self.assertIsNone(route["transfers"])
        self.assertIsNone(route["fare"]["fareUnits"][0]["baseFare"])
        self.assertIsNone(route["fare"]["fareUnits"][0]["supplement"])
        self.assertEqual(route["fare"]["noPassTotal"], self.data["records"][0]["payload"]["routes"][0]["fee"])
        self.assertEqual(result["replayProvenance"]["kind"], "test-only")
        self.assertIn("합성", result["fixtureLabel"])
        self.assertEqual(route["legs"][0]["providerEvidence"]["source"], "ekispert-test-only")
        self.assertEqual(route["fare"]["fareUnits"][0]["evidence"]["source"], "ekispert-test-only")

    async def test_client_cannot_enable_replay_cache_or_prefix_reuse(self):
        provider = self.provider(dated=True)
        ledger = Ledger(self.path / "cache.sqlite3")
        session = ledger.create_session()["sessionId"]
        ledger.begin(session, "cache-test", "x", {"rail": 2, "station": 0, "walk": 0, "rag": 0})
        ctx = Context(ledger, session, "cache-test", time.monotonic() + 30, cache_allowed=True)
        gateway = Gateway(provider)
        query = RailQuery.model_validate(self.data["records"][0]["query"])
        await gateway.rail(ctx, query)
        await gateway.rail(ctx, query)
        self.assertEqual(ctx.cache_hits, 0)
        self.assertEqual(ledger.snapshot(session, "cache-test")["railAttempts"], 2)
        self.input["options"].update(allowRepair=True, allowPrefixReuse=True, allowCache=True)
        result = await self.plan(provider)
        self.assertEqual(result["improvement"], "disabled_reuse")

    async def test_walk_only_record_reports_unsupported_instead_of_free_rail(self):
        self.data["provenance"]["kind"] = "test-only"
        self.data["records"][0]["payload"]["routes"] = [{"time": 10, "fee": 0,
            "route": [{"station": self.data["records"][0]["query"]["origin"]["name"], "line": "徒歩"},
                      {"station": self.data["records"][0]["query"]["destination"]["name"], "line": None}]}]
        result = await self.plan(self.provider())
        self.assertEqual(result["selected"]["stopReason"], "replay_walk_only_unsupported")
        self.assertEqual(result["completion"], "partial")
        self.assertIsNone(result["selected"]["fare"]["noPassTotal"])

    async def test_exact_record_identity_direction_options_and_instant_no_fallback(self):
        provider = self.provider()
        base = self.data["records"][0]["query"]
        first = await provider.rail(RailQuery.model_validate(base))
        first.clear()
        self.assertTrue(await provider.rail(RailQuery.model_validate(base)))
        equivalent = {**base, "preparedAt": "2026-10-17T09:00:05+09:00"}
        self.assertTrue(await provider.rail(RailQuery.model_validate(equivalent)))
        for mutate in (lambda q: q.update(preparedAt="2026-10-17T00:00:06Z"),
                       lambda q: q.update(origin=q["destination"], destination=q["origin"]),
                       lambda q: q.update(options={"railOnly": True}),
                       lambda q: q["origin"]["location"].update(lat=35.001),
                       lambda q: q["origin"].update(sourceVersion="other")):
            query = deepcopy(base)
            mutate(query)
            with self.assertRaisesRegex(ProviderFailure, "replay_query_miss"):
                await provider.rail(RailQuery.model_validate(query))

    async def test_place_lookup_rejects_changed_coordinates_and_returns_isolated_values(self):
        provider = self.provider()
        place = PlanRequest.model_validate(self.input).places[0]
        stations = await provider.stations(place)
        stations.clear()
        self.assertEqual(len(await provider.stations(place)), 1)
        changed = place.model_dump()
        changed["location"]["lat"] += 0.01
        with self.assertRaisesRegex(ProviderFailure, "replay_place_miss"):
            await provider.stations(type(place).model_validate(changed))
        with self.assertRaisesRegex(ProviderFailure, "replay_rag_disabled"):
            await provider.passes([])

    async def test_archive_miss_is_counted_failure_without_retry(self):
        self.input["startAt"] = "2026-10-17T00:05:00Z"
        result = await self.plan(self.provider())
        self.assertEqual(result["selected"]["stopReason"], "replay_query_miss")
        self.assertEqual(result["ledger"]["railAttempts"], 1)
        self.assertEqual(result["ledger"]["failedAttempts"], 1)
        self.assertIsNone(result["selected"]["finishAt"])

    async def test_replay_api_source_session_validation_and_no_client_dataset_path(self):
        provider = self.provider()
        with TestClient(create_app(ledger=Ledger(self.path / "api.sqlite3"), provider=provider)) as client:
            self.assertEqual(client.get("/api/v2/health").json()["dataSource"], "replay")
            issued = client.post("/api/v2/sessions").json()
            self.assertEqual(issued["dataSource"], "replay")
            raw = deepcopy(self.input)
            raw["sessionId"] = issued["sessionId"]
            reply = client.post("/api/v2/routes/plan", json=raw)
            self.assertEqual(reply.status_code, 200, reply.text)
            self.assertEqual(reply.json()["ledger"]["measurementBasis"], "replay-no-network")
            self.assertEqual(client.post("/api/v2/routes/plan", json={**raw, "dataSource": "live"}).status_code, 501)
            self.assertEqual(client.post("/api/v2/routes/plan", json={**raw, "dataSource": "fixture"}).json()["error"], "SOURCE_MISMATCH")
            self.assertEqual(client.post("/api/v2/routes/plan", json={**raw, "recordingPath": "private.json"}).status_code, 400)

    def test_duplicate_query_or_place_and_endpoint_mismatch_are_rejected(self):
        for mutate in (lambda d: d["records"].append(deepcopy(d["records"][0])),
                       lambda d: d["bindings"].append(deepcopy(d["bindings"][0])),
                       lambda d: d["records"][0]["payload"]["routes"][0]["route"][0].update(station="別駅")):
            data = deepcopy(self.data)
            mutate(data)
            path = self.path / "bad.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                ReplayProvider(path)

    def test_legacy_provenance_cannot_hide_injected_dates(self):
        self.provider(dated=True)
        path = self.path / "recording.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["provenance"]["kind"] = "legacy-team-crawler"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "test-only"):
            ReplayProvider(path)

    def test_cli_writes_actual_replay_report_and_refuses_to_overwrite(self):
        output = self.path / "result.json"
        command = [sys.executable, "-m", "planner.replay_cli", "--recording", str(SAMPLE / "legacy-recording.json"),
                   "--input", str(SAMPLE / "legacy-input.json"), "--report", str(output)]
        first = subprocess.run(command, cwd=ROOT, capture_output=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"}, timeout=20)
        self.assertEqual(first.returncode, 0, first.stderr)
        response = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(response["selected"]["stopReason"], "missing_dated_trip")
        before = output.read_bytes()
        second = subprocess.run(command, cwd=ROOT, capture_output=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"}, timeout=20)
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(output.read_bytes(), before)


class AdapterTests(unittest.TestCase):
    def test_real_eighty_candidate_corpus_audit_and_normalization(self):
        rows = json.loads((ROOT / "sample/team-crawler-route-matrix.json").read_text(encoding="utf-8"))
        actual = audit_matrix(rows)
        self.assertEqual((actual["stationPairs"], actual["candidates"], actual["grossFareKnown"]), (20, 80, 80))
        self.assertEqual(actual["datedDepartureArrivalKnown"], 0)
        self.assertTrue(actual["endpointsMatch"])
        for pair in rows:
            rides = normalize_routes({"routes": pair["routes"]}, source_version="saved-real-corpus")
            self.assertEqual(len(rides), len(pair["routes"]))
            for course, ride in zip(pair["routes"], rides):
                self.assertIsNone(ride.departureAt)
                self.assertIsNone(ride.arrivalAt)
                self.assertIsNone(ride.transfers)
                self.assertEqual(known_fare(ride), course["fee"])

    def test_explicit_midnight_dates_work_but_clock_only_and_ambiguous_dates_fail(self):
        valid = AbsoluteTimes(departureAt="2026-11-19T23:58:00+09:00", arrivalAt="2026-11-20T00:13:00+09:00", evidence="dom-time-datetime")
        self.assertLess(instant(valid.departureAt), instant(valid.arrivalAt))
        for raw in ("00:13", "翌日00:13", "11/20 00:13", "2026-11-20T00:13:00", "2026-02-30T00:13:00+09:00"):
            with self.assertRaises(ValueError):
                parse_absolute_time(raw)
        with self.assertRaises(ValueError):
            AbsoluteTimes(departureAt=valid.arrivalAt, arrivalAt=valid.departureAt, evidence="dom-time-datetime")

    def test_gross_total_does_not_invent_breakdown_and_rejects_contradictions(self):
        unit = {"fareUnitId": "one", "rideSegmentIds": ["a"], "baseFare": None, "supplement": None,
                "quotedTotal": 178, "evidence": {"source": "test", "version": "1"}}
        parsed = FareUnit.model_validate(unit)
        self.assertEqual(unit_total(parsed.model_dump()), 178)
        for change in ({"baseFare": 150, "supplement": 20}, {"baseFare": 200}, {"quotedTotal": True}):
            with self.assertRaises(ValidationError):
                FareUnit.model_validate({**unit, **change})
        no_total = FareUnit.model_validate({**unit, "quotedTotal": None})
        self.assertIsNone(unit_total(no_total.model_dump()))


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.output = Path(self.folder.name) / "capture.json"

    async def test_blocked_response_counted_once_and_preserved_without_retry(self):
        handlers = {}
        counts = {"searches": 0}
        page = SimpleNamespace(on=lambda name, fn: handlers.__setitem__(name, fn))

        async def route(pattern, handler):
            page.gate = handler
        page.route = route

        @asynccontextmanager
        async def browser_page(**kwargs):
            self.assertTrue(kwargs["headless"])
            yield page

        async def search_route(*args, **kwargs):
            counts["searches"] += 1
            handlers["request"](SimpleNamespace(resource_type="document"))
            handlers["response"](SimpleNamespace(url="https://roote.ekispert.net/", status=403))
            raise RuntimeError("access denied")

        crawler = SimpleNamespace(browser_page=browser_page, search_route=search_route)
        result = await collect(crawler, departure="東京", arrival="上野", prepared_at="2026-11-19T09:00:00+09:00", output=self.output)
        self.assertEqual((result["status"], result["stopReason"]), ("blocked", "http_403"))
        self.assertEqual((counts["searches"], result["routeSearchAttempts"], result["httpRequestsObserved"]), (1, 1, 1))
        self.assertEqual(result["retries"], 0)
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8")), result)
        with self.assertRaises(FileExistsError):
            await collect(crawler, departure="東京", arrival="上野", prepared_at="2026-11-19T09:00:00+09:00", output=self.output)
        self.assertEqual(counts["searches"], 1)

    async def test_cancelled_capture_persists_committed_search_without_retry(self):
        started = asyncio.Event()
        release = asyncio.Event()
        page = SimpleNamespace(on=lambda *args: None)

        async def route(*args):
            pass
        page.route = route

        @asynccontextmanager
        async def browser_page(**kwargs):
            yield page

        async def search_route(*args, **kwargs):
            started.set()
            await release.wait()

        task = asyncio.create_task(collect(SimpleNamespace(browser_page=browser_page, search_route=search_route),
            departure="東京", arrival="上野", prepared_at="2026-11-19T09:00:00+09:00", output=self.output))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        record = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual((record["stopReason"], record["routeSearchAttempts"]), ("cancelled", 1))

    async def test_capture_validates_explicit_dom_dates_and_can_bind_to_record(self):
        at = "2026-11-19T09:00:00+09:00"
        query = load("legacy-recording.json")["records"][0]["query"]
        query["preparedAt"] = at
        payload = load("legacy-recording.json")["records"][0]["payload"]
        payload["routes"] = payload["routes"][:1]
        for present in (True, False):
            output = Path(self.folder.name) / f"dates-{present}.json"
            class Times:
                def __init__(self, value):
                    self.value = value
                def locator(self, selector):
                    return self
                async def count(self):
                    return 1 if present else 0
                async def get_attribute(self, name):
                    return self.value
            class Points:
                first = Times("2026-11-19T09:01:00+09:00")
                last = Times("2026-11-19T09:03:00+09:00")
                def nth(self, i):
                    return self
                def locator(self, selector):
                    return self
                async def count(self):
                    return 1
            handlers = {}
            page = SimpleNamespace(on=lambda name, fn: handlers.__setitem__(name, fn), locator=lambda selector: Points(),
                url=f"https://roote.ekispert.net/result?dateTime={int(instant(at).timestamp())}&searchType=departure&isRealtime=false&depCode=one&arrCode=two&ticketType=IC&mockFileName=&depName=東大前&arrName=後楽園")
            async def evaluate(script):
                return {"bodyText": "", "courses": []}
            page.evaluate = evaluate
            async def route(*args):
                pass
            page.route = route
            @asynccontextmanager
            async def browser_page(**kwargs):
                yield page
            async def search_route(*args, **kwargs):
                handlers["request"](SimpleNamespace(resource_type="document"))
                handlers["response"](SimpleNamespace(url=page.url, status=200))
            async def extract_route(page):
                return payload
            result = await collect(SimpleNamespace(browser_page=browser_page, search_route=search_route, extract_route=extract_route),
                departure=query["origin"]["name"], arrival=query["destination"]["name"], prepared_at=at, output=output)
            self.assertEqual(result["status"], "captured" if present else "partial")
            record = record_from_capture(result, RailQuery.model_validate(query))
            self.assertEqual(record.providerOptions["ticketType"], "IC")
            self.assertEqual(record.providerOptions["mockFileName"], "")
            rides = normalize_routes(record.payload, source_version="test-only", absolute_times=record.absoluteTimes)
            self.assertEqual(rides[0].departureAt is not None, present)
            for mutate in (lambda r: r.update(status="blocked"), lambda r: r["query"].update(preparedAt="2026-11-19T09:00:01+09:00")):
                wrong = deepcopy(result)
                mutate(wrong)
                with self.assertRaises(ValueError):
                    record_from_capture(wrong, RailQuery.model_validate(query))
            page.url += "&ticketType=cash"
            repeated = await collect(SimpleNamespace(browser_page=browser_page, search_route=search_route, extract_route=extract_route),
                departure=query["origin"]["name"], arrival=query["destination"]["name"], prepared_at=at,
                output=Path(self.folder.name) / f"repeated-{present}.json")
            self.assertEqual(repeated["status"], "failed")
            self.assertIsNone(repeated["payload"])

    async def test_capture_deadline_persists_one_attempt(self):
        page = SimpleNamespace(on=lambda *args: None)
        async def route(*args):
            pass
        page.route = route
        @asynccontextmanager
        async def browser_page(**kwargs):
            yield page
        async def search_route(*args, **kwargs):
            await asyncio.Event().wait()
        result = await collect(SimpleNamespace(browser_page=browser_page, search_route=search_route),
            departure="東京", arrival="上野", prepared_at="2026-11-19T09:00:00+09:00", output=self.output, timeout_seconds=0.01)
        self.assertEqual((result["stopReason"], result["routeSearchAttempts"]), ("deadline_exceeded", 1))


if __name__ == "__main__":
    unittest.main()
