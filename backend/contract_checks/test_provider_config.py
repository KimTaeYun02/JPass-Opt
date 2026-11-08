"""모델 선택과 공급자 인증 오류의 공개 HTTP 계약을 검증한다."""

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from google.genai.errors import APIError
from http_fixture import contract_env

from app.main import create_app
from app.schemas.nearby import NearbyRequest
from app.schemas.place_search import PlaceSearchRequest, QueryRewrite
from app.services.nearby_service import recommend_nearby
from app.services.place_search_service import search_places


class ProviderConfigContract(unittest.TestCase):
    def setUp(self):
        self.enterContext(contract_env())

    def test_model_precedence_and_whitespace(self):
        request = NearbyRequest.model_validate(
            {
                "stations": [
                    {"name": "Tokyo", "location": {"lat": 35.68, "lng": 139.76}}
                ],
                "travelTypes": ["culture"],
                "placeCount": 1,
                "radius": 1000,
            }
        )
        recommendation = {
            "station": {"name": "Tokyo", "location": {"lat": 35.68, "lng": 139.76}},
            "radius": 1000,
            "requestedCount": 1,
            "returnedCount": 0,
            "recommendedPlaces": [],
        }
        for override, expected in [
            (None, "base-model"),
            ("", "base-model"),
            (" ", "base-model"),
            (" nearby-model ", "nearby-model"),
        ]:
            with (
                self.subTest(override=override),
                patch.dict(os.environ, {"GEMINI_MODEL": " base-model "}),
            ):
                os.environ.pop("GEMINI_NEARBY_MODEL", None)
                if override is not None:
                    os.environ["GEMINI_NEARBY_MODEL"] = override
                with (
                    patch(
                        "app.services.nearby_service.GeminiWrapper", autospec=True
                    ) as llm,
                    patch(
                        "app.services.nearby_service.select_anchor_station",
                        return_value=request.stations,
                    ),
                    patch(
                        "app.services.nearby_service.recommend_for_station",
                        return_value=recommendation,
                    ),
                ):
                    recommend_nearby(request)
                    self.assertEqual(llm.call_args.kwargs["model_name"], expected)
                with (
                    patch(
                        "app.services.place_search_service.GeminiWrapper", autospec=True
                    ) as llm,
                    patch("app.services.place_search_service.search_text") as google,
                ):
                    llm.return_value.__enter__.return_value.request.return_value = (
                        QueryRewrite(textQueries=None)
                    )
                    search_places(PlaceSearchRequest(userQuery="test"))
                    self.assertEqual(llm.call_args.kwargs["model_name"], "base-model")
                    google.assert_not_called()

    def test_gemini_auth_billing_errors_are_sanitized_502(self):
        for code in (401, 402, 403):
            with (
                self.subTest(code=code),
                patch("app.llm.llm_wrapper.genai.Client", autospec=True) as sdk,
                TestClient(create_app()) as client,
            ):
                sdk.return_value.models.generate_content.side_effect = APIError(
                    code,
                    {"error": {"code": code, "message": "private credential detail"}},
                )
                with self.assertLogs("app.api.places", level="WARNING") as logs:
                    response = client.post(
                        "/api/places/search", json={"userQuery": "Tokyo gardens"}
                    )
                self.assertEqual(response.status_code, 502, response.text)
                self.assertNotIn("private", response.text)
                self.assertNotIn("private", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
