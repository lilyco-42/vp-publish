"""watch 模式：盯着目录，出现新视频就自动发。

**为什么需要**：vp-pipeline 每天定时产出 4 条视频，但发布那一环是空的——
你得记得手动去发。整条「话题 → 成片 → 发布」的自动化断在最后一步。
watch 模式把它接上：视频落盘 → 自动发到所有已登录平台 → 记进状态。

**比「流水线跑完调一次 publish」好在哪**：
不依赖流水线主动调用。视频怎么来的（流水线产的、你手工拷的、scp 过来的）
都一样会被发出去。耦合更松，也更不容易漏。

三个必须处理好的细节：

1. **等文件写完再发**（最重要）。正在写入的视频会先出现一个半截文件，
   这时候发出去就是一条坏视频。判据：**连续两轮轮询大小不变**才算写完。
   只判一次「文件存在」是不够的。

2. **首次启动不要炸库存**。如果你有一个存了 50 条老视频的目录，
   一启动就全发出去，那是灾难。默认行为：首次见到已存在的视频只**登记**
   不发布，日志里说清楚；要发库存得显式 `--publish-backlog`。

3. **单条失败不能卡住队列**。失败记进状态并继续；**同一条不会自动重试**——
   否则一条坏视频会让后面全部堵住，而且会在平台侧反复触发风控。
   要重发就 `vp-publish forget <视频>` 清掉记录，下一轮自然会捡起来。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import report

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".flv", ".webm", ".m4v", ".ts"}


@dataclass
class Seen:
    """记录一个文件的「上次看到的大小」，用来判断有没有写完。"""
    size: int = -1
    stable_rounds: int = 0
    logged: bool = False
    # 第一次见到它的时候，它就已经在目录里了 → 库存。
    #
    # 为什么必须**按文件**记、而不是用一个全局的「首次启动」开关：
    # 因为文件要「连续 2 轮大小不变」才算写完。第一轮扫描时所有老视频
    # 都还没稳定，所以都没发；等第二轮它们稳定了、可以发了，如果这时
    # 全局开关已经翻过去了，库存就会被整批发出去 —— 这个坑实测踩到过。
    baseline: bool = False


@dataclass
class WatchState:
    """watch 自己的状态：哪些文件看过了、稳不稳定。

    跟 state.Store（发布记录）分开存，因为关心的是不同的事：
    这里关心「文件写完了没」，那里关心「发出去了没」。
    """
    seen: dict[str, Seen] = field(default_factory=dict)
    started_at: float = 0.0
    published: dict[str, str] = field(default_factory=dict)   # 路径 → 时间
    baseline_done: bool = False

    def to_json(self) -> dict:
        return {
            "started_at": self.started_at,
            "baseline_done": self.baseline_done,
            "published": self.published,
            "seen": {k: {"size": v.size, "stable_rounds": v.stable_rounds,
                         "logged": v.logged, "baseline": v.baseline}
                     for k, v in self.seen.items()},
        }

    @classmethod
    def from_json(cls, data: dict) -> "WatchState":
        st = cls()
        if not isinstance(data, dict):
            return st
        st.started_at = float(data.get("started_at") or 0.0)
        st.baseline_done = bool(data.get("baseline_done"))
        pub = data.get("published")
        st.published = {str(k): str(v) for k, v in pub.items()} if isinstance(pub, dict) else {}
        seen = data.get("seen")
        if isinstance(seen, dict):
            for key, val in seen.items():
                if isinstance(val, dict):
                    st.seen[str(key)] = Seen(
                        size=int(val.get("size", -1)),
                        stable_rounds=int(val.get("stable_rounds", 0)),
                        logged=bool(val.get("logged")),
                        baseline=bool(val.get("baseline")),
                    )
        return st


class Store:
    """watch 状态的持久化。

    名字跟 state.Store 一样，但两者是不同的东西：
    state.Store 记「哪个视频发到哪个平台了」（幂等用），
    watch.Store 记「哪个文件写完了、有没有被 watch 处理过」（守护用）。
    调用处都带模块前缀（watch.Store / state.Store），不会混。
    """

    def __init__(self, path: Path):
        self.path = path
        self.state = WatchState()
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            self.state = WatchState.from_json(
                json.loads(self.path.read_text(encoding="utf-8")))
        except Exception:
            self.state = WatchState()

    def save(self) -> None:
        import os
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state.to_json(), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.path)


WatchStore = Store      # 兼容旧名字


def key_for(path: Path) -> str:
    """watch 里用的文件标识（绝对路径，避免相对路径/软链造成的重复）。"""
    return str(path.resolve())


def register(state: WatchState, path: Path, *, baseline: bool) -> Seen:
    """第一次见到这个文件时建一条记录。

    `baseline=True` 表示「我们开始看的时候它就已经在了」—— 这类文件
    在写完稳定之后也要跳过，否则一启动就把整个目录的存货发出去。
    """
    key = key_for(path)
    seen = state.seen.get(key)
    if seen is None:
        seen = Seen(baseline=baseline)
        state.seen[key] = seen
    return seen


def forget(state: WatchState, path: Path) -> bool:
    """把某个文件从 watch 状态里彻底抹掉（重发前用）。"""
    key = key_for(path)
    hit = state.seen.pop(key, None) is not None
    if state.published.pop(key, None) is not None:
        hit = True
    return hit


def scan(dirs: list[Path], *, recursive: bool = True) -> list[Path]:
    """找出候选视频。跳过隐藏文件和常见的半成品命名。"""
    out: list[Path] = []
    for root in dirs:
        if not root.is_dir():
            continue
        it = root.rglob("*") if recursive else root.glob("*")
        for path in it:
            if not path.is_file():
                continue
            if path.suffix.lower() not in VIDEO_SUFFIXES:
                continue
            if path.name.startswith("."):
                continue
            # 常见「还在写」的命名约定，先跳过
            if path.name.endswith((".part", ".tmp", ".crdownload", ".download")):
                continue
            out.append(path)
    return sorted(out)


def ready_to_publish(path: Path, seen: Seen, *,
                     min_stable_rounds: int = 2) -> tuple[bool, str]:
    """判断这个文件写完了没。

    返回 (是否可发, 原因)。**连续两轮大小不变**才算写完。
    为什么不是「存在就发」：正在写入的视频会先出现半截文件，
    这时候发出去是一条坏视频，而且要等平台审核失败才发现。
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        return False, f"stat 失败：{exc}"

    if size == 0:
        return False, "文件是 0 字节"

    if size != seen.size:
        seen.size = size
        seen.stable_rounds = 1
        return False, f"大小还在变（{size} 字节），等下一轮"

    seen.stable_rounds += 1
    if seen.stable_rounds < min_stable_rounds:
        return False, f"大小已稳定 {seen.stable_rounds}/{min_stable_rounds} 轮"

    # 视频旁边如果声明了元数据文件但还在写，也再等等
    sidecar = path.with_suffix(".json")
    if sidecar.is_file():
        try:
            json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            return False, f"{sidecar.name} 还不是完整 JSON，等流水线写完"

    return True, ""


def describe_round(round_no: int, scanned: int, ready: int,
                   published: int, failed: int, skipped: int) -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    bits = [f"扫描 {scanned}"]
    if ready:
        bits.append(f"就绪 {ready}")
    if published:
        bits.append(f"已发 {published}")
    if failed:
        bits.append(f"失败 {failed}")
    if skipped:
        bits.append(f"跳过 {skipped}")
    return f"[{stamp}] 第 {round_no} 轮：{'，'.join(bits)}"
