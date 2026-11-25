"""Offline checks for the standalone PoC. No real provider requests."""

import argparse
import copy
import importlib.util
from datetime import datetime
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from app.clients.google_routes import WalkingRoute

spec = importlib.util.spec_from_file_location(
    "transit_poc", Path(__file__).resolve().parents[1] / "scripts/verify_transit_poc.py"
)
poc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poc)


def point(code, lat=34.69, lng=135.51):
    return {
        "Station": {"code": code, "Type": {"text": "bus", "detail": "local"}},
        "GeoPoint": {"gcs": "wgs84", "lati_d": str(lat), "longi_d": str(lng)},
    }


def course():
    return {
        "ResultSet": {
            "Course": {
                "Route": {
                    "timeOnBoard": "15",
                    "timeWalk": "0",
                    "timeOther": "0",
                    "Point": [point("100"), point("200")],
                    "Line": {
                        "Type": {"text": "bus", "detail": "local"},
                        "TimeReliability": "onTimetable",
                        "DepartureState": {
                            "Datetime": {"text": "2026-11-14T09:08:00+09:00"}
                        },
                        "ArrivalState": {
                            "Datetime": {"text": "2026-11-14T09:23:00+09:00"}
                        },
                    },
                }
            }
        }
    }


class FakeCalls:
    def __init__(self, response=None):
        self.counts = dict.fromkeys(poc.LIMITS, 0)
        self.response = course() if response is None else response
        self.course_params = None
        self.geo_params = []
        self.walk_pairs = []

    def get(self, kind, params):
        self.counts[kind] += 1
        if kind == "geo":
            self.geo_params.append(params)
            return {
                "ResultSet": {
                    "Point": point("100" if self.counts[kind] == 1 else "200")
                }
            }
        self.course_params = params
        return self.response

    def walk(self, origin, destination):
        self.counts["walk"] += 1
        self.walk_pairs.append((origin, destination))
        return WalkingRoute(5, 300, [origin, destination])


class TransitPocTests(TestCase):
    env = {
        "EKISPERT_API_KEY": "test-only-ekispert",
        "GOOGLE_ROUTES_API_KEY": "test-only-routes",
    }
    start = datetime.fromisoformat("2026-11-14T09:00:00+09:00")

    def test_keyless_and_configured_preflight_never_call_providers(self):
        with (
            patch.object(poc.requests, "get", side_effect=AssertionError("network")),
            patch.object(poc, "walking_route", side_effect=AssertionError("network")),
        ):
            for live in (False, True):
                code, report = poc.run(live, self.start, {})
                self.assertEqual(code, 1)
                self.assertEqual(report["calls"], {"geo": 0, "course": 0, "walk": 0})
            code, report = poc.run(False, None, self.env)
            self.assertEqual(code, 0)
            self.assertEqual(sum(report["calls"].values()), 0)

    def test_walk_direction_time_shift_wait_and_total(self):
        calls = FakeCalls()
        code, report = poc.run(True, self.start, self.env, calls)
        self.assertEqual(code, 0)
        self.assertEqual(report["totalTravelMinutes"], 28)
        self.assertEqual(report["transitWaitingAndTransferMinutes"], 3)
        self.assertEqual(report["providerElapsedDeltaMinutes"], 0)
        self.assertEqual(calls.course_params["time"], "0905")
        self.assertEqual(calls.course_params["viaList"], "100:200")
        for place, params in zip((poc.ORIGIN, poc.DESTINATION), calls.geo_params):
            self.assertEqual(
                params,
                {
                    "geoPoint": f"{place['lat']},{place['lng']},wgs84,1000",
                    "gcs": "wgs84",
                    "type": "bus.local",
                    "stationCount": 2,
                },
            )
        self.assertEqual(calls.walk_pairs[0][0], poc.ORIGIN)
        self.assertEqual(calls.walk_pairs[1][1], poc.DESTINATION)
        self.assertEqual(report["calls"], {"geo": 2, "course": 1, "walk": 2})

    def test_single_object_and_list_shapes(self):
        a, b = poc.stop(point("100")), poc.stop(point("200"))
        ready = self.start.replace(minute=5)
        one = course()
        many = copy.deepcopy(one)
        many["ResultSet"]["Course"] = [many["ResultSet"]["Course"]]
        many["ResultSet"]["Course"][0]["Route"]["Line"] = [
            many["ResultSet"]["Course"][0]["Route"]["Line"]
        ]
        self.assertEqual(
            poc.validate_course(one, a, b, ready),
            poc.validate_course(many, a, b, ready),
        )

    def test_bus_leg_ids_coordinates_timetable_and_chain_fail_closed(self):
        def route(data):
            return data["ResultSet"]["Course"]["Route"]

        corruptions = [
            lambda d: route(d)["Point"][0]["Station"].update(code="999"),
            lambda d: route(d)["Point"][0]["GeoPoint"].update(lati_d="35"),
            lambda d: route(d)["Line"].update(Type="train"),
            lambda d: route(d)["Line"].update(Type="plane"),
            lambda d: route(d)["Line"].update(TimeReliability="average"),
            lambda d: route(d)["Line"].update(DepartureState={}),
            lambda d: route(d)["Line"]["DepartureState"].update(
                Datetime="2026-11-14T08:58:00+09:00"
            ),
            lambda d: route(d).update(Point=[point("100")]),
            lambda d: route(d).update(timeOnBoard="0"),
            lambda d: route(d).update(timeOnBoard="private-key"),
        ]
        for corrupt in corruptions:
            with self.subTest(corrupt=corrupt):
                data = course()
                corrupt(data)
                calls = FakeCalls(data)
                code, report = poc.run(True, self.start, self.env, calls)
                self.assertEqual(code, 2)
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(calls.counts["walk"], 1)

    def test_no_stop_bad_coordinates_and_provider_error_result(self):
        for data in [
            {"ResultSet": {"Point": []}},
            {"ResultSet": {"Error": {"Message": "private-key"}}},
            {},
        ]:
            with self.assertRaises(poc.PocError) as raised:
                poc.first_bus_stop(data)
            self.assertNotIn("private-key", str(raised.exception))
        for field, value in [("gcs", "tokyo"), ("lati_d", "nan"), ("longi_d", "181")]:
            data = point("100")
            data["GeoPoint"][field] = value
            with self.assertRaises(poc.PocError):
                poc.stop(data)

    def test_http_restrictions_budgets_and_no_raw_error(self):
        calls = poc.LiveCalls(*self.env.values())
        with patch.object(poc.requests, "get") as get:
            get.return_value.status_code = 200
            get.return_value.json.return_value = {}
            calls.get("geo", {})
            calls.get("geo", {})
            with self.assertRaises(poc.PocError):
                calls.get("geo", {})
            self.assertEqual(get.call_count, 2)
            self.assertFalse(get.call_args.kwargs["allow_redirects"])
            self.assertEqual(get.call_args.kwargs["timeout"], 15)
            calls.get("course", {"viaList": "100:200"})
            self.assertIn("viaList=100:200", get.call_args.args[0])
            with self.assertRaises(poc.PocError):
                calls.get("course", {})
        calls = poc.LiveCalls(*self.env.values())
        with patch.object(poc.requests, "get") as get:
            get.return_value.status_code = 302
            with self.assertRaises(poc.PocError) as raised:
                calls.get("geo", {})
            self.assertEqual(str(raised.exception), "ekispert_http_302")

    def test_internal_transit_walks_are_included_once(self):
        data = course()
        route = data["ResultSet"]["Course"]["Route"]
        bus = route["Line"]
        walk = {
            "Type": "walk",
            "DepartureState": {"Datetime": "2026-11-14T09:23:00+09:00"},
            "ArrivalState": {"Datetime": "2026-11-14T09:26:00+09:00"},
        }
        route["Point"] = [point("100"), point("150"), point("200")]
        route["Line"] = [bus, walk]
        route["timeWalk"] = "3"
        code, report = poc.run(True, self.start, self.env, FakeCalls(data))
        self.assertEqual(code, 0)
        self.assertEqual(report["totalTravelMinutes"], 31)
        self.assertEqual(report["modes"], ["bus", "walk"])

    def test_departure_minute_timezone_and_midnight_rollover(self):
        self.assertEqual(poc.parse_departure("2026-11-14T09:00:00+09:00"), self.start)
        for value in [
            "2026-11-14T09:00:01+09:00",
            "2026-11-14T09:00:00Z",
            "2026-11-14T09:00",
        ]:
            with self.assertRaises(argparse.ArgumentTypeError):
                poc.parse_departure(value)
        calls = FakeCalls()
        poc.run(True, self.start.replace(hour=23, minute=58), self.env, calls)
        self.assertEqual(calls.course_params["date"], "20261115")
        self.assertEqual(calls.course_params["time"], "0003")

    def test_absent_bus_detail_is_distinct_from_no_bus(self):
        data = point("100")
        data["Station"]["Type"] = "bus"
        with self.assertRaisesRegex(poc.PocError, "bus_detail_missing"):
            poc.first_bus_stop({"ResultSet": {"Point": data}})
        data = course()
        data["ResultSet"]["Course"]["Route"]["Line"]["Type"] = "bus"
        code, report = poc.run(True, self.start, self.env, FakeCalls(data))
        self.assertEqual(code, 2)
        self.assertEqual(report["reason"], "bus_detail_missing")

    def test_google_budget_and_safe_provider_failures(self):
        calls = poc.LiveCalls(*self.env.values())
        with patch.object(
            poc,
            "walking_route",
            return_value=WalkingRoute(1, 20, [poc.ORIGIN, poc.DESTINATION]),
        ) as walk:
            calls.walk(poc.ORIGIN, poc.DESTINATION)
            calls.walk(poc.ORIGIN, poc.DESTINATION)
            with self.assertRaisesRegex(poc.PocError, "request_budget_exceeded"):
                calls.walk(poc.ORIGIN, poc.DESTINATION)
            self.assertEqual(walk.call_count, 2)
        for status in (403, 429):
            calls = poc.LiveCalls(*self.env.values())
            with patch.object(poc.requests, "get") as get:
                get.return_value.status_code = status
                with self.assertRaisesRegex(poc.PocError, f"ekispert_http_{status}"):
                    calls.get("geo", {})
        calls = poc.LiveCalls(*self.env.values())
        with patch.object(
            poc, "walking_route", side_effect=RuntimeError("private-key in URL")
        ):
            with self.assertRaisesRegex(poc.PocError, "^google_walk_error$"):
                calls.walk(poc.ORIGIN, poc.DESTINATION)
