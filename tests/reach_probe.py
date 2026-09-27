import asyncio
import time

from patchright.async_api import async_playwright

TARGETS = [
    ("抖音创作者中心", "https://creator.douyin.com/"),
    ("B站投稿", "https://member.bilibili.com/platform/home"),
    ("小红书创作者", "https://creator.xiaohongshu.com/"),
    ("视频号助手", "https://channels.weixin.qq.com/platform"),
    ("YouTube Studio", "https://studio.youtube.com/"),
]


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        for name, url in TARGETS:
            page = await browser.new_page()
            t0 = time.time()
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                status = resp.status if resp else "?"
                title = (await page.title())[:38]
                print("[OK]   %-14s HTTP %s  %5.1fs  title=%r"
                      % (name, status, time.time() - t0, title))
            except Exception as exc:
                print("[FAIL] %-14s %s: %s  (%.1fs)"
                      % (name, type(exc).__name__, str(exc)[:110], time.time() - t0))
            await page.close()
        await browser.close()


asyncio.run(main())
