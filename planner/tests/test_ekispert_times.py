"""Calendar evidence, conservative midnight handling, and headed capture wiring."""

from contextlib import asynccontextmanager
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from urllib.parse import urlencode

from pydantic import ValidationError

from planner.adapters.ekispert import AbsoluteTimes, CalendarEvidence, normalize_routes
from planner.adapters.ekispert_times import (POINT_SELECTORS, TimeResolutionError,
                                            resolve_calendar_times, result_summary)
from planner.capture import collect
from planner.evaluator import choose_ride
from planner.ledger import Ledger
from planner.models import PlanRequest
from planner.models import RailQuery, instant
from planner.replay import Recording, ReplayProvider
from planner.service import Planner


def clock(index, kind, text):
    return {"pointIndex": index, "kind": kind, "rawText": ("出発\n" if kind == "departure" else "到着\n") + text,
            "selector": POINT_SELECTORS[kind]}


def snapshot():
    return {"departureName": "東大前", "arrivalName": "後楽園", "preparedAt": "2026-11-19T09:01:00+09:00",
            "resultSummary": "東大前 ⇒ 後楽園 (2026年11月19日 9時1分 出発)",
            "pointTimes": [clock(0, "departure", "09:04"), clock(1, "arrival", "09:06")],
            "displayedDurationMinutes": 2}


def raw_payload():
    return {"routes": [{"time": 2, "fee": 178, "route": [
        {"station": "東大前", "line": "東京メトロ南北線"}, {"station": "後楽園", "line": None}]}]}


class CalendarTests(unittest.TestCase):
    def test_verified_calendar_clocks_preserve_evidence_and_wait(self):
        raw = snapshot()
        dated = resolve_calendar_times(raw)
        self.assertEqual(dated.departureAt, "2026-11-19T00:04:00Z")
        self.assertEqual(dated.arrivalAt, "2026-11-19T00:06:00Z")
        self.assertEqual(dated.calendarEvidence.resultSummary, raw["resultSummary"])
        self.assertEqual(dated.calendarEvidence.pointTimes[0].rawText, "出発\n09:04")
        rides = normalize_routes(raw_payload(), source_version="test", absolute_times=[dated], provenance_kind="test-only")
        chosen = choose_ride(rides, instant(raw["preparedAt"]))
        self.assertEqual((instant(chosen.departureAt)-instant(raw["preparedAt"])).total_seconds()/60, 3)
        self.assertIsNone(chosen.transfers)
        self.assertIsNone(chosen.fareUnits[0].baseFare)
        self.assertEqual(chosen.fareUnits[0].quotedTotal, 178)

    def test_calendar_must_match_exact_result_query_and_valid_date(self):
        for summary in ("東大前 ⇒ 後楽園 (11月19日 9時1分 出発)",
                        "東大前 ⇒ 後楽園 (2026年11月20日 9時1分 出発)",
                        "東大前 ⇒ 後楽園 (2026年11月19日 9時2分 出発)",
                        "東大前 ⇒ 後楽園 (2026年2月30日 9時1分 出発)",
                        "別駅 ⇒ 後楽園 (2026年11月19日 9時1分 出発)",
                        "東大前 ⇒ 後楽園 (2026年11月19日 9時1分 到着)"):
            with self.subTest(summary=summary), self.assertRaises(TimeResolutionError):
                resolve_calendar_times({**snapshot(), "resultSummary": summary})
        raw = snapshot()
        self.assertEqual(result_summary("検索結果\n"+raw["resultSummary"]+"\n09:04", "東大前", "後楽園"), raw["resultSummary"])
        for body in ("09:04 ⇒ 09:06", raw["resultSummary"]+"\n"+raw["resultSummary"]):
            with self.assertRaises(TimeResolutionError):
                result_summary(body, "東大前", "後楽園")

    def test_unmarked_midnight_and_unverified_day_markers_are_rejected(self):
        raw = snapshot()
        raw.update(preparedAt="2026-12-31T23:50:00+09:00",
                   resultSummary="東大前 ⇒ 後楽園 (2026年12月31日 23時50分 出発)", displayedDurationMinutes=27)
        raw["pointTimes"] = [clock(0, "departure", "23:54"), clock(1, "arrival", "00:21")]
        with self.assertRaisesRegex(TimeResolutionError, "unmarked_midnight"):
            resolve_calendar_times(raw)
        for text in ("翌日00:21", "翌00:21", "24:21", "25:00", "00:21 (翌日)"):
            with self.subTest(text=text), self.assertRaisesRegex(TimeResolutionError, "unsupported_clock"):
                resolve_calendar_times({**raw, "pointTimes": [clock(0, "departure", "23:54"), clock(1, "arrival", text)]})
        # Explicit ISO timestamps remain supported across a year boundary.
        dated = AbsoluteTimes(departureAt="2026-12-31T23:54:00+09:00", arrivalAt="2027-01-01T00:21:00+09:00", evidence="dom-time-datetime")
        self.assertGreater(instant(dated.arrivalAt), instant(dated.departureAt))

    def test_no_clock_is_created_from_duration_and_all_point_times_must_agree(self):
        raw = snapshot()
        cases = [[], [clock(0, "departure", "09:04")],
                 [clock(0, "departure", "09:04"), clock(1, "arrival", "09:60")],
                 [clock(0, "departure", "09:04"), clock(1, "arrival", "09:06 09:08")],
                 [clock(0, "departure", "09:04"), clock(1, "arrival", "09:05"), clock(1, "departure", "09:04"), clock(2, "arrival", "09:06")]]
        for points in cases:
            with self.subTest(points=points), self.assertRaises(ValueError):
                resolve_calendar_times({**raw, "pointTimes": points})
        with self.assertRaisesRegex(TimeResolutionError, "duration_disagrees"):
            resolve_calendar_times({**raw, "displayedDurationMinutes": 1440})
        with self.assertRaisesRegex(TimeResolutionError, "departure_before_query"):
            resolve_calendar_times({**raw, "pointTimes": [clock(0, "departure", "09:00"), clock(1, "arrival", "09:02")]})
        with self.assertRaisesRegex(TimeResolutionError, "departure_before_query"):
            resolve_calendar_times({**raw, "preparedAt": "2026-11-19T09:04:05+09:00", "resultSummary": "東大前 ⇒ 後楽園 (2026年11月19日 9時4分 出発)"})

    def test_evidence_enum_is_additive_but_new_path_requires_raw_evidence(self):
        dated = resolve_calendar_times(snapshot())
        self.assertEqual(AbsoluteTimes.model_validate(dated.model_dump()), dated)
        with self.assertRaises(ValidationError):
            AbsoluteTimes(departureAt=dated.departureAt, arrivalAt=dated.arrivalAt, evidence="calendar_dom_clock")
        with self.assertRaises(ValidationError):
            CalendarEvidence.model_validate({**snapshot(), "timezone": "Asia/Seoul"})
        bad = snapshot()
        bad["pointTimes"][0]["selector"] = POINT_SELECTORS["arrival"]
        with self.assertRaises(ValidationError):
            resolve_calendar_times(bad)

    def test_replay_rechecks_calendar_evidence_against_exact_query(self):
        station = lambda name: {"namespace": "manual", "sourceVersion": "test", "providerStationId": None,
                                "name": name, "line": "unknown", "prefecture": "unknown", "location": {"lat":35.0,"lng":139.0}}
        query = RailQuery(origin=station("東大前"), destination=station("後楽園"), preparedAt=snapshot()["preparedAt"])
        dated = resolve_calendar_times(snapshot()).model_dump()
        valid = Recording(query=query, payload=raw_payload(), absoluteTimes=[dated])
        self.assertEqual(valid.absoluteTimes[0].evidence, "calendar_dom_clock")
        for mutate in (lambda r: r["query"].update(preparedAt="2026-11-19T00:02:00Z"),
                       lambda r: r["absoluteTimes"][0].update(arrivalAt="2026-11-19T00:07:00Z"),
                       lambda r: r["payload"]["routes"][0].update(time=3)):
            changed = valid.model_dump()
            mutate(changed)
            with self.assertRaises(ValueError):
                Recording.model_validate(changed)


class ClockNodes:
    def __init__(self, values):
        self.values = values
    async def count(self):
        return len(self.values)
    async def get_attribute(self, name):
        return self.values[0]


class CourseNodes:
    def __init__(self, page, side=None):
        self.page, self.side = page, side
    @property
    def first(self):
        return CourseNodes(self.page, "dep")
    @property
    def last(self):
        return CourseNodes(self.page, "arr")
    async def count(self):
        return 1
    def nth(self, index):
        return self
    def locator(self, selector):
        if selector == "time[datetime]":
            return ClockNodes(self.page.iso_values[self.side])
        return self


class HeadedCaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        self.raw = snapshot()
        query = {"depName":"東大前", "arrName":"後楽園", "dateTime":int(instant(self.raw["preparedAt"]).timestamp()),
                 "searchType":"departure", "isRealtime":"false", "depCode":"one", "arrCode":"two"}
        self.page = SimpleNamespace(url="https://roote.ekispert.net/result?" + urlencode(query), iso_values={"dep":[], "arr":[]})
        self.page.locator = lambda selector: CourseNodes(self.page)
        self.page.on = lambda *args: None
        async def route(*args):
            pass
        self.page.route = route
        self.dom = {"bodyText": self.raw["resultSummary"], "courses": [{"pointCount":2,"stationNames":["東大前","後楽園"],"pointTimes":self.raw["pointTimes"]}]}
        async def evaluate(script):
            return self.dom
        self.page.evaluate = evaluate
        self.launches = []
        @asynccontextmanager
        async def browser_page(**kwargs):
            self.launches.append(kwargs)
            yield self.page
        self.searches = 0
        async def search_route(*args, **kwargs):
            self.searches += 1
        async def extract_route(page):
            return raw_payload()
        self.crawler = SimpleNamespace(browser_page=browser_page, search_route=search_route, extract_route=extract_route)

    async def capture(self, name, **kwargs):
        return await collect(self.crawler, departure="東大前", arrival="後楽園", prepared_at=self.raw["preparedAt"],
                             output=self.directory/name, **kwargs)

    async def test_headed_flag_and_default_headless_both_use_verified_calendar(self):
        for i, kwargs in enumerate(({}, {"headless":False})):
            result = await self.capture(f"{i}.json", **kwargs)
            self.assertEqual(self.launches[-1]["headless"], i == 0)
            self.assertEqual(result["headless"], i == 0)
            self.assertEqual(result["status"], "captured", result)
            self.assertEqual(result["absoluteTimes"][0]["arrivalAt"], "2026-11-19T00:06:00Z")
            self.assertEqual(result["routeSearchAttempts"], 1)
            self.assertEqual(result["retries"], 0)
        self.assertEqual(self.searches, 2)

    async def test_missing_calendar_and_ambiguous_midnight_preserve_partial_raw_payload(self):
        self.dom["bodyText"] = "09:04 ⇒ 09:06"
        result = await self.capture("missing.json", headless=False)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["timeResolution"][0]["reason"], "missing_or_ambiguous_result_calendar")
        self.assertEqual(result["payload"], raw_payload())
        self.assertEqual(result["absoluteTimes"], [None])
        self.assertEqual(result["timeResolution"][0]["rawEvidence"]["pointTimes"], self.raw["pointTimes"])
        self.assertEqual(json.loads((self.directory/'missing.json').read_text(encoding="utf-8")), result)

    async def test_mismatched_calendar_station_or_candidate_dom_rejects(self):
        self.dom["bodyText"] = self.raw["resultSummary"].replace("19日", "20日")
        result = await self.capture("calendar.json")
        self.assertEqual(result["timeResolution"][0]["reason"], "result_calendar_query_mismatch")
        self.dom["bodyText"] = self.raw["resultSummary"]
        self.dom["courses"][0]["stationNames"] = ["別駅","後楽園"]
        result = await self.capture("station.json")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["absoluteTimes"], [None])
        self.page.url = self.page.url.replace("roote.ekispert.net", "example.com")
        result = await self.capture("wrong-host.json")
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["payload"])

    async def test_invalid_explicit_iso_is_not_silently_replaced_by_calendar(self):
        self.page.iso_values = {"dep":["2026-11-18T09:04:00+09:00"], "arr":["2026-11-18T09:06:00+09:00"]}
        result = await self.capture("contradiction.json")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["timeResolution"][0]["reason"], "explicit_times_outside_query_horizon")
        self.assertEqual(result["absoluteTimes"], [None])

    async def test_invalid_mode_rejected_before_opening_report_or_launch(self):
        with self.assertRaises(ValueError):
            await self.capture("invalid.json", headless="false")
        self.assertEqual(self.launches, [])
        self.assertFalse((self.directory/'invalid.json').exists())

    async def test_headed_403_and_429_stop_without_retry_and_block_further_requests(self):
        for status in (403, 429):
            handlers = {}
            self.page.on = lambda name, handler: handlers.__setitem__(name, handler)
            gates = []
            async def route(pattern, gate):
                gates.append(gate)
            self.page.route = route
            async def search_route(*args, **kwargs):
                self.searches += 1
                handlers["request"](SimpleNamespace(resource_type="document"))
                handlers["response"](SimpleNamespace(url="https://roote.ekispert.net/", status=status))
                actions = []
                async def abort():
                    actions.append("abort")
                async def continue_():
                    actions.append("continue")
                await gates[-1](SimpleNamespace(abort=abort, continue_=continue_))
                self.assertEqual(actions, ["abort"])
                raise RuntimeError("blocked")
            self.crawler.search_route = search_route
            result = await self.capture(f"blocked-{status}.json", headless=False)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["stopReason"], f"http_{status}")
            self.assertEqual(result["routeSearchAttempts"], 1)
            self.assertEqual(result["retries"], 0)
            self.assertEqual(result["httpRequestsObserved"], 1)
            self.assertFalse(self.launches[-1]["headless"])


class SavedCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_dated_capture_completes_replay_with_wait_and_gross_fare(self):
        root = Path(__file__).resolve().parents[2] / "sample/low-call/dated"
        raw = json.loads((root/'input.json').read_text(encoding="utf-8"))
        captured = json.loads((root/'capture.json').read_text(encoding="utf-8"))
        self.assertFalse(captured["headless"])
        self.assertEqual(captured["status"], "captured")
        self.assertEqual(len(captured["absoluteTimes"]), 4)
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(Path(folder)/'ledger.sqlite3')
            raw["sessionId"] = ledger.create_session()["sessionId"]
            provider = ReplayProvider(root/'recording.json')
            result = await Planner(ledger, provider).plan(PlanRequest.model_validate(raw), raw)
        self.assertEqual(result["completion"], "complete")
        self.assertEqual(result["timing"], "reference")
        self.assertEqual(result["replayProvenance"]["kind"], "captured-team-crawler")
        self.assertEqual(result["selected"]["elapsedMinutes"], 7)
        leg = result["selected"]["legs"][0]
        self.assertEqual(leg["preparedAt"], "2026-11-19T00:01:00Z")
        self.assertEqual(leg["departureAt"], "2026-11-19T00:04:00Z")
        self.assertEqual(leg["arrivalAt"], "2026-11-19T00:06:00Z")
        self.assertEqual(leg["initialWaitMinutes"], 3)
        self.assertEqual(leg["rideMinutes"], 2)
        self.assertEqual(result["selected"]["fare"]["noPassTotal"], 178)
        self.assertIsNone(leg["transfers"])
        self.assertIsNone(leg["fareUnits"][0]["baseFare"])
        self.assertEqual(result["ledger"]["railAttempts"], 1)
        self.assertEqual(result["ledger"]["httpRequestsObserved"], 0)
        self.assertEqual(result["ledger"]["measurementBasis"], "replay-no-network")


if __name__ == "__main__":
    unittest.main()
