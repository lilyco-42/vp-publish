"""封面：没给封面时，用 ffmpeg 从视频里抽一帧，并按平台比例裁好。

为什么值得做：各平台封面比例不一样（抖音 3:4 竖、视频号要 4:3 横 + 3:4 竖、
B站/YouTube 16:9）。手工给每个平台做一张封面，是「一键发布」里最烦的一步。
自动抽帧不完美，但**比没有封面强得多**——平台不给封面会随机截一帧，
经常截到黑屏或者半句话。

抽帧策略：默认取第 1 秒（`cover_at`）。为什么不是第 0 帧？
很多视频开头是纯黑/淡入，第 0 帧基本是黑的。
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# 平台常用比例 → 输出尺寸（够用即可，别让 PNG 太大）
RATIOS: dict[str, tuple[int, int]] = {
    "16:9": (1920, 1080),
    "4:3": (1440, 1080),
    "3:4": (1080, 1440),
    "1:1": (1080, 1080),
    "9:16": (1080, 1920),
}


@dataclass
class Result:
    path: Path | None
    reason: str = ""


def tools_available() -> tuple[bool, bool]:
    return bool(shutil.which("ffmpeg")), bool(shutil.which("ffprobe"))


def probe_size(video: Path) -> tuple[int, int] | None:
    """拿到视频分辨率。拿不到就返回 None（调用方会跳过封面）。"""
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0:s=x", str(video)],
            capture_output=True, text=True, timeout=60,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    raw = out.stdout.strip().splitlines()
    if not raw:
        return None
    try:
        w, h = raw[0].split("x")[:2]
        return int(w), int(h)
    except (ValueError, IndexError):
        return None


def _crop_for(src_w: int, src_h: int, ratio: str) -> str:
    """算出「居中裁到目标比例」的 crop 参数。"""
    tw, th = RATIOS[ratio]
    target = tw / th
    source = src_w / src_h

    if abs(source - target) < 0.01:
        return f"crop={src_w}:{src_h}:0:0"

    if source > target:
        # 源更宽 → 裁两侧
        cw = int(round(src_h * target))
        cw -= cw % 2                      # H.264/PNG 都喜欢偶数
        ch = src_h - (src_h % 2)
        x = (src_w - cw) // 2
        return f"crop={cw}:{ch}:{x}:0"
    # 源更高 → 裁上下
    ch = int(round(src_w / target))
    ch -= ch % 2
    cw = src_w - (src_w % 2)
    y = (src_h - ch) // 2
    return f"crop={cw}:{ch}:0:{y}"


def extract(video: Path, out_dir: Path, *, ratio: str = "16:9",
            at: float = 1.0) -> Result:
    """抽一帧并裁成指定比例。失败返回 Result(None, 原因)。"""
    have_ffmpeg, have_ffprobe = tools_available()
    if not have_ffmpeg:
        return Result(None, "没装 ffmpeg，跳过封面生成")
    if ratio not in RATIOS:
        return Result(None, f"不支持的比例 {ratio}")

    size = probe_size(video) if have_ffprobe else None
    if size is None:
        return Result(None, "ffprobe 拿不到分辨率，跳过封面生成")
    src_w, src_h = size

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{video.stem}-{ratio.replace(':', 'x')}.png"

    tw, th = RATIOS[ratio]
    vf = f"{_crop_for(src_w, src_h, ratio)},scale={tw}:{th}:flags=lanczos"

    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", str(max(0.0, at)),
             "-i", str(video), "-frames:v", "1", "-vf", vf, str(out)],
            capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        return Result(None, "ffmpeg 抽帧超时")
    except Exception as exc:                       # pragma: no cover - 环境相关
        return Result(None, f"ffmpeg 抽帧失败：{exc}")

    if proc.returncode != 0 or not out.is_file():
        detail = (proc.stderr or "").strip().splitlines()
        return Result(None, f"ffmpeg 抽帧失败：{detail[-1] if detail else '未知错误'}")
    return Result(out, "")


def ratios_for(platform_key: str, caps: frozenset[str]) -> dict[str, str]:
    """这个平台需要哪些封面？返回 {sau 参数名: 比例}。

    注意比例是**平台无关**的：抖音的 3:4 和微博的 3:4 是同一张图。
    所以调用方应该按比例缓存，而不是按平台——否则 5 个平台会生成
    3 份一模一样的 3:4 封面（实测浪费 500KB 和 3 次 ffmpeg 调用）。
    """
    from .platforms import COVER_DUAL

    if COVER_DUAL in caps:
        # 抖音 / 视频号：横竖各来一张，平台自己挑
        return {"thumbnail_landscape": "4:3", "thumbnail_portrait": "3:4"}
    if platform_key in ("bilibili", "youtube"):
        return {"thumbnail": "16:9"}
    return {"thumbnail": "3:4"}


def ensure(video: Path, out_dir: Path, ratios: set[str], *, at: float = 1.0) -> dict[str, Result]:
    """确保这些比例的封面都存在，返回 {比例: Result}。"""
    return {ratio: extract(video, out_dir, ratio=ratio, at=at) for ratio in sorted(ratios)}
