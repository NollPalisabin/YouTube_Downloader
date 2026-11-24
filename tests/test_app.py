"""app.py 的端到端测试：用 Streamlit AppTest 驱动界面，不启动浏览器。

网络与下载都被替换成假实现，测试只验证界面状态流转与文案。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core.downloader import DownloadProgress  # noqa: E402
from core.extractor import QualityOption, VideoInfo  # noqa: E402

APP_PATH = ROOT / "app.py"


@pytest.fixture(autouse=True)
def _fake_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试不应依赖宿主机是否装了 FFmpeg。

    没有 FFmpeg 时界面会禁用「开始下载」，导致 AppTest 无法点击；
    这里统一返回一个假的检测结果，让交互路径可测。
    """
    info = config.FFmpegInfo(
        ffmpeg=Path("C:/fake/bin/ffmpeg.exe"),
        ffprobe=Path("C:/fake/bin/ffprobe.exe"),
        version="9.9-test",
        source="bundled:C:/fake/bin",
    )
    monkeypatch.setattr(config, "detect_ffmpeg", lambda refresh=False: info)


def _video_quality() -> QualityOption:
    return QualityOption(
        label="1080p60",
        selector=config.build_format_selector(1080),
        ext="mp4",
        detail="MP4 · H.264 · 约 246 MB",
        height=1080,
        fps=60,
        approx_size=246 * 1024 * 1024,
    )


def _audio_quality() -> QualityOption:
    return QualityOption(
        label="仅音频 · MP3",
        selector=config.AUDIO_ONLY_FORMAT,
        ext="mp3",
        detail="MP3 192kbps · 源约 3.3 MB",
        approx_size=3_300_000,
        is_audio_only=True,
    )


def _video_info() -> VideoInfo:
    return VideoInfo(
        video_id="dQw4w9WgXcQ",
        title="测试视频标题",
        webpage_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        duration=213,
        duration_text="3:33",
        uploader="Tester",
        upload_date="2009-10-25",
        view_count=1_234_567,
        thumbnail=None,
        qualities=(_video_quality(), _audio_quality()),
    )


def _find_button(app: AppTest, label: str):
    for button in app.button:
        if button.label == label:
            return button
    raise AssertionError(f"找不到按钮 {label!r}，现有：{[b.label for b in app.button]}")


def _all_text(app: AppTest) -> str:
    chunks: list[str] = []
    for collection in (app.markdown, app.caption, app.error, app.info,
                       app.success, app.warning, app.code, app.title):
        for element in collection:
            chunks.append(str(getattr(element, "value", "") or ""))
    for metric in app.metric:
        chunks.append(f"{getattr(metric, 'label', '')} {getattr(metric, 'value', '')}")
    return "\n".join(chunks)


def _new_app() -> AppTest:
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    return app.run()


# --------------------------------------------------------------------------- #
# 基础渲染
# --------------------------------------------------------------------------- #
def test_app_renders_without_exception() -> None:
    app = _new_app()
    assert not app.exception
    assert config.APP_TITLE in _all_text(app)
    assert config.APP_TAGLINE in _all_text(app)


def test_initial_state_shows_hint() -> None:
    app = _new_app()
    assert "粘贴链接后点击「解析」" in _all_text(app)


def test_can_change_download_dir(tmp_workdir: Path) -> None:
    """侧栏修改下载目录 → 写入 settings.json 并立即生效。"""
    target = tmp_workdir / "新的下载目录"
    app = _new_app()
    app.text_input(key="download_dir_input").set_value(str(target))
    _find_button(app, "保存并切换").click()
    app.run()

    assert not app.exception
    assert "已切换" in _all_text(app)
    assert config.get_download_dir() == target.resolve()
    assert config.paths.read_settings()["download_dir"] == str(target.resolve())


# --------------------------------------------------------------------------- #
# 解析路径
# --------------------------------------------------------------------------- #
def test_invalid_url_shows_friendly_error() -> None:
    app = _new_app()
    app.text_input(key="url_input").set_value("这显然不是一个链接")
    _find_button(app, "解析").click()
    app.run()

    assert not app.exception
    assert "链接无效" in _all_text(app)


def test_parse_renders_card_and_quality_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.extractor.extract_video_info",
        lambda url, refresh=False: _video_info(),
    )
    app = _new_app()
    app.text_input(key="url_input").set_value(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )
    _find_button(app, "解析").click()
    app.run()

    assert not app.exception
    text = _all_text(app)
    assert "测试视频标题" in text
    assert "Tester" in text and "3:33" in text

    radio = app.radio[0]
    assert "1080p60" in radio.options
    assert "仅音频 · MP3" in radio.options
    assert _find_button(app, "开始下载") is not None


def test_parse_failure_shows_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str, refresh: bool = False) -> VideoInfo:
        raise __import__("core.extractor", fromlist=["ExtractionError"]).ExtractionError(
            "视频不可用：可能已删除、设为私有或被下架。",
            hint="换一个视频试试。",
        )

    monkeypatch.setattr("core.extractor.extract_video_info", boom)
    app = _new_app()
    app.text_input(key="url_input").set_value("https://youtu.be/dQw4w9WgXcQ")
    _find_button(app, "解析").click()
    app.run()

    text = _all_text(app)
    assert "视频不可用" in text
    assert "换一个视频试试" in text


# --------------------------------------------------------------------------- #
# 下载路径
# --------------------------------------------------------------------------- #
def test_download_completion_shows_full_path(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    output = tmp_workdir / "测试视频标题 [dQw4w9WgXcQ][1080p].mp4"
    output.write_bytes(b"fake video")

    class FakeJob:
        def __init__(self, info: VideoInfo, quality: QualityOption) -> None:
            self.info = info
            self.quality = quality
            self.error = None
            self._progress = DownloadProgress(
                status="finished",
                percent=100.0,
                phase="已完成",
                output_path=str(output),
            )

        def start(self) -> None:  # 立即"完成"
            return None

        def snapshot(self) -> DownloadProgress:
            return self._progress

        def cancel(self) -> None:
            return None

    monkeypatch.setattr("core.extractor.extract_video_info",
                        lambda url, refresh=False: _video_info())
    monkeypatch.setattr("core.downloader.DownloadJob", FakeJob)

    app = _new_app()
    app.text_input(key="url_input").set_value("https://youtu.be/dQw4w9WgXcQ")
    _find_button(app, "解析").click()
    app.run()

    _find_button(app, "开始下载").click()
    app.run()

    assert not app.exception
    codes = [str(element.value) for element in app.code]
    assert str(output) in "\n".join(codes), f"应显示完整路径，实际：{codes}"
    assert "下载完成" in _all_text(app)


def test_cancelled_download_shows_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    class CancelledJob:
        def __init__(self, info: VideoInfo, quality: QualityOption) -> None:
            self.info = info
            self.quality = quality
            self.error = None
            self._progress = DownloadProgress(status="cancelled", phase="已取消",
                                              message="下载已取消")

        def start(self) -> None:
            return None

        def snapshot(self) -> DownloadProgress:
            return self._progress

        def cancel(self) -> None:
            return None

    monkeypatch.setattr("core.extractor.extract_video_info",
                        lambda url, refresh=False: _video_info())
    monkeypatch.setattr("core.downloader.DownloadJob", CancelledJob)

    app = _new_app()
    app.text_input(key="url_input").set_value("https://youtu.be/dQw4w9WgXcQ")
    _find_button(app, "解析").click()
    app.run()
    _find_button(app, "开始下载").click()
    app.run()

    assert "下载已取消" in _all_text(app)


def test_active_download_renders_progress_and_cancel(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    class ActiveJob:
        def __init__(self, info: VideoInfo, quality: QualityOption) -> None:
            self.info = info
            self.quality = quality
            self.error = None
            self.cancelled = False

        def start(self) -> None:
            return None

        def snapshot(self) -> DownloadProgress:
            return DownloadProgress(
                status="downloading", percent=42.5, phase="正在下载视频流",
                downloaded_bytes=12 * 1024 * 1024, total_bytes=100 * 1024 * 1024,
                speed=3.4 * 1024 * 1024, eta=30,
            )

        def cancel(self) -> None:
            self.cancelled = True

    monkeypatch.setattr("core.extractor.extract_video_info",
                        lambda url, refresh=False: _video_info())
    monkeypatch.setattr("core.downloader.DownloadJob", ActiveJob)

    app = _new_app()
    app.text_input(key="url_input").set_value("https://youtu.be/dQw4w9WgXcQ")
    _find_button(app, "解析").click()
    app.run()
    _find_button(app, "开始下载").click()
    app.run()

    text = _all_text(app)
    assert "正在下载视频流" in text
    assert "已下载 12 MB" in text
    assert "速度 3.4 MB/s" in text
    assert "剩余 0:30" in text
    assert _find_button(app, "取消下载") is not None
    assert app.get("progress"), "应渲染进度条"
