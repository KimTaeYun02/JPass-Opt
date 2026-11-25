from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx
from pydantic import ValidationError

from planner.app import create_app
from planner.ledger import Ledger, PlanError
from planner.mobility_data import MobilityData, MobilityFile
from planner.models import PlanRequest
from planner.order_benchmark import prepare_case
from planner.prepare_bus import import_search
from planner.prepare_walks import WalkingPair, collect_walk
from planner.pricing import compare_passes
from planner.service import Planner


SOURCE = {"source": "synthetic-mobility-test", "version": "1"}


def walk(a, b, minutes):
    return {"origin": a, "destination": b,
            "route": {"status": "routed", "minutes": minutes, "evidence": SOURCE}}


def transit(start, end, boarding_at, departure_at, arrival_at, *, station=None, fare=230):
    boarding = {"stopId": "east", "operator": "fixture-bus", "name": "East",
                "location": {"lat": start["lat"] + .001, "lng": start["lng"]}}
    alighting = {"stopId": "west", "operator": "fixture-bus", "name": "West",
                 "location": {"lat": end["lat"] + .001, "lng": end["lng"]}}
    binding = {"origin": start, "destination": end, "boarding": boarding, "alighting": alighting, "station": station}
    search = {"boarding": boarding, "alighting": alighting, "preparedAt": boarding_at, "timetable": "verified",
              "evidence": SOURCE, "trips": [{"departureAt": departure_at, "arrivalAt": arrival_at,
                                            "fareYen": fare, "line": "59"}]}
    return {"source": "fixture", "connections": [binding], "searches": [search],
            "walks": [walk(start, boarding["location"], 2), walk(alighting["location"], end, 2)]}


class MultimodalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.ledger = Ledger(Path(folder.name) / "plans.sqlite3")

    def case(self, n=2):
        raw, provider = prepare_case("scaling", n)
        raw.update(startAt="2026-11-17T09:00:00+09:00", mode="strict")
        raw.pop("endBy", None)
        raw["options"]["allowMultimodal"] = True
        raw["places"][0]["anchorRole"] = "start"
        raw["places"][-1]["anchorRole"] = "end"
        for place in raw["places"]:
            place["stayMinutes"] = 0
            place["openingWindows"] = [{"startAt": "2026-11-17T00:00:00+09:00",
                                        "endAt": "2026-11-19T00:00:00+09:00"}]
            for candidate in provider.data["stations"][place["clientPlaceId"]]:
                candidate["access"]["minutes"] = 4
                candidate["egress"]["minutes"] = 4
        provider.data["edges"] = {}
        provider.data["defaultEdge"] = {"headwayMinutes": 1, "rideMinutes": 20, "fare": 210}
        return raw, provider

    async def plan(self, raw, provider, data):
        request = deepcopy(raw)
        request.update(sessionId=self.ledger.create_session()["sessionId"], operationId="plan")
        mobility = MobilityData(MobilityFile.model_validate(data))
        with patch("socket.socket.connect", side_effect=AssertionError("offline only")):
            return await Planner(self.ledger, provider, mobility=mobility).plan(PlanRequest.model_validate(request), request)

    async def test_short_walk_replaces_rail_and_station_queries(self):
        raw, provider = self.case()
        a, b = raw["places"]
        b["location"] = {**a["location"], "lat": a["location"]["lat"] + .001}
        data = {"source": "fixture", "walks": [walk(a["location"], b["location"], 8)]}
        result = await self.plan(raw, provider, data)
        leg, = result["selected"]["legs"]
        self.assertEqual([s["mode"] for s in leg["steps"]], ["walk"])
        self.assertIsNone(leg["boardStation"])
        self.assertEqual(result["selected"]["elapsedMinutes"], 8)
        self.assertEqual(result["selected"]["fare"]["noPassTotal"], 0)
        self.assertEqual(result["ledger"]["railAttempts"], 0)
        self.assertEqual(result["ledger"]["stationAttempts"], 0)
        self.assertEqual(provider.events, [])

    async def test_detour_and_reverse_do_not_reuse_a_short_walk(self):
        raw, provider = self.case()
        a, b = raw["places"]
        b["location"] = {**a["location"], "lat": a["location"]["lat"] + .0023}
        data = {"source": "fixture", "walks": [walk(a["location"], b["location"], 42),
                                               walk(b["location"], a["location"], 5)]}
        result = await self.plan(raw, provider, data)
        self.assertIn("rail", [s["mode"] for s in result["selected"]["legs"][0]["steps"]])
        self.assertEqual(result["ledger"]["railOnlyAttempts"], 1)

    async def test_direct_bus_reports_wait_midnight_and_unknown_fare(self):
        raw, provider = self.case()
        a, b = raw["places"]
        raw["startAt"] = "2026-11-17T23:55:00+09:00"
        data = transit(a["location"], b["location"], "2026-11-17T23:57:00+09:00",
                       "2026-11-18T00:01:00+09:00", "2026-11-18T00:08:00+09:00", fare=None)
        result = await self.plan(raw, provider, data)
        leg = result["selected"]["legs"][0]
        self.assertEqual([s["mode"] for s in leg["steps"]], ["walk", "wait", "bus", "walk"])
        self.assertEqual(leg["initialWaitMinutes"], 4)
        self.assertEqual(leg["placeArrivalAt"], "2026-11-17T15:10:00Z")
        self.assertIsNone(result["selected"]["fare"]["noPassTotal"])
        self.assertEqual((result["ledger"]["railOnlyAttempts"], result["ledger"]["busAttempts"]), (1, 1))

    async def test_exact_search_miss_and_verified_no_service_are_distinct(self):
        raw, provider = self.case()
        a, b = raw["places"]
        data = transit(a["location"], b["location"], "2026-11-17T10:02:00+09:00",
                       "2026-11-17T10:04:00+09:00", "2026-11-17T10:12:00+09:00")
        result = await self.plan(raw, provider, data)
        self.assertEqual(result["selected"]["legs"][0]["busStatus"], "unknown")
        self.assertIn("travelmode=transit", result["selected"]["legs"][0]["transitLink"])
        data["searches"][0].update(preparedAt="2026-11-17T09:02:00+09:00", timetable="unknown")
        result = await self.plan(raw, provider, data)
        self.assertEqual(result["selected"]["legs"][0]["busStatus"], "unknown")
        self.assertNotIn("bus", [s["mode"] for s in result["selected"]["legs"][0]["steps"]])
        data["searches"][0].update(timetable="verified", trips=[])
        result = await self.plan(raw, provider, data)
        self.assertEqual(result["selected"]["legs"][0]["busStatus"], "no_service")
        self.assertEqual(result["ledger"]["busAttempts"], 1)

    async def test_feeder_changes_boarding_station_and_reserves_next_rail(self):
        raw, provider = self.case(3)
        a = raw["places"][0]
        station = deepcopy(provider.data["stations"]["p1"][0]["station"])
        station.update(providerStationId="farther", name="Farther")
        station["location"]["lat"] += .012
        provider.data["edges"]["farther>s2"] = {"headwayMinutes": 1, "rideMinutes": 1, "fare": 100}
        data = transit(a["location"], station["location"], "2026-11-17T09:02:00+09:00",
                       "2026-11-17T09:03:00+09:00", "2026-11-17T09:07:00+09:00", station=station)
        result = await self.plan(raw, provider, data)
        leg = result["selected"]["legs"][0]
        self.assertEqual(leg["boardStation"]["providerStationId"], "farther")
        self.assertEqual([s["mode"] for s in leg["steps"]], ["walk", "wait", "bus", "walk", "wait", "rail", "walk"])
        self.assertIn(("rail", "farther>s2", "2026-11-17T00:09:00Z"), provider.events)
        self.assertEqual((result["ledger"]["railAttempts"], result["ledger"]["busAttempts"]), (4, 1))
        self.assertEqual(result["selected"]["fare"]["noPassTotal"], 540)
        bus_fare = leg["fareUnits"][0]
        self.assertEqual((bus_fare["quotedTotal"], bus_fare["baseFare"], bus_fare["supplement"]), (230, None, None))
        route = result["selected"]
        fare_ids = [u["fareUnitId"] for u in route["fare"]["fareUnits"]]
        offer = {"routeDigest": route["digest"], "passId": "rail-only", "documentVersion": "fixture",
                 "eligibility": "eligible", "coverage": "known", "validity": "valid",
                 "validFrom": "2026-11-17T08:00:00+09:00", "validUntil": "2026-11-17T20:00:00+09:00",
                 "purchasePrice": 200, "reportedTotal": 430, "evidence": SOURCE,
                 "allocations": [{"fareUnitId": fid, "uncoveredFare": 230 if fid == bus_fare["fareUnitId"] else 0,
                                  "requiredSupplement": 0} for fid in fare_ids]}
        comparison = compare_passes([route], [offer])
        self.assertEqual((comparison["recommendation"]["total"], comparison["evaluations"][0]["savings"]), (430, 110))
        offer["allocations"][0]["uncoveredFare"] = 0
        offer["reportedTotal"] = 200
        self.assertEqual(compare_passes([route], [offer])["evaluations"][0]["reason"], "fare_components_unconfirmed")
        raw["budget"] = {"railAttempts": 3}
        limited = await self.plan(raw, provider, data)
        self.assertEqual(limited["completion"], "complete")
        self.assertEqual(limited["ledger"]["busAttempts"], 0)
        self.assertEqual(limited["ledger"]["railOnlyAttempts"], 2)
        raw["budget"] = {"railAttempts": 2}
        limited = await self.plan(raw, provider, data)
        self.assertEqual((limited["completion"], limited["ledger"]["railAttempts"]), ("complete", 2))
        raw["budget"] = {"railAttempts": 1}
        with self.assertRaisesRegex(PlanError, "INVALID_RAIL_BUDGET"):
            await self.plan(raw, provider, data)

    async def test_two_bus_search_limit_leaves_third_verified_connection_unchecked(self):
        raw, provider = self.case(4)
        data = {"source": "fixture", "walks": [], "connections": [], "searches": []}
        points = [p["location"] for p in raw["places"]]
        for index, (a, b) in enumerate(zip(points, points[1:])):
            offset = index * 9
            prepared, departure, arrival = [f"2026-11-17T09:{offset+minute:02d}:00+09:00" for minute in (2, 3, 7)]
            row = transit(a, b, prepared, departure, arrival)
            for stop_key in ("boarding", "alighting"):
                for item in (row["connections"][0], row["searches"][0]):
                    item[stop_key] = {**item[stop_key], "stopId": f"{index}-{stop_key}"}
            for key in ("walks", "connections", "searches"):
                data[key].extend(row[key])
        result = await self.plan(raw, provider, data)
        self.assertEqual(result["completion"], "complete")
        self.assertEqual((result["ledger"]["railAttempts"], result["ledger"]["busAttempts"]), (5, 2))
        self.assertEqual([leg["busStatus"] for leg in result["selected"]["legs"]], ["checked", "checked", "not_checked"])
        self.assertIn("travelmode=transit", result["selected"]["legs"][2]["transitLink"])

    async def test_feeder_can_work_without_walk_to_a_rail_station(self):
        raw, provider = self.case(3)
        origin = raw["places"][0]["location"]
        station = deepcopy(provider.data["stations"]["p1"][0]["station"])
        station.update(providerStationId="bus-only-access", name="Bus connection")
        station["location"]["lat"] += .018
        provider.data["stations"]["p1"] = []
        data = transit(origin, station["location"], "2026-11-17T09:02:00+09:00",
                       "2026-11-17T09:03:00+09:00", "2026-11-17T09:07:00+09:00", station=station)
        result = await self.plan(raw, provider, data)
        self.assertEqual(result["completion"], "complete")
        self.assertEqual(result["selected"]["legs"][0]["boardStation"]["providerStationId"], "bus-only-access")

    async def test_station_choice_can_differ_between_adjacent_legs(self):
        raw, provider = self.case(3)
        primary = provider.data["stations"]["p2"][0]
        other = deepcopy(primary)
        other["station"]["providerStationId"] = "s2-other"
        other["access"]["minutes"] = 1
        other["egress"]["minutes"] = 8
        provider.data["stations"]["p2"].append(other)
        result = await self.plan(raw, provider, {"source": "fixture"})
        first, second = result["selected"]["legs"]
        self.assertEqual(first["alightStation"]["providerStationId"], "s2")
        self.assertEqual(second["boardStation"]["providerStationId"], "s2-other")
        raw["options"]["allowStationAlternates"] = False
        fixed = await self.plan(raw, provider, {"source": "fixture"})
        self.assertEqual(fixed["selected"]["legs"][1]["boardStation"]["providerStationId"], "s2")

    async def test_failed_rail_falls_back_without_retrying_same_pair(self):
        raw, provider = self.case()
        raw["options"]["allowRepair"] = True
        primary = provider.data["stations"]["p1"][0]
        alternate = deepcopy(primary)
        alternate["station"]["providerStationId"] = "other"
        alternate["access"]["minutes"] = 8
        provider.data["stations"]["p1"].append(alternate)
        provider.failures["rail:s1>s2"] = 1
        result = await self.plan(raw, provider, {"source": "fixture"})
        self.assertEqual(result["selected"]["legs"][0]["boardStation"]["providerStationId"], "other")
        self.assertEqual(result["ledger"]["failedAttempts"], 1)
        self.assertEqual(result["ledger"]["railOnlyAttempts"], 2)

    async def test_two_transfers_trigger_alternative_even_if_nearest_walk_is_short(self):
        raw, provider = self.case()
        raw["options"]["allowRepair"] = True
        primary = provider.data["stations"]["p1"][0]
        alternate = deepcopy(primary)
        alternate["station"]["providerStationId"] = "further"
        alternate["access"]["minutes"] = 6
        provider.data["stations"]["p1"].append(alternate)
        provider.data["edges"] = {
            "s1>s2": {"headwayMinutes": 1, "rideMinutes": 20, "fare": 210, "transfers": 2},
            "further>s2": {"headwayMinutes": 1, "rideMinutes": 8, "fare": 200, "transfers": 0},
        }
        result = await self.plan(raw, provider, {"source": "fixture"})
        self.assertEqual(result["selected"]["legs"][0]["boardStation"]["providerStationId"], "further")
        self.assertEqual(result["ledger"]["railOnlyAttempts"], 2)

    async def test_same_opening_selects_cheaper_ride_in_one_search(self):
        raw, provider = self.case()
        raw["places"][1]["openingWindows"][0]["startAt"] = "2026-11-17T10:00:00+09:00"
        provider.data["defaultEdge"] = {"choices": [
            {"headwayMinutes": 1, "rideMinutes": 10, "fare": 500},
            {"headwayMinutes": 1, "rideMinutes": 20, "fare": 100},
        ]}
        result = await self.plan(raw, provider, {"source": "fixture"})
        self.assertEqual(result["selected"]["fare"]["noPassTotal"], 100)
        self.assertEqual(result["ledger"]["railOnlyAttempts"], 1)

    async def test_missing_deadline_is_reported_on_estimated_route(self):
        raw, provider = self.case()
        raw["mode"] = "reference"
        raw["places"][1]["fixedVisitAt"] = "2026-11-17T09:10:00+09:00"
        raw["endBy"] = "2026-11-17T09:15:00+09:00"
        provider.data["stations"]["p1"][0]["access"]["status"] = "estimated"
        result = await self.plan(raw, provider, {"source": "fixture"})
        checks = {c["kind"]: (c["status"], c["reason"]) for c in result["constraints"]}
        self.assertEqual(checks["fixed_visit"], ("unknown", "estimated_miss_fixed_visit"))
        self.assertEqual(checks["end_by"], ("unknown", "estimated_miss_end_by"))

    async def test_source_mismatch_is_explicit(self):
        raw, provider = self.case()
        with self.assertRaisesRegex(PlanError, "MOBILITY_SOURCE_MISMATCH"):
            await self.plan(raw, provider, {"source": "replay"})

    def test_normal_api_accepts_opt_in_only(self):
        raw, provider = self.case()
        app = create_app(ledger=self.ledger, provider=provider)
        with TestClient(app) as client:
            raw["sessionId"] = client.post("/api/v2/sessions").json()["sessionId"]
            raw["operationId"] = "api"
            response = client.post("/api/v2/routes/plan", json=raw)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("steps", response.json()["selected"]["legs"][0])
            raw["operationId"] = "legacy"
            raw["options"].pop("allowMultimodal")
            response = client.post("/api/v2/routes/plan", json=raw)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("steps", response.json()["selected"]["legs"][0])


class MobilityInputTests(unittest.TestCase):
    def test_valhalla_uses_pedestrian_profile_and_rounds_up_seconds(self):
        pair = WalkingPair(origin={"lat": 35.0, "lng": 135.0}, destination={"lat": 35.001, "lng": 135.0})

        def respond(request):
            self.assertEqual(request.url.path, "/route")
            body = json.loads(request.content)
            self.assertEqual(body["costing"], "pedestrian")
            self.assertEqual([p["lat"] for p in body["locations"]], [35, 35.001])
            return httpx.Response(200, json={"trip": {"status": 0, "legs": [{}], "summary": {"time": 121}}})

        with httpx.Client(base_url="http://127.0.0.1:8002", transport=httpx.MockTransport(respond)) as client:
            row = collect_walk(client, pair, "graph-1")
        self.assertEqual(row.route.minutes, 3)
        self.assertEqual(row.route.evidence.version, "graph-1")

    def test_bus_import_requires_full_bound_codes_and_confirmed_line(self):
        draft = transit({"lat": 35.0, "lng": 135.0}, {"lat": 35.1, "lng": 135.0},
                        "2026-11-17T09:02:00+09:00", "2026-11-17T09:03:00+09:00",
                        "2026-11-17T09:07:00+09:00")
        connection = MobilityFile.model_validate(draft).connections[0]
        raw = {
            "schemaVersion": "ekispert-capture/1", "dataSource": "live-capture", "status": "captured",
            "query": {"departure": "East", "arrival": "West", "preparedAt": "2026-11-17T09:02:00+09:00"},
            "stationCodes": {"depCode": "east", "arrCode": "west"},
            "payload": {"routes": [{"time": 4, "fee": 230,
                         "route": [{"station": "East", "line": "59"}, {"station": "West", "line": None}]}]},
            "absoluteTimes": [{"departureAt": "2026-11-17T00:03:00Z",
                               "arrivalAt": "2026-11-17T00:07:00Z", "evidence": "dom-time-datetime"}],
        }
        result = import_search(json.dumps(raw).encode(), connection, {"59"})
        self.assertEqual(result.trips[0].fareYen, 230)
        self.assertEqual(import_search(json.dumps(raw).encode(), connection, {"17"}).timetable, "unknown")
        raw["absoluteTimes"][0]["departureAt"] = "2026-11-17T00:02:00Z"
        self.assertEqual(import_search(json.dumps(raw).encode(), connection, {"59"}).timetable, "unknown")
        raw["stationCodes"]["arrCode"] = "coincident-name-different-stop"
        with self.assertRaises(ValueError):
            import_search(json.dumps(raw).encode(), connection, {"59"})

    def test_bus_trip_later_than_search_horizon_is_rejected(self):
        draft = transit({"lat": 35.0, "lng": 135.0}, {"lat": 35.1, "lng": 135.0},
                        "2026-11-17T09:02:00+09:00", "2026-11-18T10:03:00+09:00",
                        "2026-11-18T10:07:00+09:00")
        with self.assertRaises(ValidationError):
            MobilityFile.model_validate(draft)
