"""yt-dlp 封装（解析部分）：把原始元信息整理成界面可直接消费的结构。

职责边界
- 只做**解析**，不下载；下载在 ``core/downloader.py``。
- 不 import streamlit，可在纯 Python 环境下测试。
- 所有异常收敛为 :class:`ExtractionError`，带中文提示与可选 ``hint``。

清晰度策略
- 按视频高度分组，从高到低排序；
- 每组挑一个"代表格式"用于展示编码/体积，选取顺序与实际下载串保持一致：
  H.264 → 任意 MP4 → 其他容器，同档内取高帧率、高码率；
- 末尾追加一条"仅音频（MP3）"选项。
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadError

import config
from core.utils import QuietLogger, format_duration, format_filesize

MAX_CACHE_ENTRIES = 8

__all__ = [
    "ExtractionError",
    "QualityOption",
    "VideoInfo",
    "extract_video_info",
    "clear_cache",
    "classify_error",
]


class ExtractionError(RuntimeError):
    """解析失败。``message`` 面向用户，``hint`` 是可选的下一步建议。"""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class QualityOption:
    """一个可下载的清晰度。"""

    label: str            # 展示用：2160p60 / 1080p / 仅音频 · MP3
    selector: str         # 交给 yt-dlp 的格式选择串
    ext: str              # 目标容器：mp4 / mp3
    detail: str           # 副标题：MP4 · H.264 · 约 215 MB
    height: int | None = None
    fps: float | None = None
    approx_size: int | None = None
    is_audio_only: bool = False


@dataclass(frozen=True)
class VideoInfo:
    """一次解析的完整结果。"""

    video_id: str
    title: str
    webpage_url: str
    duration: int | None
    duration_text: str
    uploader: str | None
    upload_date: str | None
    view_count: int | None
    thumbnail: str | None
    qualities: tuple[QualityOption, ...]

    @property
    def best_quality(self) -> QualityOption | None:
        video_qualities = [q for q in self.qualities if not q.is_audio_only]
        return video_qualities[0] if video_qualities else None


# --------------------------------------------------------------------------- #
# 格式挑选
# --------------------------------------------------------------------------- #
_CODEC_LABELS: tuple[tuple[str, str], ...] = (
    ("avc1", "H.264"),
    ("h264", "H.264"),
    ("av01", "AV1"),
    ("vp09", "VP9"),
    ("vp9", "VP9"),
    ("hev1", "HEVC"),
    ("hvc1", "HEVC"),
)

# 与 build_format_selector 的偏好一致：H.264 最通用，其次是 MP4 里常见的 AV1/VP9
_CODEC_PREFERENCE = {"H.264": 0, "AV1": 1, "VP9": 2, "HEVC": 3}


def _codec_label(codec: str | None) -> str:
    text = (codec or "").lower()
    for prefix, label in _CODEC_LABELS:
        if text.startswith(prefix):
            return label
    return "未知"


def _codec_rank(codec: str | None) -> int:
    return _CODEC_PREFERENCE.get(_codec_label(codec), 4)


def _approx_size(fmt: dict[str, Any]) -> int | None:
    for key in ("filesize", "filesize_approx"):
        value = fmt.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return None


def _is_storyboard(fmt: dict[str, Any]) -> bool:
    """封面预览图（storyboard）不是可下载视频流。"""
    note = str(fmt.get("format_note") or "").lower()
    return (
        "storyboard" in note
        or fmt.get("ext") == "mhtml"
        or str(fmt.get("format_id") or "").startswith("sb")
    )


def _representative_key(fmt: dict[str, Any]) -> tuple[int, int, int, int, float, float]:
    """代表格式的排序键（越小越优先）。

    顺序刻意贴近 yt-dlp 的实际挑选逻辑，避免"界面显示的编码/体积"与
    "真正下载的流"不一致：
    1. 编码偏好（H.264 最通用）；
    2. MP4 容器优先；
    3. 有已知体积的优先（HLS 变体常常拿不到体积）；
    4. https/http 直连优先于 m3u8（yt-dlp 也如此）；
    5. 高帧率、高码率优先。
    """
    protocol = str(fmt.get("protocol") or "")
    return (
        _codec_rank(fmt.get("vcodec")),
        0 if fmt.get("ext") == "mp4" else 1,
        0 if _approx_size(fmt) is not None else 1,
        0 if protocol in ("https", "http", "dash") else 1,
        -(fmt.get("fps") or 0),
        -(fmt.get("tbr") or 0),
    )


def _best_audio_size(formats: list[dict[str, Any]]) -> int | None:
    candidates = [
        f for f in formats
        if (f.get("acodec") or "none") != "none" and not f.get("has_drm")
    ]
    if not candidates:
        return None
    best = max(candidates, key=lambda f: (f.get("abr") or 0, f.get("tbr") or 0))
    return _approx_size(best)


def _audio_option(formats: list[dict[str, Any]]) -> QualityOption | None:
    """末尾的"仅音频"选项：下载最佳音轨后转成 MP3。"""
    candidates = [
        f for f in formats
        if (f.get("acodec") or "none") != "none" and not f.get("has_drm")
    ]
    if not candidates:
        return None
    best = max(candidates, key=lambda f: (f.get("abr") or 0, f.get("tbr") or 0))
    size = _approx_size(best)
    detail_bits = [f"MP3 {config.AUDIO_MP3_BITRATE}kbps"]
    source_codec = _codec_label(best.get("acodec"))
    if source_codec != "未知":
        detail_bits.append(f"源 {source_codec} 音轨")
    if size:
        detail_bits.append(f"源约 {format_filesize(size)}")
    return QualityOption(
        label="仅音频 · MP3",
        selector=config.AUDIO_ONLY_FORMAT,
        ext="mp3",
        detail=" · ".join(detail_bits),
        approx_size=size,
        is_audio_only=True,
    )


def _quality_options(formats: list[dict[str, Any]]) -> list[QualityOption]:
    """把原始格式列表压缩成"按高度去重"的清晰度列表。"""
    video_formats = [
        f for f in formats
        if (f.get("vcodec") or "none") != "none"
        and f.get("height")
        and not _is_storyboard(f)
        and not f.get("has_drm")
    ]

    grouped: dict[int, list[dict[str, Any]]] = {}
    for fmt in video_formats:
        grouped.setdefault(int(fmt["height"]), []).append(fmt)

    audio_size = _best_audio_size(formats)
    options: list[QualityOption] = []

    for height in sorted(grouped, reverse=True)[: config.MAX_LISTED_FORMATS]:
        representatives = grouped[height]
        rep = min(representatives, key=_representative_key)
        size = _approx_size(rep)
        if size and audio_size:
            size += audio_size  # 合并后的实际体积 = 视频流 + 音频流

        fps = rep.get("fps")
        label = f"{height}p"
        if fps and fps >= 50:
            label += f"{int(round(fps))}"

        detail_bits = [
            str(rep.get("ext") or "").upper(),
            _codec_label(rep.get("vcodec")),
        ]
        if size:
            detail_bits.append(f"约 {format_filesize(size)}")

        options.append(
            QualityOption(
                label=label,
                selector=config.build_format_selector(height),
                ext=str(rep.get("ext") or "mp4"),
                detail=" · ".join(bit for bit in detail_bits if bit),
                height=height,
                fps=fps,
                approx_size=size,
            )
        )

    audio = _audio_option(formats)
    if audio is not None:
        options.append(audio)
    return options


# --------------------------------------------------------------------------- #
# 元信息
# --------------------------------------------------------------------------- #
def _pick_thumbnail(raw: dict[str, Any]) -> str | None:
    best_url: str | None = None
    best_score: tuple[int, int] = (-1, -1)
    for thumb in raw.get("thumbnails") or []:
        url = thumb.get("url")
        if not url:
            continue
        score = (int(thumb.get("width") or 0), int(thumb.get("preference") or 0))
        if score > best_score:
            best_url, best_score = url, score
    return best_url or raw.get("thumbnail")


def _format_upload_date(value: Any) -> str | None:
    text = str(value or "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return None


def _build_video_info(raw: dict[str, Any]) -> VideoInfo:
    if raw.get("_has_drm"):
        raise ExtractionError("该视频受 DRM 保护，无法下载。")
    if raw.get("is_live"):
        raise ExtractionError(
            "该视频正在直播，暂不支持下载。",
            hint="等直播结束、内容转为点播后再试。",
        )

    formats = list(raw.get("formats") or [])
    qualities = tuple(_quality_options(formats))
    if not qualities:
        raise ExtractionError(
            "未找到可下载的清晰度。",
            hint="可能是会员专享、地区限制或需要登录才能访问的内容。",
        )

    duration = raw.get("duration")
    try:
        duration = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None

    return VideoInfo(
        video_id=str(raw.get("id") or ""),
        title=str(raw.get("title") or "未命名视频"),
        webpage_url=str(raw.get("webpage_url") or ""),
        duration=duration,
        duration_text=format_duration(duration),
        uploader=raw.get("uploader") or raw.get("channel"),
        upload_date=_format_upload_date(raw.get("upload_date")),
        view_count=raw.get("view_count"),
        thumbnail=_pick_thumbnail(raw),
        qualities=qualities,
    )


# --------------------------------------------------------------------------- #
# 错误翻译
# --------------------------------------------------------------------------- #
_ERROR_PATTERNS: tuple[tuple[str, str, str | None], ...] = (
    (
        r"confirm you'?re not a bot|sign in to confirm",
        "YouTube 要求人机验证（反爬虫）。",
        "导出浏览器 cookies 为 Netscape 格式，设置环境变量 "
        "YTDL_COOKIES_FILE 指向该文件后重启应用。",
    ),
    (
        r"age[- ]?restricted|confirm your age|inappropriate for some users",
        "该视频有年龄限制，需要登录后才能访问。",
        "同样可通过 YTDL_COOKIES_FILE 提供登录态 cookies。",
    ),
    (r"private video", "该视频是私有视频，无法下载。", None),
    (
        r"video unavailable|this video is unavailable|video is not available"
        r"|removed by the uploader|account associated with this video has been terminated",
        "视频不可用：可能已删除、设为私有或被下架。",
        None,
    ),
    (r"members[- ]only|join this channel|channel members", "该视频仅限频道会员观看。", None),
    (
        r"available in your country|available in your region"
        r"|geo[- ]?restrict|blocked it in your country",
        "该视频在当前地区不可用（地区限制）。",
        "可尝试更换网络环境后重试。",
    ),
    (
        r"unsupported url|is not a valid url|no video formats found",
        "链接无法解析，可能不是 YouTube 视频页面。",
        "请粘贴形如 https://youtu.be/xxxxxxxxxxx 的单个视频链接。",
    ),
    (
        r"http error 429|too many requests",
        "请求过于频繁，已被 YouTube 临时限流。",
        "等待几分钟后重试。",
    ),
    (
        r"timed out|timeout|urlopen error|connection (reset|refused|aborted)"
        r"|getaddrinfo|network is unreachable|temporary failure|ssl",
        "网络连接失败，无法访问 YouTube。",
        "检查网络或代理设置后重试。",
    ),
    (
        r"requested format is not available",
        "所选清晰度当前不可用。",
        "重新解析链接后改选其他清晰度。",
    ),
    (
        r"live event will begin|premieres in|is currently live",
        "直播或首播尚未结束，暂不支持下载。",
        None,
    ),
)


def classify_error(message: str) -> tuple[str, str | None]:
    """把 yt-dlp 的原始报错翻译成 (中文提示, 可选建议)。"""
    text = message or ""
    for pattern, friendly, hint in _ERROR_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return friendly, hint
    cleaned = re.sub(r"^ERROR:\s*", "", text.strip())
    cleaned = re.sub(r"\s+", " ", cleaned)
    return f"解析失败：{cleaned[:200] or '未知错误'}", None


# --------------------------------------------------------------------------- #
# 解析入口
# --------------------------------------------------------------------------- #
def _base_options() -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": config.SOCKET_TIMEOUT,
        "retries": config.RETRIES,
        "extractor_retries": config.RETRIES,
        "cachedir": False,
        "logger": QuietLogger(),
    }
    cookies = config.get_cookies_file()
    if cookies is not None:
        options["cookiefile"] = str(cookies)
    return options


def _extract_raw(url: str, *, depth: int = 0) -> dict[str, Any]:
    if depth > 1:
        raise ExtractionError("链接解析层数过深，请直接粘贴单个视频链接。")

    try:
        with yt_dlp.YoutubeDL(_base_options()) as ydl:
            raw = ydl.extract_info(url, download=False)
    except DownloadError as exc:
        friendly, hint = classify_error(str(exc))
        raise ExtractionError(friendly, hint=hint) from exc
    except ExtractionError:
        raise
    except Exception as exc:  # 兜底：任何库内异常都不应让应用崩溃
        friendly, hint = classify_error(str(exc))
        raise ExtractionError(friendly, hint=hint) from exc

    if not raw:
        raise ExtractionError("解析失败：yt-dlp 未返回任何信息。")

    entries = raw.get("entries")
    if raw.get("_type") in ("playlist", "multi_video") or entries is not None:
        items = [item for item in (entries or []) if item]
        if not items:
            raise ExtractionError(
                "该链接不包含可下载的单集视频。",
                hint="请粘贴单个视频链接，而不是频道页或空播放列表。",
            )
        first = items[0]
        if first.get("formats") or first.get("url"):
            return first
        next_url = first.get("webpage_url") or first.get("url")
        if not next_url:
            raise ExtractionError("播放列表中的首集缺少可解析地址。")
        return _extract_raw(str(next_url), depth=depth + 1)

    return raw


_CACHE: OrderedDict[str, VideoInfo] = OrderedDict()


def clear_cache() -> None:
    """清空解析缓存（界面上的「重新解析」会用到）。"""
    _CACHE.clear()


def extract_video_info(url: str, *, refresh: bool = False) -> VideoInfo:
    """解析单个 YouTube 视频。

    抛 :class:`ExtractionError` 表示失败；成功返回 :class:`VideoInfo`。
    结果会缓存最近 :data:`MAX_CACHE_ENTRIES` 条，``refresh=True`` 强制重新解析。
    """
    text = (url or "").strip()
    if not config.is_supported_url(text):
        raise ExtractionError(
            "链接无效：请粘贴 YouTube 视频链接。",
            hint="支持 youtube.com/watch?v=…、youtu.be/…、/shorts/… 以及 11 位视频 ID。",
        )

    if not refresh:
        cached = _CACHE.get(text)
        if cached is not None:
            _CACHE.move_to_end(text)
            return cached

    raw = _extract_raw(text)
    info = _build_video_info(raw)

    _CACHE[text] = info
    _CACHE.move_to_end(text)
    while len(_CACHE) > MAX_CACHE_ENTRIES:
        _CACHE.popitem(last=False)
    return info
