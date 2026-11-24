"""core/downloader.py 的单元测试：用假的 yt-dlp 验证进度、路径解析与异常翻译。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

import pytest
from yt_dlp.utils import DownloadError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import core.downloader as downloader  # noqa: E402
from core.downloader import (  # noqa: E402
    DownloadCancelledError,
    DownloadFailed,
    DownloadJob,
    download,
)
from core.extractor import QualityOption, VideoInfo  # noqa: E402

VIDEO_ID = "dQw4w9WgXcQ"


def _video_quality() -> QualityOption:
    return QualityOption(
        label="144p",
        selector=config.build_format_selector(144),
        ext="mp4",
        detail="MP4 · H.264",
        height=144,
        fps=30,
        approx_size=1_000_000,
    )


def _audio_quality() -> QualityOption:
    return QualityOption(
        label="仅音频 · MP3",
        selector=config.AUDIO_ONLY_FORMAT,
        ext="mp3",
        detail="MP3 192kbps",
        approx_size=500_000,
        is_audio_only=True,
    )


def _info() -> VideoInfo:
    return VideoInfo(
        video_id=VIDEO_ID,
        title="测试视频",
        webpage_url=f"https://www.youtube.com/watch?v={VIDEO_ID}",
        duration=10,
        duration_text="0:10",
        uploader="Tester",
        upload_date="2020-01-01",
        view_count=1,
        thumbnail=None,
        qualities=(_video_quality(), _audio_quality()),
    )


def _ffmpeg_info(tmp: Path) -> config.FFmpegInfo:
    return config.FFmpegInfo(
        ffmpeg=tmp / "ffmpeg.exe",
        ffprobe=tmp / "ffprobe.exe",
        version="9.1",
        source="test",
    )


class _FakeYDL:
    script: Callable[[dict[str, Any]], Any] | None = None
    last_options: dict[str, Any] = {}

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.options = options or {}
        type(self).last_options = self.options

    def __enter__(self) -> "_FakeYDL":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def extract_info(self, url: str, download: bool = True) -> Any:
        assert self.script is not None
        return self.script(self.options)


@pytest.fixture
def fake_ydl(monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path):
    """安装假 yt-dlp 和假的 FFmpeg 检测结果。"""
    monkeypatch.setattr(config, "require_ffmpeg", lambda: _ffmpeg_info(tmp_workdir))
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", _FakeYDL)

    def install(script: Callable[[dict[str, Any]], Any]) -> type[_FakeYDL]:
        fake = type("FakeYDL", (_FakeYDL,), {"script": staticmethod(script)})
        monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", fake)
        return fake

    return install


def _fire_downloading(options: dict[str, Any], **data: Any) -> None:
    for hook in options.get("progress_hooks") or []:
        hook(data)


def _fire_postprocessor(options: dict[str, Any], name: str, status: str = "started") -> None:
    for hook in options.get("postprocessor_hooks") or []:
        hook({"status": status, "postprocessor": name})


def _make_file(options: dict[str, Any], name: str) -> Path:
    home = Path(options["paths"]["home"])
    home.mkdir(parents=True, exist_ok=True)
    path = home / name
    path.write_bytes(b"fake media")
    return path


def _two_stream_script(options: dict[str, Any]) -> dict[str, Any]:
    path = _make_file(options, f"测试视频 [{VIDEO_ID}].mp4")
    video = {"format_id": "160", "vcodec": "avc1", "acodec": "none"}
    audio = {"format_id": "140", "vcodec": "none", "acodec": "mp4a"}
    _fire_downloading(options, status="downloading", downloaded_bytes=50,
                      total_bytes=100, info_dict=video, speed=1024.0, eta=5)
    _fire_downloading(options, status="finished", info_dict=video)
    _fire_downloading(options, status="downloading", downloaded_bytes=10,
                      total_bytes=10, info_dict=audio, speed=2048.0, eta=0)
    _fire_downloading(options, status="finished", info_dict=audio)
    _fire_postprocessor(options, "Merger")
    return {"filepath": str(path), "requested_downloads": [{"filepath": str(path)}]}


# --------------------------------------------------------------------------- #
# 正常路径
# --------------------------------------------------------------------------- #
def test_execute_returns_final_path(fake_ydl, tmp_workdir: Path) -> None:
    fake_ydl(_two_stream_script)
    job = DownloadJob(_info(), _video_quality(), download_dir=tmp_workdir)

    path = job.execute()

    assert path.is_file() and path.suffix == ".mp4"
    assert job.snapshot().status == "finished"
    assert job.snapshot().percent == 100.0
    assert job.snapshot().output_path == str(path)
    assert job.result == path


def test_progress_is_composited_across_streams(fake_ydl, tmp_workdir: Path) -> None:
    fake_ydl(_two_stream_script)
    snapshots: list[downloader.DownloadProgress] = []
    job = DownloadJob(_info(), _video_quality(), download_dir=tmp_workdir,
                      on_update=snapshots.append)
    job.execute()

    percents = [s.percent for s in snapshots]
    assert percents == sorted(percents), "进度必须单调不减"
    assert any(s.phase.startswith("正在下载视频流") for s in snapshots)
    assert any(s.phase.startswith("正在下载音频流") for s in snapshots)
    assert any("合并" in s.phase for s in snapshots)
    # 两路流各占一半：视频下到 50% 时总进度约 25%，音频下完时接近 99%
    assert any(20 <= s.percent <= 30 for s in snapshots)
    assert max(percents) >= 99
    assert snapshots[-1].status == "finished"


def test_single_muxed_stream_counts_as_one(fake_ydl, tmp_workdir: Path) -> None:
    def script(options: dict[str, Any]) -> dict[str, Any]:
        path = _make_file(options, f"muxed [{VIDEO_ID}].mp4")
        _fire_downloading(options, status="downloading", downloaded_bytes=5, total_bytes=10,
                          info_dict={"format_id": "18", "vcodec": "avc1", "acodec": "mp4a"})
        _fire_downloading(options, status="finished",
                          info_dict={"format_id": "18", "vcodec": "avc1", "acodec": "mp4a"})
        return {"filepath": str(path)}

    fake_ydl(script)
    job = DownloadJob(_info(), _video_quality(), download_dir=tmp_workdir)
    job.execute()
    assert job.snapshot().status == "finished"


def test_audio_job_adds_mp3_postprocessor(fake_ydl, tmp_workdir: Path) -> None:
    def script(options: dict[str, Any]) -> dict[str, Any]:
        path = _make_file(options, f"测试视频 [{VIDEO_ID}].mp3")
        _fire_postprocessor(options, "ExtractAudio")
        return {"filepath": str(path)}

    fake = fake_ydl(script)
    job = DownloadJob(_info(), _audio_quality(), download_dir=tmp_workdir)
    path = job.execute()

    assert path.suffix == ".mp3"
    options = fake.last_options
    assert options["format"] == config.AUDIO_ONLY_FORMAT
    postprocessors = options["postprocessors"]
    assert postprocessors[0]["key"] == "FFmpegExtractAudio"
    assert postprocessors[0]["preferredcodec"] == "mp3"
    assert options["merge_output_format"] == "mp4"
    assert options["ffmpeg_location"] == str(tmp_workdir)
    assert "[audio]" in options["outtmpl"]


def test_video_outtmpl_contains_height(fake_ydl, tmp_workdir: Path) -> None:
    fake = fake_ydl(_two_stream_script)
    DownloadJob(_info(), _video_quality(), download_dir=tmp_workdir).execute()
    assert "[144p]" in fake.last_options["outtmpl"]


def test_output_path_falls_back_to_glob(fake_ydl, tmp_workdir: Path) -> None:
    def script(options: dict[str, Any]) -> dict[str, Any]:
        _make_file(options, f"没有路径 [{VIDEO_ID}].mp4")
        (tmp_workdir / f"残留 [{VIDEO_ID}].mp4.part").write_bytes(b"partial")
        return {"title": "没有路径"}  # 不带 filepath

    fake_ydl(script)
    job = DownloadJob(_info(), _video_quality(), download_dir=tmp_workdir)
    path = job.execute()
    assert path.suffix == ".mp4" and not path.name.endswith(".part")


def test_background_thread_reaches_finished(fake_ydl, tmp_workdir: Path) -> None:
    fake_ydl(_two_stream_script)
    job = DownloadJob(_info(), _video_quality(), download_dir=tmp_workdir)
    job.start()
    assert job.wait(timeout=10) is True
    assert job.snapshot().is_finished
    assert job.result is not None and job.result.is_file()
    assert job.error is None


def test_download_helper(fake_ydl, tmp_workdir: Path) -> None:
    fake_ydl(_two_stream_script)
    path = download(_info(), _video_quality(), download_dir=tmp_workdir)
    assert path.is_file()


# --------------------------------------------------------------------------- #
# 异常路径
# --------------------------------------------------------------------------- #
def test_missing_ffmpeg_becomes_download_failed(
    fake_ydl, tmp_workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> None:
        raise config.FFmpegNotFoundError("未检测到 FFmpeg，无法合并音视频。", "安装指引……")

    monkeypatch.setattr(config, "require_ffmpeg", boom)
    fake_ydl(_two_stream_script)
    job = DownloadJob(_info(), _video_quality(), download_dir=tmp_workdir)

    with pytest.raises(DownloadFailed) as excinfo:
        job.execute()
    assert "FFmpeg" in str(excinfo.value)
    assert excinfo.value.hint == "安装指引……"
    assert job.snapshot().status == "failed"


def test_download_error_is_translated(fake_ydl, tmp_workdir: Path) -> None:
    def script(options: dict[str, Any]) -> Any:
        raise DownloadError("ERROR: Private video. Sign in if you've been granted access")

    fake_ydl(script)
    job = DownloadJob(_info(), _video_quality(), download_dir=tmp_workdir)
    with pytest.raises(DownloadFailed) as excinfo:
        job.execute()
    assert "私有" in str(excinfo.value)
    assert job.snapshot().status == "failed"


def test_missing_output_file_raises(fake_ydl, tmp_workdir: Path) -> None:
    fake_ydl(lambda options: {"title": "nothing"})
    job = DownloadJob(_info(), _video_quality(), download_dir=tmp_workdir)
    with pytest.raises(DownloadFailed) as excinfo:
        job.execute()
    assert "成品文件" in str(excinfo.value)


def test_cancel_before_download(fake_ydl, tmp_workdir: Path) -> None:
    def script(options: dict[str, Any]) -> dict[str, Any]:
        _fire_downloading(options, status="downloading", downloaded_bytes=1, total_bytes=100,
                          info_dict={"format_id": "160", "vcodec": "avc1", "acodec": "none"})
        return {}

    fake_ydl(script)
    job = DownloadJob(_info(), _video_quality(), download_dir=tmp_workdir)
    job.cancel()
    with pytest.raises(DownloadCancelledError):
        job.execute()
    assert job.snapshot().status == "cancelled"


def test_cancel_removes_partial_files(fake_ydl, tmp_workdir: Path) -> None:
    def script(options: dict[str, Any]) -> dict[str, Any]:
        _make_file(options, f"视频 [{VIDEO_ID}][144p].f160.mp4.part")
        _fire_downloading(options, status="downloading", downloaded_bytes=1, total_bytes=100,
                          info_dict={"format_id": "160", "vcodec": "avc1", "acodec": "none"})
        return {}

    fake_ydl(script)
    job = DownloadJob(_info(), _video_quality(), download_dir=tmp_workdir)
    job.cancel()
    with pytest.raises(DownloadCancelledError):
        job.execute()
    assert not list(tmp_workdir.glob("*.part")), "取消后不应留下分片残留"


def test_background_thread_records_error(fake_ydl, tmp_workdir: Path) -> None:
    def script(options: dict[str, Any]) -> Any:
        raise DownloadError("ERROR: Video unavailable")

    fake_ydl(script)
    job = DownloadJob(_info(), _video_quality(), download_dir=tmp_workdir)
    job.start()
    assert job.wait(timeout=10) is True
    assert isinstance(job.error, DownloadFailed)
    assert job.snapshot().status == "failed"


def test_unwritable_download_dir(fake_ydl, tmp_workdir: Path) -> None:
    blocker = tmp_workdir / "blocked"
    blocker.write_text("我是文件不是目录", encoding="utf-8")
    fake_ydl(_two_stream_script)
    job = DownloadJob(_info(), _video_quality(), download_dir=blocker)
    with pytest.raises(DownloadFailed) as excinfo:
        job.execute()
    assert "下载目录" in str(excinfo.value)
