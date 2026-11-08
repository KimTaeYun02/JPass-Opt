"""배포 origin 설정과 브라우저 CORS 응답을 외부 호출 없이 검증한다."""

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from http_fixture import contract_env

from app.main import cors_origins, create_app


class CorsConfiguration(unittest.TestCase):
    def setUp(self):
        self.enterContext(contract_env())

    def test_configured_origin_receives_cors_headers_on_preflight_and_post(self):
        origin = "https://frontend.example.com"
        with patch.dict(os.environ, {"CORS_ORIGINS": origin}):
            with TestClient(create_app()) as client:
                preflight = client.options(
                    "/api/stations/resolve",
                    headers={
                        "Origin": origin,
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": "content-type",
                    },
                )
                self.assertEqual(preflight.status_code, 200)
                self.assertEqual(
                    preflight.headers["access-control-allow-origin"], origin
                )
                # Invalid input returns 422 before any provider can be called.
                response = client.post(
                    "/api/stations/resolve", json={}, headers={"Origin": origin}
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.headers["access-control-allow-origin"], origin
                )

    def test_unlisted_origin_receives_no_allow_origin_header(self):
        with TestClient(create_app()) as client:
            for origin in ["https://unlisted.example.com", "http://localhost:5174"]:
                response = client.options(
                    "/api/stations/resolve",
                    headers={
                        "Origin": origin,
                        "Access-Control-Request-Method": "POST",
                    },
                )
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("access-control-allow-origin", response.headers)

    def test_empty_or_malformed_cors_origins_prevent_server_startup(self):
        for value in [
            "",
            " ",
            ",",
            "*",
            "localhost:5173",
            "https://example.com/",
            "https://example.com/api",
            "https://u:private@example.com",
            "https://example.com?private",
            "https://example.com#private",
            "https://example.com:bad",
            "https://example.com:",
            "https://example.com:0",
            "http://bad host",
            "https://example.com,",
            "https://example.com\\private",
        ]:
            with self.subTest(value=value):
                with patch.dict(os.environ, {"CORS_ORIGINS": value}):
                    with self.assertRaisesRegex(RuntimeError, "CORS_ORIGINS") as raised:
                        create_app()
                    self.assertNotIn("private", str(raised.exception))

    def test_defaults_include_localhost_and_loopback_for_dev_and_preview(self):
        with patch.dict(os.environ):
            os.environ.pop("CORS_ORIGINS", None)
            origins = cors_origins()
            for host in ["localhost", "127.0.0.1"]:
                for port in [3000, 5173, 4173]:
                    self.assertIn(f"http://{host}:{port}", origins)

    def test_valid_origins_use_browser_case_port_and_ipv6_serialization(self):
        with patch.dict(
            os.environ,
            {
                "CORS_ORIGINS": " HTTPS://FRONTEND.example.COM:443 , "
                "http://LocalHost:80, http://[0:0:0:0:0:0:0:1]:5173 "
            },
        ):
            self.assertEqual(
                cors_origins(),
                [
                    "https://frontend.example.com",
                    "http://localhost",
                    "http://[::1]:5173",
                ],
            )
            with TestClient(create_app()) as client:
                response = client.options(
                    "/api/stations/resolve",
                    headers={
                        "Origin": "https://frontend.example.com",
                        "Access-Control-Request-Method": "POST",
                    },
                )
                self.assertEqual(
                    response.headers["access-control-allow-origin"],
                    "https://frontend.example.com",
                )

    def test_later_invalid_entry_reports_position_without_echoing_value(self):
        with patch.dict(
            os.environ,
            {
                "CORS_ORIGINS": "https://valid.example.com,https://private.example.com/api"
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "2번째") as raised:
                cors_origins()
            self.assertNotIn("private", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
