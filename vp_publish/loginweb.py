"""login-web：把「扫码登录」变成一个能立刻扫的本地网页。

**为什么需要**：扫码登录的死穴是**延迟**。二维码有时效（抖音实测一分钟量级），
而「命令行出码 → 把图片取出来 → 递给用户 → 用户打开 → 扫」这条路上
每多一次往返就烧掉几秒；更糟的是**二维码过期后命令行不会自己重出**，
用户只能干等或者从头再来一遍。

网页把这条路压到最短：浏览器里直接就是二维码，过期了一键换新的，
还能设成到期自动换。登录成功页面自己就知道（账号文件出现），不用人去命令行看。

**刻意不做的事**：

  · **页面零外部请求**。不引任何 CDN 的字体/图标/框架 —— 板子可能根本
    连不上外网。这也是不复用 `lilyco-gui` 的原因：它从公网 CDN 拉 layui，
    拉不到就整页失灵（`layui is not defined`），而且所有事件绑定都包在
    CDN 的回调里 → **按钮点了没反应，且零报错**，极难排查。
  · 不把二维码当字符串传。直接给 `<img>` 一个 URL，浏览器自己解码，
    省掉一次 base64 编解码，也省掉我这边渲染终端的麻烦。

**一个必须守住的细节**：找二维码时**只认当前平台自己的文件名**
（`cookies/{platform}_{account}_login_qrcode_*.png`）。
`sau.newest_qr()` 为了兼容历史位置会兜底 glob 整个 cookies 目录的 `*.png`，
在「一次只登一个平台」的命令行场景没问题，但在网页里会让你扫到
**上一个平台的二维码** —— 看起来完全正常，扫了就是登不上。
"""
from __future__ import annotations

import http.server
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import platforms, sau
from .config import Config

# 二维码显示多久后自动换一张。
# 抖音的码实测一分钟量级就失效，而 sau 不会自己重出，所以默认就开着自动换。
# 不能设太短：用户扫完还要在手机上点确认，中途把浏览器杀掉会让确认失败。
DEFAULT_REFRESH = 75

_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
_BLOCKS = set("█▀▄▌▐░▒▓ ")


def _strip_noise(text: str) -> str:
    """把终端二维码和 ANSI 转义清掉，只留人能读的日志。

    不清的话日志区会被几千个方块字符刷满，出错原因反而看不见。
    """
    out: list[str] = []
    for line in _ANSI.sub("", text).splitlines():
        body = line.strip()
        if len(body) > 16 and sum(c in _BLOCKS for c in body) / len(body) > 0.6:
            continue                      # 这一行是二维码的一部分
        out.append(line.rstrip())
    return "\n".join(out).strip()


def lan_ip() -> str:
    """猜本机在局域网里的地址（不会真的发包）。"""
    for probe in ("8.8.8.8", "1.1.1.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((probe, 80))
                return s.getsockname()[0]
        except Exception:
            continue
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


@dataclass
class Session:
    """一次登录会话。同一时刻只允许一个 —— 扫码这件事没法并行。"""
    platform: str = ""
    account: str = ""
    proc: subprocess.Popen | None = None
    log_path: Path | None = None
    log_fh: object | None = None
    started_at: float = 0.0
    qr_path: Path | None = None
    qr_mtime: float = 0.0
    exit_code: int | None = None
    ok: bool = False
    note: str = ""

    @property
    def active(self) -> bool:
        return bool(self.platform)


class Hub:
    """管「当前这一次登录」，并对外提供一份可轮询的状态。"""

    def __init__(self, cfg: Config, *, refresh_after: int = DEFAULT_REFRESH,
                 headless: bool = True):
        self.cfg = cfg
        self.refresh_after = refresh_after
        self.headless = headless
        self.lock = threading.Lock()
        self.session = Session()
        # 跟着 XDG 走：板子上 ~/.cache 和状态目录可能不在一个盘上，
        # 而且测试里要能圈到临时目录，别往用户家里写。
        cache = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
        self.log_dir = Path(cache) / "vp-publish" / "login"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.sau_path = sau.find_sau(cfg)
        self.problem = "" if self.sau_path else sau.sau_problem(cfg)

    # ── 子进程 ──────────────────────────────────────────────
    def _spawn(self, key: str, account: str, *, keep_qr: bool = False) -> None:
        """拉起一次登录。`keep_qr=True` 时保留上一张码继续显示。

        为什么需要 keep_qr：chromium 起来要十几秒，这段时间里页面如果
        没有码，用户会以为坏了。同一个平台换码时就让旧码继续挂着，
        新码一到自动替换。

        但**换平台时必须清掉**（keep_qr=False）—— 否则会出现
        「页面显示抖音的码，实际在登 B 站」这种事，扫了就是登不上，
        而且零报错。这正是本模块开头警告的那个坑。
        """
        argv = sau.build_login_argv(self.sau_path, key, account,
                                    headless=self.headless)
        log_path = self.log_dir / f"{key}.log"
        fh = open(log_path, "wb")
        kwargs: dict = dict(
            cwd=str(self.cfg.sau.root),
            env=sau.env_for_subprocess(self.cfg),
            # 不给 stdin：实测（2026-09-27，抖音）没有伪终端也照样会把二维码
            # 存成 PNG，我们只依赖 PNG，不依赖终端渲染。
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=subprocess.STDOUT,
        )
        if os.name == "posix":
            # 单独一个进程组：这样退出时能把 chromium 一起收掉，
            # 否则每次换二维码都会漏一个浏览器进程。
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(argv, **kwargs)

        s = self.session
        old_path, old_mtime = s.qr_path, s.qr_mtime
        s.platform = key
        s.account = account
        s.proc = proc
        s.log_path = log_path
        s.log_fh = fh
        s.started_at = time.time()
        s.qr_path = old_path if keep_qr else None
        s.qr_mtime = old_mtime if keep_qr else 0.0
        s.exit_code = None
        s.ok = False
        s.note = ""

    def _close_log(self) -> None:
        fh = self.session.log_fh
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass
            self.session.log_fh = None

    def _kill(self, proc: subprocess.Popen | None) -> None:
        if proc is None or proc.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            else:
                proc.terminate()
        except Exception:
            return
        try:
            proc.wait(timeout=8)
        except Exception:
            try:
                if os.name == "posix":
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                else:
                    proc.kill()
            except Exception:
                pass

    # ── 对外动作 ────────────────────────────────────────────
    def start(self, key: str, *, account: str = "") -> dict:
        with self.lock:
            # 先校验平台名：这是调用方的入参错误，跟本机装没装 sau 无关。
            # 顺序反过来的话，在一台没装 sau 的机器上，传个错平台名会得到
            # 「没找到 sau」这种驴唇不对马嘴的提示，排查时会被带偏。
            if key not in platforms.BY_KEY:
                return {"ok": False, "error": f"认不出平台：{key}"}
            if self.sau_path is None:
                return {"ok": False, "error": self.problem}
            # 同一个平台重开 = 换一张码 → 旧码继续挂着，别让页面空十几秒。
            # 换了平台就绝不能留，不然会显示着 A 平台的码去登 B 平台。
            same = bool(self.session.active and self.session.platform == key)
            self._kill(self.session.proc)
            self._close_log()
            acct = sau.resolve_account(self.cfg, key, override=account)
            self._spawn(key, acct, keep_qr=same)
            return {"ok": True, "platform": key, "account": acct,
                    "reused": same}

    def stop(self) -> dict:
        with self.lock:
            self._kill(self.session.proc)
            self._close_log()
            self.session = Session()
            return {"ok": True}

    def shutdown(self) -> None:
        with self.lock:
            self._kill(self.session.proc)
            self._close_log()

    # ── 状态 ────────────────────────────────────────────────
    def _qr_for(self, key: str, account: str, since: float) -> Path | None:
        """**只**认这个平台自己的二维码文件名。

        不在这里用 sau.newest_qr()：它有兜底 glob，会捞到别的平台的图。
        """
        import glob as _glob

        best: tuple[float, Path] | None = None
        for name in _glob.glob(sau.qr_glob(self.cfg, key, account)):
            cand = Path(name)
            try:
                mt = cand.stat().st_mtime
            except OSError:
                continue
            if mt + 1 < since:
                continue
            if best is None or mt > best[0]:
                best = (mt, cand)
        return best[1] if best else None

    def _poll(self) -> None:
        s = self.session
        if not s.active:
            return
        if s.proc is not None and s.exit_code is None:
            code = s.proc.poll()
            if code is not None:
                s.exit_code = code
                self._close_log()
        acct = sau.account_file(self.cfg, s.platform, s.account)
        try:
            if acct.is_file() and acct.stat().st_mtime > s.started_at:
                s.ok = True
        except OSError:
            pass
        qr = self._qr_for(s.platform, s.account, s.started_at)
        if qr is not None:
            try:
                mt = qr.stat().st_mtime
            except OSError:
                mt = 0.0
            if mt > s.qr_mtime:
                s.qr_mtime, s.qr_path = mt, qr

    def _tail(self, path: Path | None, limit: int = 3000) -> str:
        if path is None or not path.is_file():
            return ""
        try:
            text = path.read_bytes().decode("utf-8", "replace")
        except OSError:
            return ""
        return _strip_noise(text)[-limit:]

    def state(self) -> dict:
        with self.lock:
            self._poll()
            s = self.session
            try:
                accounts = sau.discover_accounts(self.cfg)
            except Exception:
                accounts = {}
            if not s.active:
                status = "idle"
            elif s.ok:
                status = "ok"
            elif s.exit_code is not None:
                status = "failed"
            elif s.qr_mtime:
                status = "waiting"
            else:
                status = "starting"
            now = time.time()
            return {
                "ok": True,
                "status": status,
                "platform": s.platform,
                "account": s.account,
                "qr_mtime": s.qr_mtime,
                "qr_age": round(now - s.qr_mtime, 1) if s.qr_mtime else 0.0,
                "refresh_after": self.refresh_after,
                "elapsed": round(now - s.started_at, 1) if s.started_at else 0.0,
                "exit_code": s.exit_code,
                "log_tail": self._tail(s.log_path),
                "problem": self.problem,
                "account_file": str(sau.account_file(self.cfg, s.platform, s.account))
                                if s.active else "",
                "accounts": {k: v for k, v in accounts.items() if v},
                "platforms": [
                    {"key": p.key, "label": p.label, "login": p.login,
                     "needs_proxy": p.needs_proxy, "note": p.note}
                    for p in platforms.PLATFORMS
                ],
            }

    def qr_bytes(self) -> bytes | None:
        with self.lock:
            path = self.session.qr_path
            if path is None or not path.is_file():
                return None
            try:
                return path.read_bytes()
            except OSError:
                return None


# ── 页面 ────────────────────────────────────────────────────────
# 刻意用**浅色**：二维码必须是白底黑块才好扫，深色主题下反色的码很多
# 手机识别率会掉。整个页面就围绕这一张白卡做。
PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>扫码登录 · vp-publish</title>
<style>
  :root{--bg:#f5f6f8;--card:#fff;--line:#e3e6ea;--fg:#1c1f23;--dim:#6b7280;
        --brand:#2f6fed;--ok:#15803d;--bad:#b91c1c;--warn:#b45309}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:15px/1.55 -apple-system,"Segoe UI",Roboto,"Noto Sans CJK SC",
       "Microsoft YaHei",sans-serif}
  .wrap{max-width:880px;margin:0 auto;padding:28px 20px 60px}
  h1{font-size:22px;margin:0 0 4px}
  .sub{color:var(--dim);margin:0 0 22px;font-size:14px}
  .grid{display:grid;grid-template-columns:360px 1fr;gap:22px;align-items:start}
  @media(max-width:760px){.grid{grid-template-columns:1fr}}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;
        padding:18px}
  .qrbox{background:#fff;border-radius:12px;display:flex;align-items:center;
         justify-content:center;min-height:320px;border:1px dashed var(--line);
         position:relative}
  .qrbox img{width:320px;height:320px;image-rendering:pixelated;display:none}
  .qrbox.ready img{display:block}
  .qrbox.ready{border-style:solid}
  .ph{color:var(--dim);text-align:center;padding:0 20px;font-size:14px}
  .qrbox.ready .ph{display:none}
  /* 码已经超过刷新间隔了：灰掉 + 盖一层说明，别让用户去扫一张废码。
     这是实测踩到的：页面打开时如果码早就过期了，原来会当成正常码显示。 */
  .qrbox .tip{display:none;position:absolute;inset:0;align-items:center;
             justify-content:center;text-align:center;padding:0 26px;
             background:rgba(255,255,255,.88);border-radius:12px;
             color:var(--warn);font-weight:600;font-size:14px}
  .qrbox.stale .tip{display:flex}
  .qrbox.stale img{filter:grayscale(1);opacity:.28}
  .meta{margin-top:14px}
  .status{font-weight:600;display:flex;align-items:center;gap:8px}
  .status .dot{width:9px;height:9px;border-radius:50%;background:var(--dim);
               flex:none}
  .status.s-waiting .dot{background:var(--brand)}
  .status.s-ok .dot{background:var(--ok)}
  .status.s-failed .dot{background:var(--bad)}
  .status.s-starting .dot{background:var(--warn)}
  .bar{height:4px;background:var(--line);border-radius:2px;margin:10px 0 6px;
       overflow:hidden}
  .bar i{display:block;height:100%;width:0;background:var(--brand);
         transition:width .4s linear}
  .hint{color:var(--dim);font-size:13px;min-height:20px}
  .actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}
  button{font:inherit;padding:9px 16px;border-radius:9px;border:1px solid var(--line);
         background:#fff;color:var(--fg);cursor:pointer}
  button:hover:not(:disabled){border-color:#c8ccd2}
  button.primary{background:var(--brand);border-color:var(--brand);color:#fff}
  button:disabled{opacity:.45;cursor:not-allowed}
  label.tg{display:flex;align-items:center;gap:6px;color:var(--dim);font-size:13px}
  .plats{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  @media(max-width:760px){.plats{grid-template-columns:1fr}}
  .p{display:flex;align-items:center;justify-content:space-between;gap:10px;
     padding:11px 13px;border:1px solid var(--line);border-radius:10px;
     background:#fff;cursor:pointer;text-align:left}
  .p:hover{border-color:#c8ccd2}
  .p.on{border-color:var(--brand);box-shadow:0 0 0 2px rgba(47,111,237,.14)}
  .p .n{font-weight:600}
  .p .t{font-size:12px;color:var(--dim);margin-top:1px}
  .badge{font-size:12px;padding:2px 8px;border-radius:99px;border:1px solid var(--line);
         color:var(--dim);white-space:nowrap;flex:none}
  .badge.yes{color:var(--ok);border-color:#bbf7d0;background:#f0fdf4}
  .badge.no{color:var(--dim)}
  h2{font-size:15px;margin:26px 0 10px}
  details{background:var(--card);border:1px solid var(--line);border-radius:12px;
          padding:12px 14px;margin-top:22px}
  summary{cursor:pointer;color:var(--dim);font-size:13px}
  pre{margin:10px 0 0;font:12px/1.5 ui-monospace,Consolas,monospace;
      white-space:pre-wrap;word-break:break-all;max-height:260px;overflow:auto;
      color:#374151}
  .banner{margin-top:14px;padding:11px 13px;border-radius:10px;font-size:14px;
          display:none}
  .banner.show{display:block}
  .banner.ok{background:#f0fdf4;border:1px solid #bbf7d0;color:#14532d}
  .banner.bad{background:#fef2f2;border:1px solid #fecaca;color:#7f1d1d}
  .foot{color:var(--dim);font-size:12px;margin-top:26px}
</style>
</head>
<body>
<div class="wrap">
  <h1>扫码登录</h1>
  <p class="sub">选一个平台，用对应 App 扫二维码。二维码过期不用管，它会自己换新的。</p>

  <div class="grid">
    <div class="card">
      <div class="qrbox" id="qrbox">
        <div class="ph" id="ph">点右边任意一个平台开始</div>
        <img id="qr" alt="登录二维码">
        <div class="tip" id="qstale">这张码已经过期了，正在换新的…</div>
      </div>
      <div class="meta">
        <div class="status" id="status"><span class="dot"></span><span id="stext">未开始</span></div>
        <div class="bar"><i id="bar"></i></div>
        <div class="hint" id="hint"></div>
      </div>
      <div class="actions">
        <button class="primary" id="again" disabled>换一张</button>
        <button id="stop" disabled>停止</button>
        <label class="tg"><input type="checkbox" id="auto" checked> 到期自动换</label>
      </div>
      <div class="banner" id="banner"></div>
    </div>

    <div>
      <div class="plats" id="plats"></div>
      <div class="foot" id="foot"></div>
    </div>
  </div>

  <details>
    <summary>运行日志（出问题时看这里）</summary>
    <pre id="log">（还没有日志）</pre>
  </details>
</div>

<script>
(function(){
  "use strict";
  var K = new URLSearchParams(location.search).get("k") || "";
  var $ = function(id){ return document.getElementById(id); };
  var busy = false, shownQr = 0, curPlat = "", logged = false;

  function api(path, opt){
    var url = path + (path.indexOf("?") >= 0 ? "&" : "?") + "k=" + encodeURIComponent(K);
    return fetch(url, opt).then(function(r){
      if (r.status === 401) { throw new Error("unauthorized"); }
      return r.json();
    });
  }
  function post(path, body){
    return api(path, {method:"POST",
                      headers:{"Content-Type":"text/plain"},
                      body: body || ""});
  }

  function setBanner(kind, text){
    var b = $("banner");
    b.className = "banner" + (text ? " show " + kind : "");
    b.textContent = text || "";
  }

  function render(st){
    // 平台列表
    var wrap = $("plats");
    var want = st.platforms.map(function(p){
      var acct = (st.accounts || {})[p.key];
      var on = p.key === st.platform;
      var extra = p.key === "hupu" ? "浏览器里输账号" :
                  (p.needs_proxy ? "需要代理" : "手机扫码");
      return '<button class="p' + (on ? " on" : "") + '" data-k="' + p.key + '">' +
             '<span><span class="n">' + p.label + '</span>' +
             '<div class="t">' + extra + '</div></span>' +
             '<span class="badge ' + (acct ? "yes" : "no") + '">' +
             (acct ? "已登录" : "未登录") + '</span></button>';
    }).join("");
    if (want !== logged) { wrap.innerHTML = want; logged = want;
      Array.prototype.forEach.call(wrap.querySelectorAll(".p"), function(el){
        el.onclick = function(){ begin(el.getAttribute("data-k")); };
      });
    }

    // 二维码
    var box = $("qrbox");
    var ra = st.refresh_after || 0;
    if (st.qr_mtime && st.qr_mtime !== shownQr) {
      shownQr = st.qr_mtime;
      $("qr").src = "/api/qr.png?v=" + st.qr_mtime + "&k=" + encodeURIComponent(K);
    }
    // 「这张码还能不能用」由**服务端算好的 qr_age** 判断，不靠页面自己数秒
    // —— 页面可能是刚打开的，也可能是睡了一觉的标签页。
    // 超过刷新间隔（没开自动换就按 120 秒兜底）就当它废了。
    // 注意这里**不看 status**：换码期间 status 是 starting，但屏幕上挂着的
    // 那张旧码同样已经废了，照样得盖住。
    var stale = !!st.qr_mtime && st.qr_age > (ra > 0 ? ra : 120) &&
                st.status !== "ok";
    // 只在**真的没有码**时才收起来。换码期间旧码继续挂着：chromium 起来要
    // 十几秒，中间空着用户会以为坏了。
    var has = !!(st.qr_mtime || (st.status === "starting" && shownQr));
    if (st.status === "idle" || st.status === "failed") {
      has = false; shownQr = 0;
    }
    box.classList[has ? "add" : "remove"]("ready");
    box.classList[(stale && has) ? "add" : "remove"]("stale");
    var blocked = stale && has;

    // 状态
    var s = $("status");
    s.className = "status s-" + st.status;
    var text = {idle:"未开始", starting:"正在拉起浏览器…", waiting:"等扫码",
                ok:"登录成功", failed:"登录失败"}[st.status] || st.status;
    if (st.platform) {
      var lbl = (st.platforms.filter(function(p){return p.key===st.platform;})[0]||{}).label;
      text = (lbl || st.platform) + " · " + text;
    }
    $("stext").textContent = text;

    // 倒计时条
    var frac = ra > 0 && st.qr_age ? Math.min(1, st.qr_age / ra) : 0;
    $("bar").style.width = (st.status === "waiting" && !blocked ? frac * 100 : 0) + "%";

    // 提示
    var hint = "";
    if (blocked) {
      hint = "这张码已经生成 " + Math.round(st.qr_age) + " 秒，多半失效了 —— " +
             "正在换新的，几秒后会出现；急着要就点「换一张」。";
    } else if (st.status === "waiting") {
      var left = ra > 0 ? Math.max(0, Math.ceil(ra - st.qr_age)) : 0;
      hint = ra > 0
        ? "建议在 " + left + " 秒内扫完（到点会自动换新码）"
        : "二维码已生成 " + Math.round(st.qr_age) + " 秒";
    } else if (st.status === "starting") {
      hint = shownQr ? "正在换一张新码，上面这张已经不能用了。"
                     : "正在拉起浏览器取二维码，一般十几秒。";
    } else if (st.status === "ok") {
      hint = "账号文件已写入：" + st.account_file;
    } else if (st.status === "failed") {
      hint = "进程退出码 " + st.exit_code + "，看下面的日志";
    } else if (st.status === "idle" && st.problem) {
      hint = "环境有问题：" + st.problem.split("\n")[0];
    }
    $("hint").textContent = hint;

    // 按钮
    $("again").disabled = !st.platform;
    $("stop").disabled = !st.platform;

    // 成功/失败横幅
    if (st.status === "ok") { setBanner("ok", "✓ 登录成功：" + st.account + "　" +
        "可以直接去登下一个平台了。"); }
    else if (st.status === "failed") { setBanner("bad", "✗ 这次没成，看下面的日志。"); }
    else { setBanner("", ""); }

    $("log").textContent = st.log_tail || "（还没有日志）";
    curPlat = st.platform;

    // 到期自动换。放在服务端不做，是因为**只有有人看着的时候才值得换**
    // —— 没人看还每 75 秒拉一次 chromium，在板子上是纯浪费。
    if (!busy && blocked && $("auto").checked && ra > 0) {
      begin(st.platform);
    }
  }

  function begin(k){
    if (busy) { return; }
    busy = true;
    setBanner("", "");
    post("/api/start", k).then(function(){
      // 换**同一个平台**时不清图：服务端会把旧码留着，新码到了自动替换，
      // 中间那十几秒用户至少还能看到东西（灰掉 + 提示已过期）。
      // 换平台就必须清，不然会显示着 A 平台的码去登 B 平台。
      if (k !== curPlat) {
        shownQr = 0;
        $("qrbox").classList.remove("ready");
        $("qrbox").classList.remove("stale");
      }
    }).catch(function(e){
      setBanner("bad", e.message === "unauthorized"
        ? "链接里缺 token —— 请用启动时打印的完整链接打开这个页面。"
        : ("请求失败：" + e.message));
    }).then(function(){ busy = false; });
  }

  function tick(){
    api("/api/state").then(render).catch(function(e){
      $("stext").textContent = "连不上服务";
      $("hint").textContent = e.message === "unauthorized"
        ? "链接里缺 token —— 请用启动时打印的完整链接打开。"
        : "服务可能已经退出了。";
    });
  }

  $("again").onclick = function(){ if (curPlat) { begin(curPlat); } };
  $("stop").onclick = function(){
    post("/api/stop").then(function(){
      shownQr = 0; $("qrbox").classList.remove("ready"); setBanner("", "");
    });
  };

  tick();
  setInterval(tick, 1000);
})();
</script>
</body>
</html>
"""


# ── HTTP ────────────────────────────────────────────────────────
def _make_handler(hub: Hub, token: str):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "vp-publish-login"
        protocol_version = "HTTP/1.1"

        # 默认会把每个请求打到 stderr，太吵
        def log_message(self, fmt, *args):        # noqa: A003
            pass

        # ── 工具 ──
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # 这个页面永远不该被缓存（二维码一秒钟一变）
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, data: dict, code: int = 200) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _authed(self) -> bool:
            if not token:
                return True
            q = parse_qs(urlparse(self.path).query)
            if q.get("k", [""])[0] == token:
                return True
            if self.headers.get("X-Token") == token:
                return True
            self._json({"ok": False, "error": "unauthorized"}, 401)
            return False

        def _body(self) -> str:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            return self.rfile.read(n).decode("utf-8", "replace") if n else ""

        # ── 路由 ──
        def do_GET(self):                          # noqa: N802
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return
            if not self._authed():
                return
            if path == "/api/state":
                self._json(hub.state())
                return
            if path == "/api/qr.png":
                blob = hub.qr_bytes()
                if blob is None:
                    self._send(404, b"no qrcode yet", "text/plain; charset=utf-8")
                else:
                    self._send(200, blob, "image/png")
                return
            self._json({"ok": False, "error": "not found"}, 404)

        def do_POST(self):                         # noqa: N802
            if not self._authed():
                return
            path = urlparse(self.path).path
            if path == "/api/start":
                key = self._body().strip()
                if not key:
                    self._json({"ok": False, "error": "没给平台"}, 400)
                    return
                self._json(hub.start(key))
                return
            if path == "/api/stop":
                self._json(hub.stop())
                return
            self._json({"ok": False, "error": "not found"}, 404)

    return Handler


def serve(cfg: Config, *, host: str = "0.0.0.0", port: int = 8765,
          token: str = "", refresh_after: int = DEFAULT_REFRESH,
          headless: bool = True, first: str = "",
          on_ready=None) -> int:
    """起服务并阻塞。`on_ready(url)` 用来把访问地址打印出来。"""
    hub = Hub(cfg, refresh_after=refresh_after, headless=headless)

    class Server(http.server.ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    try:
        httpd = Server((host, port), _make_handler(hub, token))
    except OSError as exc:
        print(f"起不来：{host}:{port} —— {exc}", file=sys.stderr)
        print("  换个端口：vp-publish login-web --port 8899", file=sys.stderr)
        return 1

    url = f"http://{lan_ip() if host in ('0.0.0.0', '::') else host}:{port}/"
    if token:
        url += f"?k={token}"
    if on_ready:
        on_ready(url)

    if first:
        hub.start(first)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n收工。")
    finally:
        hub.shutdown()
        httpd.server_close()
    return 0


def new_token() -> str:
    return secrets.token_urlsafe(9)
