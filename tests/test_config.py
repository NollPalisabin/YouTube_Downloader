"""config.py 的单元测试：路径解析、链接校验、FFmpeg 检测兜底。"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402


def test_default_download_dir_is_inside_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YTDL_DOWNLOAD_DIR", raising=False)
    monkeypatch.delenv("YOUTUBE_DOWNLOAD_DIR", raising=False)
    assert config.get_download_dir() == (config.BASE_DIR / "downloads").resolve()


def test_download_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path) -> None:
    monkeypatch.setenv("YTDL_DOWNLOAD_DIR", str(tmp_workdir / "videos"))
    assert config.get_download_dir() == (tmp_workdir / "videos").resolve()
    assert config.ensure_download_dir().is_dir()


def test_ensure_download_dir_creates_nested(monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path) -> None:
    target = tmp_workdir / "a" / "b" / "c"
    monkeypatch.setenv("YTDL_DOWNLOAD_DIR", str(target))
    assert config.ensure_download_dir() == target.resolve()
    assert target.is_dir()


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "http://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "www.youtube.com/watch?v=dQw4w9WgXcQ",
        "dQw4w9WgXcQ",
        "  https://youtube.com/shorts/dQw4w9WgXcQ  ",
    ],
)
def test_supported_urls(url: str) -> None:
    assert config.is_supported_url(url)


@pytest.mark.parametrize("url", ["", None, "   ", "not a url", "https://vimeo.com/12345",
                                 "https://youtube.com.evil.com/watch?v=x"])
def test_rejected_urls(url: str | None) -> None:
    assert not config.is_supported_url(url)


def test_format_selector() -> None:
    assert config.build_format_selector() == "bv*+ba/b"
    selector = config.build_format_selector(1080)
    assert "height<=1080" in selector and selector.endswith("/bv*+ba/b")


def test_output_template_encodes_quality() -> None:
    assert config.build_output_template(1080) == "%(title).120B [%(id)s][1080p].%(ext)s"
    assert config.build_output_template(None, audio_only=True) == \
        "%(title).120B [%(id)s][audio].%(ext)s"
    assert config.build_output_template() == "%(title).120B [%(id)s].%(ext)s"


def test_detect_ffmpeg_returns_none_or_info() -> None:
    info = config.detect_ffmpeg(refresh=True)
    if info is None:
        assert not config.ffmpeg_available()
        with pytest.raises(config.FFmpegNotFoundError) as excinfo:
            config.require_ffmpeg()
        assert "FFmpeg" in excinfo.value.hint
    else:
        assert info.ffmpeg.is_file()
        assert config.require_ffmpeg() is info
        assert info.directory.exists()


def test_install_hint_covers_all_platforms() -> None:
    for system in ("Windows", "Darwin", "Linux"):
        hint = config.ffmpeg_install_hint(system)
        assert hint and "FFmpeg" in hint


def test_environment_report_shape() -> None:
    report = config.environment_report()
    for key in ("python_version", "python_ok", "platform", "download_dir",
                "ffmpeg_ok", "ffprobe_ok"):
        assert key in report
    assert report["python_ok"] is (sys.version_info[:2] >= config.MIN_PYTHON)


# --------------------------------------------------------------------------- #
# FFmpeg 检测链路（用 monkeypatch 替代真实二进制，避免依赖本机安装）
# --------------------------------------------------------------------------- #
EXE = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
PROBE = "ffprobe.exe" if os.name == "nt" else "ffprobe"


@pytest.fixture
def reset_ffmpeg_cache():
    """测试结束后清空缓存，避免影响其它用例。"""
    yield
    config.detect_ffmpeg(refresh=True)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"stub")
    return path


def test_env_var_pointing_to_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path, reset_ffmpeg_cache: None
) -> None:
    bindir = tmp_workdir / "ffmpeg-bin"
    ffmpeg = _touch(bindir / EXE)
    _touch(bindir / PROBE)
    monkeypatch.setenv("YTDL_FFMPEG_PATH", str(bindir))
    monkeypatch.setattr(config, "_probe_version", lambda _exe: "7.1.1")

    info = config.detect_ffmpeg(refresh=True)
    assert info is not None
    assert info.ffmpeg == ffmpeg
    assert info.source.startswith("environment")
    assert info.directory == bindir
    assert info.has_ffprobe, "同目录下的 ffprobe 应被识别"


def test_env_var_pointing_to_file(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path, reset_ffmpeg_cache: None
) -> None:
    ffmpeg = _touch(tmp_workdir / "custom" / EXE)
    monkeypatch.setenv("YTDL_FFMPEG_PATH", str(ffmpeg))
    monkeypatch.setattr(config, "_probe_version", lambda _exe: "6.0")

    info = config.detect_ffmpeg(refresh=True)
    assert info is not None and info.ffmpeg == ffmpeg


def test_env_var_pointing_to_missing_path_falls_through(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path, reset_ffmpeg_cache: None
) -> None:
    monkeypatch.setenv("YTDL_FFMPEG_PATH", str(tmp_workdir / "does-not-exist"))
    monkeypatch.setattr(config, "_probe_version", lambda _exe: "7.0")
    monkeypatch.setattr(config.shutil, "which", lambda _name: None)
    monkeypatch.setattr(config.paths, "bundled_bin_dirs", list)
    monkeypatch.setattr(config, "_candidate_directories", list)
    assert config.detect_ffmpeg(refresh=True) is None
    with pytest.raises(config.FFmpegNotFoundError):
        config.require_ffmpeg()


def test_broken_binary_is_rejected(tmp_workdir: Path) -> None:
    """可执行文件存在但跑不起来（损坏 / 架构不符）时不能算作可用。"""
    fake = _touch(tmp_workdir / EXE)
    assert config._probe_version(fake) is None


def test_ffprobe_optional(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path, reset_ffmpeg_cache: None
) -> None:
    """只有 ffmpeg、没有 ffprobe 时仍应可用，但标记为降级。"""
    ffmpeg = _touch(tmp_workdir / "only-ffmpeg" / EXE)
    monkeypatch.setenv("YTDL_FFMPEG_PATH", str(ffmpeg))
    monkeypatch.setattr(config, "_probe_version", lambda _exe: "7.1")
    monkeypatch.setattr(config.shutil, "which", lambda _name: None)
    monkeypatch.setattr(config.paths, "bundled_bin_dirs", list)
    monkeypatch.setattr(config, "_candidate_directories", list)

    info = config.detect_ffmpeg(refresh=True)
    assert info is not None
    assert not info.has_ffprobe
    assert "未检测到" in info.describe()


@pytest.mark.skipif(platform.system() != "Windows", reason="仅 Windows 有 winget portable 布局")
def test_winget_portable_layout_is_scanned(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path, reset_ffmpeg_cache: None
) -> None:
    """winget 的 portable 包解压在 Packages\\<pkg>\\<ver>\\bin，必须能被发现。"""
    pkg = (tmp_workdir / "Microsoft" / "WinGet" / "Packages"
           / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe" / "ffmpeg-9.1-full_build" / "bin")
    _touch(pkg / EXE)
    _touch(pkg / PROBE)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_workdir))

    assert pkg in config._winget_package_dirs()

    monkeypatch.setattr(config.shutil, "which", lambda _name: None)
    monkeypatch.setattr(config.paths, "bundled_bin_dirs", list)
    monkeypatch.setattr(config, "_registry_path_dirs", list)
    monkeypatch.setattr(config, "_probe_version", lambda _exe: "9.1")

    info = config.detect_ffmpeg(refresh=True)
    assert info is not None and info.ffmpeg == pkg / EXE


# --------------------------------------------------------------------------- #
# 随包捆绑的 FFmpeg（打包分发依赖这一层）
# --------------------------------------------------------------------------- #
def test_bundled_ffmpeg_wins_over_system_path(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path, reset_ffmpeg_cache: None
) -> None:
    bindir = tmp_workdir / "app" / "_internal" / "bin"
    bundled = _touch(bindir / EXE)
    _touch(bindir / PROBE)
    system_ffmpeg = _touch(tmp_workdir / "system" / EXE)

    monkeypatch.setattr(config.paths, "bundled_bin_dirs", lambda: [bindir])
    monkeypatch.setattr(config.shutil, "which", lambda _name: str(system_ffmpeg))
    monkeypatch.setattr(config, "_probe_version", lambda _exe: "9.1")

    info = config.detect_ffmpeg(refresh=True)
    assert info is not None
    assert info.ffmpeg == bundled, "捆绑版必须优先于系统 PATH"
    assert info.source == f"bundled:{bindir}"
    assert info.has_ffprobe


def test_bundled_ffmpeg_falls_back_to_path(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path, reset_ffmpeg_cache: None
) -> None:
    """捆绑目录为空时，仍要能退回系统安装的版本。"""
    bindir = tmp_workdir / "empty-bin"
    bindir.mkdir()
    system_ffmpeg = _touch(tmp_workdir / "system" / EXE)

    monkeypatch.setattr(config.paths, "bundled_bin_dirs", lambda: [bindir])
    monkeypatch.setattr(config.shutil, "which", lambda _name: str(system_ffmpeg))
    monkeypatch.setattr(config, "_probe_version", lambda _exe: "8.0")

    info = config.detect_ffmpeg(refresh=True)
    assert info is not None and info.ffmpeg == system_ffmpeg
    assert info.source == "PATH"


def test_frozen_install_hint_talks_about_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.paths, "is_frozen", lambda: True)
    hint = config.ffmpeg_install_hint("Windows")
    assert "捆绑" in hint
    assert "winget" not in hint, "打包后不该再劝用户去装系统 FFmpeg"


# --------------------------------------------------------------------------- #
# 下载目录：settings.json 覆盖 / 不可写回退 / 界面修改
# --------------------------------------------------------------------------- #
def test_settings_override_download_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    target = tmp_workdir / "自定义目录"
    config.paths.write_settings(download_dir=str(target))
    config.reset_download_dir_cache()

    assert config.get_download_dir() == target.resolve()


def test_default_download_dir_is_deployment_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    """模拟打包后：默认下载目录应是 exe 同级的 downloads。"""
    exe_dir = tmp_workdir / "YouTube_Downloader"
    exe_dir.mkdir()
    monkeypatch.setattr(config.paths, "app_dir", lambda: exe_dir)
    config.reset_download_dir_cache()

    assert config.get_download_dir() == (exe_dir / "downloads").resolve()


def test_unwritable_primary_falls_back_to_user_downloads(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    blocker = tmp_workdir / "blocked"
    blocker.write_text("我是文件，不是目录", encoding="utf-8")
    monkeypatch.setattr(config.paths, "app_dir", lambda: blocker)
    config.reset_download_dir_cache()

    expected = (Path.home() / "Downloads" / "YouTube_Downloader").resolve()
    assert config.get_download_dir() == expected


def test_set_download_dir_persists(monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path) -> None:
    target = tmp_workdir / "my videos"

    saved = config.set_download_dir(str(target))
    assert saved == target.resolve()
    assert config.get_download_dir() == target.resolve()
    assert config.paths.read_settings()["download_dir"] == str(target.resolve())

    # 模拟"下次启动"：清空缓存后依然生效
    config.reset_download_dir_cache()
    assert config.get_download_dir() == target.resolve()


def test_set_download_dir_rejects_invalid(tmp_workdir: Path) -> None:
    with pytest.raises(ValueError):
        config.set_download_dir("   ")

    plain_file = tmp_workdir / "not-a-dir.txt"
    plain_file.write_text("x", encoding="utf-8")
    with pytest.raises(OSError):
        config.set_download_dir(str(plain_file))


def test_environment_report_has_packaging_fields() -> None:
    report = config.environment_report()
    for key in ("frozen", "app_dir", "resource_root", "ffmpeg_source"):
        assert key in report
    assert report["frozen"] is False  # 测试环境未打包
    assert Path(str(report["app_dir"])).exists()
