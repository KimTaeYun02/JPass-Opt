"""실제 DP·HTTP·도보 어댑터를 검증한다. 공급자 네트워크는 모두 차단한다."""

import asyncio
import copy
import itertools
import json
import os
import shutil
import subprocess
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from http_fixture import contract_env, controlled_api, serve_api
from pydantic import ValidationError

from app.clients.google_routes import WalkingRoute, decode_polyline, walking_route
from app.clients.heartrails import get_station_candidates
from app.errors import (
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from app.main import create_app
from app.optimizer import optimize_visit_order
from app.schemas.route import Leg


def request(count=2, spacing=0.02):
    return {
        "includeAccessWalking": True,
        "startDate": "2026-11-10",
        "dayCount": 1,
        "days": [
            {
                "dayIndex": 1,
                "places": [
                    {
                        "name": f"Place {i}",
                        "googlePlaceId": f"place-{i}",
                        "location": {"lat": 35 + i * spacing, "lng": 139},
                    }
                    for i in range(count)
                ],
            }
        ],
    }


def station(name, lat):
    return {
        "name": name,
        "latitude": lat,
        "longitude": 139,
        "prefecture": "test",
        "line": "rail",
    }


def walk_result(a, b, minutes=3):
    return WalkingRoute(
        minutes,
        minutes * 70,
        [a, {"lat": (a["lat"] + b["lat"]) / 2, "lng": 139.001}, b],
    )


@contextmanager
def controlled(nearest=None, walk=None, crawl=None, key="contract-only-routes"):
    audit = {"walk": [], "crawl": []}

    def candidates(lat, lng, **kwargs):
        return nearest(lat, lng) if nearest else [station(f"S-{lat}", lat + 0.001)]

    def walking(a, b, api_key):
        audit["walk"].append((a, b))
        return walk(a, b) if walk else walk_result(a, b)

    async def transit(a, b, **kwargs):
        audit["crawl"].append((a, b))
        if crawl:
            return crawl(a, b)
        return {
            "routes": [
                {
                    "time": 5,
                    "fee": 100,
                    "route": [
                        {"station": a, "line": "test rail"},
                        {"station": b, "line": None},
                    ],
                }
            ]
        }

    with (
        contract_env(),
        patch.dict(os.environ, {"GOOGLE_ROUTES_API_KEY": key}),
        patch("app.services.access_route_service.get_station_candidates", candidates),
        patch("app.services.access_route_service.walking_route", walking),
        patch("app.services.access_route_service.crawl_route", transit),
        patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("Unexpected provider request"),
        ),
        TestClient(create_app()) as client,
    ):
        yield client, audit


class WalkingClient(unittest.TestCase):
    def test_request_mask_seconds_rounding_path_and_no_route(self):
        data = {
            "routes": [
                {
                    "duration": "60.000000001s",
                    "distanceMeters": 100,
                    "polyline": {"encodedPolyline": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"},
                }
            ]
        }
        with patch("app.clients.google_routes.request_json", return_value=data) as send:
            result = walking_route(
                {"lat": 35, "lng": 139}, {"lat": 36, "lng": 139}, "private-key"
            )
        self.assertEqual(result.minutes, 2)
        self.assertEqual(result.path[0], {"lat": 38.5, "lng": -120.2})
        self.assertEqual(send.call_args.kwargs["json"]["travelMode"], "WALK")
        self.assertIn(
            "routes.polyline.encodedPolyline",
            send.call_args.kwargs["headers"]["X-Goog-FieldMask"],
        )
        with patch("app.clients.google_routes.request_json", return_value={}):
            self.assertIsNone(
                walking_route(
                    {"lat": 35, "lng": 139}, {"lat": 36, "lng": 139}, "private-key"
                )
            )

    def test_bad_polyline_cost_and_provider_failure_never_become_zero(self):
        for line in ["", "~", "?", "\x00", "가", "~" * 50001]:
            with self.subTest(polyline=line[:10]), self.assertRaises(ValueError):
                decode_polyline(line)
        base = {
            "routes": [
                {
                    "duration": "60s",
                    "distanceMeters": 100,
                    "polyline": {"encodedPolyline": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"},
                }
            ]
        }
        for key, value in [
            ("duration", "NaNs"),
            ("duration", "-3s"),
            ("distanceMeters", True),
            ("distanceMeters", -1),
        ]:
            data = copy.deepcopy(base)
            data["routes"][0][key] = value
            with (
                patch("app.clients.google_routes.request_json", return_value=data),
                self.assertRaises(ProviderResponseError),
            ):
                walking_route(
                    {"lat": 35, "lng": 139}, {"lat": 36, "lng": 139}, "secret"
                )
        with (
            patch(
                "app.clients.google_routes.request_json",
                side_effect=ProviderError("auth denied"),
            ),
            self.assertRaises(ProviderError),
        ):
            walking_route({"lat": 35, "lng": 139}, {"lat": 36, "lng": 139}, "secret")

    def test_exact_coincident_coordinates_are_zero_without_provider(self):
        with patch("app.clients.google_routes.request_json") as send:
            result = walking_route({"lat": 35, "lng": 139}, {"lat": 35, "lng": 139}, "")
        self.assertEqual(result.minutes, 0)
        send.assert_not_called()

    def test_duplicate_lines_are_removed_before_candidate_limit(self):
        rows = [station("A", 35)] * 10 + [station("B", 36), station("C", 37)]
        with patch(
            "app.clients.heartrails.get_nearest_stations", return_value=rows
        ) as get:
            self.assertEqual(
                [s["name"] for s in get_station_candidates(35, 139)], ["A", "B", "C"]
            )
        self.assertIsNone(get.call_args.kwargs["limit"])

    def test_actual_station_adapter_keeps_optional_prefecture_key(self):
        raw = [{"name": "A", "y": 35, "x": 139}]
        with patch("app.clients.heartrails._request_stations", return_value=raw):
            candidates = get_station_candidates(35, 139)
        self.assertEqual(candidates[0]["name"], "A")
        self.assertIsNone(candidates[0]["prefecture"])


class AccessRouteContract(unittest.TestCase):
    def test_timeout_during_active_walking_cancels_queued_provider_requests(self):
        started = threading.Event()
        actual_timeout = asyncio.timeout

        def slow_walking(a, b):
            started.set()
            time.sleep(0.2)
            return walk_result(a, b)

        def nearest(lat, lng):
            return [station(f"S-{lat}-{i}", lat + 0.001 * (i + 1)) for i in range(3)]

        with (
            controlled(nearest=nearest, walk=slow_walking) as (client, audit),
            patch(
                "app.services.access_route_service.asyncio.timeout",
                side_effect=lambda _: actual_timeout(0.05),
            ),
        ):
            response = client.post("/api/routes/optimize", json=request())
        self.assertTrue(started.is_set())
        self.assertEqual(response.status_code, 504, response.text)
        self.assertGreater(len(audit["walk"]), 0)
        self.assertLessEqual(len(audit["walk"]), 4)
        self.assertEqual(audit["crawl"], [])

    def test_shared_station_without_direct_walk_keeps_two_positive_access_segments(
        self,
    ):
        def walking(a, b):
            return (
                None
                if round(abs(a["lat"] - b["lat"]), 3) == 0.002
                else walk_result(a, b)
            )

        with controlled(
            nearest=lambda *_: [station("shared", 35.001)], walk=walking
        ) as (client, audit):
            response = client.post("/api/routes/optimize", json=request(spacing=0.002))
        self.assertEqual(response.status_code, 200, response.text)
        leg = response.json()["days"][0]["legs"][0]
        self.assertEqual(leg["durationMinutes"], 6)
        self.assertEqual(leg["route"], [])
        self.assertEqual([s["mode"] for s in leg["segments"]], ["walking", "walking"])
        self.assertEqual(audit["crawl"], [])

    def test_date_overflow_is_a_data_error_instead_of_server_error(self):
        body = request()
        body["startDate"] = "9999-12-31"
        body["days"][0]["dailyStartTime"] = "23:59"
        with controlled() as (client, _):
            response = client.post("/api/routes/optimize", json=body)
        self.assertEqual(response.status_code, 422, response.text)

    def test_unreachable_candidate_is_skipped_when_another_has_walking_routes(self):
        def nearest(lat, lng):
            return [
                station(f"none-{lat}", lat + 0.001),
                station(f"valid-{lat}", lat + 0.003),
            ]

        def walking(a, b):
            return (
                None
                if round(abs(a["lat"] - b["lat"]), 3) == 0.001
                else walk_result(a, b, 2)
            )

        with controlled(nearest=nearest, walk=walking) as (client, audit):
            response = client.post("/api/routes/optimize", json=request())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["totalTravelMinutes"], 9)
        self.assertTrue(
            all(
                a.startswith("valid") and b.startswith("valid")
                for a, b in audit["crawl"]
            )
        )

    def test_legacy_false_matches_omitted_bytes_and_has_no_extension_keys(self):
        with controlled_api() as (app, _), TestClient(app) as client:
            body = request()
            for place, lat in zip(body["days"][0]["places"], [35.68, 35.681]):
                place["location"] = {"lat": lat, "lng": 139.76}
            del body["includeAccessWalking"]
            omitted = client.post("/api/routes/optimize", json=body)
            body["includeAccessWalking"] = False
            explicit = client.post("/api/routes/optimize", json=body)
        self.assertEqual(omitted.status_code, 200, omitted.text)
        self.assertEqual(explicit.content, omitted.content)
        day = explicit.json()["days"][0]
        self.assertNotIn("candidateSearchTruncated", day)
        self.assertNotIn("segments", day["legs"][0])
        self.assertEqual(explicit.json()["timingBasis"], "stationTravelOnly")

    def test_coincident_places_zero_walking_reaches_actual_dp(self):
        def walking(a, b):
            return walking_route(a, b, "") if a == b else walk_result(a, b)

        with controlled(walk=walking) as (client, audit):
            response = client.post("/api/routes/optimize", json=request(3, spacing=0))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["totalTravelMinutes"], 0)
        self.assertEqual(len(response.json()["days"][0]["visits"]), 3)
        self.assertEqual(audit["crawl"], [])

    def test_invalid_crawler_endpoints_and_short_route_fail_before_coordinate_lookup(
        self,
    ):
        for stops in [
            [
                {"station": "suffix mismatch", "line": "rail"},
                {"station": "other", "line": None},
            ],
            [{"station": "one point", "line": None}],
        ]:
            with controlled(
                crawl=lambda *_: {"routes": [{"time": 5, "fee": 100, "route": stops}]}
            ) as (client, _):
                response = client.post("/api/routes/optimize", json=request())
            self.assertEqual(response.status_code, 422, response.text)

    def test_response_model_rejects_inconsistent_transit_and_segment_chains(self):
        with controlled() as (client, _):
            response = client.post("/api/routes/optimize", json=request())
        self.assertEqual(response.status_code, 200, response.text)
        leg = response.json()["days"][0]["legs"][0]
        for change in [
            lambda row: row.update(route=[]),
            lambda row: row["segments"][1]["fromPoint"]["location"].update(lat=40),
            lambda row: row["route"][0]["fromStation"]["location"].update(lat=40),
            lambda row: row["segments"][0].update(durationMinutes=99),
            lambda row: row["segments"][0]["fromPoint"].update(kind="station"),
        ]:
            broken = copy.deepcopy(leg)
            change(broken)
            with self.assertRaises(ValidationError):
                Leg.model_validate(broken)

    def test_multiple_days_have_independent_clocks_caches_and_totals(self):
        body = request()
        body["dayCount"] = 2
        second = copy.deepcopy(body["days"][0])
        second["dayIndex"] = 2
        body["days"].append(second)
        with controlled() as (client, audit):
            response = client.post("/api/routes/optimize", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["totalTravelMinutes"], 22)
        self.assertEqual(result["totalTransportFare"], 200)
        self.assertEqual(result["days"][1]["endAt"], "2026-11-11T08:11:00+09:00")
        self.assertEqual(len(audit["walk"]), 8)
        self.assertEqual(len(audit["crawl"]), 4)
        body["days"][0]["places"] = body["days"][0]["places"][:1]
        with controlled(key="") as (client, audit):
            self.assertEqual(
                client.post("/api/routes/optimize", json=body).status_code, 503
            )
            self.assertEqual(audit["walk"], [])

    def test_total_timeout_is_mapped_to_safe_504(self):
        actual_timeout = asyncio.timeout
        with (
            controlled() as (client, _),
            patch(
                "app.services.access_route_service.asyncio.timeout",
                side_effect=lambda _: actual_timeout(0),
            ),
        ):
            response = client.post("/api/routes/optimize", json=request())
        self.assertEqual(response.status_code, 504, response.text)

    @unittest.skipUnless(shutil.which("node"), "Node.js required")
    def test_actual_frontend_walking_adapter_proxy_and_backend(self):
        web = Path(__file__).resolve().parents[2] / "web"
        if not (web / "node_modules/vite").exists():
            self.skipTest("web/npm ci required")
        with controlled() as (client, audit), serve_api(client.app) as origin:
            result = subprocess.run(
                ["node", "tests/helpers/access-proxy-client.mjs", origin],
                cwd=web,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            json.loads(result.stdout.strip().splitlines()[-1])["result"], "PASS"
        )
        self.assertEqual(len(audit["crawl"]), 2)

    def test_real_optimizer_uses_whole_place_cost_and_outputs_consistent_times(self):
        with controlled() as (client, audit):
            response = client.post("/api/routes/optimize", json=request(3))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["timingBasis"], "placeTravelOnly")
        self.assertEqual(body["totalTravelMinutes"], 22)
        day = body["days"][0]
        self.assertEqual(day["endAt"], "2026-11-10T08:22:00+09:00")
        self.assertEqual(len(audit["walk"]), 6)
        self.assertEqual(len(audit["crawl"]), 6)
        for leg in day["legs"]:
            self.assertEqual(
                [s["mode"] for s in leg["segments"]], ["walking", "transit", "walking"]
            )
            self.assertEqual(
                sum(s["durationMinutes"] for s in leg["segments"]),
                leg["durationMinutes"],
            )
            self.assertEqual(leg["route"][0]["line"], "test rail")

    def test_same_station_direct_walking_nonzero_and_asymmetric(self):
        def walking(a, b):
            return walk_result(a, b, 2 if a["lat"] > b["lat"] else 4)

        with controlled(
            nearest=lambda *_: [station("shared", 35.001)], walk=walking
        ) as (client, audit):
            response = client.post("/api/routes/optimize", json=request(spacing=0.002))
        self.assertEqual(response.status_code, 200, response.text)
        day = response.json()["days"][0]
        self.assertEqual(day["totalTravelMinutes"], 2)
        self.assertEqual(day["visits"][0]["googlePlaceId"], "place-1")
        self.assertEqual(day["legs"][0]["route"], [])
        self.assertEqual(len(day["legs"][0]["segments"]), 1)
        self.assertEqual(audit["crawl"], [])
        pairs = {(a["lat"], b["lat"]) for a, b in audit["walk"]}
        self.assertIn((35, 35.002), pairs)
        self.assertIn((35.002, 35), pairs)

    def test_station_choice_uses_real_access_time_in_each_direction(self):
        def nearest(lat, lng):
            return [
                station(f"wrong-{lat}", lat + 0.001),
                station(f"better-{lat}", lat + 0.003),
            ]

        def walking(a, b):
            fraction = round(abs(a["lat"] - b["lat"]), 3)
            return walk_result(a, b, 2 if fraction == 0.003 else 20)

        with controlled(nearest=nearest, walk=walking) as (client, audit):
            response = client.post("/api/routes/optimize", json=request())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(
            all(
                a.startswith("better") and b.startswith("better")
                for a, b in audit["crawl"]
            )
        )
        self.assertEqual(response.json()["totalTravelMinutes"], 9)

    def test_direct_no_route_keeps_valid_transit_and_empty_access_skips_candidate(self):
        def walking(a, b):
            if round(abs(a["lat"] - b["lat"]), 3) == 0.002:
                return None
            return walk_result(a, b)

        with controlled(walk=walking) as (client, _):
            response = client.post("/api/routes/optimize", json=request(spacing=0.002))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["totalTravelMinutes"], 11)

    def test_valid_direct_walk_survives_normal_empty_transit_but_not_provider_errors(
        self,
    ):
        def walking(a, b):
            return walk_result(a, b, 10 if abs(a["lat"] - b["lat"]) > 0.0015 else 3)

        with controlled(walk=walking, crawl=lambda *_: {"routes": []}) as (client, _):
            response = client.post("/api/routes/optimize", json=request(spacing=0.002))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["totalTravelMinutes"], 10)
        self.assertEqual(response.json()["days"][0]["legs"][0]["route"], [])
        with controlled(walk=walking, crawl=lambda *_: {"routes": "broken"}) as (
            client,
            _,
        ):
            response = client.post("/api/routes/optimize", json=request(spacing=0.002))
        self.assertEqual(response.status_code, 502)

    def test_walk_and_crawl_budgets_for_eight_places(self):
        def nearest(lat, lng):
            return [station(f"S-{lat}-{i}", lat + 0.00001 * (i + 1)) for i in range(3)]

        with controlled(nearest=nearest, walk=lambda a, b: walk_result(a, b, 30)) as (
            client,
            audit,
        ):
            response = client.post(
                "/api/routes/optimize", json=request(8, spacing=0.0005)
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertLessEqual(len(audit["walk"]), 60)
        self.assertLessEqual(len(audit["crawl"]), 56)
        self.assertTrue(response.json()["days"][0]["candidateSearchTruncated"])

    def test_configuration_errors_provider_failures_and_invalid_optin_are_safe(self):
        with controlled(key="") as (client, audit):
            response = client.post("/api/routes/optimize", json=request())
            self.assertEqual(response.status_code, 503)
            self.assertEqual(audit["walk"], [])
        for error, status in [
            (ProviderError("private-key"), 502),
            (ProviderRateLimitError("private-key"), 429),
            (ProviderTimeoutError("private-key"), 504),
        ]:

            def failure(a, b):
                raise error

            with controlled(walk=failure) as (client, _):
                response = client.post("/api/routes/optimize", json=request())
            self.assertEqual(response.status_code, status)
            self.assertNotIn("private-key", response.text)
        with controlled() as (client, audit):
            bad = request()
            bad["includeAccessWalking"] = "true"
            self.assertEqual(
                client.post("/api/routes/optimize", json=bad).status_code, 422
            )
            self.assertEqual(audit["walk"], [])

    def test_unreachable_and_same_name_different_stations_fail_explicitly(self):
        with controlled(walk=lambda *_: None) as (client, _):
            self.assertEqual(
                client.post("/api/routes/optimize", json=request()).status_code, 422
            )
        with controlled(nearest=lambda lat, lng: [station("ambiguous", lat)]) as (
            client,
            _,
        ):
            self.assertEqual(
                client.post("/api/routes/optimize", json=request()).status_code, 422
            )

    def test_single_place_without_key_needs_no_walking(self):
        with controlled(key="") as (client, audit):
            response = client.post("/api/routes/optimize", json=request(1))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["totalTravelMinutes"], 0)
        self.assertEqual(audit, {"walk": [], "crawl": []})

    def test_cost_engine_preserves_exact_order_with_generic_segments(self):
        edges, costs = [], {}
        for index, (a, b) in enumerate(itertools.permutations("ABC", 2)):
            minutes = (index * 7 + 3) % 13 + 1
            costs[a, b] = minutes
            edges.append(
                {
                    "from": a,
                    "to": b,
                    "result": {"time": minutes, "fee": 0},
                    "segments": [
                        {
                            "mode": "walking",
                            "durationMinutes": minutes,
                            "fromPoint": {"name": a, "kind": "place"},
                            "toPoint": {"name": b, "kind": "place"},
                        }
                    ],
                }
            )
        expected = min(
            itertools.permutations("ABC"),
            key=lambda order: (
                sum(costs[a, b] for a, b in zip(order, order[1:])),
                order,
            ),
        )
        self.assertEqual(
            optimize_visit_order(list("ABC"), edges)["order"], list(expected)
        )
        edges[0]["segments"][0]["durationMinutes"] += 1
        with self.assertRaises(ValueError):
            optimize_visit_order(list("ABC"), edges)
