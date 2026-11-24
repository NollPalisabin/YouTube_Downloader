"""桌面集成（Windows 实现）：解析桌面目录、创建 / 检测 ``.lnk`` 快捷方式。

设计取舍
- 不引入 pywin32：桌面路径用 ctypes 调 ``SHGetKnownFolderPath``（能正确处理
  OneDrive 重定向后的桌面），``.lnk`` 用 Windows 自带的 PowerShell
  ``WScript.Shell`` 创建 —— 目标机器不需要装 Python，也不需要额外依赖。
- 所有失败都收敛为 :class:`ShortcutError`，由调用方决定是弹窗还是只记日志，
  绝不让"建快捷方式失败"阻断应用启动。

其他平台请用 ``desktop_posix.py``；对外统一走 ``desktop`` 门面模块。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import uuid
from ctypes import wintypes
from pathlib import Path

import paths

__all__ = [
    "SHORTCUT_NAME",
    "ShortcutError",
    "desktop_dir",
    "shortcut_path",
    "shortcut_exists",
    "create_shortcut",
    "ensure_shortcut_on_first_run",
]

SHORTCUT_NAME = "YouTube 下载器.lnk"
SHORTCUT_DESCRIPTION = "YouTube 视频下载器"

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_PS_TIMEOUT = 60

# {B4BFCC3A-DB2C-424C-B029-7FE99A87C641} = FOLDERID_Desktop
_FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"


class ShortcutError(RuntimeError):
    """创建快捷方式失败（不可用、无权限、PowerShell 报错等）。"""


# --------------------------------------------------------------------------- #
# 桌面目录
# --------------------------------------------------------------------------- #
class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid_from_string(text: str) -> _GUID:
    parsed = uuid.UUID(text)
    tail = (ctypes.c_ubyte * 8).from_buffer_copy(parsed.bytes[8:])
    return _GUID(parsed.time_low, parsed.time_mid, parsed.time_hi_version, tail)


def desktop_dir() -> Path:
    """当前用户桌面目录；优先 Known Folder（支持 OneDrive 重定向）。"""
    if os.name != "nt":
        return (Path.home() / "Desktop").resolve()

    try:
        folder_id = _guid_from_string(_FOLDERID_DESKTOP)
        pointer = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        result = shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(pointer)
        )
        if result == 0 and pointer.value:
            try:
                return Path(pointer.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(pointer)  # type: ignore[attr-defined]
    except (AttributeError, OSError, ValueError):
        pass

    return (Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop")


def shortcut_path(desktop: Path | None = None) -> Path:
    base = Path(desktop) if desktop is not None else desktop_dir()
    return base / SHORTCUT_NAME


def shortcut_exists(desktop: Path | None = None) -> bool:
    try:
        return shortcut_path(desktop).is_file()
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# 创建快捷方式
# --------------------------------------------------------------------------- #
def _quote(text: str) -> str:
    """PowerShell 单引号字符串转义。"""
    return "'" + text.replace("'", "''") + "'"


def _build_script(link: Path, exe: Path, workdir: Path) -> str:
    return (
        "$ErrorActionPreference = 'Stop';"
        "$ws = New-Object -ComObject WScript.Shell;"
        f"$sc = $ws.CreateShortcut({_quote(str(link))});"
        f"$sc.TargetPath = {_quote(str(exe))};"
        f"$sc.WorkingDirectory = {_quote(str(workdir))};"
        f"$sc.IconLocation = {_quote(str(exe))} + ',0';"
        f"$sc.Description = {_quote(SHORTCUT_DESCRIPTION)};"
        "$sc.Save();"
        "Write-Output 'OK'"
    )


def create_shortcut(
    exe_path: str | Path | None = None,
    *,
    working_dir: str | Path | None = None,
    desktop: str | Path | None = None,
    name: str | None = None,
    force: bool = False,
) -> Path:
    """在桌面创建指向 exe 的快捷方式，返回 .lnk 路径。

    ``working_dir`` 默认取 exe 所在目录 —— 与需求一致（避免相对路径问题）。
    """
    if os.name != "nt":
        raise ShortcutError("仅 Windows 支持创建快捷方式")

    if exe_path is None:
        exe_path = paths.executable_path()
    if exe_path is None:
        raise ShortcutError("当前是源码运行模式，找不到 exe；请用 --exe 指定路径")

    exe = Path(exe_path).expanduser()
    try:
        exe = exe.resolve()
    except OSError as exc:
        raise ShortcutError(f"exe 路径无效：{exe_path}（{exc}）") from exc

    if not exe.is_file():
        raise ShortcutError(f"找不到 exe：{exe}")

    target_desktop = Path(desktop).expanduser() if desktop is not None else desktop_dir()
    try:
        target_desktop.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ShortcutError(f"无法创建桌面目录 {target_desktop}：{exc}") from exc

    link = target_desktop / (name or SHORTCUT_NAME)
    if link.is_file() and not force:
        return link

    workdir = Path(working_dir).expanduser() if working_dir is not None else exe.parent
    script = _build_script(link, exe, workdir)

    try:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-Command", script,
            ],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT,
            creationflags=_CREATE_NO_WINDOW,
        )
    except FileNotFoundError as exc:
        raise ShortcutError("系统缺少 PowerShell，无法创建快捷方式") from exc
    except subprocess.SubprocessError as exc:
        raise ShortcutError(f"调用 PowerShell 失败：{exc}") from exc

    if proc.returncode != 0 or not link.is_file():
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit={proc.returncode}"
        raise ShortcutError(f"创建快捷方式失败：{tail}")

    return link


# --------------------------------------------------------------------------- #
# 首次启动静默创建
# --------------------------------------------------------------------------- #
def ensure_shortcut_on_first_run(
    exe_path: str | Path | None = None,
    *,
    desktop: str | Path | None = None,
) -> Path | None:
    """只在第一次运行时创建桌面快捷方式。

    - ``settings.json`` 里已有 ``shortcut_created`` 标记 → 跳过；
    - 桌面已有同名快捷方式 → 记标记后跳过；
    - 源码运行（无 exe）→ 不做任何事。

    返回本次新建的 .lnk 路径；跳过时返回 ``None``。
    """
    if os.name != "nt":
        return None

    settings = paths.read_settings()
    if settings.get("shortcut_created"):
        return None

    exe = Path(exe_path) if exe_path is not None else paths.executable_path()
    if exe is None:
        return None

    target_desktop = Path(desktop) if desktop is not None else desktop_dir()
    if shortcut_exists(target_desktop):
        paths.write_settings(shortcut_created=True)
        return None

    link = create_shortcut(exe, desktop=target_desktop)
    paths.write_settings(shortcut_created=True)
    return link
