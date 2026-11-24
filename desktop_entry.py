"""桌面入口：PyInstaller 打包后的启动点，也是本地调试窗口模式的入口。

执行顺序（每一步都有原因，不要随意调整）
1. ``multiprocessing.freeze_support()``：必须最先执行。打包后 Streamlit 子进程
   是用同一个 exe 以 ``--multiprocessing-fork`` 参数重启的，这里要立刻接管。
2. 把 stdout/stderr 重定向到 ``logs/app.log``：``--windowed`` 下它们是 ``None``，
   Streamlit / 第三方库一写日志就会抛出 AttributeError，表现为"双击没反应"。
3. 安装 ``sys.excepthook``：崩溃时写日志 + 弹窗提示，而不是静默退出。
4. 首次启动静默创建桌面快捷方式（失败只记日志，不影响启动）。
5. 交给 ``streamlit_desktop_app`` 打开 pywebview 窗口；关窗时它会终止
   Streamlit 子进程，因此不会留下后台残留。

命令行开关（方便排障）
- ``--self-check``      打印环境自检后退出（源码模式下可直接看到输出）
- ``--create-shortcut`` 创建桌面快捷方式后退出
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import traceback
from pathlib import Path
from typing import IO

APP_TITLE_FALLBACK = "YouTube 视频下载器"
_LOG_FILENAME = "app.log"

HELP_TEXT = """用法: YouTubeDownloader.exe [选项]

  （无参数）          启动应用
  --browser           强制浏览器模式（起服务 + 打开默认浏览器 + 宿主窗口）
  --webview           强制桌面窗口模式（环境不支持时会报错退出）
  --self-check        打印环境自检（含 FFmpeg 来源），结果写入 logs\\app.log
  --smoke-test        端到端自检：解析 + 下载最小清晰度 + FFmpeg 合并
  --create-shortcut   重新创建桌面快捷方式
  --help              显示本帮助
"""


def _bootstrap_logging() -> tuple[Path, IO[str] | None]:
    """尽早建立日志文件，并把 None 的 stdout/stderr 指过去。"""
    try:
        import paths
        log_dir = paths.logs_dir()
    except Exception:  # pragma: no cover - 极端情况下退回临时目录
        log_dir = Path(os.environ.get("TEMP", ".")) / "YouTubeDownloader-logs"

    log_path = log_dir / _LOG_FILENAME
    console = sys.stdout  # --windowed 下为 None
    stream: IO[str] | None = None
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        stream = open(log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    except OSError:
        return log_path, None

    if console is None:
        sys.stdout = stream
        sys.stderr = stream

    import logging

    handlers: list[logging.Handler] = [logging.FileHandler(log_path, encoding="utf-8")]
    if console is not None:  # 有控制台时再镜像一份，注意不要写回同一个文件
        handlers.append(logging.StreamHandler(console))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    return log_path, stream


def _show_error(message: str, title: str = APP_TITLE_FALLBACK) -> None:
    """无控制台环境下用系统弹窗告知用户（失败也不能再抛）。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)  # MB_ICONERROR
    except Exception:
        pass


def _install_excepthook(log_path: Path) -> None:
    def _hook(exc_type, exc_value, exc_tb):  # type: ignore[no-untyped-def]
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write("\n==== 未捕获异常 ====\n" + text)
        except OSError:
            pass
        _show_error(
            "程序遇到未处理的错误，已写入日志：\n"
            f"{log_path}\n\n{traceback.format_exception_only(exc_type, exc_value)[-1].strip()}"
        )

    sys.excepthook = _hook


def _run_self_check() -> int:
    import config

    config.print_self_check()
    text = config.environment_report()
    lines = "\n".join(f"{key}: {value}" for key, value in text.items())
    print(lines)
    return 0


def _run_smoke_test() -> int:
    """打包后的端到端自检：解析 → 下载最小清晰度 → FFmpeg 合并。

    只写进 logs\\smoke-test\\，不污染用户的 downloads 目录。
    """
    import logging

    import paths
    from core.downloader import DownloadJob, DownloadFailed
    from core.extractor import ExtractionError, extract_video_info

    log = logging.getLogger("smoke-test")
    url = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
    target = paths.logs_dir() / "smoke-test"
    target.mkdir(parents=True, exist_ok=True)

    try:
        info = extract_video_info(url)
        log.info("解析成功：%s（%s）", info.title, info.duration_text)

        candidates = [q for q in info.qualities if not q.is_audio_only]
        if not candidates:
            raise RuntimeError("没有可用的视频清晰度")
        quality = min(candidates, key=lambda q: q.height or 0)
        log.info("选择清晰度：%s / %s", quality.label, quality.detail)

        path = DownloadJob(info, quality, download_dir=target).execute()
        size = path.stat().st_size / 1048576
        log.info("冒烟测试通过：%s（%.1f MB）", path, size)
        print(f"SMOKE OK {path} {size:.1f}MB")
        return 0
    except (ExtractionError, DownloadFailed) as exc:
        log.error("冒烟测试失败：%s", exc)
        print(f"SMOKE FAIL {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        log.exception("冒烟测试异常")
        print(f"SMOKE FAIL {type(exc).__name__}: {exc}")
        return 1


# --------------------------------------------------------------------------- #
# 回退模式：PyInstaller + 自动打开默认浏览器
#
# 为什么需要：pywebview 5.4 的 Windows 后端是 WinForms 实现，依赖 .NET Framework
# 的 System.Windows.Forms.ContextMenu；.NET Core 移除了该类型，而 pythonnet 3.x
# 只能跑 CoreCLR（netfx 路径加载不了 Python.Runtime.Loader）。两者叠加导致窗口
# 无法创建，于是按方案约定回退到"起 Streamlit + 打开浏览器 + 一个宿主窗口"。
# --------------------------------------------------------------------------- #
def _find_free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        return int(sock.getsockname()[1])


def _streamlit_options(port: int) -> dict[str, str]:
    import config

    options = dict(config.STREAMLIT_OPTIONS)
    options.update(
        {
            "server.address": "localhost",
            "server.port": str(port),
            "server.headless": "true",
            "global.developmentMode": "false",
        }
    )
    return options


def _run_streamlit(script_path: str, options: dict[str, str]) -> None:
    """子进程入口：把 Streamlit 服务跑起来。"""
    sys.argv = ["streamlit", "run", script_path]
    sys.argv.extend(f"--{key}={value}" for key, value in options.items())

    from streamlit.web import cli as stcli

    stcli.main()


def _wait_for_server(port: int, timeout: float = 90.0) -> bool:
    import time
    import urllib.error
    import urllib.request

    url = f"http://localhost:{port}/_stcore/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    return False


def _run_browser_mode(script: Path) -> int:
    """起服务 → 打开默认浏览器 → 显示一个"宿主窗口"，关闭它即退出。"""
    import logging
    import multiprocessing
    import threading
    import webbrowser

    import config
    import paths

    log = logging.getLogger("desktop_entry")
    port = _find_free_port()
    url = f"http://localhost:{port}"
    options = _streamlit_options(port)

    server = multiprocessing.Process(
        target=_run_streamlit,
        args=(str(script), options),
        name="streamlit-server",
        daemon=True,
    )
    server.start()
    log.info("Streamlit 子进程已启动 pid=%s port=%s", server.pid, port)

    try:
        if not _wait_for_server(port):
            raise RuntimeError("Streamlit 服务启动超时（90 秒）")

        log.info("服务就绪：%s，打开默认浏览器", url)
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.title(f"{config.WINDOW_TITLE} — 运行中")
        root.geometry("520x210")
        root.minsize(480, 200)

        try:  # 任务栏图标（打包后带上自己的图标；iconbitmap 仅 Windows 支持）
            if os.name == "nt":
                icon = paths.resource_root() / "assets" / "app.ico"
                if icon.is_file():
                    root.iconbitmap(default=str(icon))
        except Exception:  # noqa: BLE001 - 图标失败不影响使用
            pass

        frame = ttk.Frame(root, padding=18)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text="界面已在默认浏览器中打开",
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w")
        ttk.Label(frame, text=url, foreground="#555555").pack(anchor="w", pady=(6, 0))
        ttk.Label(
            frame,
            text="关闭本窗口将停止下载服务，浏览器中的页面会随之失效。",
            foreground="#777777",
            wraplength=470,
        ).pack(anchor="w", pady=(12, 0))

        buttons = ttk.Frame(frame)
        buttons.pack(anchor="w", pady=(16, 0))

        def _exit() -> None:
            root.destroy()

        ttk.Button(
            buttons,
            text="再次打开浏览器",
            command=lambda: webbrowser.open(url),
        ).pack(side="left")
        ttk.Button(buttons, text="退出程序", command=_exit).pack(side="left", padx=8)

        root.protocol("WM_DELETE_WINDOW", _exit)
        log.info("宿主窗口已显示，等待用户关闭")
        root.mainloop()
        return 0
    finally:
        if server.is_alive():
            server.terminate()
            server.join(timeout=10)
        log.info("Streamlit 子进程已终止（exitcode=%s）", server.exitcode)


def _should_try_webview(mode: str) -> bool:
    """是否尝试桌面窗口模式。

    ``auto`` 模式下会看 settings.json 里的 ``webview_unavailable`` 标记：
    曾经失败过的机器不再每次启动都白等 2~3 秒。
    """
    if mode == "webview":
        return True
    if mode == "browser":
        return False
    try:
        import paths

        return not bool(paths.read_settings().get("webview_unavailable"))
    except Exception:  # noqa: BLE001 - 读设置失败就按"可以试"处理
        return True


def _mark_webview_unavailable() -> None:
    try:
        import paths

        paths.write_settings(webview_unavailable=True)
    except Exception:  # noqa: BLE001
        pass


def _clear_webview_unavailable() -> None:
    try:
        import paths

        paths.write_settings(webview_unavailable=None)
    except Exception:  # noqa: BLE001
        pass


def _run_webview_mode(script: Path) -> None:
    """尝试用 pywebview 打开真正的桌面窗口（环境支持时优先）。"""
    import config
    from streamlit_desktop_app import start_desktop_app

    start_desktop_app(
        str(script),
        title=config.WINDOW_TITLE,
        width=config.WINDOW_WIDTH,
        height=config.WINDOW_HEIGHT,
        options=dict(config.STREAMLIT_OPTIONS),
    )


def _run_create_shortcut() -> int:
    import desktop
    import paths

    exe = paths.executable_path()
    if exe is None:
        message = "源码模式下没有 exe，请先打包，或用 --exe 参数指定。"
        print(message)
        _show_error(message)
        return 2
    try:
        link = desktop.create_shortcut(exe, force=True)
    except desktop.ShortcutError as exc:
        print(f"创建失败：{exc}")
        _show_error(f"创建桌面快捷方式失败：\n{exc}")
        return 1
    print(f"已创建：{link}")
    return 0


def _prepare_dotnet(logging_module) -> None:
    """让 pywebview 的 WinForms 后端能在 CoreCLR 下加载（仅 Windows 需要）。

    pythonnet 3.x 在 Windows 上默认尝试 .NET Framework(netfx)，但它自带的
    Python.Runtime.dll 需要 CoreCLR；而 CoreCLR 默认只解析 Microsoft.NETCore.App，
    ``clr.AddReference('System.Windows.Forms')`` 会报 FileNotFoundException。
    解法：用一份把 ``Microsoft.WindowsDesktop.App`` 写进 framework 的 runtimeconfig
    预加载 pythonnet，之后 pywebview 的 ``import clr`` 就能直接复用该运行时。

    macOS 走 PyObjC/WKWebView，Linux 走 GTK/Qt，都不需要这些准备工作。
    """
    if os.name != "nt":
        return

    os.environ.setdefault("PYTHONNET_RUNTIME", "coreclr")

    log = logging_module.getLogger("desktop_entry")
    import paths

    runtime_config = paths.resource_root() / "assets" / "pythonnet.runtimeconfig.json"
    if not runtime_config.is_file():
        log.warning("缺少 runtimeconfig：%s（桌面窗口可能无法创建）", runtime_config)
        return

    try:
        import pythonnet

        pythonnet.load("coreclr", runtime_config=str(runtime_config))
        log.info("已加载 .NET 运行时配置：%s", runtime_config)
    except Exception as exc:  # noqa: BLE001 - 失败时交给 pywebview 自己报错
        log.warning("预加载 .NET 运行时失败：%s", exc)


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()

    args = list(sys.argv[1:] if argv is None else argv)
    log_path, _stream = _bootstrap_logging()
    _install_excepthook(log_path)

    import logging

    log = logging.getLogger("desktop_entry")
    log.info("启动：argv=%s frozen=%s", args, getattr(sys, "frozen", False))

    if "--help" in args or "-h" in args:
        print(HELP_TEXT)
        return 0
    if "--self-check" in args:
        return _run_self_check()
    if "--smoke-test" in args:
        return _run_smoke_test()
    if "--create-shortcut" in args:
        return _run_create_shortcut()

    import paths

    script = paths.resource_root() / "app.py"
    if not script.is_file():
        message = (
            "找不到应用脚本 app.py：\n"
            f"{script}\n\n"
            "请确认整个 _internal 目录与 exe 放在同一层（不要只复制 exe）。"
        )
        log.error(message)
        _show_error(message)
        return 1

    # 首启静默建快捷方式：失败只记日志
    try:
        import desktop

        created = desktop.ensure_shortcut_on_first_run()
        if created is not None:
            log.info("已创建桌面快捷方式：%s", created)
    except Exception as exc:  # noqa: BLE001 - 快捷方式失败不应阻断启动
        log.warning("创建桌面快捷方式失败：%s", exc)

    # 提前把下载目录建出来，让用户一眼看到文件会落在哪
    try:
        import config

        log.info("下载目录：%s", config.ensure_download_dir())
    except Exception as exc:  # noqa: BLE001
        log.warning("下载目录不可用：%s", exc)

    mode = "auto"
    if "--browser" in args:
        mode = "browser"
    elif "--webview" in args:
        mode = "webview"

    # 优先尝试真正的桌面窗口；环境不支持时自动回退浏览器模式
    if _should_try_webview(mode):
        try:
            log.info("尝试桌面窗口模式：script=%s", script)
            _prepare_dotnet(logging)
            _run_webview_mode(script)
            _clear_webview_unavailable()
            log.info("窗口已关闭，进程退出")
            return 0
        except Exception as exc:  # noqa: BLE001
            detail = str(exc).splitlines()[0] if str(exc) else ""
            log.warning(
                "桌面窗口不可用（%s: %s），回退浏览器模式",
                type(exc).__name__,
                detail,
            )
            _mark_webview_unavailable()
            if mode == "webview":
                _show_error(f"桌面窗口启动失败：\n{exc}\n\n详见日志：{log_path}")
                return 1
    else:
        log.info("设置记录显示窗口模式不可用，直接使用浏览器模式（--webview 可强制重试）")

    try:
        code = _run_browser_mode(script)
    except Exception as exc:  # noqa: BLE001
        log.exception("浏览器模式启动失败")
        _show_error(f"启动失败：\n{exc}\n\n详见日志：{log_path}")
        return 1

    log.info("宿主窗口已关闭，进程退出")
    return code


if __name__ == "__main__":
    sys.exit(main())
