"""TikTok 驱动 —— sau 的命令行里没有 tiktok，这里替它跑。

## 为什么是「独立脚本」而不是被 vp-publish import

vp-publish 刻意保持**零运行时依赖**（要能用系统 python3 直接跑，见 pyproject.toml）。
而 TikTok 需要 playwright，playwright 装在 **sau 那个 venv** 里。
所以这个文件是「交给 sau 的 python 执行」的，不是被 vp-publish import 的：

    <sau venv>/bin/python  <这个文件>  <子命令> ...

vp-publish 侧对应 `sau.find_sau_python()` / `sau.driver_argv()`。
本文件**只依赖标准库 + playwright + sau 自己的模块**，别引 vp_publish 里的东西
（两个解释器不是同一个，引了就是 ImportError）。

## 两个子命令

    login    扫码登录。把二维码从 canvas 上截下来存成 PNG，等手机扫，
             扫上了就把登录态写成 Playwright storage_state。
    upload   调 sau 自己的 `TiktokVideo` 上传（上游写好了，只是没接进 CLI）。

## 为什么登录要自己写（上游明明有 `get_tiktok_cookie`）

因为上游那版用的是 `await page.pause()` —— 它会拉起 **Playwright Inspector**，
必须有图形界面、必须有人手点「继续」。无头环境、远程 SSH、板子上全都没法用。

而 TikTok 的二维码是画在 `<canvas>` 上的（不是 `<img>`，拿不到 src）。
canvas 的元素截图走的是合成器，不受 canvas taint 限制 —— 实测能截、能解，
所以无头登录是**做得到**的，只是上游没这么做。

## 实测确认（2026-09-27）

    · canvas 元素 170×170，`/login/qrcode` 直接就是扫码页（不用先点「使用 QR 碼」）
    · 截出来的图用 opencv 能解回 `https://www.tiktok.com/t/<id>/` —— 是真码
    · 登录态标记：出现 `sessionid`（扫码前只有 msToken/ttwid 等无关 cookie）
    · **码不会自己换**：盯着 canvas 采了 200 秒（40 次），画面一个像素都没变，
      也就是说有效期 ≥200 秒、且没有自动刷新。所以这里**不需要**任何
      「检测过期 → 点刷新按钮」的逻辑（那种逻辑依赖具体 UI，跨语言/改版就废）。
      等 300 秒没扫上就报超时，让用户重开一次，比猜按钮在哪可靠得多。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

# 扫码页 —— 直接就是二维码，不需要先点「使用 QR 碼」
TIKTOK_QR_URL = "https://www.tiktok.com/login/qrcode?lang=en"

# 出现任意一个就算登录上了。实测扫码前一个都没有。
SESSION_COOKIES = ("sessionid", "sessionid_ss", "sid_tt")

# 截图密度：太稀会漏掉换新，太密纯属浪费。2 秒够用。
POLL_SECONDS = 2.0

# canvas 在页面上只有 170×170 CSS 像素。手机对着这个尺寸扫会很难受，
# 所以让浏览器按 3 倍设备像素比渲染 —— 截出来就是 510×510 的真细节
# （不是把小图拉大，是原本就按高分辨率画）。
DEVICE_SCALE = 3


# ── 环境准备 ────────────────────────────────────────────────────
def prepare_sau(sau_root: str, account_file: str) -> None:
    """把 sau 的源码根塞进 sys.path，并保证 cookies 目录存在。

    两件事都是**必须**的，不是防御性编程：

    1. 用 `python <脚本路径>` 跑的时候，sys.path[0] 是**脚本所在目录**，
       不是 cwd。所以 `from uploader.tk_uploader...` 会 ImportError，
       除非手动把 sau 根加进去。

    2. `uploader/tk_uploader/__init__.py` 在 import 时会执行
       `Path(BASE_DIR / "cookies" / "tk_uploader").mkdir(exist_ok=True)` ——
       注意**没有 parents=True**。全新环境里 `cookies/` 还不存在的话，
       这一行会 FileNotFoundError，而且报错位置离真正的原因很远。
    """
    root = os.path.abspath(sau_root)
    if root not in sys.path:
        sys.path.insert(0, root)
    Path(account_file).parent.mkdir(parents=True, exist_ok=True)


def _stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def qr_filename(prefix: str, stamp: str | None = None) -> str:
    """驱动出的二维码叫什么名字。

    **必须**能被 `vp_publish.sau.qr_glob()` 捞到（那个 glob 是
    `{平台}_{账号}*qrcode*.png`）。两边一旦脱钩，症状是
    「扫码页永远显示没有二维码」，而码其实好好地躺在盘上 ——
    这正是 v0.3.2 修过的那个 bug 的形态。所以名字的拼法只此一处，
    并且有单测直接拿 `qr_glob` 去捞它（不是照着同一套假设再写一遍）。
    """
    return f"{prefix}_tk_login_qrcode_{stamp or _stamp()}.png"


def _unique(path: Path) -> Path:
    """同一秒里连写两张时别互相覆盖。"""
    if not path.exists():
        return path
    n = 1
    while True:
        cand = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not cand.exists():
            return cand
        n += 1


async def _logged_in(ctx) -> bool:
    try:
        cookies = await ctx.cookies()
    except Exception:                                     # noqa: BLE001
        return False
    for c in cookies:
        if c.get("name") in SESSION_COOKIES and (c.get("value") or "").strip():
            return True
    return False


# ── 子命令：login ───────────────────────────────────────────────
async def do_login(a: argparse.Namespace) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        print(f"[失败] 这个 python 里没有 playwright：{exc}\n"
              f"       TikTok 驱动必须用 **sau 的 venv** 里的解释器跑。")
        return 4

    qr_dir = Path(a.qr_dir)
    qr_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + a.wait
    last: bytes | None = None
    written: list[Path] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=a.headless)
        ctx = await browser.new_context(
            locale="zh-CN",
            viewport={"width": 1280, "height": 900},
            device_scale_factor=DEVICE_SCALE,
        )
        page = await ctx.new_page()
        try:
            await page.goto(TIKTOK_QR_URL, wait_until="domcontentloaded",
                            timeout=45000)
            canvas = page.locator("canvas").first
            await canvas.wait_for(state="visible", timeout=30000)
        except Exception as exc:                          # noqa: BLE001
            print(f"[失败] 打不开扫码页 / 找不到二维码 canvas：{exc}\n"
                  f"       TikTok 改版了，或者这台机器连不上 tiktok.com。")
            await browser.close()
            return 3

        print(f"[信息] 扫码页已就绪，等 {a.wait}s。二维码出现就存到 {qr_dir}",
              flush=True)

        while time.time() < deadline:
            try:
                blob = await canvas.screenshot()
            except Exception:                             # noqa: BLE001
                blob = None
            # 只在**画面变了**的时候存新文件 —— TikTok 会自己换码，
            # 存成新文件后网页端靠 mtime 变化自动刷新（见 loginweb 的
            # `/api/qr.png?v=<mtime>`），所以这里不用通知任何人。
            if blob and blob != last:
                last = blob
                out = _unique(qr_dir / qr_filename(a.qr_prefix))
                out.write_bytes(blob)
                written.append(out)
                print(f"[二维码] {out}", flush=True)

            if await _logged_in(ctx):
                await ctx.storage_state(path=a.account_file)
                print(f"[成功] 登录态已写入 {a.account_file}", flush=True)
                await browser.close()
                return 0
            await asyncio.sleep(POLL_SECONDS)

        await browser.close()

    tail = f"，最后一张码是 {written[-1]}" if written else "，一张码都没截到"
    print(f"[超时] {a.wait}s 内没等到扫码{tail}")
    return 1


def build_caption(title: str, desc: str) -> str:
    """TikTok 只有一个 caption 输入框，没有独立的「简介」字段。

    desc 跟 title 一样时（vp-publish 没给 desc，就会把 title 当 desc 传下来）
    **不能**再贴一遍 —— 否则 caption 变成两行一模一样的话。
    只有真的不一样才并成两行。
    """
    d = (desc or "").strip()
    if d and d != (title or "").strip():
        return f"{title}\n{d}"
    return title


def patch_empty_chrome_path(mc) -> bool:
    """把 `LOCAL_CHROME_PATH` 的空字符串改成 None。返回「改没改」。

    🔴 上游的坑，实测挖出来的（2026-09-27）：

        conf.py 里 `LOCAL_CHROME_PATH = ""`
        TiktokVideo.upload() 原样传给 `chromium.launch(executable_path=...)`
        playwright 把**空字符串**当成「要执行的程序路径」，于是
            Error: BrowserType.launch: Failed to launch: spawn . ENOENT
        只有 None 才表示「用自带的 chromium」。

    实测对照（同一台机器、同一个 playwright）：

        executable_path=''   -> 失败 spawn . ENOENT
        executable_path=None -> OK

    抽成函数是为了**能被测到** —— 这段逻辑藏在 async 函数里的话，
    只能靠 grep 源码来「确认」，那不叫验证。
    """
    if mc.LOCAL_CHROME_PATH:
        return False
    mc.LOCAL_CHROME_PATH = None
    return True


# ── 子命令：upload ──────────────────────────────────────────────
async def do_upload(a: argparse.Namespace) -> int:
    prepare_sau(a.sau_root, a.account_file)
    try:
        from playwright.async_api import async_playwright
        import uploader.tk_uploader.main_chrome as mc
    except ImportError as exc:
        print(f"[失败] import sau 的 TikTok 上传模块失败：{exc}\n"
              f"       --sau-root 是不是指错地方了？（现在是 {a.sau_root}）")
        return 4

    if patch_empty_chrome_path(mc):
        print("[信息] 已把上游的 LOCAL_CHROME_PATH=\"\" 修正为 None"
              "（空字符串会让 playwright 去执行「.」然后崩）")

    tags = [t.strip().lstrip("#") for t in (a.tags or []) if t.strip()]
    caption = build_caption(a.title, a.desc)

    print(f"[信息] 上传 {a.file}\n"
          f"       caption={caption[:80]!r}  tags={tags}  "
          f"封面={a.thumbnail or '无'}", flush=True)

    app = mc.TiktokVideo(caption, a.file, tags, 0, a.account_file,
                         a.thumbnail or None)
    async with async_playwright() as p:
        await app.upload(p)
    print("[成功] TikTok 上传流程结束")
    return 0


# ── 入口 ────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tk_driver",
        description="TikTok 驱动（由 sau 的 python 执行，不是给用户直接调的）",
    )
    sub = p.add_subparsers(dest="action", required=True)

    lg = sub.add_parser("login", help="扫码登录，写出 storage_state")
    lg.add_argument("--account-file", required=True, help="登录态写到哪（json）")
    lg.add_argument("--qr-dir", required=True, help="二维码存到哪个目录")
    lg.add_argument("--qr-prefix", required=True,
                    help="二维码文件名前缀，形如 tiktok_我的TikTok")
    lg.add_argument("--headless", action="store_true", default=True)
    lg.add_argument("--headed", dest="headless", action="store_false",
                    help="开个窗口看过程（调试用）")
    lg.add_argument("--wait", type=int, default=300, help="等扫码的秒数")

    up = sub.add_parser("upload", help="调 sau 的 TiktokVideo 发视频")
    up.add_argument("--sau-root", required=True, help="sau 源码根（含 uploader/）")
    up.add_argument("--account-file", required=True)
    up.add_argument("--file", required=True, help="视频文件")
    up.add_argument("--title", required=True)
    up.add_argument("--desc", default="")
    up.add_argument("--tags", default="", help="逗号分隔，可带 #")
    up.add_argument("--thumbnail", default="")
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.action == "login":
        return asyncio.run(do_login(a))
    return asyncio.run(do_upload(a))


if __name__ == "__main__":
    sys.exit(main())
