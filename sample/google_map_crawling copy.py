import asyncio

from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        page = await browser.new_page(
            viewport={"width": 1440, "height": 900},
            locale="ko-KR",
        )

        try:
            print("Google Maps 접속 중...")

            response = await page.goto(
                "https://www.google.com/maps?hl=kr",
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await page.wait_for_timeout(5000)

            print("HTTP Status:", response.status if response else None)
            print("Title:", await page.title())
            print("URL:", page.url)

            await page.screenshot(
                path="google_maps.png",
                full_page=False,
            )

            print("스크린샷 저장 완료: google_maps.png")

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
