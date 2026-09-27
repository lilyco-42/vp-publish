"""发布记录：让「重跑」变成幂等操作。

为什么需要：多平台上传是**慢**操作（每个平台都要开浏览器、上传、过风控，
10 个平台十几分钟很正常）。中途第 7 个平台挂了，你不会想从头再发一遍——
尤其是前 6 个已经发出去了。

记录存哪儿：`~/.local/state/vp-publish/records.json`（不写进视频目录）。
理由：视频目录可能是只读挂载、可能被 rsync 同步、可能被清理脚本扫到。
状态文件不该混在内容里。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import default_state_dir

OK = "ok"
FAIL = "fail"
SKIP = "skip"


def video_key(video: Path) -> str:
    """视频指纹：路径 + 大小 + mtime。

    故意**不做内容哈希**——10GB 的视频读一遍要几分钟，而「路径+大小+mtime」
    已经足够回答「这是不是刚才那个文件」。
    """
    p = video.resolve()
    try:
        st = p.stat()
        size, mtime = st.st_size, int(st.st_mtime)
    except OSError:
        size, mtime = 0, 0
    return f"{p}|{size}|{mtime}"


@dataclass
class Record:
    platforms: dict[str, dict] = field(default_factory=dict)

    def status(self, platform: str) -> str:
        return (self.platforms.get(platform) or {}).get("status", "")

    def is_ok(self, platform: str) -> bool:
        return self.status(platform) == OK

    def mark(self, platform: str, status: str, **extra) -> None:
        entry = {"status": status, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        entry.update({k: v for k, v in extra.items() if v not in (None, "")})
        self.platforms[platform] = entry


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or (default_state_dir() / "records.json")
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(raw, dict):
            self._data = {k: v for k, v in raw.items() if isinstance(v, dict)}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def get(self, video: Path) -> Record:
        raw = self._data.get(video_key(video)) or {}
        platforms = raw.get("platforms") if isinstance(raw, dict) else None
        return Record(platforms=dict(platforms) if isinstance(platforms, dict) else {})

    def put(self, video: Path, record: Record) -> None:
        key = video_key(video)
        self._data[key] = {
            "video": str(video.resolve()),
            "platforms": record.platforms,
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def forget(self, video: Path) -> bool:
        return self._data.pop(video_key(video), None) is not None

    def prune(self, keep: int = 500) -> int:
        """防止记录文件无限长。"""
        if len(self._data) <= keep:
            return 0
        ordered = sorted(
            self._data.items(),
            key=lambda kv: kv[1].get("updated", ""),
            reverse=True,
        )
        dropped = ordered[keep:]
        self._data = dict(ordered[:keep])
        return len(dropped)
