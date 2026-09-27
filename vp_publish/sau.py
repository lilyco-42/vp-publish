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

import glob
import os
import re
import shutil
import subprocess
import sys
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


# ── 自带驱动（sau CLI 里没有的平台）──────────────────────────────
# 为什么需要「另一个解释器」：vp-publish 是零依赖的（用系统 python3 就能跑），
# 而 TikTok 需要 playwright —— playwright 只装在 sau 的 venv 里。
# 所以驱动脚本是**由 sau 的 python 执行**的，不是被 import 的。
_PY_NAMES = ("python", "python3", "python.exe")


def find_sau_python(cfg: Config) -> Path | None:
    """找 sau 那个 venv 的解释器。

    三个来源，按可靠程度排：
      1. 配置里写死的 `sau.python`（默认 ~/sau/.venv/bin/python）
      2. **sau 可执行文件旁边**的 python —— 同一个 bin/Scripts 目录，
         这个最可靠：能找到 sau 就一定能找到它旁边的 python
      3. venv 的两个常规位置：POSIX 的 bin/python、Windows 的 Scripts/python.exe

    第 3 条里的 Windows 位置不能省：配置默认值写的是 POSIX 路径
    （bin/python），在 Windows 上永远不存在，只试第 1 条会必然失败。
    """
    cands: list[Path] = []
    if cfg.sau.python:
        cands.append(cfg.sau.python)
    sau_path = find_sau(cfg)
    if sau_path is not None:
        cands += [sau_path.parent / n for n in _PY_NAMES]
    for sub in ("bin", "Scripts"):
        cands += [cfg.sau.root / ".venv" / sub / n for n in _PY_NAMES]
    for cand in cands:
        if cand.is_file():
            return cand
    return None


def backend_ok(cfg: Config) -> bool:
    """有没有可用的后端。

    判据是「有 CLI **或** 有解释器」，不是「有 CLI」——
    TikTok 走自带驱动，它要的是解释器，不是那个可执行文件。
    只认 CLI 的话，一台「venv 在但没装 console script」的机器
    会被整机拒掉，而它其实能发 TikTok。
    """
    return find_sau(cfg) is not None or find_sau_python(cfg) is not None


def driver_script(driver: str) -> Path | None:
    """驱动脚本的路径。按约定推：vp_publish/<driver>_driver.py。

    约定优于配置 —— 加新驱动只要往包里放一个同名文件，
    platforms.py 里写个 driver 值就行，这里一行都不用改。
    """
    if not driver or driver == platforms.DRIVER_CLI:
        return None
    path = Path(__file__).resolve().parent / f"{driver}_driver.py"
    return path if path.is_file() else None


def driver_problem(cfg: Config, plat: platforms.Platform) -> str:
    """驱动平台跑不起来时，说清缺的是哪一样。"""
    bits = [f"{plat.label}（{plat.key}）的活由 vp-publish 自带的驱动脚本干"
            f"（sau 的命令行里没有这个平台）。"]
    if driver_script(plat.driver) is None:
        bits.append(f"✗ 驱动脚本不见了：vp_publish/{plat.driver}_driver.py"
                    f" —— 重装一下 vp-publish（pip install -e .）")
    if find_sau_python(cfg) is None:
        bits.append(f"✗ 没找到 sau 的 venv 解释器（驱动脚本要用它跑，"
                    f"playwright 装在里头）。\n"
                    f"    找过：{cfg.sau.python}、sau 可执行文件旁边、"
                    f"{cfg.sau.root}/.venv/{{bin,Scripts}}/python\n"
                    f"    一键装：bash bootstrap.sh")
    return "\n  ".join(bits)


# 等扫码的秒数。给足 —— 掏手机、开 App、对准屏幕都要时间。
DRIVER_LOGIN_WAIT = 300


def build_driver_login_argv(cfg: Config, plat: platforms.Platform, account: str,
                            *, headless: bool = True) -> list[str] | None:
    """组装「自带驱动」的登录命令。缺东西就返回 None（由调用方解释）。"""
    py = find_sau_python(cfg)
    script = driver_script(plat.driver)
    if py is None or script is None:
        return None
    return [
        str(py), str(script), "login",
        "--account-file", str(account_file(cfg, plat.key, account)),
        "--qr-dir", str(cfg.sau.cookies_dir),
        # 前缀 = `{平台}_{账号}`，跟 sau 自己出码的命名对齐，
        # 这样 qr_glob 那一套（网页端 / 命令行）不用为驱动平台写特例。
        "--qr-prefix", f"{plat.key}_{account}",
        "--wait", str(DRIVER_LOGIN_WAIT),
        "--headless" if headless else "--headed",
    ]


def build_driver_upload_argv(cfg: Config, plat: platforms.Platform, account: str,
                             video: Path, meta, covers: dict[str, Path],
                             *, headless: bool = True) -> list[str] | None:
    py = find_sau_python(cfg)
    script = driver_script(plat.driver)
    if py is None or script is None:
        return None
    argv = [
        str(py), str(script), "upload",
        "--sau-root", str(cfg.sau.root),
        "--account-file", str(account_file(cfg, plat.key, account)),
        "--file", str(video),
        "--title", meta.title,
        "--desc", meta.desc or meta.title,
    ]
    if meta.tags:
        argv += ["--tags", ",".join(meta.tags)]
    if covers.get("thumbnail"):
        argv += ["--thumbnail", str(covers["thumbnail"])]
    return argv


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


def resolve_account(cfg: Config, plat_key: str, *,
                    override: str = "",
                    available: dict[str, list[str]] | None = None) -> str:
    """决定这次用哪个账号名。

    优先级：命令行指定 > 配置里配的 > 已经登录过的那个 > 「我的{平台名}」。
    抽出来是因为命令行登录和网页登录必须用**同一套规则** ——
    否则网页里登录写进 A 文件，命令行去读 B 文件，两边永远对不上。
    """
    avail = available if available is not None else discover_accounts(cfg)
    plat = platforms.BY_KEY.get(plat_key)
    label = plat.label if plat else plat_key
    return (override
            or cfg.sau.accounts.get(plat_key, "")
            or (avail.get(plat_key) or [""])[0]
            or f"我的{label}")


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
    """组装登录命令。

    `--headless/--headed` **不是每个平台都认** —— 实测 bilibili 的 login
    子命令只声明了 `--account`，多给一个 `--headless` 会被 argparse 当场
    打回（`unrecognized arguments: --headless`），连登录流程都进不去。
    所以这里按平台能力决定要不要带这个参数。
    """
    argv = [str(sau), plat_key, "login", "--account", account]
    plat = platforms.BY_KEY.get(plat_key)
    if plat is None or plat.accepts_headless:
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


# 公开别名：login-web 要用同一套环境起子进程，不能各写一份
env_for_subprocess = _env


def run_stream(argv: list[str], cfg: Config, *, cwd: Path | None = None,
               timeout: int | None = None) -> RunResult:
    """跑一条命令，**输出直接透传到用户的终端**。

    为什么登录必须用这个而不是 run()：
    sau 登录时会把二维码用 Unicode 方块字符**打印到 stdout**（见
    sau 的 utils/login_qrcode.py: print_terminal_qrcode）。
    如果这里用 capture_output=True，二维码就被吞进变量里了，
    用户在终端上什么都看不到，只能干等超时——这是实测踩到的坑。

    代价是拿不到输出文本，所以成功与否靠退出码判断。
    """
    import time
    started = time.time()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd or cfg.sau.root),
            env=_env(cfg),
            timeout=timeout or cfg.timeout,
            # 关键：不捕获，让子进程直接用当前终端
            stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
        )
    except subprocess.TimeoutExpired:
        return RunResult(ok=False, code=-9, argv=argv,
                         elapsed=time.time() - started,
                         reason=f"超时（{timeout or cfg.timeout}s）")
    except FileNotFoundError as exc:
        return RunResult(ok=False, code=-2, argv=argv,
                         elapsed=time.time() - started, reason=f"执行失败：{exc}")
    except Exception as exc:                       # pragma: no cover - 环境相关
        return RunResult(ok=False, code=-1, argv=argv,
                         elapsed=time.time() - started, reason=f"异常：{exc}")

    return RunResult(ok=proc.returncode == 0, code=proc.returncode,
                     argv=argv, elapsed=time.time() - started)


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


def upload(sau: Path | None, plat: platforms.Platform, account: str, video: Path,
           meta, covers: dict[str, Path], cfg: Config,
           *, headless: bool = True, dry_run: bool = False) -> RunResult:
    """上传的统一出口。按 `plat.driver` 决定调 sau CLI 还是跑自带驱动。

    上层（publish / watch）只认这一个函数，所以两条路径的
    结果格式、超时、错误处理全都一致。
    """
    if plat.driver != platforms.DRIVER_CLI:
        argv = build_driver_upload_argv(cfg, plat, account, video, meta, covers,
                                        headless=headless)
        if argv is None:
            return RunResult(ok=False, code=-3,
                             reason=driver_problem(cfg, plat).replace("\n", " "))
        if dry_run:
            return RunResult(ok=True, argv=argv, reason="dry-run（没真发）")
        return run(argv, cfg)

    if sau is None:
        return RunResult(ok=False, code=-3,
                         reason=sau_problem(cfg).replace("\n", " "))
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
    # 登录要人扫码/输账号，给足时间；且必须用 run_stream 让二维码透传到终端
    return run_stream(build_login_argv(sau, plat_key, account, headless=headless),
                      cfg, timeout=max(cfg.timeout, 900))


def login_command(cfg: Config, plat_key: str, account: str, *,
                  headless: bool = True,
                  sau_path: Path | None = None) -> tuple[list[str] | None, str]:
    """登录命令的统一出口。返回 `(argv, 问题说明)`；argv 为 None 表示跑不了。

    **命令行和网页都必须走这里** —— 否则会出现「网页里点了没反应、
    命令行却能用」这种最难查的分裂（TikTok 就是这种平台：
    它压根不经过 sau CLI）。
    """
    plat = platforms.BY_KEY.get(plat_key)
    if plat is not None and plat.driver != platforms.DRIVER_CLI:
        argv = build_driver_login_argv(cfg, plat, account, headless=headless)
        if argv is None:
            return None, driver_problem(cfg, plat)
        return argv, ""

    if sau_path is None:
        sau_path = find_sau(cfg)
    if sau_path is None:
        return None, sau_problem(cfg)
    return build_login_argv(sau_path, plat_key, account, headless=headless), ""


def qr_glob(cfg: Config, plat_key: str, account: str) -> str:
    """sau 保存二维码的命名规则 —— **各平台并不统一**。

    实测（2026-09-27，把每个平台的登录真跑一遍）拿到的真实文件名：

        douyin        douyin_我的抖音_login_qrcode_20260927_142027.png
        weibo         weibo_我的微博_login_qrcode_20260927_142255.png
        alipay        alipay_我的支付宝生活号_login_qrcode_20260927_142333.png
        xiaohongshu   xiaohongshu_我的小红书_xhs_login_qrcode_....png   ← 多个 _xhs
        kuaishou      kuaishou_我的快手_ks_login_qrcode_....png         ← 多个 _ks
        hupu          hupu_我的虎扑_qq_qrcode.png                       ← 连时间戳都没有

    规律只有「{platform}_{account} 开头 + 名字里有 qrcode + .png」这一条。
    后缀各写各的，是因为 sau 的 build_login_qrcode_path 支持自定义 suffix
    （抖音/微博/支付宝用默认值，小红书传 xhs_login_qrcode，快手传
    ks_login_qrcode），hupu 干脆自己拼了个 _qq_qrcode。

    所以这里**按前缀匹配**，不硬编后缀。硬编的后果就是小红书/快手/虎扑
    三个平台**静默失效** —— 网页上显示「没有二维码」，其实码就躺在
    cookies 目录里（这是实测抓到的，单测发现不了）。

    注意仍然是**平台严格**的：前缀里带着 platform 和 account，
    捞不到别的平台的图 —— 那才是真正致命的（扫了登不上，且零报错）。
    """
    # 前缀要转义：账号名里如果碰巧有 * ? [ 之类的字符，glob 会当成通配符
    prefix = glob.escape(str(cfg.sau.cookies_dir / f"{plat_key}_{account}"))
    return f"{prefix}*qrcode*.png"


def newest_qr(cfg: Config, plat_key: str, account: str, since: float) -> Path | None:
    """找出本次登录生成的二维码图片（最新的那个）。

    顺带兼容几种历史/其他平台的落盘位置，免得 sau 改路径就找不到。
    """
    import glob as _glob

    patterns = [
        qr_glob(cfg, plat_key, account),          # 主路径（实测确认）
        str(cfg.sau.cookies_dir / "*.png"),       # 兜底：cookies 下任何 png
        str(cfg.sau.root / "qrcode.png"),         # 文档提到的老位置
        str(Path.cwd() / "qrcode.png"),
        "/tmp/qrcode.png",
    ]
    best: tuple[float, Path] | None = None
    seen: set[Path] = set()
    for pattern in patterns:
        for name in _glob.glob(pattern):
            cand = Path(name)
            if cand in seen:
                continue
            seen.add(cand)
            try:
                mtime = cand.stat().st_mtime
            except OSError:
                continue
            if mtime < since - 2:                 # 只认本次登录产生的
                continue
            if best is None or mtime > best[0]:
                best = (mtime, cand)
    return best[1] if best else None
