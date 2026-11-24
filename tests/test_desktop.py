"""desktop.py 的单元测试：桌面目录解析、快捷方式创建、首启静默创建。

快捷方式通过 PowerShell + WScript.Shell 创建，测试会真实生成 .lnk 文件
（写到临时目录，不污染真实桌面）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import desktop  # noqa: E402
import paths  # noqa: E402
import setup_shortcut  # noqa: E402

pytestmark = pytest.mark.skipif(os.name != "nt", reason="快捷方式仅 Windows 支持")


def _fake_exe(directory: Path) -> Path:
    exe = directory / paths.EXE_FILENAME
    exe.write_bytes(b"MZ\x90\x00 fake executable")
    return exe


def _require_shortcut_support(tmp_workdir: Path) -> None:
    """当前环境能否真正创建 .lnk。

    某些 Windows Server SKU（例如 GitHub Actions 的 windows-latest）缺少
    WSH 的 COM 组件，`New-Object -ComObject WScript.Shell` 会失败；那是环境限制，
    不是产品缺陷，测试应跳过而不是误报失败。
    """
    probe_dir = tmp_workdir / "shortcut-probe"
    probe_dir.mkdir(exist_ok=True)
    probe_exe = probe_dir / paths.EXE_FILENAME
    probe_exe.write_bytes(b"MZ")
    try:
        desktop.create_shortcut(probe_exe, desktop=probe_dir / "Desktop")
    except desktop.ShortcutError as exc:
        pytest.skip(f"当前环境无法创建 .lnk 快捷方式（{str(exc)[:120]}）")


def _contains(raw: bytes, text: str) -> bool:
    """.lnk 里字符串可能是 Unicode 或 ANSI，两种都认。"""
    return text.encode("utf-16-le", "ignore") in raw or text.encode("ascii", "ignore") in raw


# --------------------------------------------------------------------------- #
# 桌面目录
# --------------------------------------------------------------------------- #
def test_desktop_dir_is_usable() -> None:
    folder = desktop.desktop_dir()
    assert folder.is_absolute()
    assert folder.is_dir()


def test_shortcut_path_default_name() -> None:
    folder = Path("D:/some-desktop")
    assert desktop.shortcut_path(folder) == folder / desktop.SHORTCUT_NAME


# --------------------------------------------------------------------------- #
# 创建快捷方式
# --------------------------------------------------------------------------- #
def test_create_shortcut_points_to_exe(tmp_workdir: Path) -> None:
    _require_shortcut_support(tmp_workdir)
    exe = _fake_exe(tmp_workdir)
    target_desktop = tmp_workdir / "Desktop"

    link = desktop.create_shortcut(exe, desktop=target_desktop)

    assert link.is_file()
    assert link.parent == target_desktop
    raw = link.read_bytes()
    assert _contains(raw, str(exe.resolve())), "TargetPath 应为 exe 完整路径"
    assert _contains(raw, str(exe.resolve().parent)), "WorkingDirectory 应为 exe 所在目录"


def test_create_shortcut_is_idempotent(tmp_workdir: Path) -> None:
    _require_shortcut_support(tmp_workdir)
    exe = _fake_exe(tmp_workdir)
    target_desktop = tmp_workdir / "Desktop"

    first = desktop.create_shortcut(exe, desktop=target_desktop)
    stamp = first.stat().st_mtime_ns

    second = desktop.create_shortcut(exe, desktop=target_desktop)
    assert second == first
    assert second.stat().st_mtime_ns == stamp, "未指定 force 时不应重写"


def test_create_shortcut_force_overwrites(tmp_workdir: Path) -> None:
    _require_shortcut_support(tmp_workdir)
    exe = _fake_exe(tmp_workdir)
    target_desktop = tmp_workdir / "Desktop"
    desktop.create_shortcut(exe, desktop=target_desktop)

    link = desktop.create_shortcut(exe, desktop=target_desktop, force=True)
    assert link.is_file()


def test_create_shortcut_requires_existing_exe(tmp_workdir: Path) -> None:
    with pytest.raises(desktop.ShortcutError):
        desktop.create_shortcut(tmp_workdir / "missing.exe", desktop=tmp_workdir / "Desktop")


def test_create_shortcut_requires_exe_in_source_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "executable_path", lambda: None)
    with pytest.raises(desktop.ShortcutError):
        desktop.create_shortcut(None, desktop=Path("D:/unused"))


def test_shortcut_exists(tmp_workdir: Path) -> None:
    _require_shortcut_support(tmp_workdir)
    target_desktop = tmp_workdir / "Desktop"
    target_desktop.mkdir()
    assert desktop.shortcut_exists(target_desktop) is False

    exe = _fake_exe(tmp_workdir)
    desktop.create_shortcut(exe, desktop=target_desktop)
    assert desktop.shortcut_exists(target_desktop) is True


# --------------------------------------------------------------------------- #
# 首次启动静默创建
# --------------------------------------------------------------------------- #
def test_first_run_creates_and_records_flag(tmp_workdir: Path) -> None:
    _require_shortcut_support(tmp_workdir)
    exe = _fake_exe(tmp_workdir)
    target_desktop = tmp_workdir / "Desktop"

    link = desktop.ensure_shortcut_on_first_run(exe, desktop=target_desktop)

    assert link is not None and link.is_file()
    assert config.paths.read_settings().get("shortcut_created") is True


def test_first_run_skips_when_already_flagged(tmp_workdir: Path) -> None:
    exe = _fake_exe(tmp_workdir)
    target_desktop = tmp_workdir / "Desktop"
    paths.write_settings(shortcut_created=True)

    assert desktop.ensure_shortcut_on_first_run(exe, desktop=target_desktop) is None
    assert not desktop.shortcut_exists(target_desktop)


def test_first_run_skips_when_link_already_exists(tmp_workdir: Path) -> None:
    exe = _fake_exe(tmp_workdir)
    target_desktop = tmp_workdir / "Desktop"
    target_desktop.mkdir()
    (target_desktop / desktop.SHORTCUT_NAME).write_bytes(b"placeholder")

    assert desktop.ensure_shortcut_on_first_run(exe, desktop=target_desktop) is None
    assert paths.read_settings().get("shortcut_created") is True


def test_first_run_is_noop_in_source_mode(monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path) -> None:
    monkeypatch.setattr(paths, "executable_path", lambda: None)
    assert desktop.ensure_shortcut_on_first_run(desktop=tmp_workdir / "Desktop") is None


# --------------------------------------------------------------------------- #
# setup_shortcut.py 命令行
# --------------------------------------------------------------------------- #
def test_setup_shortcut_cli(tmp_workdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _require_shortcut_support(tmp_workdir)
    exe = _fake_exe(tmp_workdir)
    target_desktop = tmp_workdir / "Desktop"

    code = setup_shortcut.main(["--exe", str(exe), "--desktop", str(target_desktop)])

    out = capsys.readouterr().out
    assert code == 0
    assert "已创建快捷方式" in out
    assert str(exe.resolve()) in out
    assert (target_desktop / desktop.SHORTCUT_NAME).is_file()


def test_setup_shortcut_cli_reports_missing_exe(
    tmp_workdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = setup_shortcut.main(["--exe", str(tmp_workdir / "nope.exe")])
    assert code == 1
    assert "创建失败" in capsys.readouterr().err
