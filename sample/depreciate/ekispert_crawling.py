import asyncio
import json
import re
import unicodedata

from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        page = await browser.new_page(
            viewport={"width": 1920, "height": 1080},
            screen={"width": 1920, "height": 1080},
            locale="ko-KR",
            timezone_id="Asia/Seoul",
        )

        try:
            response = await page.goto(
                "https://roote.ekispert.net/",
                wait_until="domcontentloaded",
                timeout=60000,
            )

            print("HTTP:", response.status if response else None)

            if not response or response.status != 200:
                raise RuntimeError("페이지 접속 실패")

            inputs = page.locator('input[placeholder="駅名・バス停名を入力"]:visible')

            print("입력창 수:", await inputs.count())

            # 출발역 / 도착역
            stations = ["池袋", "みどり台"]

            for i, station in enumerate(stations):
                field = inputs.nth(i)

                # 입력창 클릭
                await field.click()

                # 기존 입력값 삭제
                await field.press("ControlOrMeta+A")
                await field.press("Backspace")

                # 한 글자씩 직접 입력
                await field.press_sequentially(
                    station,
                    delay=150,
                )

                # 자동완성 로딩 대기
                await page.wait_for_timeout(1500)

                # Enter로 입력 확정
                await page.keyboard.press("Enter")

                await page.wait_for_timeout(1000)

                print(
                    f"{'출발역' if i == 0 else '도착역'}:",
                    await field.input_value(),
                )

                await page.screenshot(
                    path=f"step_{i + 1}.png",
                )

            # 검색 전 스크린샷
            await page.screenshot(
                path="before_search.png",
                full_page=True,
            )

            # 검색 버튼 클릭
            search_button = page.locator(
                'button[data-testid="search-button"]:visible'
            ).first

            print("검색 버튼:", await search_button.inner_text())

            await search_button.click(timeout=15000)

            print("검색 버튼 클릭 완료")

            # 검색 결과 이동 대기
            try:
                await page.wait_for_url(
                    "**/result?**",
                    timeout=30000,
                )
            except Exception:
                print("결과 페이지 이동을 확인하지 못했습니다.")

            await page.wait_for_timeout(2000)

            print("\n===== 검색 결과 =====")
            print("URL:", page.url)
            print("Title:", await page.title())

            await page.screenshot(
                path="after_search.png",
                full_page=True,
            )

            section = page.locator('[data-testid="course-detail"]').first

            await section.wait_for(state="visible")

            # 1. 전체 소요시간
            time_text = await section.locator(
                '[data-testid="course-detail-header-total-time"]'
            ).first.inner_text()

            time_text = unicodedata.normalize("NFKC", time_text)
            time_text = time_text.split("(")[0]

            hours = re.search(r"(\d+)\s*時間", time_text)
            minutes = re.search(r"(\d+)\s*分", time_text)

            total_time = (int(hours.group(1)) * 60 if hours else 0) + (
                int(minutes.group(1)) if minutes else 0
            )

            if not hours and not minutes:
                total_time = None

            # 2. 전체 운임
            fee_text = await section.locator(
                '[data-testid="course-detail-header-one-way-price"]'
            ).first.inner_text()

            fee_text = unicodedata.normalize("NFKC", fee_text)

            fee_match = re.search(r"([\d,]+)\s*円", fee_text)

            total_fee = int(fee_match.group(1).replace(",", "")) if fee_match else None

            # 3. 역 + 이동 노선을 DOM 순서대로 추출
            body = section.locator('[data-testid="course-detail-body"]')

            elements = body.locator(
                '[data-testid="course-detail-point"], '
                '[data-testid="course-detail-line"]'
            )

            route = []
            line_count = 0

            for i in range(await elements.count()):
                element = elements.nth(i)
                element_type = await element.get_attribute("data-testid")

                if element_type == "course-detail-point":
                    station = await element.locator(
                        '[data-testid="course-detail-point-station-name"]'
                    ).first.inner_text()

                    route.append(
                        {
                            "station": station.strip(),
                            "line": None,
                        }
                    )

                elif element_type == "course-detail-line":
                    line_count += 1

                    line_element = element.locator(
                        '[data-testid="course-detail-line-name"]'
                    ).first

                    line = None

                    if await line_element.count():
                        # 첫 번째 p 태그는 노선명
                        name_element = line_element.locator("p").first

                        if await name_element.count():
                            line = (await name_element.inner_text()).strip()
                        else:
                            text = await line_element.inner_text()
                            line = (
                                text.splitlines()[0].strip() if text.strip() else None
                            )

                    # 도보 구간 처리
                    if not line:
                        raw = await element.inner_text()
                        walk_icon = element.locator(
                            '[aria-label*="徒歩"], img[alt*="徒歩"]'
                        )

                        if "徒歩" in raw or await walk_icon.count():
                            line = "도보"

                    # 앞 역에서 다음 역으로 이동하는 노선
                    if route:
                        route[-1]["line"] = line

            # 4. 최종 JSON
            result = {
                "time": total_time,
                "fee": total_fee,
                "route": route,
            }

            print(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    indent=2,
                )
            )

            if line_count != max(0, len(route) - 1):
                print("WARNING: 역과 이동 구간 개수가 일치하지 않습니다.")

            with open("route_result.json", "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

        except Exception as e:
            print("ERROR:", repr(e))

            await page.screenshot(
                path="error.png",
                full_page=True,
            )

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
