"""在桌面创建指向本程序的快捷方式（独立脚本，不依赖任何第三方库）。

用法：
    python setup_shortcut.py
    python setup_shortcut.py --exe D:\\YouTube_Downloader\\YouTubeDownloader.exe --force
    python setup_shortcut.py --desktop D:\\SomeDesktop        # 指定桌面目录（测试/特殊场景）

说明：目标机器上如果没有 Python，请直接双击 exe —— 首次启动会自动创建快捷方式，
或在界面左侧点「创建桌面快捷方式」。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import desktop  # noqa: E402
import paths  # noqa: E402


def _guess_exe(explicit: str | None) -> Path | None:
    """定位要指向的 exe：显式参数 → 打包运行时的自身 → dist 产物 → 当前目录。"""
    if explicit:
        return Path(explicit).expanduser()

    exe = paths.executable_path()
    if exe is not None:
        return exe

    candidates = [
        Path.cwd() / "dist" / "YouTubeDownloader" / paths.EXE_FILENAME,
        Path.cwd() / paths.EXE_FILENAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="在用户桌面创建 YouTube 下载器快捷方式",
    )
    parser.add_argument("--exe", help="YouTubeDownloader.exe 的完整路径")
    parser.add_argument("--desktop", help="桌面目录，默认自动探测（含 OneDrive 重定向）")
    parser.add_argument("--name", default=desktop.SHORTCUT_NAME, help="快捷方式文件名")
    parser.add_argument("--force", action="store_true", help="已存在时覆盖")
    args = parser.parse_args(argv)

    exe = _guess_exe(args.exe)
    if exe is None:
        print("找不到 exe，请用 --exe 指定完整路径。", file=sys.stderr)
        return 2

    try:
        link = desktop.create_shortcut(
            exe,
            desktop=args.desktop,
            name=args.name,
            force=args.force,
        )
    except desktop.ShortcutError as exc:
        print(f"创建失败：{exc}", file=sys.stderr)
        return 1

    resolved = Path(exe).expanduser().resolve()
    print(f"已创建快捷方式：{link}")
    print(f"  TargetPath       : {resolved}")
    print(f"  WorkingDirectory : {resolved.parent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
