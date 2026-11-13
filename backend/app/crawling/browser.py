import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.errors import ProviderError, ProviderTimeoutError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def browser_page(*, headless: bool = False) -> AsyncIterator[Page]:
    """페이지를 제공하고, 사용이 끝나면 브라우저와 드라이버를 정리한다."""
    playwright = None
    browser = None
    try:
        try:
            playwright = await async_playwright().start()
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
            page = await browser.new_page(
                viewport={"width": 1920, "height": 1080},
                screen={"width": 1920, "height": 1080},
                locale="ko-KR",
                timezone_id="Asia/Seoul",
            )
        except PlaywrightTimeoutError as exc:
            raise ProviderTimeoutError(
                "외부 경로 정보 조회 시간이 초과되었습니다."
            ) from exc
        except PlaywrightError as exc:
            raise ProviderError("외부 경로 정보 조회에 실패했습니다.") from exc

        yield page
    finally:
        try:
            # 정리 실패가 성공 결과나 본문의 원래 오류를 덮어쓰지 않게 한다.
            if browser is not None:
                try:
                    await browser.close()
                except Exception as exc:
                    logger.warning("브라우저 종료 실패: %s", type(exc).__name__)
        finally:
            if playwright is not None:
                try:
                    await playwright.stop()
                except Exception as exc:
                    logger.warning(
                        "Playwright 드라이버 종료 실패: %s", type(exc).__name__
                    )
