"""配置：零依赖（JSON，不引入 PyYAML）。

为什么不跟 vp-pipeline 一样用 YAML？因为 sau 的 venv 里**没有** PyYAML，
而本工具刻意设计成「用系统 python3 就能跑」——这样它坏了也不会连累 sau，
sau 升级了也不会连累它。多一个 YAML 依赖就多一个装不上的理由。

配置位置：`~/.config/vp-publish/config.json`（可用 VPP_CONFIG 覆盖）
配置里写不写都行——**所有字段都有能用的默认值**。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(p)))


def default_config_path() -> Path:
    override = os.environ.get("VPP_CONFIG")
    if override:
        return _expand(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = _expand(xdg) if xdg else Path.home() / ".config"
    return base / "vp-publish" / "config.json"


def default_state_dir() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME")
    base = _expand(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "vp-publish"


@dataclass
class SauConfig:
    root: Path = field(default_factory=lambda: Path.home() / "sau")
    bin: Path = field(default_factory=lambda: Path.home() / "sau" / ".venv" / "bin" / "sau")
    python: Path = field(default_factory=lambda: Path.home() / "sau" / ".venv" / "bin" / "python")
    headless: bool = True
    # 平台 → 账号名。留空则自动从 cookies 目录发现。
    accounts: dict[str, str] = field(default_factory=dict)

    @property
    def cookies_dir(self) -> Path:
        return self.root / "cookies"


@dataclass
class Config:
    sau: SauConfig = field(default_factory=SauConfig)
    default_tags: list[str] = field(default_factory=list)
    tid: int = 171                      # B站分区：171=科技·人工智能
    visibility: str = "public"          # YouTube
    proxy: str = "http://127.0.0.1:7890"
    cover: bool = True                  # 没给封面时自动抽帧
    cover_at: float = 1.0               # 抽帧时间点（秒）
    cover_dir: Path = field(default_factory=lambda: Path.home() / ".cache" / "vp-publish" / "covers")
    # 平台 → 标题长度上限覆盖（用于 sau 文档没写、你又踩过坑的平台）
    title_max: dict[str, int] = field(default_factory=dict)
    timeout: int = 900                  # 单平台上传超时（秒）
    retries: int = 0                    # 单平台失败重试次数
    path: Path = field(default_factory=default_config_path)

    def resolve_cover_dir(self) -> Path:
        return _expand(str(self.cover_dir))


def _as_path(value, fallback: Path) -> Path:
    if not value:
        return fallback
    return _expand(str(value))


def load(path: Path | None = None) -> Config:
    """读配置。文件不存在 / 读坏了都不报错——用默认值继续。"""
    cfg_path = path or default_config_path()
    cfg = Config(path=cfg_path)
    if not cfg_path.is_file():
        return cfg

    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return cfg
    if not isinstance(raw, dict):
        return cfg

    sau = raw.get("sau") or {}
    if isinstance(sau, dict):
        cfg.sau.root = _as_path(sau.get("root"), cfg.sau.root)
        cfg.sau.bin = _as_path(sau.get("bin"), cfg.sau.root / ".venv" / "bin" / "sau")
        cfg.sau.python = _as_path(sau.get("python"), cfg.sau.root / ".venv" / "bin" / "python")
        if isinstance(sau.get("headless"), bool):
            cfg.sau.headless = sau["headless"]
        acct = sau.get("accounts")
        if isinstance(acct, dict):
            cfg.sau.accounts = {str(k): str(v) for k, v in acct.items() if v}

    if isinstance(raw.get("default_tags"), list):
        cfg.default_tags = [str(t).strip() for t in raw["default_tags"] if str(t).strip()]
    if isinstance(raw.get("tid"), int):
        cfg.tid = raw["tid"]
    if isinstance(raw.get("visibility"), str) and raw["visibility"]:
        cfg.visibility = raw["visibility"]
    if isinstance(raw.get("proxy"), str):
        cfg.proxy = raw["proxy"]
    if isinstance(raw.get("cover"), bool):
        cfg.cover = raw["cover"]
    if isinstance(raw.get("cover_at"), (int, float)):
        cfg.cover_at = float(raw["cover_at"])
    cfg.cover_dir = _as_path(raw.get("cover_dir"), cfg.cover_dir)
    if isinstance(raw.get("title_max"), dict):
        cfg.title_max = {
            str(k): int(v) for k, v in raw["title_max"].items()
            if isinstance(v, (int, float)) and int(v) > 0
        }
    if isinstance(raw.get("timeout"), (int, float)) and raw["timeout"] > 0:
        cfg.timeout = int(raw["timeout"])
    if isinstance(raw.get("retries"), (int, float)) and raw["retries"] >= 0:
        cfg.retries = int(raw["retries"])

    return cfg


TEMPLATE = {
    "_说明": "vp-publish 配置。所有字段都可省略；省略即用默认值。",
    "sau": {
        "root": "~/sau",
        "bin": "~/sau/.venv/bin/sau",
        "headless": True,
        "accounts": {
            "_说明": "平台 → 账号名。留空则自动从 ~/sau/cookies/ 发现。",
            "douyin": "我的抖音",
            "xiaohongshu": "我的小红书",
            "bilibili": "我的B站"
        }
    },
    "default_tags": ["AI", "科技"],
    "tid": 171,
    "visibility": "public",
    "proxy": "http://127.0.0.1:7890",
    "cover": True,
    "cover_at": 1.0,
    "timeout": 900,
    "retries": 0
}


def write_template(path: Path | None = None, *, force: bool = False) -> Path:
    target = path or default_config_path()
    if target.exists() and not force:
        raise FileExistsError(f"配置已存在：{target}（加 --force 覆盖）")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(TEMPLATE, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target
