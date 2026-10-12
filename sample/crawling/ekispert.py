from datetime import datetime
from pathlib import Path
from typing import TypedDict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from playwright.async_api import Locator, Page, TimeoutError

from .browser import browser_page
from .parsers import parse_duration, parse_fee

EKISPERT_URL = "https://roote.ekispert.net/"
JAPAN_TIMEZONE = ZoneInfo("Asia/Tokyo")


class RoutePoint(TypedDict):
    station: str
    line: str | None


class RouteResult(TypedDict):
    time: int | None
    fee: int | None
    route: list[RoutePoint]


def normalize_departure_time(departure_time: datetime) -> datetime:
    if departure_time.utcoffset() is None:
        raise ValueError("departure_time에는 시간대가 포함된 datetime을 전달해주세요.")
    return departure_time.astimezone(JAPAN_TIMEZONE)


async def ensure_departure_time(page: Page, departure_time: datetime) -> None:
    """결과 URL에 지정한 출발 시각이 반영되지 않았으면 수정해서 다시 연다."""
    parts = urlsplit(page.url)
    if parts.path != "/result":
        raise RuntimeError("결과 URL을 확인하지 못해 출발 시각을 적용할 수 없습니다.")

    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    expected = {
        "dateTime": str(int(departure_time.timestamp())),
        "searchType": "departure",
        "isRealtime": "false",
    }
    if all(query.get(key) == value for key, value in expected.items()):
        return

    query.update(expected)
    url = urlunsplit(parts._replace(query=urlencode(query)))
    print("지정한 출발 시각으로 결과 페이지를 다시 엽니다:", departure_time.isoformat())
    response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if not response or response.status != 200:
        raise RuntimeError("출발 시각을 지정한 결과 페이지 접속 실패")

    applied = dict(parse_qsl(urlsplit(page.url).query, keep_blank_values=True))
    if not all(applied.get(key) == value for key, value in expected.items()):
        raise RuntimeError("결과 URL에 지정한 출발 시각이 적용되지 않았습니다.")


async def take_screenshot(
    page: Page, directory: Path | None, filename: str, *, full_page: bool = False
) -> None:
    if directory is not None:
        await page.screenshot(path=directory / filename, full_page=full_page)


async def enter_station(field: Locator, station: str) -> None:
    await field.click()
    await field.press("ControlOrMeta+A")
    await field.press("Backspace")
    await field.press_sequentially(station, delay=150)
    # 기존 자동완성 입력 동작을 유지한다.
    await field.page.wait_for_timeout(1500)
    await field.press("Enter")
    await field.page.wait_for_timeout(1000)


async def search_route(
    page: Page,
    departure: str,
    arrival: str,
    *,
    departure_time: datetime | None = None,
    screenshot_dir: Path | None = None,
) -> None:
    if departure_time is not None:
        departure_time = normalize_departure_time(departure_time)
        # Date만 고정하고 자동완성·페이지 로딩에 쓰이는 타이머는 계속 실행한다.
        await page.clock.set_fixed_time(departure_time)
        print("검색 출발 시각:", departure_time.isoformat())

    response = await page.goto(
        EKISPERT_URL, wait_until="domcontentloaded", timeout=60000
    )
    print("HTTP:", response.status if response else None)
    if not response or response.status != 200:
        raise RuntimeError("페이지 접속 실패")

    inputs = page.locator('input[placeholder="駅名・バス停名を入力"]:visible')
    await inputs.nth(1).wait_for(state="visible")
    print("입력창 수:", await inputs.count())
    for index, station in enumerate((departure, arrival)):
        field = inputs.nth(index)
        await enter_station(field, station)
        print("출발역:" if index == 0 else "도착역:", await field.input_value())
        await take_screenshot(page, screenshot_dir, f"step_{index + 1}.png")

    await take_screenshot(page, screenshot_dir, "before_search.png", full_page=True)
    search_button = page.locator('button[data-testid="search-button"]:visible').first
    print("검색 버튼:", await search_button.inner_text())
    await search_button.click(timeout=15000)
    print("검색 버튼 클릭 완료")
    try:
        await page.wait_for_url("**/result?**", timeout=30000)
    except TimeoutError:
        print("결과 페이지 이동을 확인하지 못했습니다. 결과 요소를 확인합니다.")

    if departure_time is not None:
        await ensure_departure_time(page, departure_time)

    await page.locator('[data-testid="course-detail"]').first.wait_for(state="visible")
    print("\n===== 검색 결과 =====")
    print("URL:", page.url)
    print("Title:", await page.title())
    await take_screenshot(page, screenshot_dir, "after_search.png", full_page=True)


async def extract_line(element: Locator) -> str | None:
    line_element = element.locator('[data-testid="course-detail-line-name"]').first
    line = None
    if await line_element.count():
        name_element = line_element.locator("p").first
        if await name_element.count():
            line = (await name_element.inner_text()).strip()
        else:
            text = await line_element.inner_text()
            line = text.splitlines()[0].strip() if text.strip() else None

    if not line:
        raw = await element.inner_text()
        walk_icon = element.locator('[aria-label*="徒歩"], img[alt*="徒歩"]')
        if "徒歩" in raw or await walk_icon.count():
            line = "도보"
    return line


async def extract_route(page: Page) -> RouteResult:
    """첫 번째 검색 결과를 기존 time/fee/route 구조로 반환한다."""
    section = page.locator('[data-testid="course-detail"]').first
    await section.wait_for(state="visible")
    time_text = await section.locator(
        '[data-testid="course-detail-header-total-time"]'
    ).first.inner_text()
    fee_text = await section.locator(
        '[data-testid="course-detail-header-one-way-price"]'
    ).first.inner_text()
    elements = section.locator('[data-testid="course-detail-body"]').locator(
        '[data-testid="course-detail-point"], [data-testid="course-detail-line"]'
    )

    route: list[RoutePoint] = []
    line_count = 0
    for index in range(await elements.count()):
        element = elements.nth(index)
        element_type = await element.get_attribute("data-testid")
        if element_type == "course-detail-point":
            station = await element.locator(
                '[data-testid="course-detail-point-station-name"]'
            ).first.inner_text()
            route.append({"station": station.strip(), "line": None})
        elif element_type == "course-detail-line":
            line_count += 1
            line = await extract_line(element)
            if route:
                route[-1]["line"] = line

    if line_count != max(0, len(route) - 1):
        print("WARNING: 역과 이동 구간 개수가 일치하지 않습니다.")
    return {
        "time": parse_duration(time_text),
        "fee": parse_fee(fee_text),
        "route": route,
    }


async def crawl_route(
    departure: str,
    arrival: str,
    *,
    headless: bool = False,
    departure_time: datetime | None = None,
    screenshot_dir: Path | None = None,
) -> RouteResult:
    """지정한 출발 시각으로 검색한다. None이면 사이트의 현재 시각을 사용한다."""
    if departure_time is not None:
        departure_time = normalize_departure_time(departure_time)
    if screenshot_dir is not None:
        screenshot_dir.mkdir(parents=True, exist_ok=True)

    async with browser_page(headless=headless) as page:
        try:
            await search_route(
                page,
                departure,
                arrival,
                departure_time=departure_time,
                screenshot_dir=screenshot_dir,
            )
            return await extract_route(page)
        except Exception:
            if screenshot_dir is not None:
                try:
                    await take_screenshot(
                        page, screenshot_dir, "error.png", full_page=True
                    )
                except Exception as screenshot_error:
                    print("오류 스크린샷 저장 실패:", repr(screenshot_error))
            raise
