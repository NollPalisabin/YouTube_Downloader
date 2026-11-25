"""cookies 配置的单元测试：优先级、导入/清除、传给 yt-dlp 的参数。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import paths  # noqa: E402

NETSCAPE = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tSID\tabc\n"
)


def test_cookie_options_empty_by_default() -> None:
    assert config.cookie_options() == {}
    assert config.get_cookies_file() is None
    assert config.get_cookies_browser() is None


def test_save_and_remove_cookie_file(tmp_workdir: Path) -> None:
    saved = config.save_cookies_file(NETSCAPE.encode("utf-8"))

    assert saved.is_file()
    assert saved == config.cookies_file_path()
    assert config.cookie_options() == {"cookiefile": str(saved)}
    assert "已启用 cookies 文件" in config.cookies_status()

    assert config.remove_cookies_file() is True
    assert config.cookie_options() == {}
    assert config.remove_cookies_file() is False


@pytest.mark.parametrize("payload", [b"", b"   ", b"not a netscape cookie file"])
def test_reject_invalid_cookie_file(payload: bytes) -> None:
    with pytest.raises(ValueError):
        config.save_cookies_file(payload)


def test_browser_choice_is_persisted() -> None:
    config.set_cookies_browser("chrome")

    assert config.get_cookies_browser() == "chrome"
    assert config.cookie_options() == {"cookiesfrombrowser": ("chrome",)}
    assert "Chrome" in config.cookies_status()

    config.set_cookies_browser(None)
    assert config.get_cookies_browser() is None
    assert config.cookie_options() == {}


def test_reject_unknown_browser() -> None:
    with pytest.raises(ValueError):
        config.set_cookies_browser("internet-explorer")


def test_file_wins_over_browser() -> None:
    config.set_cookies_browser("safari")
    saved = config.save_cookies_file(NETSCAPE.encode("utf-8"))

    assert config.cookie_options() == {"cookiefile": str(saved)}


def test_env_var_has_highest_priority(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    config.set_cookies_browser("firefox")
    config.save_cookies_file(NETSCAPE.encode("utf-8"))

    external = tmp_workdir / "external-cookies.txt"
    external.write_text(NETSCAPE, encoding="utf-8")
    monkeypatch.setenv("YTDL_COOKIES_FILE", str(external))

    assert config.get_cookies_file() == external
    assert config.cookie_options() == {"cookiefile": str(external)}


def test_missing_env_file_falls_through(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    monkeypatch.setenv("YTDL_COOKIES_FILE", str(tmp_workdir / "nope.txt"))
    config.set_cookies_browser("edge")

    assert config.get_cookies_file() is None
    assert config.cookie_options() == {"cookiesfrombrowser": ("edge",)}


# --------------------------------------------------------------------------- #
# 两个 yt-dlp 封装层都要带上 cookies 参数
# --------------------------------------------------------------------------- #
def test_extractor_options_include_cookies() -> None:
    from core.extractor import _base_options

    config.set_cookies_browser("chrome")
    options = _base_options()
    assert options["cookiesfrombrowser"] == ("chrome",)


def test_downloader_options_include_cookies(tmp_workdir: Path) -> None:
    from core.downloader import DownloadJob
    from core.extractor import QualityOption, VideoInfo

    config.save_cookies_file(NETSCAPE.encode("utf-8"))

    info = VideoInfo(
        video_id="dQw4w9WgXcQ",
        title="t",
        webpage_url="https://youtu.be/dQw4w9WgXcQ",
        duration=1,
        duration_text="0:01",
        uploader=None,
        upload_date=None,
        view_count=None,
        thumbnail=None,
        qualities=(),
    )
    quality = QualityOption(label="144p", selector="bv*+ba/b", ext="mp4", detail="", height=144)
    job = DownloadJob(info, quality, download_dir=tmp_workdir)

    class _FakeFFmpeg:
        directory = tmp_workdir

    options = job._build_options(_FakeFFmpeg())  # noqa: SLF001 - 直接测内部选项
    assert options["cookiefile"] == str(config.cookies_file_path())
