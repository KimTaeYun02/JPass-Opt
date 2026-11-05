"""기준역 조회의 HTTP 계약과 실패 격리를 외부 호출 없이 검증한다."""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from requests import RequestException, Response, Timeout

from app.main import create_app
from app.schemas.place_search import QueryRewrite


def place(place_id, lat=35, lng=139):
    return {"googlePlaceId": place_id, "location": {"lat": lat, "lng": lng}}


def nearest(lat, lng, limit=3):
    return [{"name": "same station", "latitude": lat, "longitude": lng, "line": "test"}]


class StationContract(unittest.TestCase):
    def test_openapi_snapshot_matches_registered_app(self):
        snapshot = Path(__file__).resolve().parents[1] / "docs/openapi.json"
        self.assertEqual(
            json.loads(snapshot.read_text(encoding="utf-8")), create_app().openapi()
        )

    def test_maximum_days_and_places_need_no_google_or_gemini_keys(self):
        request = {
            "days": [
                {
                    "dayIndex": day,
                    "places": [
                        place(f"place-{index}", 35 + day / 100 + index / 1000)
                        for index in range(8)
                    ],
                }
                for day in range(1, 8)
            ]
        }
        with (
            patch.dict(os.environ, {"GOOGLE_PLACE_API_KEY": "", "GEMINI_API_KEY": ""}),
            patch(
                "app.services.route_service.get_nearest_stations",
                autospec=True,
                side_effect=nearest,
            ) as lookup,
            TestClient(create_app()) as client,
        ):
            response = client.post("/api/stations/resolve", json=request)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["days"]), 7)
        self.assertTrue(all(len(day["places"]) == 8 for day in response.json()["days"]))
        self.assertEqual(lookup.call_count, 56)

    def test_id_mapping_same_station_and_request_scoped_coordinate_reuse(self):
        request = {
            "days": [
                {"dayIndex": 2, "places": [place("b"), place("a")]},
                {"dayIndex": 1, "places": [place("c", 36), place("d")]},
            ]
        }
        with (
            patch(
                "app.services.route_service.get_nearest_stations",
                autospec=True,
                side_effect=nearest,
            ) as lookup,
            patch("app.services.route_service.crawl_route") as crawler,
            patch("app.clients.google_maps.search_nearby") as google,
            TestClient(create_app()) as client,
        ):
            response = client.post("/api/stations/resolve", json=request)
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual([day["dayIndex"] for day in result["days"]], [2, 1])
            self.assertEqual(
                [item["googlePlaceId"] for item in result["days"][0]["places"]],
                ["b", "a"],
            )
            self.assertEqual(
                result["days"][0]["places"][0]["station"],
                {
                    "name": "same station",
                    "location": {"lat": 35, "lng": 139},
                    "line": "test",
                },
            )
            self.assertEqual(lookup.call_count, 2)
            client.post("/api/stations/resolve", json=request)
            self.assertEqual(
                lookup.call_count, 4, "lookup cache must not persist between requests"
            )
            crawler.assert_not_called()
            google.assert_not_called()

    def test_per_place_failure_retains_success_and_hides_provider_details(self):
        def lookup(lat, lng, limit=3):
            if lat == 36:
                return []
            if lat == 37:
                raise Timeout("private provider detail")
            if lat == 38:
                raise RequestException("private provider detail")
            if lat == 39:
                return [{"name": "bad", "latitude": float("nan"), "longitude": lng}]
            if lat == 40:
                raise RuntimeError("private unexpected client detail")
            return nearest(lat, lng)

        request = {
            "days": [
                {
                    "dayIndex": 1,
                    "places": [place(str(lat), lat) for lat in range(35, 41)],
                }
            ]
        }
        with (
            patch(
                "app.services.route_service.get_nearest_stations",
                autospec=True,
                side_effect=lookup,
            ),
            TestClient(create_app()) as client,
        ):
            response = client.post("/api/stations/resolve", json=request)
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["days"][0]["places"]
        self.assertEqual(
            [item["status"] for item in items],
            ["ok", "not_found", "error", "error", "error", "error"],
        )
        self.assertEqual(
            [item["reason"] for item in items],
            [None, None, "timeout", "unavailable", "invalid_response", "unavailable"],
        )
        self.assertTrue(all(item["station"] is None for item in items[1:]))
        self.assertNotIn("private", response.text)

    def test_real_heartrails_client_mapping_accepts_numeric_and_string_coordinates(
        self,
    ):
        # Exercise the real existing get_nearest_stations; stub only its HTTP transport.
        for raw_lat, raw_lng in [(35.68, 139.76), ("35.68", "139.76")]:
            raw_response = Response()
            raw_response.status_code = 200
            raw_response._content = json.dumps(
                {
                    "response": {
                        "station": [
                            {
                                "name": "Tokyo",
                                "line": "test line",
                                "prefecture": "Tokyo",
                                "x": raw_lng,
                                "y": raw_lat,
                                "distance": "120m",
                            }
                        ]
                    }
                }
            ).encode()
            with (
                self.subTest(raw_lat=raw_lat),
                patch(
                    "app.clients.heartrails.requests.get",
                    autospec=True,
                    return_value=raw_response,
                ) as transport,
                TestClient(create_app()) as client,
            ):
                response = client.post(
                    "/api/stations/resolve",
                    json={"days": [{"dayIndex": 1, "places": [place("a")]}]},
                )
                self.assertEqual(response.status_code, 200, response.text)
                resolved = response.json()["days"][0]["places"][0]
                self.assertEqual(resolved["status"], "ok")
                self.assertEqual(
                    resolved["station"]["location"], {"lat": 35.68, "lng": 139.76}
                )
                self.assertEqual(
                    transport.call_args.kwargs["params"],
                    {"method": "getStations", "x": 139, "y": 35},
                )

    def test_text_search_accepts_frontend_rectangle_and_passes_it_to_google_adapter(
        self,
    ):
        bias = {
            "rectangle": {
                "low": {"latitude": 34.99, "longitude": 138.99},
                "high": {"latitude": 35.02, "longitude": 139.01},
            }
        }
        with (
            patch.dict(
                os.environ,
                {
                    "GOOGLE_PLACE_API_KEY": "contract-stub",
                    "GEMINI_API_KEY": "contract-stub",
                },
            ),
            patch("app.services.place_search_service.GeminiWrapper", autospec=True),
            patch(
                "app.services.place_search_service.rewrite_query",
                return_value=QueryRewrite(textQueries=["test query"]),
            ),
            patch(
                "app.services.place_search_service.search_text",
                autospec=True,
                return_value={"places": []},
            ) as search,
            TestClient(create_app()) as client,
        ):
            response = client.post(
                "/api/places/search",
                json={"userQuery": "gardens", "pageSize": 8, "locationBias": bias},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(search.call_args.kwargs["location_bias"], bias)
        self.assertEqual(search.call_args.kwargs["language_code"], "ko")
        self.assertEqual(search.call_args.kwargs["region_code"], "JP")

    def test_invalid_input_is_rejected_before_lookup(self):
        invalid = [
            {},
            {"days": []},
            {"days": [{"dayIndex": 0, "places": [place("a")]}]},
            {"days": [{"dayIndex": True, "places": [place("a")]}]},
            {"days": [{"dayIndex": 8, "places": [place("a")]}]},
            {"days": [{"dayIndex": 1, "places": []}]},
            {"days": [{"dayIndex": 1, "places": [place("a"), place("a")]}]},
            {"days": [{"dayIndex": 1, "places": [place(str(i)) for i in range(9)]}]},
            {"days": [{"dayIndex": 1, "places": [place("a", 91)]}]},
            {"days": [{"dayIndex": 1, "places": [place("a", True)]}]},
            {"days": [{"dayIndex": 1, "places": [place("a", "NaN")]}]},
            {"days": [{"dayIndex": 1, "places": [place(" ")]}]},
            {"days": [{"dayIndex": 1, "places": [place("a")]}] * 2},
            {"days": [{"dayIndex": 1, "places": [place("a")], "extra": True}]},
            {"days": [{"dayIndex": 1, "places": [{**place("a"), "name": "extra"}]}]},
            {"days": [{"dayIndex": 1, "places": [place("a", -91)]}]},
        ]
        with (
            patch("app.services.route_service.get_nearest_stations") as lookup,
            TestClient(create_app()) as client,
        ):
            for payload in invalid:
                with self.subTest(payload=payload):
                    self.assertEqual(
                        client.post("/api/stations/resolve", json=payload).status_code,
                        422,
                    )
            lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
