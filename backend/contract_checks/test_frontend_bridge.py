"""실제 프론트 어댑터·Vite 프록시·FastAPI·최적화 엔진을 함께 검증한다."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from http_fixture import controlled_api, serve_api

WEB = Path(__file__).resolve().parents[2] / "web"


class FrontendBridge(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("node") and (WEB / "node_modules/vite").exists(),
        "Node.js 22.20+ and web/npm ci required for cross-stack checks",
    )
    def test_actual_adapter_proxy_and_backend_contracts(self):
        with controlled_api() as (app, audit), serve_api(app) as origin:
            result = subprocess.run(
                ["node", "tests/helpers/backend-proxy-client.mjs", origin],
                cwd=WEB,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(
            set(report["calls"]),
            {
                "/api/places/search",
                "/api/stations/resolve",
                "/api/nearby/recommend",
                "/api/routes/optimize",
            },
        )
        self.assertEqual(audit["blocked"], [])
        self.assertEqual(set(audit["crawl"]), {("A", "B"), ("B", "A")})
        self.assertEqual(
            len(audit["text"]), 2, "ambiguous queries must not call Google"
        )
        self.assertTrue(audit["text"][1]["location_bias"]["rectangle"])
        self.assertEqual(len(audit["nearby"]), 2)
