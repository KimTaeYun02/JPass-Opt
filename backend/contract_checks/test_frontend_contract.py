"""Keyless HTTP-contract checks. External stations, crawler and LLM are stubbed."""

import itertools
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from http_fixture import contract_env, controlled_api

from app.main import create_app


class FrontendContract(unittest.TestCase):
    def setUp(self):
        self.enterContext(contract_env())

    def test_route_uses_real_optimizer_with_controlled_travel_matrix(self):
        costs = {
            ("A", "B"): (12, 220),
            ("A", "C"): (5, 100),
            ("B", "A"): (9, 180),
            ("B", "C"): (20, 300),
            ("C", "A"): (30, 500),
            ("C", "B"): (3, 90),
        }

        def nearest(lat, lng, **kwargs):
            return [
                {
                    "name": {35: "A", 36: "B", 37: "C"}[lat],
                    "latitude": lat,
                    "longitude": lng,
                    "prefecture": "test",
                }
            ]

        async def crawl(origin, destination, **kwargs):
            minutes, fare = costs[origin, destination]
            return {
                "routes": [
                    {
                        "time": minutes,
                        "fee": fare,
                        "route": [
                            {"station": origin, "line": "test line"},
                            {"station": destination, "line": None},
                        ],
                    }
                ]
            }

        request = {
            "startDate": "2026-11-10",
            "dayCount": 1,
            "days": [
                {
                    "dayIndex": 1,
                    "dailyStartTime": "08:00",
                    "places": [
                        {
                            "name": "same name",
                            "googlePlaceId": f"contract-{name}",
                            "location": {"lat": lat, "lng": 139},
                        }
                        for name, lat in zip("ABC", [35, 36, 37])
                    ],
                }
            ],
        }
        with (
            patch("app.services.route_service.get_nearest_stations", nearest),
            patch("app.services.route_service.crawl_route", crawl),
            TestClient(create_app()) as client,
        ):
            response = client.post("/api/routes/optimize", json=request)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        expected = min(
            itertools.permutations("ABC"),
            key=lambda order: (
                sum(costs[a, b][0] for a, b in zip(order, order[1:])),
                sum(costs[a, b][1] for a, b in zip(order, order[1:])),
                order,
            ),
        )
        day = result["days"][0]
        self.assertEqual(
            [visit["googlePlaceId"] for visit in day["visits"]],
            [f"contract-{name}" for name in expected],
        )
        self.assertEqual(result["timingBasis"], "stationTravelOnly")
        self.assertEqual(result["totalTravelMinutes"], 8)
        self.assertEqual(result["totalTransportFare"], 190)
        self.assertEqual(
            day["visits"][-1]["estimatedArrivalAt"], "2026-11-10T08:08:00+09:00"
        )
        self.assertEqual(len(day["legs"]), 2)
        self.assertEqual(day["legs"][0]["route"][0]["line"], "test line")

    def test_validation_rejects_invalid_input_before_provider_calls(self):
        with controlled_api() as (app, audit), TestClient(app) as client:
            self.assertEqual(
                client.post("/api/routes/optimize", json={}).status_code, 422
            )
            self.assertEqual(
                client.post("/api/places/search", json={}).status_code,
                422,
            )
            self.assertEqual(
                client.post(
                    "/api/nearby/recommend",
                    json={},
                ).status_code,
                422,
            )
        self.assertTrue(all(not calls for calls in audit.values()))

    def test_missing_required_setting_prevents_server_startup(self):
        settings = ("GOOGLE_PLACE_API_KEY", "GEMINI_API_KEY", "GEMINI_MODEL")
        for setting in settings:
            for value in (None, "", " "):
                with (
                    self.subTest(setting=setting, value=value),
                    patch.dict(os.environ),
                    patch("app.main.load_dotenv"),
                ):
                    os.environ.pop(setting, None)
                    if value is not None:
                        os.environ[setting] = value
                    with self.assertRaisesRegex(RuntimeError, setting) as raised:
                        with TestClient(create_app()):
                            self.fail("The server must reject missing configuration")
                    self.assertNotIn("contract-only-not-a-key", str(raised.exception))
        with patch.dict(os.environ, clear=True), patch("app.main.load_dotenv"):
            with self.assertRaises(RuntimeError) as raised:
                with TestClient(create_app()):
                    self.fail("The server must reject missing configuration")
            for setting in settings:
                self.assertIn(setting, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
