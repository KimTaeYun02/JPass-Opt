"""Keyless HTTP-contract checks. External stations, crawler and LLM are stubbed."""

import itertools
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app


class FrontendContract(unittest.TestCase):
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

    def test_validation_and_missing_keys_are_errors_not_fake_success(self):
        with (
            patch.dict(os.environ, {"GOOGLE_PLACE_API_KEY": "", "GEMINI_API_KEY": ""}),
            TestClient(create_app()) as client,
        ):
            self.assertEqual(
                client.post("/api/routes/optimize", json={}).status_code, 422
            )
            self.assertEqual(
                client.post(
                    "/api/places/search", json={"userQuery": "Kyoto"}
                ).status_code,
                503,
            )
            self.assertEqual(
                client.post(
                    "/api/nearby/recommend",
                    json={
                        "travelTypes": ["culture"],
                        "userRequest": "",
                        "stations": [
                            {"name": "test", "location": {"lat": 35, "lng": 139}}
                        ],
                        "placeCount": 3,
                        "radius": 1000,
                    },
                ).status_code,
                503,
            )


if __name__ == "__main__":
    unittest.main()
