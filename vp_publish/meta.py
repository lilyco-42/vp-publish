"""元数据：从各种来源推导出标题/简介/标签，再按平台规则适配。

「一键」的关键在这里——用户敲下 `vp-publish final.mp4` 之后，
标题从哪来？简介从哪来？标签从哪来？

查找顺序（先找到的赢）：
  1. 命令行参数（--title / --desc / --tags）
  2. `<视频同名>.json`      —— 例如 final.mp4 → final.json
  3. 同目录 `meta.json`      —— vp-pipeline 产出的就是这个名字
  4. 同目录 `<视频名>.txt`   —— 第一行当标题
  5. 文件名本身（去掉日期前缀和常见后缀）

一条都没找到也不报错——用文件名兜底，至少能发出去。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import platforms

# 文件名里常见的噪声：日期前缀、渲染标记、分辨率后缀
_NOISE_PATTERNS = (
    r"^\d{4}[-_]?\d{2}[-_]?\d{2}[-_ ]*",       # 20260927 / 2026-09-27 / 2026_09_27
    r"^\d{8}[-_ ]*",
    r"[-_ ]*(final|output|render|export|out)$",
    r"[-_ ]*\d{3,4}p$",                         # 1080p / 720p
    r"[-_ ]*v\d+$",                             # v2
)


def _clean_name(stem: str) -> str:
    name = stem
    for pat in _NOISE_PATTERNS:
        name = re.sub(pat, "", name, flags=re.IGNORECASE)
    name = name.replace("_", " ").replace("-", " ").strip()
    return re.sub(r"\s+", " ", name) or stem


def split_tags(raw) -> list[str]:
    """标签可以是 list，也可以是 `a,b,c` / `a，b，c` / `#a #b` 字符串。"""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        items = re.split(r"[,，、]|\s+", str(raw))
    out: list[str] = []
    for item in items:
        tag = str(item).strip().lstrip("#").strip()
        if tag and tag not in out:
            out.append(tag)
    return out


@dataclass
class Meta:
    title: str = ""
    desc: str = ""
    tags: list[str] = field(default_factory=list)
    cover: str = ""
    tid: int | None = None          # B站分区
    collection: str = ""            # 合集
    playlist: str = ""              # YouTube 播放列表
    visibility: str = "public"      # YouTube 可见性
    schedule: str = ""              # 定时发布时间
    source: str = ""                # 元数据来自哪个文件（给人看/排障用）

    def to_dict(self) -> dict:
        return {
            "title": self.title, "desc": self.desc, "tags": list(self.tags),
            "cover": self.cover, "tid": self.tid, "collection": self.collection,
            "playlist": self.playlist, "visibility": self.visibility,
            "schedule": self.schedule, "source": self.source,
        }


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def find_meta_file(video: Path) -> Path | None:
    """按优先级找出视频旁边的元数据文件。"""
    candidates = [
        video.with_suffix(".json"),                 # final.mp4 → final.json
        video.parent / "meta.json",                 # vp-pipeline 的约定
        video.with_suffix(".txt"),                  # 纯文本：第一行标题
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def load(video: Path, *, title: str = "", desc: str = "", tags="",
         cover: str = "", tid: int | None = None, collection: str = "",
         playlist: str = "", visibility: str = "", schedule: str = "") -> Meta:
    """把「命令行 + 旁边的文件 + 文件名」合成一份元数据。"""
    meta = Meta()

    src = find_meta_file(video)
    if src is not None:
        meta.source = str(src)
        if src.suffix.lower() == ".json":
            data = _load_json(src)
            meta.title = str(data.get("title") or "").strip()
            meta.desc = str(data.get("desc") or data.get("description") or "").strip()
            meta.tags = split_tags(data.get("tags"))
            meta.cover = str(data.get("cover") or "").strip()
            if data.get("tid") is not None:
                try:
                    meta.tid = int(data["tid"])
                except (TypeError, ValueError):
                    pass
            meta.collection = str(data.get("collection") or "").strip()
            meta.playlist = str(data.get("playlist") or "").strip()
            meta.visibility = str(data.get("visibility") or "").strip() or meta.visibility
            meta.schedule = str(data.get("schedule") or "").strip()
        else:  # .txt
            lines = [ln.strip() for ln in src.read_text(encoding="utf-8").splitlines()]
            lines = [ln for ln in lines if ln]
            if lines:
                meta.title = lines[0]
            if len(lines) > 1:
                meta.desc = "\n".join(lines[1:])

    # 命令行覆盖文件
    if title.strip():
        meta.title = title.strip()
    if desc.strip():
        meta.desc = desc.strip()
    if tags:
        meta.tags = split_tags(tags)
    if cover.strip():
        meta.cover = cover.strip()
    if tid is not None:
        meta.tid = tid
    if collection.strip():
        meta.collection = collection.strip()
    if playlist.strip():
        meta.playlist = playlist.strip()
    if visibility.strip():
        meta.visibility = visibility.strip()
    if schedule.strip():
        meta.schedule = schedule.strip()

    # 兜底：文件名
    if not meta.title:
        meta.title = _clean_name(video.stem)
    if not meta.desc:
        meta.desc = meta.title

    return meta


# ── 平台适配 ────────────────────────────────────────────────────
def adapt_title(title: str, plat: platforms.Platform) -> tuple[str, list[str]]:
    """按平台的长度规矩调整标题。

    返回 (调整后的标题, 警告列表)。

    策略：**能裁就裁，但一定说清楚**。悄悄改用户标题比报错更糟，
    所以每次裁剪都产出一条警告，最后统一打印。
    """
    warns: list[str] = []
    out = title

    if plat.title_max and len(out) > plat.title_max:
        original = out
        # 留一个字符给省略号，避免出现「刚好截断」的怪标题
        out = out[: max(1, plat.title_max - 1)].rstrip() + "…"
        warns.append(
            f"{plat.label} 标题上限 {plat.title_max} 字，已裁剪："
            f"「{original}」→「{out}」"
        )

    if plat.title_min and len(out) < plat.title_min:
        original = out
        # 补足到下限，用平台名当垫词——总比上传失败好
        filler = f"｜{plat.label}"
        while len(out) < plat.title_min and len(out) + len(filler) <= plat.title_min:
            out += filler
        out = out.ljust(plat.title_min, "·") if len(out) < plat.title_min else out
        warns.append(
            f"{plat.label} 标题至少 {plat.title_min} 字，已补足："
            f"「{original}」→「{out}」"
        )

    return out, warns


def adapt_tags(tags: list[str], plat: platforms.Platform) -> list[str]:
    if plat.tags_max:
        return tags[: plat.tags_max]
    return tags


def check_required(meta: Meta, plat: platforms.Platform) -> list[str]:
    """检查该平台有没有「必填但没给」的东西。返回问题列表。"""
    problems: list[str] = []
    if platforms.TID in plat.caps and meta.tid is None:
        # 不是错——调用方会给默认值。这里只在真没默认值时才报。
        problems.append(f"{plat.label} 需要分区 id（--tid），例如 --tid 171")
    return problems
