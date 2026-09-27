"""平台能力矩阵。

一键发多平台，90% 的坑都在这里：**每个平台的规矩都不一样**。
标题长度、封面比例、必填参数、能不能定时、能不能进合集……
sau 把每个平台的实现藏在各自的 uploader 里，对外没有一张统一的表。
这里把差异显式列出来，编排层才知道该给谁多传什么、该给谁裁短标题。

数据来源：sau 的 docs/CLI.md（实测对齐，2026-09）。
凡是文档里没写的长度限制，一律留 `None`（不裁剪），
绝不用「大概是 30 个字吧」这种猜测去悄悄改用户标题。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 能力位
COVER = "cover"                    # 支持单张封面（3:4 竖版）
COVER_DUAL = "cover_dual"          # 支持横版 + 竖版两张封面
SCHEDULE = "schedule"              # 支持定时发布
COLLECTION = "collection"          # 支持指定合集
PLAYLIST = "playlist"              # 支持指定播放列表（YouTube）
VISIBILITY = "visibility"          # 支持可见性（YouTube）
TID = "tid"                        # 必须传分区 id（B站）
NOTE = "note"                      # 支持图文（本次只发视频，占位）

# 下面这些能力是**照着 sau_cli.py 的 argparse 逐个核出来的**，不是照抄文档——
# 文档和实现对不上是常态。核对时间：2026-09-27。
# 核对方法：grep -n "add_argument" sau_cli.py，逐平台比对。
#
# 几个容易踩的点：
#   · bilibili 的 --desc 和 --tid 都是 required=True（不是可选！）
#   · hupu 既没有 --schedule 也没有 --collection
#   · weibo / alipay 有 --collection 但**没有** --schedule
#   · douyin / tencent 同时支持 --thumbnail-landscape 和 --thumbnail-portrait
#   · **bilibili 的 login 子命令只认 --account**，多给一个 --headless 会被
#     argparse 直接打回（`unrecognized arguments: --headless`），
#     而且它要求 sys.stdin/stdout 都是 tty —— 也就是说**扫码网页做不了它**。


@dataclass(frozen=True)
class Platform:
    key: str                       # sau 子命令名，唯一标识
    label: str                     # 中文名（给人看）
    title_max: int | None = None   # 标题上限（字符数）；None = 平台自管
    title_min: int = 0             # 标题下限
    tags_max: int | None = None    # 标签个数上限
    caps: frozenset[str] = field(default_factory=frozenset)
    # qr=扫码（网页里能用）；browser=浏览器里输账号（网页里能起，但得有人操作）；
    # terminal=**必须在真终端里跑**（bilibili），网页里点了也是白点
    login: str = "qr"
    accepts_headless: bool = True  # login 子命令认不认 --headless / --headed
    needs_proxy: bool = False      # 是否需要走代理才能连上
    note: str = ""                 # 给人看的补充说明


# ── 全平台清单（顺序 = 默认发布顺序，先发国内的）─────────────────
PLATFORMS: tuple[Platform, ...] = (
    Platform(
        key="douyin", label="抖音",
        caps=frozenset({COVER, COVER_DUAL, SCHEDULE, COLLECTION, NOTE}),
        login="qr",
    ),
    Platform(
        key="xiaohongshu", label="小红书",
        caps=frozenset({COVER, SCHEDULE, NOTE}),
        login="qr",
    ),
    Platform(
        key="kuaishou", label="快手",
        caps=frozenset({COVER, SCHEDULE, COLLECTION, NOTE}),
        login="qr",
    ),
    Platform(
        key="bilibili", label="B站",
        caps=frozenset({COVER, SCHEDULE, TID}),
        login="terminal",          # biliup 要真终端，网页里做不了
        accepts_headless=False,    # login 子命令只认 --account
        note="必须指定分区（--tid），默认 171=科技·人工智能；"
             "登录要在真终端里跑（sau 用的 biliup 要求 tty）",
    ),
    Platform(
        key="tencent", label="视频号",
        caps=frozenset({COVER, COVER_DUAL, SCHEDULE, COLLECTION}),
        login="qr",
    ),
    Platform(
        key="weibo", label="微博",
        title_max=30,
        caps=frozenset({COVER, COLLECTION}),
        login="qr",
        note="标题最多 30 字；封面建议 <5MB；不支持定时发布",
    ),
    Platform(
        key="hupu", label="虎扑",
        title_max=40, title_min=4,
        caps=frozenset({COVER}),
        login="browser",
        note="标题必须 4~40 字；登录要在浏览器里输 QQ/手机号；不支持定时/合集",
    ),
    Platform(
        key="baijiahao", label="百家号",
        caps=frozenset({COVER, COLLECTION}),
        login="qr",
        note="不支持定时发布",
    ),
    Platform(
        key="alipay", label="支付宝生活号",
        caps=frozenset({COVER, COLLECTION}),
        login="qr",
        note="需先在支付宝内容创作后台开通生活号权限；不支持定时发布",
    ),
    Platform(
        key="youtube", label="YouTube",
        title_max=100,
        caps=frozenset({COVER, PLAYLIST, VISIBILITY}),
        login="browser",
        needs_proxy=True,
        note="标题上限 100 字；走 Google 账号登录（非扫码）；必须挂代理",
    ),
)

BY_KEY: dict[str, Platform] = {p.key: p for p in PLATFORMS}

# 常用别名 —— 让人能写 `--only b站,抖音,油管`
ALIASES: dict[str, str] = {
    "b站": "bilibili", "bili": "bilibili", "b站": "bilibili",
    "抖音": "douyin", "dy": "douyin",
    "快手": "kuaishou", "ks": "kuaishou",
    "小红书": "xiaohongshu", "xhs": "xiaohongshu", "red": "xiaohongshu", "rednote": "xiaohongshu",
    "视频号": "tencent", "微信视频号": "tencent", "wechat": "tencent", "channels": "tencent",
    "微博": "weibo", "wb": "weibo",
    "虎扑": "hupu", "hp": "hupu",
    "百家号": "baijiahao", "bjh": "baijiahao",
    "支付宝": "alipay", "生活号": "alipay",
    "油管": "youtube", "yt": "youtube", "youtube": "youtube",
}


def resolve(name: str) -> str | None:
    """把用户写的名字解析成平台 key；认不出来返回 None。"""
    raw = (name or "").strip()
    if not raw:
        return None
    low = raw.lower()
    if low in BY_KEY:
        return low
    if raw in ALIASES:
        return ALIASES[raw]
    if low in ALIASES:
        return ALIASES[low]
    return None


def parse_list(spec: str) -> tuple[list[str], list[str]]:
    """解析逗号/空格分隔的平台列表。

    返回 (认出来的 key 列表, 认不出来的原文列表)。
    认不出来的**不静默丢弃** —— 让调用方报错，否则用户写了
    `--only douyin,tiktok` 会以为 tiktok 发出去了。
    """
    parts = [x for x in spec.replace("，", ",").replace(" ", ",").split(",") if x.strip()]
    good: list[str] = []
    bad: list[str] = []
    for part in parts:
        key = resolve(part)
        if key is None:
            bad.append(part.strip())
        elif key not in good:
            good.append(key)
    return good, bad


def ordered(keys) -> list[str]:
    """按 PLATFORMS 的默认顺序排列（保证每次跑的日志顺序一致）。"""
    wanted = set(keys)
    out = [p.key for p in PLATFORMS if p.key in wanted]
    # 保底：PLATFORMS 里没有的 key 也带上（将来 sau 加平台时不会漏）
    out += [k for k in keys if k not in BY_KEY]
    return out
