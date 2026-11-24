"""桌面集成（macOS / Linux 实现）。

- macOS：在桌面放一个指向 ``YouTubeDownloader.app`` 的**符号链接**，
  Finder 里与快捷方式等价，双击即启动（.app 自带 Python 运行时，无需装环境）；
- Linux：写一个 ``.desktop`` 启动器；
- 不依赖 pywin32 / ctypes，也不需要目标机器装 Python。

对外统一通过 ``desktop`` 门面使用，接口与 ``desktop_win`` 一致。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import paths

SHORTCUT_DESCRIPTION = "YouTube 视频下载器"

_IS_MAC = sys.platform == "darwin"


def _default_name() -> str:
    """macOS 用 .app 后缀（Finder 才会当作应用）；Linux 用 .desktop 启动器。"""
    return "YouTube 下载器.app" if _IS_MAC else "youtube-downloader.desktop"


#: 导入时的默认名称（供 UI / CLI 展示用）；实际创建时以 :func:`_default_name` 为准
SHORTCUT_NAME = _default_name()


class ShortcutError(RuntimeError):
    """创建快捷方式失败（目标不存在、无权限、符号链接失败等）。"""


# --------------------------------------------------------------------------- #
# 目录
# --------------------------------------------------------------------------- #
def _linux_desktop_dir() -> Path:
    """读取 XDG 桌面目录配置，失败则退回 ~/Desktop。"""
    config = Path.home() / ".config" / "user-dirs.dirs"
    try:
        for line in config.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("XDG_DESKTOP_DIR="):
                value = line.split("=", 1)[1].strip().strip('"')
                value = value.replace("$HOME", str(Path.home()))
                if value:
                    return Path(value)
    except OSError:
        pass
    return Path.home() / "Desktop"


def desktop_dir() -> Path:
    """当前用户桌面目录。"""
    return Path.home() / "Desktop" if _IS_MAC else _linux_desktop_dir()


def app_bundle_path(executable: Path) -> Path | None:
    """从 ``Foo.app/Contents/MacOS/Foo`` 推出 ``Foo.app``；非 bundle 返回 ``None``。"""
    parts = Path(executable).parts
    indexes = [index for index, part in enumerate(parts) if part.endswith(".app")]
    if not indexes:
        return None
    return Path(*parts[: indexes[-1] + 1])


def shortcut_path(desktop: str | Path | None = None) -> Path:
    base = Path(desktop) if desktop is not None else desktop_dir()
    return base / _default_name()


def shortcut_exists(desktop: str | Path | None = None) -> bool:
    try:
        # 符号链接可能悬空，用 lexists 判断"链接本身是否存在"
        return os.path.lexists(shortcut_path(desktop))
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# 创建
# --------------------------------------------------------------------------- #
def _desktop_entry_text(executable: Path, workdir: Path) -> str:
    icon = paths.resource_root() / "assets" / "app.ico"
    icon_line = str(icon) if icon.is_file() else str(executable)
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=YouTube 视频下载器\n"
        f"Comment={SHORTCUT_DESCRIPTION}\n"
        f'Exec="{executable}"\n'
        f"Path={workdir}\n"
        f"Icon={icon_line}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Utility;\n"
    )


def create_shortcut(
    exe_path: str | Path | None = None,
    *,
    working_dir: str | Path | None = None,
    desktop: str | Path | None = None,
    name: str | None = None,
    force: bool = False,
) -> Path:
    """在桌面创建启动入口，返回其路径。

    macOS 返回指向 ``.app`` 的符号链接；Linux 返回 ``.desktop`` 文件。
    """
    raw_target = Path(exe_path) if exe_path is not None else paths.executable_path()
    if raw_target is None:
        raise ShortcutError("当前是源码运行模式，找不到可执行文件；请用 --exe 指定路径")

    executable = Path(raw_target).expanduser()
    if not executable.exists():
        raise ShortcutError(f"找不到可执行文件：{executable}")

    target_desktop = Path(desktop).expanduser() if desktop is not None else desktop_dir()
    try:
        target_desktop.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ShortcutError(f"无法创建桌面目录 {target_desktop}：{exc}") from exc

    link = target_desktop / (name or _default_name())
    if os.path.lexists(link):
        if not force:
            return link
        try:
            link.unlink()
        except OSError as exc:
            raise ShortcutError(f"无法覆盖已有快捷方式 {link}：{exc}") from exc

    if _IS_MAC:
        bundle = app_bundle_path(executable)
        target = bundle if bundle is not None else executable
        try:
            os.symlink(target, link)
        except OSError as exc:
            raise ShortcutError(f"创建符号链接失败：{exc}") from exc
        return link

    workdir = Path(working_dir) if working_dir is not None else executable.parent
    try:
        link.write_text(_desktop_entry_text(executable, workdir), encoding="utf-8")
        link.chmod(0o755)
    except OSError as exc:
        raise ShortcutError(f"写入 .desktop 文件失败：{exc}") from exc
    return link


# --------------------------------------------------------------------------- #
# 首次启动静默创建
# --------------------------------------------------------------------------- #
def ensure_shortcut_on_first_run(
    exe_path: str | Path | None = None,
    *,
    desktop: str | Path | None = None,
) -> Path | None:
    """只在第一次运行时创建桌面入口（与 Windows 版语义一致）。"""
    if paths.read_settings().get("shortcut_created"):
        return None

    executable = Path(exe_path) if exe_path is not None else paths.executable_path()
    if executable is None:
        return None

    target_desktop = Path(desktop) if desktop is not None else desktop_dir()
    if shortcut_exists(target_desktop):
        paths.write_settings(shortcut_created=True)
        return None

    link = create_shortcut(executable, desktop=target_desktop)
    paths.write_settings(shortcut_created=True)
    return link
