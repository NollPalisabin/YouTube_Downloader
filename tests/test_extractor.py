"""core/extractor.py 的单元测试：不联网，用假的 yt-dlp 返回值驱动。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from yt_dlp.utils import DownloadError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.extractor as extractor  # noqa: E402
from config import build_format_selector  # noqa: E402
from core.extractor import (  # noqa: E402
    ExtractionError,
    classify_error,
    clear_cache,
    extract_video_info,
)

MIB = 1024 * 1024


def _fmt(
    fmt_id: str,
    *,
    ext: str = "mp4",
    height: int | None = None,
    vcodec: str = "avc1.64002A",
    acodec: str = "none",
    fps: float | None = 30,
    tbr: float = 1000,
    size: int | None = None,
    proto: str = "https",
    note: str = "",
    drm: bool = False,
    abr: float | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "format_id": fmt_id,
        "ext": ext,
        "height": height,
        "vcodec": vcodec,
        "acodec": acodec,
        "fps": fps,
        "tbr": tbr,
        "filesize": size,
        "protocol": proto,
        "format_note": note,
        "has_drm": drm,
    }
    if abr is not None:
        data["abr"] = abr
    return data


def _sample_formats() -> list[dict[str, Any]]:
    return [
        _fmt("299", height=1080, fps=60, tbr=3200, size=100 * MIB),
        _fmt("303", ext="webm", height=1080, fps=60, vcodec="vp9",
             tbr=2100, size=60 * MIB),
        # HLS 变体：码率更高但没有体积，不应被当成代表格式
        _fmt("312", height=1080, fps=60, tbr=7400, size=None, proto="m3u8_native"),
        _fmt("298", height=720, fps=60, tbr=1900, size=40 * MIB),
        _fmt("sb0", ext="mhtml", height=90, vcodec="none", acodec="none", note="storyboard"),
        _fmt("140", ext="m4a", acodec="mp4a.40.2", fps=None, tbr=129,
             size=5 * MIB, abr=129.0),
        _fmt("251", ext="webm", acodec="opus", fps=None, tbr=128,
             size=5 * MIB, abr=128.0),
    ]


def _sample_payload() -> dict[str, Any]:
    return {
        "id": "dQw4w9WgXcQ",
        "title": "测试视频",
        "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "uploader": "Tester",
        "duration": 213,
        "upload_date": "20091025",
        "view_count": 12345,
        "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/default.jpg",
        "thumbnails": [
            {"url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/default.jpg", "width": 120},
            {"url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg", "width": 1280},
        ],
        "formats": _sample_formats(),
    }


class _FakeYDL:
    payload: Any = {}
    calls = 0

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.options = options or {}

    def __enter__(self) -> "_FakeYDL":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def extract_info(self, url: str, download: bool = False) -> Any:
        type(self).calls += 1
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


@pytest.fixture
def fake_ydl(monkeypatch: pytest.MonkeyPatch):
    """安装假的 yt-dlp：返回值由测试指定。"""
    clear_cache()

    def install(payload: Any) -> type[_FakeYDL]:
        fake = type("FakeYDL", (_FakeYDL,), {"payload": payload, "calls": 0})
        monkeypatch.setattr(extractor.yt_dlp, "YoutubeDL", fake)
        return fake

    yield install
    clear_cache()


# --------------------------------------------------------------------------- #
# 元信息映射
# --------------------------------------------------------------------------- #
def test_extract_maps_metadata(fake_ydl) -> None:
    fake_ydl(_sample_payload())
    info = extract_video_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert info.video_id == "dQw4w9WgXcQ"
    assert info.title == "测试视频"
    assert info.uploader == "Tester"
    assert info.duration == 213 and info.duration_text == "3:33"
    assert info.upload_date == "2009-10-25"
    assert info.view_count == 12345
    # 最大宽度的封面优先
    assert info.thumbnail.endswith("maxresdefault.jpg")


def test_quality_list_sorted_desc_with_audio_last(fake_ydl) -> None:
    fake_ydl(_sample_payload())
    info = extract_video_info("https://youtu.be/dQw4w9WgXcQ")

    labels = [q.label for q in info.qualities]
    assert labels == ["1080p60", "720p60", "仅音频 · MP3"]
    heights = [q.height for q in info.qualities if not q.is_audio_only]
    assert heights == sorted(heights, reverse=True)
    assert info.qualities[-1].is_audio_only
    assert info.qualities[-1].ext == "mp3"
    assert info.best_quality is not None and info.best_quality.height == 1080


def test_representative_skips_hls_and_sums_audio_size(fake_ydl) -> None:
    fake_ydl(_sample_payload())
    info = extract_video_info("https://youtu.be/dQw4w9WgXcQ")

    top = info.qualities[0]
    assert "H.264" in top.detail          # 不选 HLS 变体、不选 vp9
    assert top.ext == "mp4"
    # 视频流 100 MiB + 最佳音频 5 MiB
    assert top.approx_size == 105 * MIB
    assert "105 MB" in top.detail


def test_storyboard_is_filtered() -> None:
    options = extractor._quality_options(_sample_formats())
    assert all(q.height != 90 for q in options)


def test_selectors_carry_height_limit(fake_ydl) -> None:
    fake_ydl(_sample_payload())
    info = extract_video_info("https://youtu.be/dQw4w9WgXcQ")
    for quality in info.qualities:
        if quality.is_audio_only:
            continue
        assert f"height<={quality.height}" in quality.selector
    assert build_format_selector(720) == info.qualities[1].selector


def test_drm_format_excluded() -> None:
    formats = [
        _fmt("137", height=1080, size=10 * MIB),
        _fmt("999", height=1080, size=99 * MIB, drm=True),
        _fmt("140", ext="m4a", acodec="mp4a.40.2", fps=None, size=MIB, abr=128),
    ]
    options = extractor._quality_options(formats)
    assert options[0].approx_size == 11 * MIB


# --------------------------------------------------------------------------- #
# 异常路径
# --------------------------------------------------------------------------- #
def test_invalid_url_raises_before_network(fake_ydl) -> None:
    fake = fake_ydl(_sample_payload())
    with pytest.raises(ExtractionError) as excinfo:
        extract_video_info("https://vimeo.com/12345")
    assert "链接无效" in str(excinfo.value)
    assert fake.calls == 0


def test_live_video_raises(fake_ydl) -> None:
    payload = _sample_payload()
    payload["is_live"] = True
    fake_ydl(payload)
    with pytest.raises(ExtractionError) as excinfo:
        extract_video_info("https://youtu.be/dQw4w9WgXcQ")
    assert "直播" in str(excinfo.value)


def test_drm_video_raises(fake_ydl) -> None:
    payload = _sample_payload()
    payload["_has_drm"] = True
    fake_ydl(payload)
    with pytest.raises(ExtractionError) as excinfo:
        extract_video_info("https://youtu.be/dQw4w9WgXcQ")
    assert "DRM" in str(excinfo.value)


def test_no_formats_raises(fake_ydl) -> None:
    payload = _sample_payload()
    payload["formats"] = []
    fake_ydl(payload)
    with pytest.raises(ExtractionError) as excinfo:
        extract_video_info("https://youtu.be/dQw4w9WgXcQ")
    assert "清晰度" in str(excinfo.value)


def test_download_error_is_translated(fake_ydl) -> None:
    fake_ydl(DownloadError("ERROR: Private video. Sign in if you've been granted access"))
    with pytest.raises(ExtractionError) as excinfo:
        extract_video_info("https://youtu.be/dQw4w9WgXcQ")
    assert "私有" in str(excinfo.value)


def test_playlist_picks_first_entry(fake_ydl) -> None:
    payload = {
        "_type": "playlist",
        "entries": [
            {"id": "a", "formats": _sample_formats(), "title": "第一集",
             "webpage_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"},
            {"id": "b", "formats": _sample_formats(), "title": "第二集"},
        ],
    }
    fake_ydl(payload)
    info = extract_video_info("https://www.youtube.com/playlist?list=PL123")
    assert info.title == "第一集"


def test_empty_playlist_raises(fake_ydl) -> None:
    fake_ydl({"_type": "playlist", "entries": []})
    with pytest.raises(ExtractionError) as excinfo:
        extract_video_info("https://www.youtube.com/playlist?list=PL123")
    assert "播放列表" in str(excinfo.value) or "单集" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 缓存
# --------------------------------------------------------------------------- #
def test_cache_avoids_repeat_extraction(fake_ydl) -> None:
    fake = fake_ydl(_sample_payload())
    url = "https://youtu.be/dQw4w9WgXcQ"
    extract_video_info(url)
    extract_video_info(url)
    assert fake.calls == 1

    extract_video_info(url, refresh=True)
    assert fake.calls == 2


def test_cache_is_bounded(fake_ydl) -> None:
    fake_ydl(_sample_payload())
    for index in range(extractor.MAX_CACHE_ENTRIES + 3):
        extract_video_info(f"https://youtu.be/aaaaaaaaa{index:02d}")
    assert len(extractor._CACHE) <= extractor.MAX_CACHE_ENTRIES


# --------------------------------------------------------------------------- #
# 错误翻译表
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected_fragment"),
    [
        ("ERROR: [youtube] x: Video unavailable", "视频不可用"),
        ("ERROR: Sign in to confirm you're not a bot", "人机验证"),
        ("ERROR: This video is age-restricted", "年龄限制"),
        ("ERROR: The uploader has not made this video available in your country", "地区"),
        ("ERROR: Unsupported URL: https://example.com", "链接无法解析"),
        ("ERROR: HTTP Error 429: Too Many Requests", "限流"),
        ("ERROR: Unable to download API page: <urlopen error timed out>", "网络"),
        ("ERROR: Requested format is not available", "清晰度"),
    ],
)
def test_classify_error(raw: str, expected_fragment: str) -> None:
    friendly, _hint = classify_error(raw)
    assert expected_fragment in friendly


def test_classify_error_falls_back_to_raw_text() -> None:
    friendly, hint = classify_error("ERROR: 某种没见过的错误")
    assert "某种没见过的错误" in friendly
    assert hint is None
