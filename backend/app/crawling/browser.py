from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from playwright.async_api import Page, async_playwright


@asynccontextmanager
async def browser_page(*, headless: bool = False) -> AsyncIterator[Page]:
    """페이지를 제공하고, 사용이 끝나면 브라우저를 종료한다."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-gpu-compositing",
                "--disable-accelerated-2d-canvas",
            ],
        )
        try:
            page = await browser.new_page(
                viewport={"width": 1920, "height": 1080},
                screen={"width": 1920, "height": 1080},
                locale="ko-KR",
                timezone_id="Asia/Seoul",
            )
            yield page
        finally:
            await browser.close()
