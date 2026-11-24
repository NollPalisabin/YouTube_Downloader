"""纯函数小工具：时长 / 体积 / 速度 / 文本格式化。

刻意不依赖 Streamlit 与 yt-dlp，方便单测，也方便下载层与界面层共用。
"""

from __future__ import annotations

import re
import sys

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")

__all__ = [
    "format_duration",
    "format_filesize",
    "format_speed",
    "format_eta",
    "format_count",
    "sanitize_filename",
    "truncate_text",
    "QuietLogger",
    "enable_utf8_stdout",
]


def enable_utf8_stdout() -> None:
    """让命令行脚本能安全打印中文。

    Windows 的 CI runner / 旧控制台默认是 cp1252 之类的本地编码，直接 print
    中文会抛 ``UnicodeEncodeError``（打包脚本就踩过）。在 CLI 入口调用一次即可。
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # 已被重定向/关闭时忽略
            pass


class QuietLogger:
    """吞掉 yt-dlp 的控制台输出，避免污染 Streamlit 终端。"""

    def debug(self, msg: str) -> None:  # pragma: no cover - 仅静默
        pass

    def info(self, msg: str) -> None:  # pragma: no cover
        pass

    def warning(self, msg: str) -> None:  # pragma: no cover
        pass

    def error(self, msg: str) -> None:  # pragma: no cover
        pass


def format_duration(seconds: float | int | None) -> str:
    """秒数 -> ``12:34`` / ``1:02:03``；未知返回 ``--:--``。"""
    if seconds is None:
        return "--:--"
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return "--:--"
    if total < 0:
        return "--:--"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_filesize(num_bytes: float | None) -> str:
    """字节数 -> ``215.3 MB``（1024 进制）；未知返回 ``未知``。"""
    if num_bytes is None:
        return "未知"
    try:
        value = float(num_bytes)
    except (TypeError, ValueError):
        return "未知"
    if value < 0 or value != value or value == float("inf"):
        return "未知"

    unit_index = 0
    while value >= 1024 and unit_index < len(_UNITS) - 1:
        value /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{int(value)} B"
    if value < 10:
        return f"{value:.1f} {_UNITS[unit_index]}"
    return f"{value:.0f} {_UNITS[unit_index]}"


def format_speed(bytes_per_second: float | None) -> str:
    """下载速度，如 ``3.4 MB/s``；无数据返回 ``—``。"""
    if not bytes_per_second or bytes_per_second <= 0:
        return "—"
    return f"{format_filesize(bytes_per_second)}/s"


def format_eta(seconds: float | int | None) -> str:
    """剩余时间；未知返回 ``未知``。"""
    text = format_duration(seconds)
    return "未知" if text == "--:--" else text


def format_count(count: int | None) -> str:
    """播放量等大数字 -> ``1234`` / ``1.2 万`` / ``3.4 亿``。"""
    if count is None:
        return "—"
    try:
        value = int(count)
    except (TypeError, ValueError):
        return "—"
    if value < 10000:
        return str(value)
    if value < 100_000_000:
        return f"{value / 10000:.1f} 万".replace(".0 万", " 万")
    return f"{value / 100_000_000:.1f} 亿".replace(".0 亿", " 亿")


def sanitize_filename(name: str, *, max_length: int = 120) -> str:
    """把任意标题清洗成安全的文件名（去掉 Windows 非法字符与首尾点号）。"""
    cleaned = _INVALID_FILENAME_CHARS.sub("_", name or "")
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    cleaned = cleaned.strip(" .")
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" .")
    return cleaned or "video"


def truncate_text(text: str, limit: int = 80) -> str:
    """超长文本截断并加省略号。"""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"
