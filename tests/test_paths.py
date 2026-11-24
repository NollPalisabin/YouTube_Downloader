"""paths.py 的单元测试：开发形态与打包形态下的路径解析。

打包形态用 monkeypatch 模拟 ``sys.frozen`` / ``sys._MEIPASS`` / ``sys.executable``，
不依赖真的构建一次 exe。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import paths  # noqa: E402


def _drop(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.delattr(sys, name, raising=False)


def test_development_mode_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    _drop(monkeypatch, "frozen")
    _drop(monkeypatch, "_MEIPASS")

    assert paths.is_frozen() is False
    assert paths.executable_path() is None
    assert paths.resource_root() == paths.BASE_DIR
    assert paths.app_dir() == paths.BASE_DIR
    assert paths.settings_path().name == paths.SETTINGS_FILENAME


def test_frozen_onedir_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    exe_dir = tmp_workdir / "YouTube_Downloader"
    exe_dir.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / paths.EXE_FILENAME))
    monkeypatch.setattr(sys, "_MEIPASS", str(exe_dir / "_internal"), raising=False)

    assert paths.is_frozen() is True
    assert paths.app_dir() == exe_dir
    assert paths.resource_root() == exe_dir / "_internal"
    assert paths.executable_path() == exe_dir / paths.EXE_FILENAME
    # 可写数据落在 exe 同级；只读资源落在 _internal
    assert paths.downloads_dir() == exe_dir / "downloads"
    assert paths.logs_dir() == exe_dir / "logs"
    assert paths.bundled_bin_dirs() == [exe_dir / "_internal" / "bin", exe_dir / "bin"]


def test_frozen_without_meipass_falls_back_to_exe_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    exe_dir = tmp_workdir / "app"
    exe_dir.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "x.exe"))
    _drop(monkeypatch, "_MEIPASS")

    assert paths.resource_root() == exe_dir
    assert paths.app_dir() == exe_dir


def test_bundled_bin_dirs_deduped_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    _drop(monkeypatch, "frozen")
    _drop(monkeypatch, "_MEIPASS")
    assert paths.bundled_bin_dirs() == [paths.BASE_DIR / "bin"]


def test_settings_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path) -> None:
    monkeypatch.setenv(paths.SETTINGS_FILE_ENV, str(tmp_workdir / "settings.json"))
    paths.reload_settings()
    assert paths.read_settings() == {}

    target = paths.write_settings(download_dir="D:/videos", shortcut_created=True)
    assert target.is_file()

    data = paths.read_settings()
    assert data["download_dir"] == "D:/videos"
    assert data["shortcut_created"] is True

    paths.write_settings(download_dir=None)
    assert "download_dir" not in paths.read_settings()
    assert paths.read_settings()["shortcut_created"] is True


def test_settings_corrupt_file_is_tolerated(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    settings = tmp_workdir / "settings.json"
    settings.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setenv(paths.SETTINGS_FILE_ENV, str(settings))
    paths.reload_settings()

    assert paths.read_settings() == {}


def test_write_settings_is_atomic(monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path) -> None:
    monkeypatch.setenv(paths.SETTINGS_FILE_ENV, str(tmp_workdir / "settings.json"))
    paths.reload_settings()
    paths.write_settings(download_dir="D:/a")

    assert not list(tmp_workdir.glob("*.tmp")), "不应留下临时文件"


def test_is_writable_dir(tmp_workdir: Path) -> None:
    assert paths.is_writable_dir(tmp_workdir) is True

    nested = tmp_workdir / "a" / "b" / "c"
    assert paths.is_writable_dir(nested) is True, "祖先可写即视为可写"

    plain_file = tmp_workdir / "file.txt"
    plain_file.write_text("x", encoding="utf-8")
    assert paths.is_writable_dir(plain_file) is False


# --------------------------------------------------------------------------- #
# 跨平台目录约定（用 monkeypatch 模拟 Windows / macOS / Linux）
# --------------------------------------------------------------------------- #
def _fake_platform(monkeypatch: pytest.MonkeyPatch, *, windows: bool, macos: bool) -> None:
    monkeypatch.setattr(paths, "is_windows", lambda: windows)
    monkeypatch.setattr(paths, "is_macos", lambda: macos)
    monkeypatch.setattr(paths, "is_linux", lambda: not windows and not macos)


def _fake_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))


def test_windows_layout(monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path) -> None:
    _fake_platform(monkeypatch, windows=True, macos=False)
    app = tmp_workdir / "YouTube_Downloader"
    monkeypatch.setattr(paths, "app_dir", lambda: app)
    monkeypatch.delenv(paths.SETTINGS_FILE_ENV, raising=False)

    assert paths.EXE_FILENAME.startswith(paths.APP_NAME)
    assert paths.data_dir() == app
    assert paths.downloads_dir() == app / "downloads"
    assert paths.logs_dir() == app / "logs"
    assert paths.settings_path() == app / "settings.json"


def test_macos_layout(monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path) -> None:
    _fake_platform(monkeypatch, windows=False, macos=True)
    _fake_home(monkeypatch, tmp_workdir)
    monkeypatch.delenv(paths.SETTINGS_FILE_ENV, raising=False)

    support = tmp_workdir / "Library" / "Application Support" / paths.APP_NAME
    assert paths.data_dir() == support
    assert paths.settings_path() == support / "settings.json"
    assert paths.logs_dir() == support / "logs"
    # .app 常驻 /Applications（不可写），下载目录必须落在用户目录
    assert paths.downloads_dir() == tmp_workdir / "Downloads" / paths.APP_NAME


def test_linux_layout_uses_xdg_data_home(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    _fake_platform(monkeypatch, windows=False, macos=False)
    _fake_home(monkeypatch, tmp_workdir)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_workdir / "xdg"))
    monkeypatch.delenv(paths.SETTINGS_FILE_ENV, raising=False)

    assert paths.data_dir() == tmp_workdir / "xdg" / paths.APP_NAME
    assert paths.settings_path() == tmp_workdir / "xdg" / paths.APP_NAME / "settings.json"
    assert paths.downloads_dir() == tmp_workdir / "Downloads" / paths.APP_NAME


def test_linux_layout_falls_back_to_local_share(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    _fake_platform(monkeypatch, windows=False, macos=False)
    _fake_home(monkeypatch, tmp_workdir)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    assert paths.data_dir() == tmp_workdir / ".local" / "share" / paths.APP_NAME


def test_macos_app_bundle_resources(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    """macOS .app：资源在 Contents/Frameworks，数据在 Application Support。"""
    _fake_platform(monkeypatch, windows=False, macos=True)
    _fake_home(monkeypatch, tmp_workdir)

    macos_dir = tmp_workdir / "YouTubeDownloader.app" / "Contents" / "MacOS"
    frameworks = tmp_workdir / "YouTubeDownloader.app" / "Contents" / "Frameworks"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(macos_dir / paths.EXE_FILENAME))
    monkeypatch.setattr(sys, "_MEIPASS", str(frameworks), raising=False)

    assert paths.app_dir() == macos_dir
    assert paths.resource_root() == frameworks
    assert paths.bundled_bin_dirs() == [frameworks / "bin", macos_dir / "bin"]
    assert paths.data_dir().name == paths.APP_NAME
    assert paths.data_dir().parent.name == "Application Support"
