"""桌面集成门面：按运行平台选择实现。

- Windows → ``desktop_win``（PowerShell 生成 ``.lnk``）
- macOS / Linux → ``desktop_posix``（macOS：指向 ``.app`` 的符号链接；Linux：``.desktop``）

对外 API 在各平台保持一致，调用方（``ui/components.py``、``desktop_entry.py``、
``setup_shortcut.py``）不需要关心平台差异。
"""

from __future__ import annotations

import os

if os.name == "nt":  # pragma: no cover - 由平台决定分支
    from desktop_win import (
        SHORTCUT_DESCRIPTION,
        SHORTCUT_NAME,
        ShortcutError,
        create_shortcut,
        desktop_dir,
        ensure_shortcut_on_first_run,
        shortcut_exists,
        shortcut_path,
    )
else:  # pragma: no cover
    from desktop_posix import (
        SHORTCUT_DESCRIPTION,
        SHORTCUT_NAME,
        ShortcutError,
        create_shortcut,
        desktop_dir,
        ensure_shortcut_on_first_run,
        shortcut_exists,
        shortcut_path,
    )

__all__ = [
    "SHORTCUT_NAME",
    "SHORTCUT_DESCRIPTION",
    "ShortcutError",
    "desktop_dir",
    "shortcut_path",
    "shortcut_exists",
    "create_shortcut",
    "ensure_shortcut_on_first_run",
]
