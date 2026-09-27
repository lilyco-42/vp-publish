"""体检：发之前先知道「能不能发、能发到哪、哪个快过期了」。

为什么这个功能重要：多平台发布的失败，**大多数不是上传那一刻才发生的**，
而是「那个平台的 cookie 三天前就过期了」。等到跑完 10 分钟流水线才看见
第 6 个平台失败，体验很差。体检就是把这 10 分钟提前到 3 秒。

两个层次：
  · 默认（离线）：只看本地——sau 在不在、浏览器在不在、哪些平台登录了、
    cookie 大概什么时候过期。秒级出结果，不碰网络。
  · `--live`：真的调 `sau <平台> check` 去平台验活。慢（每个平台几十秒），
    但能发现「cookie 文件在、其实已经失效」这种离线看不出来的情况。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import cover, platforms, report, sau
from .config import Config

# 看起来像「登录态」的 cookie 名。用来估算还有多久要重新登录。
# 命中不了就退化成「全部 cookie 里最早过期的那个」。
SESSION_HINTS = (
    "sessionid", "sid_guard", "sid_tt", "uid_tt", "sessionid_ss",
    "session", "sid", "token", "auth", "login", "passport", "ticket",
    "ttwid", "odin_tt", "SID", "SUB", "ssid", "web_session",
)


@dataclass
class PlatformHealth:
    key: str
    label: str
    account: str = ""
    logged_in: bool = False
    expires_at: float | None = None
    days_left: float | None = None
    live_ok: bool | None = None
    live_note: str = ""
    extra_accounts: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)   # 还缺什么（biliup / 真 Chrome）

    @property
    def status(self) -> str:
        # 「缺东西」排在「没登录」前面：登录再多次也发不出去，
        # 而缺什么才是用户真正要动手的那件事。
        if self.missing:
            return report.FAIL
        if not self.logged_in:
            return report.SKIP
        if self.live_ok is False:
            return report.FAIL
        if self.days_left is not None and self.days_left < 0:
            return report.FAIL
        return report.OK


@dataclass
class Environment:
    sau_path: Path | None = None
    sau_problem: str = ""
    ffmpeg: bool = False
    ffprobe: bool = False
    chromium: Path | None = None
    browsers: list[str] = field(default_factory=list)
    proxy_ok: bool | None = None
    proxy: str = ""
    biliup: Path | None = None      # B站 的后端，sau 会从 GitHub 自动下载
    chrome: Path | None = None      # 真 Chrome（YouTube 只认它）
    sau_python: Path | None = None  # sau venv 的解释器（TikTok 驱动要用它跑）
    warnings: list[str] = field(default_factory=list)


# sau 把 biliup 下到 ~/.social-auto-upload/tools/biliup/<系统>-<架构>/biliup。
# 这套路径规则是照着 uploader/bilibili_uploader/runtime.py 抄的 ——
# 抄的原因是我们得在**不启动 sau** 的前提下判断它下没下下来。
def _biliup_platform_key() -> str:
    import platform as _p

    system = (_p.system() or "").strip().lower()
    if system == "darwin":
        system = "macos"
    machine = (_p.machine() or "").strip().lower()
    machine = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    return f"{system}-{machine}"


def find_biliup() -> Path | None:
    import platform as _p

    exe = "biliup.exe" if _p.system().lower() == "windows" else "biliup"
    path = Path.home() / ".social-auto-upload" / "tools" / "biliup" / _biliup_platform_key() / exe
    return path if path.is_file() else None


# 「真 Chrome」的标准落点。sau 用的是 playwright 的 channel="chrome"，
# 它会自己去这些地方找；这里只是提前告诉用户「有没有」。
CHROME_CANDIDATES = (
    "/opt/google/chrome/chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def find_chrome() -> Path | None:
    import os
    import shutil

    for cand in CHROME_CANDIDATES:
        p = Path(cand)
        if p.is_file():
            return p
    local = os.environ.get("LOCALAPPDATA")
    if local:
        p = Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe"
        if p.is_file():
            return p
    for name in ("google-chrome", "google-chrome-stable", "chrome"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def missing_requirements(plat: platforms.Platform, env: Environment) -> list[str]:
    """这个平台还缺什么。返回的是**依赖 key**（biliup / chrome / sau_python），
    给人看的短标签和长解释都在 platforms.REQUIREMENT_* 里 ——
    这样加新依赖只需要动 platforms.py 一处。
    """
    have = {
        platforms.BILIUP: env.biliup is not None,
        platforms.CHROME: env.chrome is not None,
        platforms.SAU_PY: env.sau_python is not None,
    }
    return [req for req in plat.requires if not have.get(req, True)]


def read_cookie_expiry(path: Path) -> tuple[float | None, str]:
    """从 Playwright storage_state 里估出「最早会失效的时间」。

    返回 (时间戳, 依据说明)。拿不到就返回 (None, 原因)。

    为什么是「最早」而不是「最晚」：只要有一个关键 cookie 死了，
    整条登录态基本就废了。用最早的值是**保守估计**——宁可提前提醒你重新登录，
    也不要等你发到一半才发现。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"读不了 cookie 文件：{exc}"

    cookies = data.get("cookies") if isinstance(data, dict) else None
    if not isinstance(cookies, list):
        return None, "不是 Playwright storage_state 格式"

    now = time.time()
    alive: list[tuple[float, str]] = []
    for c in cookies:
        if not isinstance(c, dict):
            continue
        exp = c.get("expires")
        if not isinstance(exp, (int, float)) or exp <= 0:
            continue
        if exp < now:
            continue                       # 已经过期的不管
        alive.append((float(exp), str(c.get("name", ""))))

    if not alive:
        return None, "所有 cookie 都是会话级（关浏览器即失效）或已过期"

    hinted = [x for x in alive if any(h.lower() in x[1].lower() for h in SESSION_HINTS)]
    pool = hinted or alive
    exp, name = min(pool)
    scope = "登录态 cookie" if hinted else "全部 cookie"
    return exp, f"{scope}「{name}」最早过期"


def inspect_environment(cfg: Config, *, check_proxy: bool = False) -> Environment:
    env = Environment(proxy=cfg.proxy)

    env.sau_path = sau.find_sau(cfg)
    env.sau_python = sau.find_sau_python(cfg)
    if env.sau_path is None and env.sau_python is None:
        env.sau_problem = sau.sau_problem(cfg)

    env.ffmpeg, env.ffprobe = cover.tools_available()
    if not env.ffmpeg:
        env.warnings.append("没装 ffmpeg —— 不会自动生成封面（其他功能不受影响）")

    # 浏览器：sau 用 patchright，部分平台走 playwright，两套都要
    cache = Path.home() / ".cache" / "ms-playwright"
    if cache.is_dir():
        # 只列真的浏览器目录；`.links` 是 playwright 的内部记账目录，不是浏览器
        env.browsers = sorted(
            p.name for p in cache.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )
    if not any(b.startswith("chromium") for b in env.browsers):
        env.warnings.append(
            "没找到 patchright/playwright 的 chromium —— 扫码登录和上传都会失败。"
            f"（找过 {cache}）修：bash bootstrap.sh"
        )

    env.biliup = find_biliup()
    env.chrome = find_chrome()

    if check_proxy and cfg.proxy:
        env.proxy_ok = _probe_proxy(cfg.proxy)
        # 这里**不**加警告 —— 代理只有发 YouTube 才需要。
        # 没装代理的人看到一行红字会去修一个不存在的问题（假阳性比不报还坏）。
        # 真正的警告在 render() 里，等知道 YouTube 有没有登录了再说。
    return env


def _probe_proxy(proxy: str, timeout: float = 5.0) -> bool:
    """探一下代理端口有没有人在听。只看 TCP，不发请求。"""
    import socket
    from urllib.parse import urlparse

    try:
        parsed = urlparse(proxy if "://" in proxy else f"http://{proxy}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 7890
    except Exception:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def inspect_platforms(cfg: Config, env: Environment, *,
                      live: bool = False, only: list[str] | None = None,
                      quiet: bool = True) -> list[PlatformHealth]:
    available = sau.discover_accounts(cfg)
    out: list[PlatformHealth] = []

    for plat in platforms.PLATFORMS:
        if only and plat.key not in only:
            continue
        names = available.get(plat.key) or []
        account = sau.pick_account(cfg, plat.key, available)
        health = PlatformHealth(key=plat.key, label=plat.label, account=account)
        health.missing = missing_requirements(plat, env)

        if names:
            health.logged_in = True
            health.extra_accounts = [n for n in names if n != account]
            cpath = sau.account_file(cfg, plat.key, account)
            if cpath.is_file():
                exp, _why = read_cookie_expiry(cpath)
                if exp is not None:
                    health.expires_at = exp
                    health.days_left = (exp - time.time()) / 86400

        if live and health.logged_in and env.sau_path:
            res = sau.check(env.sau_path, plat.key, account, cfg)
            health.live_ok = res.ok
            if not res.ok:
                health.live_note = res.reason or res.tail or f"退出码 {res.code}"
        out.append(health)
    return out


def render(cfg: Config, env: Environment, healths: list[PlatformHealth],
           *, live: bool = False) -> str:
    lines: list[str] = []

    # 代理只有发 YouTube 才需要 —— 没登录 YouTube 时它就不是个问题
    yt = next((h for h in healths if h.key == "youtube"), None)
    proxy_needed = bool(yt and yt.logged_in)
    warnings = list(env.warnings)
    if proxy_needed and env.proxy_ok is False:
        warnings.append(
            f"代理 {env.proxy} 连不上 —— YouTube 会失败（其他平台不受影响）"
        )

    # ── 环境 ────────────────────────────────────────────────
    lines.append("环境")
    lines.append("─" * 60)
    if env.sau_path:
        lines.append(f"  ✓ sau            {env.sau_path}")
    else:
        lines.append("  ✗ sau            没找到")
        for ln in env.sau_problem.splitlines():
            lines.append(f"      {ln.strip()}")
    # 解释器单独一行：它不是「sau 的附属品」，TikTok 这类平台只认它。
    # 只报「sau 没找到」会让「venv 在、console script 没装」的机器白挨一顿排查。
    # 标签按**显示宽度**补齐（中文算两格），否则这一列会跟上面几行错开。
    if env.sau_python:
        lines.append(f"  ✓ sau 解释器     {env.sau_python}")
    else:
        lines.append("  · sau 解释器     没找到（TikTok 需要它）")
    lines.append(f"  {'✓' if env.ffmpeg else '·'} ffmpeg          "
                 f"{'可用（自动生成封面）' if env.ffmpeg else '不可用'}")
    if env.browsers:
        lines.append(f"  ✓ 浏览器          {', '.join(env.browsers)}")
    else:
        lines.append("  ✗ 浏览器          没找到 chromium")
    if env.proxy_ok is True:
        lines.append(f"  ✓ 代理            {env.proxy} 可达")
    elif env.proxy_ok is False:
        mark = "✗" if proxy_needed else "·"
        tail = "（YouTube 会失败）" if proxy_needed else "（不影响到国内平台）"
        lines.append(f"  {mark} 代理            {env.proxy} 连不上{tail}")
    lines.append("")

    # ── 平台 ────────────────────────────────────────────────
    rows = []
    for h in healths:
        # 「缺什么」永远排在最前面 —— 它是唯一需要用户动手的事
        bits = [platforms.REQUIREMENT_SHORT.get(k, k) for k in h.missing]
        if not h.logged_in:
            bits.append("还没登录")
        else:
            if h.days_left is not None:
                if h.days_left < 0:
                    bits.append("登录已过期")
                elif h.days_left < 3:
                    bits.append(f"⚠ 只剩 {h.days_left:.1f} 天")
                else:
                    bits.append(f"约 {h.days_left:.0f} 天后过期")
            if h.extra_accounts:
                bits.append(f"另有账号：{', '.join(h.extra_accounts)}")
            if h.live_note:
                bits.append(h.live_note)
        note = "；".join(bits)

        # 体检里的 OK 是「可以发」，不是「已发布」——同一个状态码，两种语境
        label_text = {report.OK: "就绪", report.FAIL: "失效", report.SKIP: "未登录"}
        rows.append(report.line_for(
            h.label, h.account or "—", h.status, 0.0, note,
            text=label_text.get(h.status, "")))

    lines.append("平台" + ("（--live 已实连平台验活）" if live else "（离线判断，加 --live 可实连验活）"))
    lines.append("─" * 60)
    lines.append(report.table(rows, [
        ("platform", "平台", "left"),
        ("account", "账号", "left"),
        ("result", "状态", "left"),
        ("note", "说明", "left"),
    ]))

    # 缺依赖比 cookie 过期更值得单独说清楚：它不是「去登录一下」能解决的
    for req in sorted({r for h in healths for r in h.missing}):
        warnings.append(platforms.REQUIREMENT_LABEL.get(req, req))

    ready = [h for h in healths if h.status == report.OK]
    lines.append("")
    lines.append(f"可以直接发：{len(ready)}/{len(healths)} 个平台"
                 + (f" —— {', '.join(h.label for h in ready)}" if ready else ""))
    if warnings:
        lines.append("")
        for w in warnings:
            lines.append(f"  ⚠ {w}")
    return "\n".join(lines)


def to_dict(cfg: Config, env: Environment, healths: list[PlatformHealth]) -> dict:
    return {
        # 后端判据是「CLI 或解释器」，跟 sau.backend_ok() 一致
        "ok": (env.sau_path is not None or env.sau_python is not None)
              and any(h.status == report.OK for h in healths),
        "env": {
            "sau": str(env.sau_path) if env.sau_path else None,
            "sau_python": str(env.sau_python) if env.sau_python else None,
            "sau_problem": env.sau_problem,
            "ffmpeg": env.ffmpeg,
            "browsers": env.browsers,
            "biliup": str(env.biliup) if env.biliup else None,
            "chrome": str(env.chrome) if env.chrome else None,
            "proxy": env.proxy,
            "proxy_ok": env.proxy_ok,
            "warnings": env.warnings,
        },
        "platforms": [
            {
                "key": h.key,
                "label": h.label,
                "account": h.account,
                "logged_in": h.logged_in,
                "status": h.status,
                "days_left": round(h.days_left, 2) if h.days_left is not None else None,
                "live_ok": h.live_ok,
                "live_note": h.live_note,
                "extra_accounts": h.extra_accounts,
                "requires": list(platforms.BY_KEY[h.key].requires)
                            if h.key in platforms.BY_KEY else [],
                "missing": h.missing,
            }
            for h in healths
        ],
    }
