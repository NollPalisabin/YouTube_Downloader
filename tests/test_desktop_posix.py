"""desktop_posix.py 的单元测试（macOS 符号链接 / Linux .desktop）。

这些用例在 Windows 上也能跑：macOS 分支把 ``os.symlink`` 换成记录调用，
Linux 分支只是写一个文本文件，不依赖真实平台。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import desktop_posix  # noqa: E402
import paths  # noqa: E402


def _fake_exe(tmp_workdir: Path) -> Path:
    exe = tmp_workdir / paths.EXE_FILENAME
    exe.write_bytes(b"#!/bin/sh\n")
    return exe


# --------------------------------------------------------------------------- #
# .app 路径推导
# --------------------------------------------------------------------------- #
def test_app_bundle_path_from_bundle_executable() -> None:
    exe = Path("/Applications/YouTubeDownloader.app/Contents/MacOS/YouTubeDownloader")
    assert desktop_posix.app_bundle_path(exe) == Path("/Applications/YouTubeDownloader.app")


def test_app_bundle_path_returns_none_for_plain_binary(tmp_workdir: Path) -> None:
    assert desktop_posix.app_bundle_path(tmp_workdir / "YouTubeDownloader") is None


# --------------------------------------------------------------------------- #
# Linux: .desktop 启动器
# --------------------------------------------------------------------------- #
def test_linux_desktop_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    monkeypatch.setattr(desktop_posix, "_IS_MAC", False)
    exe = _fake_exe(tmp_workdir)
    desktop = tmp_workdir / "Desktop"

    link = desktop_posix.create_shortcut(exe, desktop=desktop)

    assert link.parent == desktop
    assert link.suffix == ".desktop"
    text = link.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in text
    assert f'Exec="{exe}"' in text
    assert "Terminal=false" in text
    if os.name != "nt":  # Windows 不保存 POSIX 执行位
        assert link.stat().st_mode & 0o111, "应可执行"


def test_linux_force_overwrites(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    monkeypatch.setattr(desktop_posix, "_IS_MAC", False)
    exe = _fake_exe(tmp_workdir)
    desktop = tmp_workdir / "Desktop"

    first = desktop_posix.create_shortcut(exe, desktop=desktop)
    first.write_text("stale", encoding="utf-8")

    second = desktop_posix.create_shortcut(exe, desktop=desktop, force=True)
    assert second == first
    assert "[Desktop Entry]" in second.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# macOS: 指向 .app 的符号链接
# --------------------------------------------------------------------------- #
def test_macos_symlink_points_to_app_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    monkeypatch.setattr(desktop_posix, "_IS_MAC", True)
    bundle = tmp_workdir / paths.APP_BUNDLE_NAME
    macos_dir = bundle / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True)
    exe = macos_dir / paths.EXE_FILENAME
    exe.write_bytes(b"")

    calls: list[tuple[Path, Path]] = []

    def fake_symlink(target: Path, link: Path) -> None:
        calls.append((Path(target), Path(link)))
        Path(link).write_text("symlink-placeholder", encoding="utf-8")

    # Windows 上创建符号链接需要特权，这里只验证"链接目标算对了"
    monkeypatch.setattr(desktop_posix.os, "symlink", fake_symlink)
    monkeypatch.setattr(desktop_posix.os.path, "lexists", lambda _path: False)

    desktop = tmp_workdir / "Desktop"
    link = desktop_posix.create_shortcut(exe, desktop=desktop)

    assert link.name.endswith(".app")
    assert calls and calls[0][0] == bundle, "应链接到 .app 而不是内部可执行文件"


def test_missing_executable_raises(tmp_workdir: Path) -> None:
    with pytest.raises(desktop_posix.ShortcutError):
        desktop_posix.create_shortcut(tmp_workdir / "nope", desktop=tmp_workdir / "Desktop")


def test_shortcut_exists_handles_dangling_link(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    monkeypatch.setattr(desktop_posix, "_IS_MAC", False)
    desktop = tmp_workdir / "Desktop"
    desktop.mkdir()
    assert desktop_posix.shortcut_exists(desktop) is False

    link = desktop / desktop_posix._default_name()
    link.write_text("x", encoding="utf-8")
    assert desktop_posix.shortcut_exists(desktop) is True


# --------------------------------------------------------------------------- #
# 首启静默创建
# --------------------------------------------------------------------------- #
def test_first_run_creates_and_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    monkeypatch.setattr(desktop_posix, "_IS_MAC", False)
    exe = _fake_exe(tmp_workdir)
    desktop = tmp_workdir / "Desktop"

    link = desktop_posix.ensure_shortcut_on_first_run(exe, desktop=desktop)

    assert link is not None and link.is_file()
    assert paths.read_settings().get("shortcut_created") is True


def test_first_run_skipped_when_flagged(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    monkeypatch.setattr(desktop_posix, "_IS_MAC", False)
    exe = _fake_exe(tmp_workdir)
    desktop = tmp_workdir / "Desktop"
    paths.write_settings(shortcut_created=True)

    assert desktop_posix.ensure_shortcut_on_first_run(exe, desktop=desktop) is None
    assert not desktop_posix.shortcut_exists(desktop)


def test_first_run_noop_without_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path
) -> None:
    monkeypatch.setattr(paths, "executable_path", lambda: None)
    assert desktop_posix.ensure_shortcut_on_first_run(desktop=tmp_workdir / "Desktop") is None
