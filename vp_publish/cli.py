"""命令行入口。

设计取向：**默认动作就是发布**，不需要敲子命令。
    vp-publish final.mp4
这一条就够。其余子命令（doctor / login / accounts）都是辅助。

为什么把「发布」当默认动作：因为这是「一键」的全部意义。
如果非要 `vp-publish publish final.mp4`，那就不叫一键了。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__, config as config_mod, cover, doctor, meta as meta_mod
from . import platforms, report, sau, state

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".flv", ".webm", ".m4v", ".ts", ".wmv"}


# ── 输出小工具 ──────────────────────────────────────────────────
def say(msg: str = "") -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print(f"  ⚠ {msg}", file=sys.stderr, flush=True)


def die(msg: str, code: int = 2) -> "NoReturn":      # type: ignore[valid-type]
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(code)


# ── 参数解析 ────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vp-publish",
        description="一键上传视频到各个平台（抖音/小红书/快手/B站/视频号/微博/虎扑/百家号/支付宝/YouTube）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例子：\n"
            "  vp-publish final.mp4                      # 发到所有已登录平台\n"
            "  vp-publish videos/*.mp4 --only 抖音,B站    # 只发两个平台\n"
            "  vp-publish final.mp4 --skip youtube        # 跳过某个平台\n"
            "  vp-publish final.mp4 --dry-run             # 只打印要执行什么\n"
            "  vp-publish doctor                          # 体检：能发到哪\n"
            "  vp-publish login douyin                    # 扫码登录抖音\n"
        ),
    )
    p.add_argument("videos", nargs="*", help="视频文件，或包含视频的目录")
    p.add_argument("-t", "--title", default="", help="标题（默认自动推导）")
    p.add_argument("-d", "--desc", default="", help="简介（默认同标题）")
    p.add_argument("-T", "--tags", default="", help="标签，逗号分隔")
    p.add_argument("-c", "--cover", default="", help="封面图片（默认自动抽帧）")
    p.add_argument("--only", default="", help="只发这些平台，逗号分隔")
    p.add_argument("--skip", default="", help="跳过这些平台，逗号分隔")
    p.add_argument("--account", default="", help="强制指定账号名（所有平台共用）")
    p.add_argument("--schedule", default="", help='定时发布，格式 "2026-03-24 21:30"')
    p.add_argument("--tid", type=int, default=None, help="B站分区 id（默认 171=科技·人工智能）")
    p.add_argument("--collection", default="", help="合集名（视频号/百家号/支付宝/抖音/快手/微博）")
    p.add_argument("--playlist", default="", help="YouTube 播放列表")
    p.add_argument("--visibility", default="", choices=["", "public", "unlisted", "private"],
                   help="YouTube 可见性")
    p.add_argument("--force", action="store_true", help="忽略发布记录，重发已成功的平台")
    p.add_argument("--no-cover", action="store_true", help="不自动生成封面")
    p.add_argument("--dry-run", action="store_true", help="只打印将要执行的命令，不真发")
    p.add_argument("--headed", action="store_true", help="显示浏览器窗口（排障用）")
    p.add_argument("--json", action="store_true", help="输出 JSON（给脚本/agent 用）")
    p.add_argument("-V", "--version", action="version", version=f"vp-publish {__version__}")
    return p


def build_sub_parser() -> argparse.ArgumentParser:
    """子命令解析器。

    为什么要跟发布参数分成两个解析器：argparse 有个硬限制——
    同一个解析器里不能既有 `nargs="*"` 的位置参数、又有子命令。
    第一个位置参数会被拿去匹配子命令名，于是
    `vp-publish final.mp4` 会报 `invalid choice: 'final.mp4'`。

    解决办法：在 main() 里先看 argv[0] 是不是已知子命令，
    是就用这个解析器，不是就用发布解析器。
    """
    p = argparse.ArgumentParser(
        prog="vp-publish",
        description="vp-publish 子命令",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-V", "--version", action="version", version=f"vp-publish {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="体检：环境 + 各平台登录状态")
    d.add_argument("--live", action="store_true", help="实连各平台验活（慢但准）")
    d.add_argument("--only", default="", help="只检查这些平台")
    d.add_argument("--json", action="store_true", help="输出 JSON")
    d.add_argument("--no-proxy-check", action="store_true", help="跳过代理连通性检查")

    l = sub.add_parser("login", help="登录某个平台（出二维码/开浏览器）")
    l.add_argument("platform", help="平台名，如 douyin / 抖音 / bilibili")
    l.add_argument("--account", default="", help="账号名（默认用配置或平台名）")
    l.add_argument("--headed", action="store_true", help="显示浏览器窗口（推荐）")
    l.add_argument("--qr-out", default="", help="把二维码另存到这个路径")

    a = sub.add_parser("accounts", help="列出已登录的账号")
    a.add_argument("--json", action="store_true")

    sub.add_parser("platforms", help="列出所有平台及其能力")

    i = sub.add_parser("init", help="生成配置文件")
    i.add_argument("--force", action="store_true", help="覆盖已存在的配置")

    f = sub.add_parser("forget", help="清掉某个视频的发布记录")
    f.add_argument("videos", nargs="+")

    return p


SUBCOMMANDS = ("doctor", "login", "accounts", "platforms", "init", "forget")


# ── 视频收集 ────────────────────────────────────────────────────
def collect_videos(raw: list[str]) -> list[Path]:
    out: list[Path] = []
    for item in raw:
        path = Path(item).expanduser()
        if path.is_dir():
            found = sorted(
                p for p in path.iterdir()
                if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
            )
            if not found:
                warn(f"目录里没有视频文件：{path}")
            out += found
        elif path.is_file():
            out.append(path)
        else:
            die(f"找不到：{path}")
    # 去重但保持顺序
    seen: set[Path] = set()
    uniq: list[Path] = []
    for p in out:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            uniq.append(p)
    return uniq


def validate_schedule(value: str) -> str:
    if not value:
        return ""
    try:
        time.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        die(f'定时发布时间格式不对："{value}"。\n'
            f'  正确格式：2026-03-24 21:30（年-月-日 时:分）')
    return value


# ── 目标平台 ────────────────────────────────────────────────────
def resolve_targets(cfg: config_mod.Config, only: str, skip: str,
                    available: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """算出这次要发哪些平台，返回 (要发的, 提示信息)。"""
    notes: list[str] = []

    if only:
        wanted, bad = platforms.parse_list(only)
        if bad:
            die(f"认不出这些平台名：{', '.join(bad)}\n"
                f"  可用：{', '.join(p.key for p in platforms.PLATFORMS)}")
        candidates = platforms.ordered(wanted)
    else:
        # 默认：所有已登录的平台
        candidates = platforms.ordered(list(available.keys()))

    if skip:
        dropped, bad = platforms.parse_list(skip)
        if bad:
            die(f"认不出这些平台名：{', '.join(bad)}")
        before = len(candidates)
        candidates = [c for c in candidates if c not in dropped]
        if before != len(candidates):
            notes.append(f"--skip 去掉了 {before - len(candidates)} 个平台")

    # 只保留真的登录了的（除非 --only 明确点名，那也要求登录）
    ready = [c for c in candidates if available.get(c)]
    not_logged = [c for c in candidates if not available.get(c)]
    if not_logged:
        labels = [platforms.BY_KEY[c].label if c in platforms.BY_KEY else c
                  for c in not_logged]
        notes.append(f"跳过未登录：{', '.join(labels)}（先 vp-publish login <平台>）")

    return ready, notes


# ── 主流程：发布 ────────────────────────────────────────────────
def cmd_publish(args, cfg: config_mod.Config) -> int:
    videos = collect_videos(args.videos)
    if not videos:
        die("没给视频。用法：vp-publish final.mp4\n"
            "  看看有哪些平台可用：vp-publish doctor")

    sau_path = sau.find_sau(cfg)
    if sau_path is None and not args.dry_run:
        die(sau.sau_problem(cfg))

    available = sau.discover_accounts(cfg)
    targets, notes = resolve_targets(cfg, args.only, args.skip, available)
    if not targets:
        die("没有可发的平台。\n"
            f"  已登录：{', '.join(available) or '（一个都没有）'}\n"
            f"  先登录一个：vp-publish login douyin\n"
            f"  或者体检看看：vp-publish doctor")

    schedule = validate_schedule(args.schedule)
    headless = not args.headed
    store = state.Store()
    started = time.time()

    if not args.json:
        say(f"目标平台（{len(targets)}）：" +
            "、".join(platforms.BY_KEY[t].label for t in targets))
        say(f"视频（{len(videos)}）：" + "、".join(v.name for v in videos))
        if schedule:
            say(f"定时发布：{schedule}")
        for n in notes:
            say(f"  · {n}")
        if args.dry_run:
            say("  · dry-run 模式，不会真发")
        say()

    all_rows: list[dict] = []
    all_status: list[str] = []
    json_out: list[dict] = []
    any_fail = False

    for video in videos:
        if not args.json and len(videos) > 1:
            say(f"── {video.name} ──")

        m = meta_mod.load(
            video,
            title=args.title, desc=args.desc, tags=args.tags,
            cover=args.cover, tid=args.tid, collection=args.collection,
            playlist=args.playlist, visibility=args.visibility,
            schedule=schedule,
        )
        if not m.tags and cfg.default_tags:
            m.tags = list(cfg.default_tags)

        record = store.get(video)
        ratio_cache: dict[str, Path | None] = {}     # 比例 → 封面文件（跨平台复用）
        cover_warned: set[str] = set()
        video_rows: list[dict] = []

        for key in targets:
            plat = platforms.BY_KEY[key]
            account = args.account or sau.pick_account(cfg, key, available)

            if record.is_ok(key) and not args.force:
                row = report.line_for(plat.label, account, report.SKIP, 0.0,
                                      "已经发过了（--force 可重发）")
                video_rows.append(row)
                all_status.append(report.SKIP)
                continue

            # 标题按平台裁剪
            title, warns = meta_mod.adapt_title(m.title, plat)
            for w in warns:
                warn(w)
            if key in cfg.title_max and len(title) > cfg.title_max[key]:
                title = title[: cfg.title_max[key] - 1] + "…"
            tags = meta_mod.adapt_tags(m.tags, plat)

            # 封面：先算这个平台要哪些比例，再按**比例**取缓存
            slots = cover.ratios_for(key, plat.caps)
            covers: dict[str, Path] = {}
            if m.cover and Path(m.cover).is_file():
                # 用户自己给了封面 → 直接用，不做任何裁剪（尊重用户）
                for slot in slots:
                    covers[slot] = Path(m.cover)
            elif cfg.cover and not args.no_cover:
                for ratio in set(slots.values()):
                    if ratio not in ratio_cache:
                        res = cover.extract(video, cfg.resolve_cover_dir(),
                                            ratio=ratio, at=cfg.cover_at)
                        ratio_cache[ratio] = res.path
                        if res.path is None and res.reason and res.reason not in cover_warned:
                            cover_warned.add(res.reason)
                            warn(f"封面：{res.reason}")
                    got = ratio_cache[ratio]
                    if got:
                        for slot, want in slots.items():
                            if want == ratio:
                                covers[slot] = got

            # 本次的元数据副本（标题/标签已按平台适配）
            use = meta_mod.Meta(**{**m.to_dict(), "title": title, "tags": tags})
            use.tid = m.tid if m.tid is not None else cfg.tid

            if not args.json:
                say(f"  → {plat.label}（账号 {account}）…")

            res = sau.upload(sau_path, plat, account, video, use, covers, cfg,
                             headless=headless, dry_run=args.dry_run)

            if args.dry_run:
                status = report.DRY
                note = ""
            elif res.ok:
                status = report.OK
                note = ""
            else:
                status = report.FAIL
                note = res.reason or res.tail or f"退出码 {res.code}"
                any_fail = True

            if not args.dry_run:
                record.mark(key, state.OK if res.ok else state.FAIL, note=note[:200])

            video_rows.append(report.line_for(
                plat.label, account, status, res.elapsed,
                report.truncate(note, 60)))
            all_status.append(status)

            json_out.append({
                "video": str(video),
                "platform": key,
                "label": plat.label,
                "account": account,
                "status": status,
                "elapsed": round(res.elapsed, 1),
                "title": title,
                "note": note,
                "argv": res.argv,
            })

            if not args.json:
                mark = report.STATUS_MARK.get(status, "?")
                extra = f"  {note}" if note and not args.dry_run else ""
                say(f"    {mark} {report.STATUS_TEXT.get(status, status)}"
                    f"（{res.elapsed:.0f}s）{extra}")
                if args.dry_run:
                    # 命令太长，塞进表格会挤爆——单独一行更好读
                    say(f"      $ {' '.join(res.argv)}")

        if not args.dry_run:
            store.put(video, record)
        all_rows += video_rows
        if not args.json:
            say()

    if not args.dry_run:
        store.prune()
        store.save()

    if args.json:
        say(json.dumps({
            "ok": not any_fail,
            "videos": [str(v) for v in videos],
            "targets": targets,
            "results": json_out,
            "summary": report.summarize(
                [{"status": r["status"]} for r in json_out]),
            "elapsed": round(time.time() - started, 1),
        }, ensure_ascii=False, indent=2))
    else:
        say(report.table(all_rows, [
            ("platform", "平台", "left"),
            ("account", "账号", "left"),
            ("result", "结果", "left"),
            ("elapsed", "耗时", "right"),
            ("note", "说明", "left"),
        ]))
        counts = report.summarize([{"status": s} for s in all_status])
        say()
        say(f"合计：成功 {counts.get('ok', 0)}，失败 {counts.get('fail', 0)}，"
            f"跳过 {counts.get('skip', 0)}，试运行 {counts.get('dry', 0)}"
            f"（总耗时 {report.human_duration(time.time() - started)}）")
        if any_fail:
            say("  失败的看上面的「说明」列；cookie 过期了就重新 vp-publish login <平台>")

    return 1 if any_fail else 0


# ── 子命令 ──────────────────────────────────────────────────────
def cmd_doctor(args, cfg: config_mod.Config) -> int:
    only: list[str] = []
    if args.only:
        only, bad = platforms.parse_list(args.only)
        if bad:
            die(f"认不出这些平台名：{', '.join(bad)}")
    env = doctor.inspect_environment(cfg, check_proxy=not args.no_proxy_check)
    healths = doctor.inspect_platforms(cfg, env, live=args.live, only=only or None)
    if args.json:
        say(json.dumps(doctor.to_dict(cfg, env, healths), ensure_ascii=False, indent=2))
    else:
        say(doctor.render(cfg, env, healths, live=args.live))
    ready = [h for h in healths if h.status == report.OK]
    return 0 if ready else 1


def cmd_login(args, cfg: config_mod.Config) -> int:
    key = platforms.resolve(args.platform)
    if key is None:
        die(f"认不出平台名：{args.platform}\n"
            f"  可用：{', '.join(p.key for p in platforms.PLATFORMS)}")
    plat = platforms.BY_KEY[key]
    sau_path = sau.find_sau(cfg)
    if sau_path is None:
        die(sau.sau_problem(cfg))

    available = sau.discover_accounts(cfg)
    account = args.account or cfg.sau.accounts.get(key) or \
        (available.get(key) or [""])[0] or f"我的{plat.label}"

    acct_file = sau.account_file(cfg, key, account)
    before = acct_file.stat().st_mtime if acct_file.is_file() else 0.0

    say(f"登录 {plat.label}（{key}），账号名：{account}")
    say(f"  方式：{'浏览器里输账号' if plat.login == 'browser' else '手机扫码'}")
    if plat.login == "browser" and not args.headed:
        say("  · 这个平台要在浏览器里操作，建议加 --headed 看得到窗口")
    say("  · sau 会把二维码直接打印在下面（没有的话看最后打印的图片路径）")
    say()

    since = time.time()
    headless = not args.headed
    # 注意：这一步的输出**直接透传到你的终端**（含二维码），不经过我们捕获。
    # 见 sau.run_stream() 的注释 —— 早期版本捕获了输出，二维码被吞掉，
    # 用户在终端上什么都看不到，只能干等超时。
    res = sau.login(sau_path, key, account, cfg, headless=headless)

    # 成功判据：账号文件被创建/更新（因为输出被透传，拿不到文本）
    after = acct_file.stat().st_mtime if acct_file.is_file() else 0.0
    saved = after > before

    qr = sau.newest_qr(cfg, key, account, since)
    if qr:
        say(f"\n二维码图片：{qr}")
        if args.qr_out:
            try:
                import shutil as _shutil
                _shutil.copy2(qr, args.qr_out)
                say(f"已另存到：{args.qr_out}")
            except Exception as exc:
                warn(f"另存二维码失败：{exc}")

    if res.ok and saved:
        say(f"\n✓ {plat.label} 登录成功。")
        say(f"  账号文件：{acct_file}")
        say(f"  验一下：vp-publish doctor --only {key} --live")
        return 0

    if res.ok and not saved:
        # 退出码 0 但文件没动 —— sau 有时会这样，别报「成功」骗人
        say(f"\n? {plat.label} 登录流程跑完了，但账号文件没变化（{acct_file}）。")
        say("  可能你中途取消/超时了。重跑一次，并加上 --headed 看清过程。")
        return 1

    say(f"\n✗ {plat.label} 登录失败。")
    if res.reason:
        say(f"  原因：{res.reason}")
    say("  提示：加 --headed 看得到浏览器窗口，最容易定位问题")
    return 1


def cmd_accounts(args, cfg: config_mod.Config) -> int:
    available = sau.discover_accounts(cfg)
    if args.json:
        say(json.dumps(available, ensure_ascii=False, indent=2))
        return 0
    if not available:
        say(f"还没有登录任何账号。\n  cookies 目录：{cfg.sau.cookies_dir}\n"
            f"  登录一个：vp-publish login douyin")
        return 1
    rows = []
    for key in platforms.ordered(list(available.keys())):
        label = platforms.BY_KEY[key].label if key in platforms.BY_KEY else key
        for name in available[key]:
            path = sau.account_file(cfg, key, name)
            exp, why = doctor.read_cookie_expiry(path)
            left = ""
            if exp is not None:
                days = (exp - time.time()) / 86400
                left = f"{days:.1f} 天" if days >= 0 else "已过期"
            rows.append({"platform": label, "account": name, "expires": left, "why": why})
    say(report.table(rows, [
        ("platform", "平台", "left"),
        ("account", "账号", "left"),
        ("expires", "剩余", "right"),
        ("why", "依据", "left"),
    ]))
    return 0


def cmd_platforms(args, cfg: config_mod.Config) -> int:
    rows = []
    for p in platforms.PLATFORMS:
        caps = []
        if platforms.COVER_DUAL in p.caps:
            caps.append("封面横+竖")
        elif platforms.COVER in p.caps:
            caps.append("封面")
        if platforms.SCHEDULE in p.caps:
            caps.append("定时")
        if platforms.COLLECTION in p.caps:
            caps.append("合集")
        if platforms.PLAYLIST in p.caps:
            caps.append("播放列表")
        if platforms.VISIBILITY in p.caps:
            caps.append("可见性")
        if platforms.TID in p.caps:
            caps.append("需--tid")
        rows.append({
            "key": p.key,
            "label": p.label,
            "login": "浏览器" if p.login == "browser" else "扫码",
            "title": (f"{p.title_min}~{p.title_max}" if p.title_min
                      else (f"≤{p.title_max}" if p.title_max else "—")),
            "caps": "、".join(caps) or "—",
            "note": p.note,
        })
    say(report.table(rows, [
        ("key", "key", "left"),
        ("label", "平台", "left"),
        ("login", "登录", "left"),
        ("title", "标题长度", "left"),
        ("caps", "支持", "left"),
        ("note", "说明", "left"),
    ]))
    return 0


def cmd_init(args, cfg: config_mod.Config) -> int:
    try:
        path = config_mod.write_template(force=args.force)
    except FileExistsError as exc:
        die(str(exc))
    say(f"✓ 配置已写入：{path}")
    say("  改完直接生效，不需要重启任何东西。")
    return 0


def cmd_forget(args, cfg: config_mod.Config) -> int:
    store = state.Store()
    n = 0
    for item in args.videos:
        if store.forget(Path(item).expanduser()):
            n += 1
            say(f"✓ 已清掉记录：{item}")
        else:
            say(f"· 没有记录：{item}")
    if n:
        store.save()
    return 0


# ── main ────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg = config_mod.load()

    handlers = {
        "doctor": cmd_doctor,
        "login": cmd_login,
        "accounts": cmd_accounts,
        "platforms": cmd_platforms,
        "init": cmd_init,
        "forget": cmd_forget,
    }

    # 第一个参数是已知子命令 → 走子命令；否则整个 argv 当发布参数。
    # 这样 `vp-publish final.mp4` 和 `vp-publish doctor` 都能用。
    head = argv[0] if argv else ""
    if head in SUBCOMMANDS:
        args = build_sub_parser().parse_args(argv)
        return handlers[args.command](args, cfg)

    args = build_parser().parse_args(argv)
    return cmd_publish(args, cfg)
