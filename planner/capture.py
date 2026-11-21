"""One bounded team-crawler search, with no retry or access-control workaround."""

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
import re
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from .adapters.ekispert import AbsoluteTimes, RawSearch
from .adapters.ekispert_dom import DOM_COURSE_SCRIPT, parse_dom_routes
from .adapters.ekispert_times import (DOM_SNAPSHOT_SCRIPT, POINT_SELECTORS, SUMMARY_SELECTOR,
                                      TimeResolutionError, resolve_calendar_times, result_summary)
from .models import instant, iso


def load_team_crawler(backend):
    sys.path.insert(0, str(backend))
    crawler = importlib.import_module("app.crawling.ekispert")
    if hasattr(crawler, "browser_page"):
        return crawler
    browser = importlib.import_module("app.crawling.browser")
    return SimpleNamespace(browser_page=browser.browser_page, search_route=crawler.search_route,
                           extract_route=crawler.extract_route, __file__=crawler.__file__)


async def collect(crawler, *, departure, arrival, prepared_at, output, timeout_seconds=90, headless=True,
                  calendar_names=None, expected_stop_codes=None):
    if type(headless) is not bool:
        raise ValueError("headless는 bool이어야 합니다.")
    if not 0 < timeout_seconds <= 300:
        raise ValueError("수집 기한은 최대 300초입니다.")
    if calendar_names and not expected_stop_codes:
        raise ValueError("화면의 정류장 약칭을 쓰려면 양쪽 정류장 코드를 확인해야 합니다.")
    prepared = instant(prepared_at)
    if prepared.microsecond:
        raise ValueError("기존 크롤러는 초 단위 검색만 지원합니다.")
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Reserve a new report before any network; a repeated run cannot overwrite it.
    with target.open("x", encoding="utf-8") as stream:
        stream.write("{}\n")
    started = time.monotonic()
    result = {"schemaVersion": "ekispert-capture/1", "dataSource": "live-capture",
              "capturedAt": iso(datetime.now(timezone.utc)),
              "query": {"departure": departure, "arrival": arrival, "preparedAt": iso(prepared), "options": {}},
              "status": "failed", "stopReason": None, "routeSearchAttempts": 0,
              "stationRequests": 0, "httpRequestsObserved": 0, "documentRequestsObserved": 0,
              "responseStatuses": {}, "payload": None, "absoluteTimes": None,
              "stationCodes": None, "retries": 0, "liveModeEnabled": False}
    result["stage"] = "browser_launch"
    result["headless"] = headless
    result["timeResolution"] = []
    statuses = Counter()
    blocked = None

    def request_seen(request):
        result["httpRequestsObserved"] += 1
        result["documentRequestsObserved"] += request.resource_type == "document"

    def response_seen(response):
        nonlocal blocked
        if urlsplit(response.url).hostname == "roote.ekispert.net":
            statuses[str(response.status)] += 1
            if response.status in (403, 429):
                blocked = f"http_{response.status}"

    async def work():
        async with crawler.browser_page(headless=headless) as page:
            page.on("request", request_seen)
            page.on("response", response_seen)

            async def gate(route):
                if blocked:
                    await route.abort()
                else:
                    await route.continue_()

            await page.route("**/*", gate)
            result["routeSearchAttempts"] = 1
            result["stage"] = "search_route"
            await crawler.search_route(page, departure, arrival, departure_time=prepared)
            if blocked:
                raise RuntimeError("blocked")
            final_url = urlsplit(page.url)
            if final_url.scheme != "https" or final_url.hostname != "roote.ekispert.net" or final_url.path != "/result":
                raise ValueError("unexpected_result_url")
            query = parse_qs(final_url.query, keep_blank_values=True)
            if any(len(values) != 1 for values in query.values()):
                raise ValueError("ambiguous_url_parameters")
            if query.get("dateTime") != [str(int(prepared.timestamp()))] or query.get("searchType") != ["departure"] or query.get("isRealtime") != ["false"]:
                raise ValueError("query_time_not_verified")
            if query.get("depName") != [departure] or query.get("arrName") != [arrival]:
                raise ValueError("query_station_names_not_verified")
            observed_codes = {key: query.get(key, [None])[0] for key in ("depCode", "arrCode")}
            if expected_stop_codes and (observed_codes["depCode"], observed_codes["arrCode"]) != expected_stop_codes:
                raise ValueError("query_station_codes_do_not_match")
            result["stage"] = "extract_route"
            layout_snapshot = None
            try:
                # Existing search_route() waits for the first visible course before
                # returning. Schema dispatch does not issue another page/search.
                if await page.locator('[data-testid="course-detail-header-total-time"]').count() == 0:
                    layout_snapshot = await page.evaluate(DOM_COURSE_SCRIPT)
                    payload = parse_dom_routes(layout_snapshot)
                    result["extractionMethod"] = "public-dom-layout-compat"
                    result["layoutEvidence"] = [{"headerText":course["headerText"],"elements":course["elements"]}
                                                for course in layout_snapshot["courses"]]
                else:
                    payload = RawSearch.model_validate(await crawler.extract_route(page))
                    result["extractionMethod"] = "existing-team-crawler"
            except Exception:
                # Public result fields only; no HTML, cookies or browser storage.
                try:
                    result["extractionDiagnostic"] = await page.locator('[data-testid="course-detail"]').evaluate_all("""sections => sections.slice(0,32).map(section => ({
                        text:section.innerText.slice(0,3000),
                        fields:[...section.querySelectorAll('[data-testid]')].slice(0,160).map(node=>({id:node.getAttribute('data-testid'),text:node.innerText.slice(0,256)}))
                    }))""")
                except Exception:
                    result["extractionDiagnostic"] = None
                raise
            if not payload.routes or any(c.route[0].station != departure or c.route[-1].station != arrival for c in payload.routes):
                raise ValueError("returned_station_names_do_not_match")
            result["payload"] = payload.model_dump(mode="json")
            result["stationCodes"] = observed_codes
            if calendar_names:
                result["calendarAliases"] = list(calendar_names)
            result["providerOptions"] = {key: values[0] for key, values in query.items()
                                         if key not in ("depName", "arrName", "depCode", "arrCode", "dateTime")}
            dates = []
            result["stage"] = "resolve_times"
            fallback = layout_snapshot
            sections = page.locator('[data-testid="course-detail"]')
            if await sections.count() != len(payload.routes):
                raise ValueError("candidate_count_changed")
            for i in range(len(payload.routes)):
                points = sections.nth(i).locator('[data-testid="course-detail-point"]')
                dep = points.first.locator("time[datetime]")
                arr = points.last.locator("time[datetime]")
                dep_count, arr_count = await dep.count(), await arr.count()
                if (dep_count, arr_count) != (0, 0):
                    try:
                        if (dep_count, arr_count) != (1, 1):
                            raise ValueError("ambiguous_explicit_dom_datetime")
                        dated = AbsoluteTimes(departureAt=await dep.get_attribute("datetime"),
                                              arrivalAt=await arr.get_attribute("datetime"), evidence="dom-time-datetime")
                        if instant(dated.departureAt) < prepared or instant(dated.arrivalAt) - prepared > timedelta(days=1):
                            raise ValueError("explicit_times_outside_query_horizon")
                        dates.append(dated.model_dump())
                        result["timeResolution"].append({"candidateIndex": i, "method": dated.evidence, "reason": None})
                    except ValueError as error:
                        dates.append(None)
                        result["timeResolution"].append({"candidateIndex": i, "method": None, "reason": str(error)[:512]})
                    continue
                if fallback is None:
                    fallback = await page.evaluate(DOM_SNAPSHOT_SCRIPT)
                snapshot = None
                try:
                    if len(fallback["courses"]) != len(payload.routes):
                        raise TimeResolutionError("candidate_count_changed")
                    course = fallback["courses"][i]
                    summary_departure, summary_arrival = calendar_names or (departure, arrival)
                    snapshot = {"departureName": summary_departure, "arrivalName": summary_arrival, "preparedAt": iso(prepared),
                                "resultSummary": None, "pointTimes": course["pointTimes"],
                                "displayedDurationMinutes": payload.routes[i].time}
                    if (course["pointCount"] != len(payload.routes[i].route)
                            or course["stationNames"] != [point.station for point in payload.routes[i].route]):
                        raise TimeResolutionError("point_count_changed")
                    snapshot["resultSummary"] = result_summary(fallback["bodyText"], summary_departure, summary_arrival)
                    dated = resolve_calendar_times(snapshot)
                    dates.append(dated.model_dump())
                    result["timeResolution"].append({"candidateIndex": i, "method": dated.evidence, "reason": None,
                                                      "summarySelector": SUMMARY_SELECTOR, "pointSelectors": POINT_SELECTORS})
                except ValueError as error:
                    dates.append(None)
                    if str(error) == "missing_or_ambiguous_result_calendar":
                        result["calendarSummaryCandidates"] = [
                            line.strip()[:2048] for line in fallback["bodyText"].splitlines()
                            if re.search(r"\d{4}年\d{1,2}月\d{1,2}日", line) or "⇒" in line
                        ][:8]
                    result["timeResolution"].append({"candidateIndex": i, "method": None, "reason": str(error)[:512],
                                                      "rawEvidence": snapshot,
                                                      "summarySelector": SUMMARY_SELECTOR, "pointSelectors": POINT_SELECTORS})
            result["absoluteTimes"] = dates
            result["status"] = "captured" if all(dates) else "partial"
            result["stopReason"] = None if all(dates) else "unresolved_absolute_times"
            result["stage"] = "complete"

    try:
        await asyncio.wait_for(work(), timeout=timeout_seconds)
    except asyncio.CancelledError:
        result["stopReason"] = "cancelled"
        raise
    except TimeoutError:
        result["stopReason"] = blocked or "deadline_exceeded"
    except Exception as error:
        result["stopReason"] = blocked or type(error).__name__
        result["errorDetail"] = re.sub(r'https?://[^\s<>"\']+',
                                       lambda match: urlsplit(match[0])._replace(query="",fragment="").geturl(),
                                       str(error))[:2000]
    finally:
        if blocked:
            result.update(status="blocked", stopReason=blocked)
        result["responseStatuses"] = dict(statuses)
        result["elapsedSeconds"] = round(time.monotonic() - started, 3)
        module_path = getattr(crawler, "__file__", None)
        result["crawlerSourceSha256"] = hashlib.sha256(Path(module_path).read_bytes()).hexdigest() if module_path else None
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description="기존 크롤러 1회 수집. 403/429 시 중단; 재시도 없음")
    parser.add_argument("--backend", required=True, help="app/crawling/ekispert.py가 있는 backend 경로")
    parser.add_argument("--departure", required=True)
    parser.add_argument("--arrival", required=True)
    parser.add_argument("--at", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--calendar-names", nargs=2, help="결과 요약에 표시되는 출발·도착 정류장 약칭")
    parser.add_argument("--stop-codes", nargs=2, help="검색 결과에서 일치해야 하는 출발·도착 정류장 코드")
    parser.add_argument("--headed", action="store_true", help="브라우저 창을 표시한다. Linux에서는 가상 디스플레이가 필요할 수 있다.")
    args = parser.parse_args()
    if args.calendar_names and not args.stop_codes:
        parser.error("--calendar-names에는 --stop-codes가 필요합니다.")
    backend = Path(args.backend).resolve()
    if not (backend / "app/crawling/ekispert.py").is_file():
        parser.error("팀 크롤러의 backend 경로가 아닙니다.")
    crawler = load_team_crawler(backend)
    result = asyncio.run(collect(crawler, departure=args.departure, arrival=args.arrival,
                                 prepared_at=args.at, output=args.output, timeout_seconds=args.timeout,
                                 headless=not args.headed, calendar_names=tuple(args.calendar_names) if args.calendar_names else None,
                                 expected_stop_codes=tuple(args.stop_codes) if args.stop_codes else None))
    print(json.dumps({k: result[k] for k in ("status", "stopReason", "routeSearchAttempts", "httpRequestsObserved")}, ensure_ascii=False))
    sys.exit(0 if result["status"] in ("captured", "partial") else 2)


if __name__ == "__main__":
    main()
