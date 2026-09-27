import asyncio
import time

from patchright.async_api import async_playwright

PROXY = "http://127.0.0.1:7890"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = await browser.new_page(proxy={"server": PROXY})
        t0 = time.time()
        try:
            resp = await page.goto("https://studio.youtube.com/",
                                   wait_until="domcontentloaded", timeout=60000)
            print("[OK]   YouTube Studio via proxy: HTTP %s  %.1fs  title=%r"
                  % (resp.status if resp else "?", time.time() - t0,
                     (await page.title())[:50]))
        except Exception as exc:
            print("[FAIL] YouTube Studio via proxy: %s: %s (%.1fs)"
                  % (type(exc).__name__, str(exc)[:120], time.time() - t0))
        await browser.close()


asyncio.run(main())
