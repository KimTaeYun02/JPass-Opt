"""Opt-in web crawler probe; bus names are hints, never provider mode evidence.

No API keys, paid providers, retries, saved traffic, or product integration.
"""

import argparse
import asyncio
import contextlib
import io
import json
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.crawling.browser import browser_page  # noqa: E402
from app.crawling.ekispert import (  # noqa: E402
    extract_route,
    normalize_departure_time,
    search_route,
)


def normalize_name(value):
    return " ".join(unicodedata.normalize("NFKC", value).split())


async def inspect_topology(page):
    valid = []
    sections = page.locator('[data-testid="course-detail"]')
    for index in range(await sections.count()):
        elements = (
            sections.nth(index)
            .locator('[data-testid="course-detail-body"]')
            .locator(
                '[data-testid="course-detail-point"], [data-testid="course-detail-line"]'
            )
        )
        kinds = [
            await elements.nth(i).get_attribute("data-testid")
            for i in range(await elements.count())
        ]
        valid.append(
            len(kinds) >= 3
            and len(kinds) % 2 == 1
            and all(
                kind == ("course-detail-point" if i % 2 == 0 else "course-detail-line")
                for i, kind in enumerate(kinds)
            )
        )
    return valid


def summarize(data, departure, arrival, topology=()):
    """Compare complete displayed names, including operator suffixes."""
    courses = []
    for index, course in enumerate(data["routes"]):
        points = course["route"]
        lines = [point["line"] for point in points[:-1]]
        endpoints_match = bool(points) and (
            normalize_name(points[0]["station"]) == normalize_name(departure)
            and normalize_name(points[-1]["station"]) == normalize_name(arrival)
        )
        complete = (
            len(points) >= 2
            and all(isinstance(line, str) and line.strip() for line in lines)
            and points[-1]["line"] is None
            and isinstance(course["time"], int)
            and course["time"] > 0
            and isinstance(course["fee"], int)
            and course["fee"] >= 0
        )
        bus_hint = any("バス" in (line or "") for line in lines)
        courses.append(
            {
                "departure": points[0]["station"] if points else None,
                "arrival": points[-1]["station"] if points else None,
                "endpointsMatch": endpoints_match,
                "completeParsedFields": bool(complete),
                "topologyValid": len(topology) == len(data["routes"])
                and bool(topology[index]),
                "pointCount": len(points),
                "minutes": course["time"],
                "fareYen": course["fee"],
                "lines": lines,
                "busNameHint": bus_hint,
            }
        )
    supported = any(
        row["endpointsMatch"]
        and row["completeParsedFields"]
        and row["topologyValid"]
        and row["busNameHint"]
        for row in courses
    )
    return {
        "status": "BUS_NAME_EVIDENCE_ONLY" if supported else "NOT_CONFIRMED",
        "evidence": "heuristic_name_only",
        "providerModeVerified": False,
        "requested": {"departure": departure, "arrival": arrival},
        "courses": courses,
    }


async def probe(departure, arrival, instant, *, headless=True):
    async with browser_page(headless=headless) as page:
        blocked = asyncio.Event()
        blocked_status = None

        def on_response(response):
            nonlocal blocked_status
            if (
                response.status in (403, 429)
                and response.request.resource_type == "document"
                and response.frame == page.main_frame
                and blocked_status is None
            ):
                blocked_status = response.status
                blocked.set()

        page.on("response", on_response)

        async def search():
            await search_route(page, departure, arrival, departure_time=instant)
            data = await extract_route(page)
            return summarize(data, departure, arrival, await inspect_topology(page))

        search_task = asyncio.create_task(search())
        blocked_task = asyncio.create_task(blocked.wait())
        try:
            await asyncio.wait(
                (search_task, blocked_task), return_when=asyncio.FIRST_COMPLETED
            )
            if blocked.is_set():
                return {"status": f"HTTP_{blocked_status}", "attempts": 1}
            return await search_task
        finally:
            for task in (search_task, blocked_task):
                task.cancel()
            await asyncio.gather(search_task, blocked_task, return_exceptions=True)
            page.remove_listener("response", on_response)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--departure-stop")
    parser.add_argument("--arrival-stop")
    parser.add_argument("--departure", help="Timezone-aware ISO 8601 timestamp")
    args = parser.parse_args(argv)
    if not args.live:
        print(json.dumps({"status": "NOT_RUN", "network": False}))
        return 0
    try:
        if not args.departure_stop or not args.arrival_stop:
            raise ValueError
        for name in (args.departure_stop, args.arrival_stop):
            stop, separator, operator = normalize_name(name).partition("/")
            if not separator or not stop.strip() or not operator.strip():
                raise ValueError
        instant = normalize_departure_time(datetime.fromisoformat(args.departure))
    except (TypeError, ValueError):
        print(json.dumps({"status": "INVALID_INPUT", "network": False}))
        return 1

    # The reused crawler prints URLs and raw diagnostics; do not surface them.
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            report = asyncio.run(
                asyncio.wait_for(
                    probe(
                        args.departure_stop,
                        args.arrival_stop,
                        instant,
                        headless=not args.headed,
                    ),
                    timeout=90,
                )
            )
    except (TimeoutError, PlaywrightTimeoutError):
        report = {"status": "TIMEOUT", "attempts": 1}
    except Exception:
        report = {"status": "PROBE_FAILED", "attempts": 1}
    report.update({"headless": not args.headed, "departureAt": instant.isoformat()})
    # Keep JSON portable to Windows terminals using legacy encodings.
    print(json.dumps(report))
    return 0 if report["status"] == "BUS_NAME_EVIDENCE_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
