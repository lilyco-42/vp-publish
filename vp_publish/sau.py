"""sau 后端：把「一键」翻译成 social-auto-upload 的命令行。

本模块是**唯一**知道 sau 长什么样的地方。sau 换参数名、加平台，
只要改这里；上层编排逻辑一行都不用动。

设计原则：
  1. **能力来自 argparse，不是文档**。sau 的文档和实现对不上是常态
     （见 platforms.py 顶部注释）。这里只传每个平台 argparse 里真实存在的参数。
  2. **参数值走 argv 数组，绝不拼 shell 字符串**。标题里有 `$`、引号、emoji
     都不会出问题——这是最容易埋的坑。
  3. **每个平台单独超时**。一个平台卡死不能拖垮整批。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import platforms
from .config import Config


# ── 定位 sau ────────────────────────────────────────────────────
def find_sau(cfg: Config) -> Path | None:
    """按优先级找 sau 可执行文件。"""
    candidates = [cfg.sau.bin, cfg.sau.root / ".venv" / "bin" / "sau"]
    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            return cand
    found = shutil.which("sau")
    return Path(found) if found else None


def sau_problem(cfg: Config) -> str:
    """sau 不可用时，给人话解释。"""
    return (
        f"没找到 sau（social-auto-upload）。\n"
        f"  找过这些位置：{cfg.sau.bin}、{cfg.sau.root}/.venv/bin/sau、$PATH\n"
        f"  一键装：bash bootstrap.sh\n"
        f"  或者改配置里的 sau.bin：vp-publish init"
    )


# ── 账号发现 ────────────────────────────────────────────────────
_ACCOUNT_RE = re.compile(r"^(?P<platform>[a-z0-9]+)_(?P<account>.+)\.json$")


def discover_accounts(cfg: Config) -> dict[str, list[str]]:
    """扫 cookies 目录，得出「哪个平台登录了哪些账号」。

    sau 的账号文件命名是 `{platform}_{account_name}.json`
    （见 sau_cli.py: resolve_account_file）。平台名不含下划线，
    所以按**第一个**下划线切分是安全的，账号名里带下划线也没事。
    """
    out: dict[str, list[str]] = {}
    cookies = cfg.sau.cookies_dir
    if not cookies.is_dir():
        return out
    for entry in sorted(cookies.glob("*.json")):
        m = _ACCOUNT_RE.match(entry.name)
        if not m:
            continue
        plat = m.group("platform")
        if plat not in platforms.BY_KEY:
            continue
        out.setdefault(plat, []).append(m.group("account"))
    return out


def pick_account(cfg: Config, plat_key: str, available: dict[str, list[str]]) -> str:
    """决定用哪个账号。

    优先级：配置里写死的 > 唯一一个已登录的 > 名字最短的（可预测，不随机）。
    多个账号又没配时会给一条提示，让人知道发生了什么。
    """
    if cfg.sau.accounts.get(plat_key):
        return cfg.sau.accounts[plat_key]
    names = available.get(plat_key) or []
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return sorted(names, key=lambda s: (len(s), s))[0]


def account_file(cfg: Config, plat_key: str, account: str) -> Path:
    return cfg.sau.cookies_dir / f"{plat_key}_{account}.json"


# ── 结果 ────────────────────────────────────────────────────────
@dataclass
class RunResult:
    ok: bool
    code: int = 0
    stdout: str = ""
    stderr: str = ""
    argv: list[str] = field(default_factory=list)
    elapsed: float = 0.0
    reason: str = ""

    @property
    def tail(self) -> str:
        """给人看的最后几行输出。"""
        text = (self.stderr or "").strip() or (self.stdout or "").strip()
        if not text:
            return ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return " / ".join(lines[-3:])[:400]


# ── 构造 argv ───────────────────────────────────────────────────
def build_upload_argv(sau: Path, plat: platforms.Platform, account: str,
                      video: Path, meta, covers: dict[str, Path],
                      cfg: Config, *, headless: bool = True) -> list[str]:
    """按平台能力组装 upload-video 命令。"""
    argv: list[str] = [
        str(sau), plat.key, "upload-video",
        "--account", account,
        "--file", str(video),
        "--title", meta.title,
        "--desc", meta.desc or meta.title,
    ]

    tags = meta.tags or []
    if tags:
        argv += ["--tags", ",".join(tags)]

    # 封面 —— 只在平台真的支持时才传，否则 argparse 直接报错
    if platforms.COVER_DUAL in plat.caps:
        if covers.get("thumbnail_landscape"):
            argv += ["--thumbnail-landscape", str(covers["thumbnail_landscape"])]
        if covers.get("thumbnail_portrait"):
            argv += ["--thumbnail-portrait", str(covers["thumbnail_portrait"])]
    elif platforms.COVER in plat.caps and covers.get("thumbnail"):
        argv += ["--thumbnail", str(covers["thumbnail"])]

    # B站：--tid 是 required=True，不给直接报错
    if platforms.TID in plat.caps:
        argv += ["--tid", str(meta.tid if meta.tid is not None else cfg.tid)]

    # 合集
    if platforms.COLLECTION in plat.caps and meta.collection:
        argv += ["--collection", meta.collection]

    # YouTube 专属
    if platforms.PLAYLIST in plat.caps and meta.playlist:
        argv += ["--playlist", meta.playlist]
    if platforms.VISIBILITY in plat.caps:
        argv += ["--visibility", meta.visibility or cfg.visibility]

    # 定时发布 —— 只有支持的平台才传
    if platforms.SCHEDULE in plat.caps and meta.schedule:
        argv += ["--schedule", meta.schedule]

    argv += ["--headless" if headless else "--headed"]
    return argv


def build_check_argv(sau: Path, plat_key: str, account: str) -> list[str]:
    return [str(sau), plat_key, "check", "--account", account]


def build_login_argv(sau: Path, plat_key: str, account: str,
                     *, headless: bool = True) -> list[str]:
    argv = [str(sau), plat_key, "login", "--account", account]
    argv += ["--headless" if headless else "--headed"]
    return argv


# ── 执行 ────────────────────────────────────────────────────────
def _env(cfg: Config) -> dict[str, str]:
    env = os.environ.copy()
    # 让子进程的输出别被 Python 缓冲住——不然日志是「一口气冒出来」的，
    # 看不出卡在哪一步
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def run(argv: list[str], cfg: Config, *, cwd: Path | None = None,
        timeout: int | None = None) -> RunResult:
    import time
    started = time.time()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd or cfg.sau.root),
            env=_env(cfg),
            capture_output=True,
            text=True,
            timeout=timeout or cfg.timeout,
        )
    except subprocess.TimeoutExpired:
        return RunResult(
            ok=False, code=-9, argv=argv,
            elapsed=time.time() - started,
            reason=f"超时（{timeout or cfg.timeout}s）—— 平台可能卡住了，看日志确认",
        )
    except FileNotFoundError as exc:
        return RunResult(
            ok=False, code=-2, argv=argv,
            elapsed=time.time() - started,
            reason=f"执行失败：{exc}",
        )
    except Exception as exc:                       # pragma: no cover - 环境相关
        return RunResult(
            ok=False, code=-1, argv=argv,
            elapsed=time.time() - started,
            reason=f"异常：{exc}",
        )

    return RunResult(
        ok=proc.returncode == 0,
        code=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        argv=argv,
        elapsed=time.time() - started,
    )


def upload(sau: Path, plat: platforms.Platform, account: str, video: Path,
           meta, covers: dict[str, Path], cfg: Config,
           *, headless: bool = True, dry_run: bool = False) -> RunResult:
    argv = build_upload_argv(sau, plat, account, video, meta, covers, cfg,
                             headless=headless)
    if dry_run:
        return RunResult(ok=True, argv=argv, reason="dry-run（没真发）")
    return run(argv, cfg)


def check(sau: Path, plat_key: str, account: str, cfg: Config,
          *, timeout: int = 180) -> RunResult:
    return run(build_check_argv(sau, plat_key, account), cfg, timeout=timeout)


def login(sau: Path, plat_key: str, account: str, cfg: Config,
          *, headless: bool = True) -> RunResult:
    # 登录要人扫码/输账号，给足时间
    return run(build_login_argv(sau, plat_key, account, headless=headless),
               cfg, timeout=max(cfg.timeout, 900))


def login_qr_candidates(cfg: Config) -> list[Path]:
    """登录过程可能产出的二维码图片位置（sau 会写在 cwd 或 cookies 目录）。"""
    return [
        cfg.sau.root / "qrcode.png",
        Path.cwd() / "qrcode.png",
        cfg.sau.root / "cookies" / "qrcode.png",
        Path("/tmp/qrcode.png"),
    ]


def newest_qr(cfg: Config, since: float) -> Path | None:
    """找出登录期间新生成的二维码图片。"""
    best: tuple[float, Path] | None = None
    for cand in login_qr_candidates(cfg):
        try:
            mtime = cand.stat().st_mtime
        except OSError:
            continue
        if mtime >= since - 2:
            if best is None or mtime > best[0]:
                best = (mtime, cand)
    return best[1] if best else None
