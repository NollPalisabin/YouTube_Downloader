"""冻结感知的路径解析：区分「只读资源」与「可写部署目录」。

为什么需要这一层
- 打包后 ``Path(__file__)`` 指向 PyInstaller 的运行时目录（onedir 为 ``_internal``，
  onefile 为临时解压目录），拿它当下载目录会写进临时区甚至只读区；
- 因此所有路径都必须回答两个问题之一：
  1. 这是**只读资源**吗（FFmpeg、app.py、Streamlit 静态文件）→ :func:`resource_root`
  2. 这是**用户可写数据**吗（downloads、logs、settings.json）→ :func:`app_dir`

约定
- 冻结模式：``resource_root()`` = ``sys._MEIPASS``（onedir 下即 ``_internal``），
  ``app_dir()`` = exe 所在目录；
- 开发模式：两者都等于项目根目录，保证本地运行与测试行为可预期。

本模块不 import streamlit / yt_dlp，可被任何层安全引用。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

__all__ = [
    "BASE_DIR",
    "APP_NAME",
    "EXE_FILENAME",
    "APP_BUNDLE_NAME",
    "SETTINGS_FILENAME",
    "DOWNLOADS_DIRNAME",
    "LOGS_DIRNAME",
    "BUNDLE_BIN_DIRNAME",
    "is_windows",
    "is_macos",
    "is_linux",
    "is_frozen",
    "resource_root",
    "app_dir",
    "data_dir",
    "executable_path",
    "bundled_bin_dirs",
    "downloads_dir",
    "logs_dir",
    "settings_path",
    "read_settings",
    "write_settings",
    "reload_settings",
    "is_writable_dir",
]

BASE_DIR = Path(__file__).resolve().parent

APP_NAME = "YouTubeDownloader"

#: 打包后的可执行文件名（macOS 的 .app 内部同名可执行文件）
EXE_FILENAME = f"{APP_NAME}.exe" if os.name == "nt" else APP_NAME
#: macOS 应用包名
APP_BUNDLE_NAME = f"{APP_NAME}.app"

SETTINGS_FILENAME = "settings.json"
COOKIES_FILENAME = "cookies.txt"
DOWNLOADS_DIRNAME = "downloads"
LOGS_DIRNAME = "logs"
BUNDLE_BIN_DIRNAME = "bin"

SETTINGS_FILE_ENV = "YTDL_SETTINGS_FILE"

_settings_cache: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
# 平台判断（集中在一处，便于测试里模拟其他平台）
# --------------------------------------------------------------------------- #
def is_windows() -> bool:
    return os.name == "nt"


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return not is_windows() and not is_macos()


# --------------------------------------------------------------------------- #
# 运行形态
# --------------------------------------------------------------------------- #
def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包后的环境里。"""
    return bool(getattr(sys, "frozen", False))


def executable_path() -> Path | None:
    """打包后 exe / 可执行文件的完整路径；开发模式返回 ``None``。"""
    if not is_frozen():
        return None
    return Path(sys.executable).resolve()


def resource_root() -> Path:
    """只读资源根目录（捆绑的 FFmpeg、app.py、Streamlit 静态资源）。

    - Windows/Linux(onedir)：``_internal``
    - macOS(.app)：``Contents/Frameworks``
    - 开发模式：项目根
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    exe = executable_path()
    if exe is not None:
        return exe.parent
    return BASE_DIR


def app_dir() -> Path:
    """程序自身所在目录（只读或半只读）。

    Windows：exe 同级；macOS：``.app/Contents/MacOS``；开发模式：项目根。
    可写数据不要放这里，用 :func:`data_dir`。
    """
    exe = executable_path()
    return exe.parent if exe is not None else BASE_DIR


def data_dir() -> Path:
    """可写数据根目录（settings.json、logs）。

    - Windows：exe 同级（部署目录通常可写，保持既有行为）
    - macOS：``~/Library/Application Support/YouTubeDownloader``
    - Linux：``$XDG_DATA_HOME/YouTubeDownloader`` 或 ``~/.local/share/YouTubeDownloader``
    """
    if is_windows():
        return app_dir()
    if is_macos():
        return Path.home() / "Library" / "Application Support" / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / APP_NAME


def bundled_bin_dirs() -> list[Path]:
    """随包 FFmpeg 的候选目录，按优先级去重。

    同时兼顾两种情况：PyInstaller 把 ``--add-data "vendor/bin;bin"`` 放进
    ``_internal/bin``（macOS 为 ``Contents/Frameworks/bin``）；以及用户手动在
    可执行文件旁新建 ``bin`` 放一份。
    """
    candidates = [
        resource_root() / BUNDLE_BIN_DIRNAME,
        app_dir() / BUNDLE_BIN_DIRNAME,
    ]
    unique: list[Path] = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    return unique


def downloads_dir() -> Path:
    """默认下载目录。

    - Windows：部署目录旁的 ``downloads``（保持既有行为）
    - macOS / Linux：用户 ``~/Downloads/YouTubeDownloader``（程序本体常在不
      可写的 ``/Applications``，不能把下载目录放在它旁边）
    """
    if is_windows():
        return app_dir() / DOWNLOADS_DIRNAME
    return Path.home() / "Downloads" / APP_NAME


def logs_dir() -> Path:
    return data_dir() / LOGS_DIRNAME


# --------------------------------------------------------------------------- #
# settings.json（下载目录覆盖、快捷方式标记等轻量状态）
# --------------------------------------------------------------------------- #
def settings_path() -> Path:
    override = os.environ.get(SETTINGS_FILE_ENV, "").strip()
    if override:
        return Path(os.path.expandvars(override)).expanduser()
    return data_dir() / SETTINGS_FILENAME


def reload_settings() -> dict[str, Any]:
    """丢弃缓存并重新读取（界面保存设置后调用）。"""
    global _settings_cache
    _settings_cache = None
    return read_settings()


def read_settings() -> dict[str, Any]:
    global _settings_cache
    if _settings_cache is not None:
        return dict(_settings_cache)

    path = settings_path()
    data: dict[str, Any] = {}
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            data = parsed
    except (OSError, ValueError):
        data = {}
    _settings_cache = data
    return dict(data)


def write_settings(**updates: Any) -> Path:
    """原子写入：先写临时文件再替换，避免中断留下半个 JSON。"""
    data = read_settings()
    for key, value in updates.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value

    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

    global _settings_cache
    _settings_cache = data
    return path


# --------------------------------------------------------------------------- #
# 可写性探测
# --------------------------------------------------------------------------- #
def is_writable_dir(path: Path) -> bool:
    """判断目录是否可写；必要时向上找到已存在的祖先再实测写入。

    不用 ``os.access``：Windows 上它对 ACL 的判断经常与真实写入结果不一致。
    """
    probe = Path(path)
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            return False
        probe = parent

    if not probe.is_dir():
        return False

    marker = probe / ".ytdl-write-test.tmp"
    try:
        marker.write_bytes(b"")
        marker.unlink()
        return True
    except OSError:
        return False
