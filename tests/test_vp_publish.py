"""vp-publish 单元测试。

跑法（不需要装任何东西，stdlib unittest）：
    python3 -m unittest discover -s tests -v

覆盖重点：**平台适配逻辑**。上传本身要靠人扫码，测不了；
但「给 B站 有没有带 --tid」「给虎扑有没有错误地塞 --schedule」
这类事完全可测，而且正是最容易写错的地方。
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vp_publish import cover, meta, platforms, report, sau, state   # noqa: E402
from vp_publish.config import Config, SauConfig                     # noqa: E402


# ── 平台 ────────────────────────────────────────────────────────
class TestPlatforms(unittest.TestCase):
    def test_keys_unique(self):
        keys = [p.key for p in platforms.PLATFORMS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_alias_resolution(self):
        for written, expected in [
            ("抖音", "douyin"), ("dy", "douyin"),
            ("b站", "bilibili"), ("B站", "bilibili"), ("bili", "bilibili"),
            ("小红书", "xiaohongshu"), ("xhs", "xiaohongshu"),
            ("油管", "youtube"), ("yt", "youtube"),
            ("视频号", "tencent"), ("虎扑", "hupu"),
        ]:
            with self.subTest(written=written):
                self.assertEqual(platforms.resolve(written), expected)

    def test_resolve_unknown(self):
        self.assertIsNone(platforms.resolve("tiktok"))
        self.assertIsNone(platforms.resolve(""))

    def test_parse_list_reports_bad(self):
        good, bad = platforms.parse_list("douyin, 抖音, tiktok, 不存在")
        self.assertEqual(good, ["douyin"])          # 抖音 是 douyin 的别名，去重
        self.assertEqual(bad, ["tiktok", "不存在"])

    def test_parse_list_chinese_comma(self):
        good, bad = platforms.parse_list("抖音，小红书")
        self.assertEqual(good, ["douyin", "xiaohongshu"])
        self.assertEqual(bad, [])

    def test_ordered_follows_declaration(self):
        # 传入顺序打乱，输出应按 PLATFORMS 的顺序
        self.assertEqual(platforms.ordered(["youtube", "douyin", "bilibili"]),
                         ["douyin", "bilibili", "youtube"])

    def test_bilibili_requires_tid_and_weibo_has_limit(self):
        self.assertIn(platforms.TID, platforms.BY_KEY["bilibili"].caps)
        self.assertEqual(platforms.BY_KEY["weibo"].title_max, 30)

    def test_hupu_has_no_schedule(self):
        # 这条是从 sau_cli.py 的 argparse 核出来的：虎扑没有 --schedule
        self.assertNotIn(platforms.SCHEDULE, platforms.BY_KEY["hupu"].caps)
        self.assertNotIn(platforms.COLLECTION, platforms.BY_KEY["hupu"].caps)


# ── 元数据 ──────────────────────────────────────────────────────
class TestMeta(unittest.TestCase):
    def test_split_tags_variants(self):
        self.assertEqual(meta.split_tags("a,b,c"), ["a", "b", "c"])
        self.assertEqual(meta.split_tags("a，b、c"), ["a", "b", "c"])
        self.assertEqual(meta.split_tags("#a #b"), ["a", "b"])
        self.assertEqual(meta.split_tags(["a", "a", "b"]), ["a", "b"])
        self.assertEqual(meta.split_tags(None), [])

    def test_filename_fallback_strips_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "20260927-AI视频生成-final.mp4"
            video.write_bytes(b"x")
            m = meta.load(video)
            self.assertNotIn("20260927", m.title)
            self.assertNotIn("final", m.title)
            self.assertIn("AI", m.title)

    def test_sidecar_json_wins_over_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "final.mp4"
            video.write_bytes(b"x")
            (Path(tmp) / "final.json").write_text(
                json.dumps({"title": "真标题", "desc": "简介",
                            "tags": ["AI", "科技"], "tid": 249}),
                encoding="utf-8")
            m = meta.load(video)
            self.assertEqual(m.title, "真标题")
            self.assertEqual(m.desc, "简介")
            self.assertEqual(m.tags, ["AI", "科技"])
            self.assertEqual(m.tid, 249)

    def test_meta_json_convention(self):
        # vp-pipeline 的约定：同目录 meta.json
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "final.mp4"
            video.write_bytes(b"x")
            (Path(tmp) / "meta.json").write_text(
                json.dumps({"title": "来自meta.json"}), encoding="utf-8")
            self.assertEqual(meta.load(video).title, "来自meta.json")

    def test_cli_overrides_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "final.mp4"
            video.write_bytes(b"x")
            (Path(tmp) / "final.json").write_text(
                json.dumps({"title": "文件标题"}), encoding="utf-8")
            self.assertEqual(meta.load(video, title="命令行标题").title, "命令行标题")

    def test_txt_first_line_is_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "final.mp4"
            video.write_bytes(b"x")
            (Path(tmp) / "final.txt").write_text("标题行\n简介第一行\n简介第二行",
                                                 encoding="utf-8")
            m = meta.load(video)
            self.assertEqual(m.title, "标题行")
            self.assertIn("简介第二行", m.desc)

    def test_adapt_title_truncates_weibo(self):
        plat = platforms.BY_KEY["weibo"]           # 上限 30
        long_title = "啊" * 50
        out, warns = meta.adapt_title(long_title, plat)
        self.assertLessEqual(len(out), 30)
        self.assertTrue(out.endswith("…"))
        self.assertEqual(len(warns), 1)

    def test_adapt_title_short_title_untouched(self):
        out, warns = meta.adapt_title("短标题", platforms.BY_KEY["douyin"])
        self.assertEqual(out, "短标题")
        self.assertEqual(warns, [])

    def test_adapt_title_pads_hupu_minimum(self):
        plat = platforms.BY_KEY["hupu"]            # 4~40
        out, warns = meta.adapt_title("好", plat)
        self.assertGreaterEqual(len(out), 4)
        self.assertEqual(len(warns), 1)

    def test_adapt_tags_cap(self):
        plat = platforms.Platform(key="x", label="X", tags_max=2)
        self.assertEqual(meta.adapt_tags(["a", "b", "c"], plat), ["a", "b"])


# ── argv 组装（最要紧的一组）────────────────────────────────────
class TestBuildArgv(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(sau=SauConfig())
        self.sau = Path("/fake/sau")
        self.video = Path("/tmp/v.mp4")
        self.m = meta.Meta(title="标题", desc="简介", tags=["AI", "科技"], tid=171)

    def argv(self, key, covers=None, m=None):
        return sau.build_upload_argv(
            self.sau, platforms.BY_KEY[key], "acct", self.video,
            m or self.m, covers or {}, self.cfg)

    def test_common_flags(self):
        a = self.argv("douyin")
        # 用 str(Path) 比较，否则 Windows 上 '/fake/sau' 会变成 '\fake\sau'
        self.assertEqual(a[0], str(self.sau))
        self.assertEqual(a[1:3], ["douyin", "upload-video"])
        self.assertIn("--account", a)
        self.assertIn("--file", a)
        self.assertIn("--title", a)
        self.assertIn("--tags", a)
        self.assertIn("--headless", a)
        self.assertIn("AI,科技", a)

    def test_bilibili_gets_tid(self):
        self.assertIn("--tid", self.argv("bilibili"))
        self.assertIn("171", self.argv("bilibili"))

    def test_login_argv_skips_headless_for_bilibili(self):
        """bilibili 的 login 子命令不认 --headless，多给它会被 argparse 打回。

        实测：`sau: error: unrecognized arguments: --headless`，
        连登录流程都进不去。所以按平台能力决定带不带这个参数。
        """
        b = sau.build_login_argv(self.sau, "bilibili", "我的B站")
        self.assertNotIn("--headless", b)
        self.assertNotIn("--headed", b)
        self.assertEqual(b[1:4], ["bilibili", "login", "--account"])
        # 别的平台照旧
        d = sau.build_login_argv(self.sau, "douyin", "我的抖音")
        self.assertIn("--headless", d)
        self.assertIn("--headed",
                      sau.build_login_argv(self.sau, "douyin", "x", headless=False))

    def test_tid_override_from_meta(self):
        m = meta.Meta(title="t", desc="d", tid=249)
        a = self.argv("bilibili", m=m)
        self.assertEqual(a[a.index("--tid") + 1], "249")

    def test_non_bilibili_never_gets_tid(self):
        for key in ("douyin", "weibo", "youtube", "hupu"):
            with self.subTest(key=key):
                self.assertNotIn("--tid", self.argv(key))

    def test_dual_cover_only_for_douyin_tencent(self):
        covers = {"thumbnail_landscape": Path("/c/43.png"),
                  "thumbnail_portrait": Path("/c/34.png")}
        for key in ("douyin", "tencent"):
            with self.subTest(key=key):
                a = self.argv(key, covers)
                self.assertIn("--thumbnail-landscape", a)
                self.assertIn("--thumbnail-portrait", a)
        # 小红书只支持单张
        a = self.argv("xiaohongshu", covers)
        self.assertNotIn("--thumbnail-landscape", a)

    def test_single_cover_uses_thumbnail(self):
        a = self.argv("xiaohongshu", {"thumbnail": Path("/c/34.png")})
        self.assertIn("--thumbnail", a)
        self.assertNotIn("--thumbnail-portrait", a)

    def test_cover_not_passed_when_platform_lacks_capability(self):
        # hupu 支持封面；构造一个没有 COVER 能力的平台来验证「不乱传」
        plat = platforms.Platform(key="x", label="X")
        a = sau.build_upload_argv(self.sau, plat, "acct", self.video,
                                  self.m, {"thumbnail": Path("/c/x.png")}, self.cfg)
        self.assertNotIn("--thumbnail", a)

    def test_schedule_only_where_supported(self):
        m = meta.Meta(title="t", desc="d", schedule="2026-03-24 21:30")
        for key in ("douyin", "kuaishou", "xiaohongshu", "bilibili", "tencent"):
            with self.subTest(key=key):
                self.assertIn("--schedule", self.argv(key, m=m))
        # 虎扑/微博/YouTube/百家号/支付宝都不支持定时
        for key in ("hupu", "weibo", "youtube", "baijiahao", "alipay"):
            with self.subTest(key=key):
                self.assertNotIn("--schedule", self.argv(key, m=m))

    def test_youtube_visibility_and_playlist(self):
        m = meta.Meta(title="t", desc="d", playlist="我的系列", visibility="unlisted")
        a = self.argv("youtube", m=m)
        self.assertIn("--visibility", a)
        self.assertEqual(a[a.index("--visibility") + 1], "unlisted")
        self.assertIn("--playlist", a)

    def test_collection_only_where_supported(self):
        m = meta.Meta(title="t", desc="d", collection="我的合集")
        self.assertIn("--collection", self.argv("tencent", m=m))
        self.assertNotIn("--collection", self.argv("hupu", m=m))

    def test_bilibili_desc_never_empty(self):
        # sau 里 bilibili 的 --desc 是 required=True，空字符串也会被 argparse 收下，
        # 但为空时传的是标题兜底 —— 验证这个兜底逻辑
        m = meta.Meta(title="只有标题", desc="")
        a = self.argv("bilibili", m=m)
        self.assertEqual(a[a.index("--desc") + 1], "只有标题")

    def test_no_shell_string_joining(self):
        # 标题里带引号和 $ 不能出问题（因为传的是 argv 数组，不拼 shell）
        m = meta.Meta(title='a"b$c`d', desc="d")
        a = self.argv("douyin", m=m)
        self.assertIn('a"b$c`d', a)

    def test_headed_flag(self):
        a = sau.build_upload_argv(self.sau, platforms.BY_KEY["douyin"], "acct",
                                  self.video, self.m, {}, self.cfg, headless=False)
        self.assertIn("--headed", a)
        self.assertNotIn("--headless", a)


# ── 账号发现 ────────────────────────────────────────────────────
class TestAccounts(unittest.TestCase):
    def test_discover_and_pick(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cookies = root / "cookies"
            cookies.mkdir()
            (cookies / "douyin_我的抖音.json").write_text("{}", encoding="utf-8")
            (cookies / "douyin_备用_账号.json").write_text("{}", encoding="utf-8")
            (cookies / "bilibili_我的B站.json").write_text("{}", encoding="utf-8")
            (cookies / "乱七八糟.json").write_text("{}", encoding="utf-8")

            cfg = Config(sau=SauConfig(root=root))
            found = sau.discover_accounts(cfg)
            self.assertEqual(sorted(found["douyin"]), sorted(["我的抖音", "备用_账号"]))
            self.assertEqual(found["bilibili"], ["我的B站"])
            # 认不出平台的应被忽略
            self.assertNotIn("乱七八糟", found)

            # 多个账号且没配 → 取名字最短的（可预测，不随机）
            # 「我的抖音」和「备用_账号」都是 4 个字，按 (长度, 码点) 排序取第一个
            self.assertEqual(
                sau.pick_account(cfg, "douyin", found),
                sorted(["我的抖音", "备用_账号"], key=lambda s: (len(s), s))[0])
            # 配了就用配的
            cfg.sau.accounts["douyin"] = "备用_账号"
            self.assertEqual(sau.pick_account(cfg, "douyin", found), "备用_账号")

    def test_account_file_naming(self):
        cfg = Config(sau=SauConfig(root=Path("/root")))
        self.assertEqual(sau.account_file(cfg, "douyin", "我的抖音").name,
                         "douyin_我的抖音.json")

    def test_no_cookies_dir(self):
        cfg = Config(sau=SauConfig(root=Path("/definitely/not/here")))
        self.assertEqual(sau.discover_accounts(cfg), {})


class TestLoginQrPath(unittest.TestCase):
    """二维码路径是实测踩出来的坑，加测试防回归。

    sau 的 utils/login_qrcode.py: build_login_qrcode_path() 生成的是
        {cookies}/{platform}_{account}_login_qrcode_{YYYYmmdd_HHMMSS}.png
    早期版本我们去找 cookies/qrcode.png，永远找不到。
    """

    def test_glob_matches_sau_naming(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(sau=SauConfig(root=Path(tmp)))
            cookies = cfg.sau.cookies_dir
            cookies.mkdir()
            # 模拟 sau 生成的文件（带时间戳）
            (cookies / "douyin_我的抖音_login_qrcode_20260927_131500.png").write_bytes(b"x")
            (cookies / "douyin_我的抖音.json").write_text("{}", encoding="utf-8")

            pattern = sau.qr_glob(cfg, "douyin", "我的抖音")
            import glob
            hits = glob.glob(pattern)
            self.assertEqual(len(hits), 1)
            self.assertIn("login_qrcode_20260927_131500", hits[0])

    def test_glob_matches_every_real_filename_pattern(self):
        """各平台二维码文件名**不统一**，glob 必须都能认出来。

        下面这些文件名不是编的，是把每个平台的登录真跑一遍之后
        从 sau 的日志里抄下来的（2026-09-27）。原来按
        `{platform}_{account}_login_qrcode_*.png` 硬编，结果
        小红书/快手/虎扑三个平台**静默失效** —— 网页上显示「没有二维码」，
        其实码就躺在 cookies 目录里。这种问题单测不写就发现不了，
        因为它不报错，只是"没有码"。
        """
        import glob
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(sau=SauConfig(root=Path(tmp)))
            cookies = cfg.sau.cookies_dir
            cookies.mkdir()
            real = {
                "douyin": ("我的抖音", "douyin_我的抖音_login_qrcode_20260927_142027.png"),
                "weibo": ("我的微博", "weibo_我的微博_login_qrcode_20260927_142255.png"),
                "alipay": ("我的支付宝生活号",
                           "alipay_我的支付宝生活号_login_qrcode_20260927_142333.png"),
                # ↓ 后缀多一截（sau 传了自定义 suffix）
                "xiaohongshu": ("我的小红书",
                                "xiaohongshu_我的小红书_xhs_login_qrcode_20260927_142037.png"),
                "kuaishou": ("我的快手",
                             "kuaishou_我的快手_ks_login_qrcode_20260927_142145.png"),
                # ↓ 连时间戳都没有
                "hupu": ("我的虎扑", "hupu_我的虎扑_qq_qrcode.png"),
            }
            for _, (_, name) in real.items():
                (cookies / name).write_bytes(b"x")

            for key, (acct, name) in real.items():
                hits = glob.glob(sau.qr_glob(cfg, key, acct))
                self.assertEqual(
                    [Path(h).name for h in hits], [name],
                    f"{key} 的二维码认不出来（实际文件名是 {name}）")

    def test_glob_stays_platform_strict(self):
        """放宽成前缀匹配之后，绝不能捞到别的平台的码。

        放宽和「平台严格」是一对张力：这条就是那个张力点的哨兵。
        """
        import glob
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(sau=SauConfig(root=Path(tmp)))
            cookies = cfg.sau.cookies_dir
            cookies.mkdir()
            (cookies / "douyin_我的抖音_login_qrcode_20260927_142027.png").write_bytes(b"a")
            (cookies / "xiaohongshu_我的小红书_xhs_login_qrcode_20260927_142037.png").write_bytes(b"b")
            # 名字里都带 douyin / qrcode，但不是这个平台的
            (cookies / "bilibili_douyin_qrcode.png").write_bytes(b"c")

            hits = [Path(h).name for h in glob.glob(sau.qr_glob(cfg, "douyin", "我的抖音"))]
            self.assertEqual(hits, ["douyin_我的抖音_login_qrcode_20260927_142027.png"])

    def test_glob_escapes_metacharacters_in_account(self):
        """账号名里如果有 glob 通配符，不能把别的文件捞进来。

        用 `[` 而不是 `*`：Windows 上文件名里不许出现 `*`，而 `[` 两边都合法
        且同样是 glob 元字符。
        """
        import glob
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(sau=SauConfig(root=Path(tmp)))
            cookies = cfg.sau.cookies_dir
            cookies.mkdir()
            # 真的那张：账号名里带一对中括号
            (cookies / "douyin_我的[抖音]_login_qrcode_1.png").write_bytes(b"a")
            # 陷阱：如果不转义，`[抖音]` 会被当成字符类，只吃一个字 ——
            # 于是匹配到这个、反而漏掉真的那张
            (cookies / "douyin_我的抖_login_qrcode_2.png").write_bytes(b"b")

            hits = [Path(h).name
                    for h in glob.glob(sau.qr_glob(cfg, "douyin", "我的[抖音]"))]
            self.assertEqual(hits, ["douyin_我的[抖音]_login_qrcode_1.png"])

    def test_newest_qr_picks_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(sau=SauConfig(root=Path(tmp)))
            cookies = cfg.sau.cookies_dir
            cookies.mkdir()
            old = cookies / "douyin_a_login_qrcode_20260927_120000.png"
            new = cookies / "douyin_a_login_qrcode_20260927_131500.png"
            old.write_bytes(b"old")
            new.write_bytes(b"new")
            os.utime(old, (time.time() - 3600, time.time() - 3600))
            os.utime(new, (time.time(), time.time()))

            got = sau.newest_qr(cfg, "douyin", "a", since=time.time() - 60)
            self.assertIsNotNone(got)
            self.assertEqual(got.name, new.name)

    def test_newest_qr_ignores_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(sau=SauConfig(root=Path(tmp)))
            cookies = cfg.sau.cookies_dir
            cookies.mkdir()
            stale = cookies / "douyin_a_login_qrcode_20260927_120000.png"
            stale.write_bytes(b"old")
            os.utime(stale, (time.time() - 3600, time.time() - 3600))
            # since 是刚才 → 一小时前的文件不算本次生成的
            self.assertIsNone(sau.newest_qr(cfg, "douyin", "a", since=time.time() - 60))

    def test_newest_qr_none_when_no_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(sau=SauConfig(root=Path(tmp)))
            self.assertIsNone(sau.newest_qr(cfg, "douyin", "a", since=0))

    def test_login_uses_stream_not_capture(self):
        """登录必须透传输出，否则终端上的二维码会被吞掉。

        这条用源码检查来守——因为「有没有捕获输出」很难用行为测。
        """
        import inspect
        src = inspect.getsource(sau.login)
        self.assertIn("run_stream", src)
        self.assertNotIn("run(", src.replace("run_stream(", ""))


# ── 封面 ────────────────────────────────────────────────────────
class TestCover(unittest.TestCase):
    def test_crop_wider_source(self):
        # 1920x1080 裁成 3:4 → 宽度收到 810（1080*3/4），高度不动
        self.assertEqual(cover._crop_for(1920, 1080, "3:4"), "crop=810:1080:555:0")

    def test_crop_taller_source(self):
        # 1080x1920 裁成 16:9 → 高度收到 608（1080/1.777）
        out = cover._crop_for(1080, 1920, "16:9")
        self.assertTrue(out.startswith("crop=1080:608:0:"), out)

    def test_crop_same_ratio(self):
        self.assertEqual(cover._crop_for(1920, 1080, "16:9"), "crop=1920:1080:0:0")

    def test_ratios_for(self):
        dual = cover.ratios_for("douyin", platforms.BY_KEY["douyin"].caps)
        self.assertEqual(dual, {"thumbnail_landscape": "4:3",
                                "thumbnail_portrait": "3:4"})
        self.assertEqual(cover.ratios_for("bilibili", platforms.BY_KEY["bilibili"].caps),
                         {"thumbnail": "16:9"})
        self.assertEqual(cover.ratios_for("weibo", platforms.BY_KEY["weibo"].caps),
                         {"thumbnail": "3:4"})

    def test_same_ratio_shared_across_platforms(self):
        # 抖音竖版和微博都是 3:4 → 应该指向同一个比例，调用方才能按比例去重
        a = cover.ratios_for("douyin", platforms.BY_KEY["douyin"].caps)
        b = cover.ratios_for("weibo", platforms.BY_KEY["weibo"].caps)
        self.assertEqual(a["thumbnail_portrait"], b["thumbnail"])


# ── 报表（中文对齐）─────────────────────────────────────────────
class TestReport(unittest.TestCase):
    def test_width_counts_cjk_as_two(self):
        self.assertEqual(report.width("abc"), 3)
        self.assertEqual(report.width("抖音"), 4)
        self.assertEqual(report.width("抖音ab"), 6)

    def test_table_columns_line_up(self):
        rows = [
            {"p": "抖音", "r": "✓ 已发布"},
            {"p": "YouTube", "r": "✗ 失败"},
        ]
        text = report.table(rows, [("p", "平台", "left"), ("r", "结果", "left")])
        lines = text.splitlines()
        # 表头、分隔线、两行数据 —— 每行的显示宽度必须一致
        widths = {report.width(ln) for ln in lines}
        self.assertEqual(len(widths), 1, f"表格没对齐：{widths}\n{text}")

    def test_truncate_respects_width(self):
        self.assertEqual(report.truncate("抖音抖音抖音", 4), "抖…")

    def test_summarize(self):
        got = report.summarize([{"status": "ok"}, {"status": "ok"},
                                {"status": "fail"}, {"status": "skip"}])
        self.assertEqual((got["ok"], got["fail"], got["skip"], got["total"]),
                         (2, 1, 1, 4))

    def test_human_duration(self):
        self.assertEqual(report.human_duration(45), "45.0 秒")
        self.assertEqual(report.human_duration(125), "2 分 5 秒")


# ── 状态 ────────────────────────────────────────────────────────
class TestState(unittest.TestCase):
    def test_roundtrip_and_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "v.mp4"
            video.write_bytes(b"hello")
            store = state.Store(Path(tmp) / "rec.json")
            rec = store.get(video)
            self.assertFalse(rec.is_ok("douyin"))
            rec.mark("douyin", state.OK)
            store.put(video, rec)
            store.save()

            again = state.Store(Path(tmp) / "rec.json")
            self.assertTrue(again.get(video).is_ok("douyin"))
            self.assertFalse(again.get(video).is_ok("bilibili"))

    def test_key_changes_when_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "v.mp4"
            video.write_bytes(b"a")
            k1 = state.video_key(video)
            time.sleep(0.01)
            video.write_bytes(b"bb")            # 大小变了
            os.utime(video, (time.time() + 5, time.time() + 5))
            self.assertNotEqual(k1, state.video_key(video))

    def test_forget(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "v.mp4"
            video.write_bytes(b"x")
            store = state.Store(Path(tmp) / "rec.json")
            rec = store.get(video)
            rec.mark("douyin", state.OK)
            store.put(video, rec)
            self.assertTrue(store.forget(video))
            self.assertFalse(store.get(video).is_ok("douyin"))


# ── cookie 过期估算 ─────────────────────────────────────────────
class TestCookieExpiry(unittest.TestCase):
    def _write(self, tmp, cookies):
        p = Path(tmp) / "c.json"
        p.write_text(json.dumps({"cookies": cookies, "origins": []}), encoding="utf-8")
        return p

    def test_prefers_session_cookie(self):
        from vp_publish import doctor
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            p = self._write(tmp, [
                {"name": "some_tracking", "expires": now + 365 * 86400},
                {"name": "sessionid", "expires": now + 7 * 86400},
            ])
            exp, why = doctor.read_cookie_expiry(p)
            self.assertIsNotNone(exp)
            self.assertIn("sessionid", why)
            self.assertAlmostEqual((exp - now) / 86400, 7, delta=0.1)

    def test_ignores_expired_and_session_cookies(self):
        from vp_publish import doctor
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            p = self._write(tmp, [
                {"name": "dead", "expires": now - 86400},
                {"name": "session_only", "expires": -1},
            ])
            exp, why = doctor.read_cookie_expiry(p)
            self.assertIsNone(exp)

    def test_bad_file_does_not_raise(self):
        from vp_publish import doctor
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.json"
            p.write_text("not json", encoding="utf-8")
            exp, why = doctor.read_cookie_expiry(p)
            self.assertIsNone(exp)


# ── 平台额外依赖（biliup / 真 Chrome）────────────────────────────
class TestDoctorRequirements(unittest.TestCase):
    """光有 sau 还不够的那些平台，体检必须说得出来。

    这两条是**实测挖出来的**，不是照文档抄的：
      · B站：sau 会从 GitHub Releases 自动下载 biliup 的二进制
        （`uploader/bilibili_uploader/runtime.py`），所以它需要能连 GitHub。
      · YouTube：sau 的上传把 `channel="chrome"` 写死了，chromium 顶不上。

    踩过的坑：这两件事原来只在**上传那一刻**才暴露，报的还是
    playwright 的英文异常；体检里一片绿。用户根本不知道该去装什么。
    """

    def _env(self, **kw):
        from vp_publish import doctor
        return doctor.Environment(**kw)

    def test_extra_requirements_are_declared(self):
        self.assertEqual(platforms.BY_KEY["bilibili"].requires, (platforms.BILIUP,))
        self.assertEqual(platforms.BY_KEY["youtube"].requires, (platforms.CHROME,))

    def test_bilibili_needs_biliup(self):
        from vp_publish import doctor
        plat = platforms.BY_KEY["bilibili"]
        self.assertEqual(doctor.missing_requirements(plat, self._env()), ["biliup"])

    def test_biliup_present_clears_it(self):
        from vp_publish import doctor
        plat = platforms.BY_KEY["bilibili"]
        env = self._env(biliup=Path("/x/biliup"))
        self.assertEqual(doctor.missing_requirements(plat, env), [])

    def test_youtube_needs_real_chrome(self):
        from vp_publish import doctor
        plat = platforms.BY_KEY["youtube"]
        self.assertEqual(doctor.missing_requirements(plat, self._env()), ["chrome"])
        env = self._env(chrome=Path("/x/chrome"))
        self.assertEqual(doctor.missing_requirements(plat, env), [])

    def test_other_platforms_are_never_blocked_by_extra_deps(self):
        """没有额外依赖的平台不能被误报 —— 假阳性比不报还坏。"""
        from vp_publish import doctor
        env = self._env()
        for key in ("douyin", "xiaohongshu", "kuaishou", "weibo",
                    "alipay", "hupu", "baijiahao", "tencent"):
            self.assertEqual(doctor.missing_requirements(platforms.BY_KEY[key], env),
                             [], f"{key} 不该被额外依赖挡住")

    def test_missing_beats_not_logged_in(self):
        """「缺依赖」要排在「还没登录」前面 —— 登录一百次也发不出去。"""
        from vp_publish import doctor
        h = doctor.PlatformHealth(key="bilibili", label="B站",
                                  missing=["biliup"], logged_in=False)
        self.assertEqual(h.status, report.FAIL)

    def test_biliup_path_rule_matches_sau(self):
        """biliup 的落点规则必须和 sau 的 runtime.py 一致，
        否则「有没有下下来」这个判断本身就是错的。
        """
        from vp_publish import doctor
        key = doctor._biliup_platform_key()
        self.assertRegex(key, r"^[a-z0-9_]+-[a-z0-9_]+$")
        # 别名映射要跟 sau 一样（amd64/x64 → x86_64，arm64 → aarch64）
        self.assertEqual(doctor._biliup_platform_key(), key)

    def test_render_and_dict_say_what_is_missing(self):
        from vp_publish import doctor
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(sau=SauConfig(root=Path(tmp)))
            env = doctor.Environment()          # 故意造一个「什么都没有」的环境
            healths = doctor.inspect_platforms(cfg, env)
            text = doctor.render(cfg, env, healths)
            self.assertIn("缺 biliup", text)
            self.assertIn("缺真 Chrome", text)
            self.assertIn("GitHub", text)       # 底部长解释也要在

            data = doctor.to_dict(cfg, env, healths)
            by = {p["key"]: p for p in data["platforms"]}
            self.assertEqual(by["bilibili"]["missing"], ["biliup"])
            self.assertEqual(by["bilibili"]["requires"], ["biliup"])
            self.assertEqual(by["youtube"]["missing"], ["chrome"])
            # 缺依赖的平台不能算「就绪」
            self.assertNotEqual(by["bilibili"]["status"], report.OK)


# ── 配置 ────────────────────────────────────────────────────────
class TestConfig(unittest.TestCase):
    def test_missing_file_uses_defaults(self):
        from vp_publish import config as config_mod
        cfg = config_mod.load(Path("/definitely/not/here.json"))
        self.assertEqual(cfg.tid, 171)
        self.assertTrue(cfg.cover)

    def test_partial_override(self):
        from vp_publish import config as config_mod
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.json"
            p.write_text(json.dumps({"tid": 249, "default_tags": ["AI"],
                                     "sau": {"root": "/opt/sau"}}), encoding="utf-8")
            cfg = config_mod.load(p)
            self.assertEqual(cfg.tid, 249)
            self.assertEqual(cfg.default_tags, ["AI"])
            self.assertEqual(cfg.sau.root, Path("/opt/sau"))
            # bin 应跟着 root 推导
            self.assertEqual(cfg.sau.bin, Path("/opt/sau/.venv/bin/sau"))

    def test_broken_json_falls_back(self):
        from vp_publish import config as config_mod
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.json"
            p.write_text("{ broken", encoding="utf-8")
            cfg = config_mod.load(p)
            self.assertEqual(cfg.tid, 171)

    def test_watch_dirs_parsed(self):
        from vp_publish import config as config_mod
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.json"
            p.write_text(json.dumps({"watch_dirs": ["~/vp/videos", "", "  "]}),
                         encoding="utf-8")
            self.assertEqual(config_mod.load(p).watch_dirs, ["~/vp/videos"])

    def test_template_is_loadable_and_clean(self):
        """模板必须能原样被 load 回来，而且不能混进伪键。

        踩过的坑：accounts 里原本塞了一句 "_说明"，它会被当成一个叫
        「_说明」的平台账号解析进去。
        """
        from vp_publish import config as config_mod
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.json"
            p.write_text(json.dumps(config_mod.TEMPLATE, ensure_ascii=False),
                         encoding="utf-8")
            cfg = config_mod.load(p)
            self.assertNotIn("_说明", cfg.sau.accounts)
            self.assertEqual(set(cfg.sau.accounts), {"douyin", "xiaohongshu", "bilibili"})
            self.assertEqual(cfg.watch_dirs, ["~/vp/videos"])
            # 模板写出来的文件也要能被 write_template → load 走通
            out = Path(tmp) / "sub" / "config.json"
            config_mod.write_template(out)
            self.assertTrue(config_mod.load(out).cover)


# ── watch 守护模式 ───────────────────────────────────────────────
class TestWatchReady(unittest.TestCase):
    """ready_to_publish 的稳定性判定。

    这是 watch 模式最要紧的一条逻辑：正在写入的视频会先出现一个半截文件，
    这时候发出去就是一条坏视频（而且要等平台审核失败才发现）。
    所以「文件存在」不够，必须「连续 N 轮大小不变」。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _make(self, name="v.mp4", size=1000) -> Path:
        p = self.dir / name
        p.write_bytes(b"x" * size)
        return p

    def test_zero_byte_is_not_ready(self):
        from vp_publish import watch
        p = self._make(size=0)
        ok, why = watch.ready_to_publish(p, watch.Seen())
        self.assertFalse(ok)
        self.assertIn("0 字节", why)

    def test_growing_file_never_ready_until_stable(self):
        from vp_publish import watch
        p = self._make(size=1000)
        seen = watch.Seen()

        ok, why = watch.ready_to_publish(p, seen)
        self.assertFalse(ok, "第一次见到就该等下一轮")
        self.assertIn("还在变", why)

        # 模拟「流水线还在写」：大小又变了 → 计数必须重置
        p.write_bytes(b"x" * 2000)
        ok, why = watch.ready_to_publish(p, seen)
        self.assertFalse(ok)
        self.assertEqual(seen.stable_rounds, 1, "大小变了要把稳定计数清零")
        self.assertIn("2000", why)

        # 大小不动了 → 稳定 2 轮 → 可以发
        ok, why = watch.ready_to_publish(p, seen)
        self.assertTrue(ok, f"应该就绪了，实际：{why}")
        self.assertEqual(why, "")

    def test_min_stable_rounds_is_configurable(self):
        from vp_publish import watch
        p = self._make()
        seen = watch.Seen()
        self.assertFalse(watch.ready_to_publish(p, seen, min_stable_rounds=3)[0])
        ok, why = watch.ready_to_publish(p, seen, min_stable_rounds=3)
        self.assertFalse(ok)
        self.assertIn("2/3", why)
        self.assertTrue(watch.ready_to_publish(p, seen, min_stable_rounds=3)[0])

    def test_incomplete_sidecar_blocks(self):
        """视频写完了，但旁边的 .json 元数据还是半截 → 再等等。

        不等的话会拿半截 JSON 去解析，标题/标签就丢了。
        """
        from vp_publish import watch
        p = self._make()
        (self.dir / "v.json").write_text('{"title": "还没写完', encoding="utf-8")
        seen = watch.Seen()
        watch.ready_to_publish(p, seen)
        ok, why = watch.ready_to_publish(p, seen)
        self.assertFalse(ok)
        self.assertIn("不是完整 JSON", why)

        # 流水线写完了 → 放行
        (self.dir / "v.json").write_text('{"title": "写完了"}', encoding="utf-8")
        ok, _ = watch.ready_to_publish(p, seen)
        self.assertTrue(ok)

    def test_missing_file_reports_gracefully(self):
        from vp_publish import watch
        ok, why = watch.ready_to_publish(self.dir / "gone.mp4", watch.Seen())
        self.assertFalse(ok)
        self.assertIn("stat 失败", why)


class TestWatchScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_skips_hidden_and_partials(self):
        from vp_publish import watch
        (self.dir / "good.mp4").write_bytes(b"x")
        (self.dir / "movie.MP4").write_bytes(b"x")          # 大写后缀要认
        (self.dir / ".hidden.mp4").write_bytes(b"x")
        (self.dir / "writing.mp4.part").write_bytes(b"x")
        (self.dir / "downloading.mp4.crdownload").write_bytes(b"x")
        (self.dir / "note.txt").write_bytes(b"x")
        names = [p.name for p in watch.scan([self.dir])]
        self.assertEqual(names, ["good.mp4", "movie.MP4"])

    def test_recursive_flag(self):
        from vp_publish import watch
        sub = self.dir / "2026-09-27"
        sub.mkdir()
        (sub / "deep.mp4").write_bytes(b"x")
        self.assertEqual(len(watch.scan([self.dir], recursive=True)), 1)
        self.assertEqual(len(watch.scan([self.dir], recursive=False)), 0)

    def test_missing_dir_is_not_fatal(self):
        from vp_publish import watch
        self.assertEqual(watch.scan([self.dir / "nope"]), [])


class TestWatchState(unittest.TestCase):
    def test_roundtrip(self):
        from vp_publish import watch
        st = watch.WatchState(started_at=1.5, baseline_done=True,
                              published={"/a.mp4": "ok@2026-09-27 10:00"})
        st.seen["/a.mp4"] = watch.Seen(size=123, stable_rounds=2, logged=True,
                                       baseline=True)
        back = watch.WatchState.from_json(st.to_json())
        self.assertTrue(back.baseline_done)
        self.assertEqual(back.published["/a.mp4"], "ok@2026-09-27 10:00")
        self.assertEqual(back.seen["/a.mp4"].size, 123)
        self.assertTrue(back.seen["/a.mp4"].logged)
        self.assertTrue(back.seen["/a.mp4"].baseline)

    def test_register_keeps_first_sighting(self):
        """baseline 由**第一次**见到它时决定，之后不再变。

        这条是防回归的核心：库存保护如果写成全局开关，就会因为
        「文件要 2 轮才算写完」而失效 —— 第二轮库存全变成就绪时，
        全局开关已经翻过去了，于是整批发出去。
        """
        from vp_publish import watch
        st = watch.WatchState()
        seen = watch.register(st, Path("/tmp/old.mp4"), baseline=True)
        self.assertTrue(seen.baseline)
        # 第二轮再登记（这次 baseline=False）不能把它洗白
        again = watch.register(st, Path("/tmp/old.mp4"), baseline=False)
        self.assertIs(again, seen)
        self.assertTrue(again.baseline)

        new = watch.register(st, Path("/tmp/new.mp4"), baseline=False)
        self.assertFalse(new.baseline)

    def test_forget_drops_both_records(self):
        from vp_publish import watch
        st = watch.WatchState()
        p = Path("/tmp/x.mp4")
        watch.register(st, p, baseline=True)
        st.published[watch.key_for(p)] = "ok@t"
        self.assertTrue(watch.forget(st, p))
        self.assertNotIn(watch.key_for(p), st.seen)
        self.assertNotIn(watch.key_for(p), st.published)
        self.assertFalse(watch.forget(st, p), "清第二次应该报「没有记录」")

    def test_store_persists_and_survives_garbage(self):
        from vp_publish import watch
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "watch.json"
            store = watch.Store(p)
            self.assertFalse(store.state.baseline_done)
            store.state.baseline_done = True
            store.state.published["/x.mp4"] = "ok@t"
            store.save()

            again = watch.Store(p)
            self.assertTrue(again.state.baseline_done)
            self.assertIn("/x.mp4", again.state.published)

            p.write_text("{ 坏掉的 json", encoding="utf-8")
            self.assertFalse(watch.Store(p).state.baseline_done)

    def test_from_json_tolerates_wrong_types(self):
        from vp_publish import watch
        st = watch.WatchState.from_json({"seen": {"a": "不是字典"},
                                         "published": ["不是字典"]})
        self.assertEqual(st.seen, {})
        self.assertEqual(st.published, {})

    def test_cli_reference_is_valid(self):
        """cli.py 里写的是 watch.Store —— 守住这个名字，别再对不上。"""
        from vp_publish import cli, watch
        self.assertTrue(callable(watch.Store))
        self.assertIs(watch.WatchStore, watch.Store)
        self.assertIn("watch.Store(", inspect.getsource(cli))

    def test_watch_has_no_force_flag(self):
        """watch 不该有 --force。

        守护进程里 --force 意味着「每轮都重发一遍」，是个纯粹的脚枪。
        要重发某一条，用 `vp-publish forget <视频>`。
        """
        from vp_publish import cli
        with self.assertRaises(SystemExit):
            cli.build_sub_parser().parse_args(["watch", "--force"])
        src = inspect.getsource(cli.cmd_watch)
        self.assertNotIn("args.force", src)

    def test_watch_dry_run_prints_argv(self):
        """试运行必须能看见要执行什么，否则等于没试。"""
        from vp_publish import cli
        src = inspect.getsource(cli.cmd_watch)
        self.assertIn("res['argv']", src)


class TestWatchDescribe(unittest.TestCase):
    def test_round_line_mentions_only_nonzero(self):
        from vp_publish import watch
        line = watch.describe_round(3, 5, ready=1, published=1, failed=0, skipped=0)
        self.assertIn("第 3 轮", line)
        self.assertIn("扫描 5", line)
        self.assertIn("已发 1", line)
        self.assertNotIn("失败", line)


class TestWatchNoPlatform(unittest.TestCase):
    """「一个平台都没登录」时 watch 该怎么办。

    两件事必须同时成立，而且它们是连在一起的：
      ① 不能退出 —— 常驻服务会变成 systemd 重启死循环（cookie 全过期时同样）；
      ② 更不能「假装发了」—— 目标为空时如果算成功，视频会被静默标记成
         「已发布」永久跳过，而且毫无痕迹。
    """

    def test_publish_one_video_with_no_targets_is_not_ok(self):
        from vp_publish import cli
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("XDG_STATE_HOME")
            os.environ["XDG_STATE_HOME"] = tmp
            try:
                out = cli.publish_one_video(
                    Path(tmp) / "v.mp4", Config(), targets=[], available={},
                    sau_path=None, opts=cli.Options(quiet=True),
                    store=state.Store())
            finally:
                if old is None:
                    os.environ.pop("XDG_STATE_HOME", None)
                else:
                    os.environ["XDG_STATE_HOME"] = old
        self.assertFalse(out["ok"], "目标为空绝不能算成功")
        self.assertEqual(out["attempted"], 0)
        self.assertEqual(out["statuses"], [])

    def test_watch_discovers_platforms_inside_the_loop(self):
        """平台探测必须在 while 循环里 —— 扫码登录后不该需要重启服务。"""
        from vp_publish import cli
        src = inspect.getsource(cli.cmd_watch)
        self.assertIn("while True", src)
        self.assertGreater(
            src.index("discover_accounts"), src.index("while True"),
            "平台探测写在了循环外面：登录后必须重启服务才能生效")
        self.assertNotIn("守护起来也没用", src,
                         "没有平台时不该退出（常驻服务会重启死循环）")

    def test_watch_does_not_mark_state_without_statuses(self):
        from vp_publish import cli
        src = inspect.getsource(cli.cmd_watch)
        self.assertIn('not out["statuses"]', src,
                      "缺少「什么都没发就不记状态」的兜底")


# ── 网页扫码登录 ─────────────────────────────────────────────────
class TestLoginWeb(unittest.TestCase):
    """login-web 里最容易写错的三件事：日志清理、「认哪张二维码」、
    以及换码/换平台时那张旧码该不该留。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "cookies").mkdir()
        self.cfg = Config(sau=SauConfig(root=self.root))
        # 别往用户家里写日志
        self._cache = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = str(self.root / "cache")

    def tearDown(self):
        if self._cache is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = self._cache
        self.tmp.cleanup()

    def _hub(self):
        """造一个**能真的 spawn 起来**的假 sau（直接用当前解释器）。

        不然 Hub 会在「没找到 sau」那一步就返回，后面的逻辑一行都走不到 ——
        这正是 test_start_rejects_unknown_platform 最初失败的原因。
        用 sys.executable 是为了跨平台：Windows 上没法执行 `#!/bin/sh` 脚本。
        """
        from vp_publish import loginweb
        cfg = Config(sau=SauConfig(root=self.root, bin=Path(sys.executable)))
        return loginweb.Hub(cfg)

    def _qr(self, plat, acct, stamp="20260101_000001", body=b"x"):
        p = self.root / "cookies" / f"{plat}_{acct}_login_qrcode_{stamp}.png"
        p.write_bytes(body)
        return p

    def test_strip_noise_drops_qr_blocks_keeps_real_lines(self):
        """日志区不能被终端二维码刷满 —— 否则真错误反而看不见。"""
        from vp_publish import loginweb
        raw = ("\x1b[32mINFO\x1b[0m: 二维码已经准备好啦\n"
               "████████████████████████████████████\n"
               "▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀▄▀\n"
               "ERROR: 登录失败：网络超时\n")
        out = loginweb._strip_noise(raw)
        self.assertIn("二维码已经准备好啦", out)
        self.assertIn("ERROR: 登录失败：网络超时", out)
        self.assertNotIn("█", out)
        self.assertNotIn("\x1b", out, "ANSI 转义也要清掉")

    def test_qr_lookup_is_platform_strict(self):
        """只认当前平台自己的二维码文件名。

        这是实测踩过的坑：sau.newest_qr() 会兜底 glob 整个 cookies 目录的
        *.png，在网页里就会让你扫到**上一个平台的二维码** ——
        页面看起来完全正常，扫了就是登不上。
        """
        from vp_publish import loginweb
        t0 = time.time()
        mine = self.root / "cookies" / "douyin_我的抖音_login_qrcode_20260101_000001.png"
        mine.write_bytes(b"mine")
        # 另一个平台的码更新 —— 绝不能被抓过来
        other = self.root / "cookies" / "bilibili_我的B站_login_qrcode_20260101_000002.png"
        other.write_bytes(b"other")
        os.utime(mine, (t0, t0))
        os.utime(other, (t0 + 5, t0 + 5))

        hub = loginweb.Hub(self.cfg)
        got = hub._qr_for("douyin", "我的抖音", t0 - 1)
        self.assertEqual(got, mine, "抓到了别的平台的二维码")

    def test_qr_lookup_ignores_stale_files(self):
        from vp_publish import loginweb
        old = self.root / "cookies" / "douyin_a_login_qrcode_20200101_000000.png"
        old.write_bytes(b"old")
        t_old = time.time() - 86400
        os.utime(old, (t_old, t_old))
        hub = loginweb.Hub(self.cfg)
        self.assertIsNone(hub._qr_for("douyin", "a", time.time() - 10),
                          "上一次登录留下的旧二维码不该被复用")

    def test_page_makes_no_external_requests(self):
        """页面必须零外部请求 —— 板子可能根本连不上外网。

        这也是不复用 lilyco-gui 的原因：它从公网 CDN 拉 layui，
        拉不到就整页失灵，而且事件绑定全在 CDN 回调里 → 按钮点了没反应还不报错。
        """
        from vp_publish import loginweb
        page = loginweb.PAGE
        for bad in ("http://", "https://", "//cdn", "cdn."):
            self.assertNotIn(bad, page, f"页面里有外部引用：{bad}")
        self.assertIn("<img", page)
        self.assertNotIn("layui", page.lower())

    def test_idle_state_shape(self):
        from vp_publish import loginweb
        st = loginweb.Hub(self.cfg).state()
        self.assertEqual(st["status"], "idle")
        self.assertFalse(st["platform"])
        self.assertEqual(len(st["platforms"]), len(platforms.PLATFORMS))
        self.assertIn("refresh_after", st)

    def test_start_rejects_unknown_platform(self):
        from vp_publish import loginweb
        hub = loginweb.Hub(self.cfg)
        out = hub.start("不存在的平台")
        self.assertFalse(out["ok"])
        self.assertIn("认不出", out["error"])

    def test_start_rejects_unknown_platform_even_without_sau(self):
        """平台名是**调用方的入参错误**，不该被「本机没装 sau」盖住。

        顺序反过来的话，在一台没装 sau 的机器上传个错平台名，
        得到的是「没找到 sau」——驴唇不对马嘴，排查时会被带偏。

        注意**不要**靠「这台机器上有没有 sau」来构造前提：第一版这么写，
        本机（没装 sau）绿、板子（venv 在 PATH 里）红。直接改属性，
        跟环境无关。
        """
        from vp_publish import loginweb
        hub = loginweb.Hub(self.cfg)
        hub.sau_path = None                       # 装成「这台机器上没 sau」
        hub.problem = "没找到 sau（social-auto-upload）。"
        out = hub.start("不存在的平台")
        self.assertFalse(out["ok"])
        self.assertIn("认不出", out["error"])
        self.assertNotIn("sau", out["error"])

    def test_same_platform_keeps_old_qr_while_swapping(self):
        """同一个平台换码时，旧码要留着。

        chromium 起来要十几秒，这段时间页面如果没码，用户会以为坏了。
        """
        from vp_publish import loginweb
        hub = self._hub()
        qr = self._qr("douyin", "我的抖音")
        hub.session.platform = "douyin"
        hub.session.account = "我的抖音"
        hub.session.qr_path = qr
        hub.session.qr_mtime = 123.0
        try:
            out = hub.start("douyin")
            self.assertTrue(out["ok"])
            self.assertTrue(out["reused"], "同一个平台应该算「换一张码」")
            self.assertEqual(hub.session.qr_path, qr, "换码期间旧码被清掉了")
            self.assertEqual(hub.session.qr_mtime, 123.0)
        finally:
            hub.shutdown()

    def test_switching_platform_clears_old_qr(self):
        """换平台**绝不能**留着上一个平台的码。

        否则页面显示着抖音的二维码，实际在登小红书 —— 扫了就是登不上，
        而且零报错。这是本模块最危险的一个坑。

        （用小红书而不是 B站：B站 现在会被「必须真终端」挡在前面，
        测不到这里的换码逻辑。）
        """
        from vp_publish import loginweb
        hub = self._hub()
        hub.session.platform = "douyin"
        hub.session.account = "我的抖音"
        hub.session.qr_path = self._qr("douyin", "我的抖音")
        hub.session.qr_mtime = 123.0
        try:
            out = hub.start("xiaohongshu")
            self.assertTrue(out["ok"], out.get("error"))
            self.assertFalse(out["reused"])
            self.assertIsNone(hub.session.qr_path, "旧平台的码被留下来了！")
            self.assertEqual(hub.session.qr_mtime, 0.0)
        finally:
            hub.shutdown()

    def test_page_warns_about_a_stale_qr(self):
        """页面要能识别「这张码已经过期了」，并且有地方说这句话。

        实测踩到过：页面打开时码已经过了 114 秒，原来会当成正常码显示出来，
        用户扫了就是「该二维码已过期」——正是要修的那个体验。
        """
        from vp_publish import loginweb
        page = loginweb.PAGE
        self.assertIn('id="qstale"', page, "没有过期提示的位置")
        self.assertIn("已经过期", page)
        # 判断依据必须是服务端给的 qr_age，不能靠页面自己数秒
        self.assertIn("qr_age", page)
        self.assertIn("classList", page)

    def test_start_refuses_platforms_that_need_a_real_terminal(self):
        """网页里注定做不成的平台，要说清楚该怎么办，而不是让人白点一次。

        bilibili 走 biliup，硬性要求 sys.stdin/stdout 都是 tty，
        而网页起子进程时 stdin 是 DEVNULL —— 永远不可能成。
        """
        from vp_publish import loginweb
        hub = self._hub()
        out = hub.start("bilibili")
        self.assertFalse(out["ok"])
        self.assertIn("真终端", out["error"])
        self.assertIn("vp-publish login bilibili", out["error"])
        self.assertIsNone(hub.session.proc, "不该真的去拉进程")

    def test_http_token_and_routes(self):
        """没 token 的 API 请求要被挡住；页面本身要能打开。"""
        import http.server
        import threading
        import urllib.error
        import urllib.request
        from vp_publish import loginweb

        hub = loginweb.Hub(self.cfg)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                              loginweb._make_handler(hub, "s3cret"))
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}"
        try:
            # 页面：不需要 token
            with urllib.request.urlopen(base + "/", timeout=5) as r:
                self.assertEqual(r.status, 200)
                self.assertIn("扫码登录", r.read().decode("utf-8"))
            # API：没 token → 401
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/api/state", timeout=5)
            self.assertEqual(cm.exception.code, 401)
            # API：带 token → 200
            with urllib.request.urlopen(base + "/api/state?k=s3cret", timeout=5) as r:
                data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            # 二维码还没生成 → 404（不是 500）
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/api/qr.png?k=s3cret", timeout=5)
            self.assertEqual(cm.exception.code, 404)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
