"""Synthetic DOM tests exercise the unchanged parser, not the live website."""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.async_api import async_playwright

from app.crawling.ekispert import extract_route

BACKEND = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "bus_probe", BACKEND / "scripts/verify_ekispert_bus.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
SYNTHETIC = (
    BACKEND / "contract_checks/fixtures/ekispert/synthetic_bus_course.html"
).read_text(encoding="utf-8")


class BusProbeContract(unittest.TestCase):
    def test_live_report_is_safe_on_windows_cp949_output(self):
        report = {"status": "BUS_NAME_EVIDENCE_ONLY", "name": "金閣寺道／京都市営バス"}
        args = [
            "--live",
            "--departure-stop",
            "A/B",
            "--arrival-stop",
            "C/D",
            "--departure",
            "2026-11-14T13:55:05+09:00",
        ]
        buffer = io.BytesIO()
        output = io.TextIOWrapper(buffer, encoding="cp949")
        try:
            with (
                patch.object(probe, "probe", return_value=report),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(probe.main(args), 0)
            output.flush()
            result = json.loads(buffer.getvalue().decode("cp949"))
            self.assertEqual(result["name"], report["name"])
        finally:
            output.detach()

    def test_default_and_invalid_input_do_not_open_browser(self):
        with patch.object(probe, "probe", side_effect=AssertionError("network")):
            for args, status, code in [
                ([], "NOT_RUN", 0),
                (["--live"], "INVALID_INPUT", 1),
                (
                    [
                        "--live",
                        "--departure-stop",
                        "A/B",
                        "--arrival-stop",
                        "C/D",
                        "--departure",
                        "2026-11-16T09:00:00",
                    ],
                    "INVALID_INPUT",
                    1,
                ),
            ]:
                with (
                    self.subTest(args=args),
                    contextlib.redirect_stdout(io.StringIO()) as out,
                ):
                    self.assertEqual(probe.main(args), code)
                    self.assertEqual(json.loads(out.getvalue())["status"], status)

    def test_name_comparison_keeps_operator(self):
        self.assertEqual(probe.normalize_name("Ａ／Ｂ　Ｃ"), "A/B C")
        data = {
            "routes": [
                {
                    "time": 4,
                    "fee": 230,
                    "route": [
                        {"station": "A/Bus One", "line": "市バス・１"},
                        {"station": "B/Bus One", "line": None},
                    ],
                }
            ]
        }
        self.assertEqual(
            probe.summarize(data, "A/Bus One", "B/Bus One", [True])["status"],
            "BUS_NAME_EVIDENCE_ONLY",
        )
        self.assertEqual(
            probe.summarize(data, "A/Bus Two", "B/Bus One")["status"], "NOT_CONFIRMED"
        )
        self.assertEqual(
            probe.summarize(data, "A/Bus One", "B/Bus One", [False])["status"],
            "NOT_CONFIRMED",
        )
        data["routes"][0]["fee"] = None
        self.assertEqual(
            probe.summarize(data, "A/Bus One", "B/Bus One")["status"], "NOT_CONFIRMED"
        )

    def test_raw_exception_is_not_printed(self):
        args = [
            "--live",
            "--departure-stop",
            "A/B",
            "--arrival-stop",
            "C/D",
            "--departure",
            "2026-11-16T09:00:00+09:00",
        ]
        with (
            patch.object(probe, "probe", side_effect=RuntimeError("private-detail")),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            self.assertEqual(probe.main(args), 1)
        self.assertEqual(json.loads(out.getvalue())["status"], "PROBE_FAILED")
        self.assertNotIn("private-detail", out.getvalue())


class SyntheticParserContract(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.page = await self.browser.new_page()
        await self.page.route("**/*", lambda route: route.abort())

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def parse_synthetic(self, html=SYNTHETIC):
        await self.page.set_content(html)
        return await extract_route(self.page)

    async def test_synthetic_mixed_bus_walking_rail_keeps_order_and_operator(self):
        data = await self.parse_synthetic()
        self.assertEqual(
            data,
            {
                "routes": [
                    {
                        "time": 68,
                        "fee": 797,
                        "route": [
                            {"station": "東大前", "line": "도보"},
                            {
                                "station": "東大農学部前／都営バス",
                                "line": "都営バス・茶５１・秋葉原駅前行",
                            },
                            {"station": "秋葉原駅前／都営バス", "line": "도보"},
                            {
                                "station": "秋葉原",
                                "line": "ＪＲ京浜東北・根岸線・磯子行",
                            },
                            {"station": "新橋", "line": None},
                        ],
                    }
                ]
            },
        )
        report = probe.summarize(data, "東大農学部前／都営バス", "秋葉原駅前／都営バス")
        self.assertEqual(report["status"], "NOT_CONFIRMED")
        self.assertFalse(report["providerModeVerified"])

    async def test_synthetic_unknown_money_and_time_stay_null(self):
        data = await self.parse_synthetic(
            SYNTHETIC.replace("797円", "--").replace("１時間８分", "--")
        )
        self.assertIsNone(data["routes"][0]["fee"])
        self.assertIsNone(data["routes"][0]["time"])

    async def test_synthetic_alternatives_keep_provider_order(self):
        data = await self.parse_synthetic(
            SYNTHETIC + SYNTHETIC.replace("797円", "230円")
        )
        self.assertEqual([row["fee"] for row in data["routes"]], [797, 230])

    async def test_synthetic_known_line_beats_surrounding_walk_note(self):
        data = await self.parse_synthetic(
            SYNTHETIC.replace(
                "<p>都営バス・茶５１・秋葉原駅前行</p>",
                "<p>都営バス・茶５１・秋葉原駅前行</p><small>徒歩で乗換</small>",
            )
        )
        self.assertEqual(
            data["routes"][0]["route"][1]["line"], "都営バス・茶５１・秋葉原駅前行"
        )

    async def test_synthetic_malformed_topology_is_not_accepted(self):
        await self.parse_synthetic()
        self.assertEqual(await probe.inspect_topology(self.page), [True])
        await self.page.locator('[data-testid="course-detail-line"]').first.evaluate(
            "element => element.remove()"
        )
        self.assertEqual(await probe.inspect_topology(self.page), [False])

    async def test_synthetic_http_rejection_stops_one_attempt(self):
        from contextlib import asynccontextmanager
        from datetime import datetime

        @asynccontextmanager
        async def page_context(**kwargs):
            yield self.page

        for status in (403, 429):
            navigations = []

            async def rejected(route):
                navigations.append(route.request.url)
                await route.fulfill(status=status, body="rejected")

            async def navigate(page, *args, **kwargs):
                await page.goto("https://fixture.invalid/")
                await page.wait_for_timeout(5000)

            await self.page.route("https://fixture.invalid/**", rejected)
            with (
                patch.object(probe, "browser_page", page_context),
                patch.object(probe, "search_route", navigate),
            ):
                import asyncio

                result = await asyncio.wait_for(
                    probe.probe(
                        "A/B",
                        "C/D",
                        datetime.fromisoformat("2026-11-16T09:00:00+09:00"),
                    ),
                    timeout=3,
                )
            self.assertEqual(result, {"status": f"HTTP_{status}", "attempts": 1})
            self.assertEqual(len(navigations), 1)
            await self.page.unroute("https://fixture.invalid/**", rejected)

    async def test_synthetic_asset_rejection_is_not_document_rejection(self):
        from contextlib import asynccontextmanager
        from datetime import datetime

        @asynccontextmanager
        async def page_context(**kwargs):
            yield self.page

        async def document(route):
            await route.fulfill(
                status=200,
                content_type="text/html",
                body=SYNTHETIC + '<img src="https://asset.invalid/blocked.png">',
            )

        async def navigate(page, *args, **kwargs):
            await page.goto("https://fixture.invalid/")

        await self.page.route("https://fixture.invalid/**", document)
        for status in (403, 429):

            async def asset(route):
                await route.fulfill(status=status, body="rejected asset")

            await self.page.route("https://asset.invalid/**", asset)
            with (
                patch.object(probe, "browser_page", page_context),
                patch.object(probe, "search_route", navigate),
            ):
                result = await probe.probe(
                    "A/B", "C/D", datetime.fromisoformat("2026-11-16T09:00:00+09:00")
                )
            self.assertEqual(result["status"], "NOT_CONFIRMED")
            self.assertEqual(len(result["courses"]), 1)
            await self.page.unroute("https://asset.invalid/**", asset)

    async def test_synthetic_timeout_cancels_search_and_closes_browser(self):
        import asyncio
        from contextlib import asynccontextmanager
        from datetime import datetime

        actual_context = probe.browser_page
        opened, closed = [], []
        started = asyncio.Event()

        @asynccontextmanager
        async def tracked_context(**kwargs):
            async with actual_context(**kwargs) as page:
                opened.append(page)
                yield page

        async def stalled(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.append("search_cancelled")

        with (
            patch.object(probe, "browser_page", tracked_context),
            patch.object(probe, "search_route", stalled),
        ):
            task = asyncio.create_task(
                probe.probe(
                    "A/B", "C/D", datetime.fromisoformat("2026-11-16T09:00:00+09:00")
                )
            )
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(task, timeout=0.03)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(closed, ["search_cancelled"])
        self.assertTrue(opened[0].is_closed())


if __name__ == "__main__":
    unittest.main()
