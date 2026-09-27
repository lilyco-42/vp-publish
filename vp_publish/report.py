"""报表：把一堆结果渲染成人一眼能扫的表。

中文对齐是个真问题：`len("抖音")` 是 2，但它占 4 个字符宽。
所以这里自己算**显示宽度**（东亚宽字符算 2），不靠 len()。
不然表格会歪，歪的表格比没有表格还难读。
"""
from __future__ import annotations

import unicodedata

OK = "ok"
FAIL = "fail"
SKIP = "skip"
DRY = "dry"


def width(text: str) -> int:
    """字符串在等宽终端里占几列。"""
    total = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        total += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return total


def pad(text: str, target: int, align: str = "left") -> str:
    gap = max(0, target - width(text))
    if align == "right":
        return " " * gap + text
    return text + " " * gap


def truncate(text: str, target: int) -> str:
    """按显示宽度截断（超宽就加省略号）。"""
    if width(text) <= target:
        return text
    out = ""
    for ch in text:
        if width(out + ch) > target - 1:
            break
        out += ch
    return out + "…"


STATUS_TEXT = {
    OK: "已发布",
    FAIL: "失败",
    SKIP: "跳过",
    DRY: "试运行",
}
STATUS_MARK = {
    OK: "✓",
    FAIL: "✗",
    SKIP: "·",
    DRY: "~",
}


def table(rows: list[dict], columns: list[tuple[str, str, str]]) -> str:
    """columns = [(key, 表头, 对齐)]"""
    if not rows:
        return ""
    widths: dict[str, int] = {}
    for key, header, _ in columns:
        widths[key] = width(header)
        for row in rows:
            widths[key] = max(widths[key], width(str(row.get(key, ""))))

    def render(cells: dict[str, str]) -> str:
        return "  ".join(
            pad(truncate(cells.get(key, ""), widths[key] + 2), widths[key], align)
            for key, _, align in columns
        )

    lines = [render({k: h for k, h, _ in columns})]
    lines.append("─" * width(lines[0]))
    for row in rows:
        lines.append(render({k: str(row.get(k, "")) for k, _, _ in columns}))
    return "\n".join(lines)


def summarize(results: list) -> dict:
    """把结果列表汇成计数。"""
    counts = {OK: 0, FAIL: 0, SKIP: 0, DRY: 0}
    for r in results:
        counts[r.get("status", SKIP)] = counts.get(r.get("status", SKIP), 0) + 1
    counts["total"] = len(results)
    return counts


def line_for(plat_label: str, account: str, status: str, elapsed: float,
             note: str = "", text: str = "") -> dict:
    """渲染一行表格数据。

    `text` 用来覆盖默认的状态文案 —— 同一个 OK 状态，
    发布流程里该说「已发布」，体检里该说「就绪」。
    """
    mark = STATUS_MARK.get(status, "?")
    text = text or STATUS_TEXT.get(status, status)
    secs = f"{elapsed:.0f}s" if elapsed else ""
    if elapsed >= 60:
        secs = f"{int(elapsed // 60)}m{int(elapsed % 60):02d}s"
    return {
        "platform": plat_label,
        "account": account,
        "result": f"{mark} {text}",
        "elapsed": secs,
        "note": note,
    }


def human_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} 秒"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分 {int(seconds % 60)} 秒"
    return f"{int(seconds // 3600)} 小时 {int((seconds % 3600) // 60)} 分"
