"""全局配置与运行环境自检。

本模块是整个应用唯一的“配置来源”，负责三件事：

1. 存放默认下载路径（支持环境变量覆盖）；
2. 自动检测系统中的 FFmpeg / FFprobe，并给出可执行的安装提示；
3. 收敛 yt-dlp 的输出模板、格式选择串、网络参数、链接校验规则等常量。

约定：``core/*`` 与 ``ui/*`` 只从这里 import，不再各自散落魔法值。
本模块不依赖 Streamlit，可单独运行自检：

    python config.py
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import paths

# --------------------------------------------------------------------------- #
# 1. 应用元信息
# --------------------------------------------------------------------------- #
APP_NAME = "YouTube 视频下载器"
APP_TITLE = "YouTube 视频下载器"
APP_TAGLINE = "粘贴链接 · 选择清晰度 · 一键合并为 MP4"
PAGE_ICON = "🎬"

# 开发模式的"项目根"。打包后：只读资源用 paths.resource_root()，
# 可写数据用 paths.app_dir()，不要把 BASE_DIR 当成写入口。
BASE_DIR = paths.BASE_DIR
MIN_PYTHON = (3, 11)

# 桌面窗口（由 desktop_entry.py 使用）
WINDOW_TITLE = APP_TITLE
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 860

# 显式传给 Streamlit 的配置：不依赖 .streamlit/config.toml，也不依赖当前工作目录
STREAMLIT_OPTIONS: dict[str, str] = {
    "theme.base": "light",
    "theme.primaryColor": "#E11D48",
    "theme.backgroundColor": "#FFFFFF",
    "theme.secondaryBackgroundColor": "#F6F7F9",
    "theme.textColor": "#111827",
    "browser.gatherUsageStats": "false",
    "server.fileWatcherType": "none",
}

# --------------------------------------------------------------------------- #
# 2. 下载目录
# --------------------------------------------------------------------------- #
DOWNLOAD_DIR_ENV_VARS = ("YTDL_DOWNLOAD_DIR", "YOUTUBE_DOWNLOAD_DIR")
DEFAULT_DOWNLOAD_DIRNAME = paths.DOWNLOADS_DIRNAME
FALLBACK_DOWNLOAD_SUBPATH = ("Downloads", "YouTube_Downloader")

_download_dir_cache: Path | None = None


def _first_env(names: tuple[str, ...]) -> str | None:
    """返回第一个非空环境变量值。"""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def fallback_download_dir() -> Path:
    """兜底目录：%USERPROFILE%\\Downloads\\YouTube_Downloader。"""
    return (Path.home() / FALLBACK_DOWNLOAD_SUBPATH[0] / FALLBACK_DOWNLOAD_SUBPATH[1]).resolve()


def _configured_download_dir() -> Path | None:
    """settings.json 里用户指定的目录（界面可改）。"""
    configured = paths.read_settings().get("download_dir")
    if isinstance(configured, str) and configured.strip():
        return Path(os.path.expandvars(configured)).expanduser()
    return None


def _resolve_download_dir() -> Path:
    """优先级：环境变量 → settings.json → 部署目录/downloads → 用户 Downloads 兜底。"""
    override = _first_env(DOWNLOAD_DIR_ENV_VARS)
    if override:
        return Path(os.path.expandvars(override)).expanduser().resolve()

    configured = _configured_download_dir()
    if configured is not None:
        return configured.resolve()

    primary = paths.downloads_dir()  # 打包后 = exe 同级 downloads（D:\YouTube_Downloader\downloads）
    if paths.is_writable_dir(primary):
        return primary.resolve()
    return fallback_download_dir()


def get_download_dir() -> Path:
    """下载目录（不保证已存在）。

    结果按进程缓存（避免每次 rerun 都探测磁盘）；修改后调用
    :func:`reset_download_dir_cache` 或 :func:`set_download_dir`。
    """
    global _download_dir_cache
    if _download_dir_cache is None:
        _download_dir_cache = _resolve_download_dir()
    return _download_dir_cache


def reset_download_dir_cache() -> None:
    """丢弃缓存的下载目录（环境变量或设置变更后调用）。"""
    global _download_dir_cache
    _download_dir_cache = None
    paths.reload_settings()


def set_download_dir(value: str | Path) -> Path:
    """校验并保存下载目录到 settings.json，立即生效。"""
    text = str(value or "").strip()
    if not text:
        raise ValueError("下载目录不能为空")

    candidate = Path(os.path.expandvars(text)).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError as exc:  # 路径非法（例如含 NUL）
        raise ValueError(f"路径无效：{exc}") from exc

    if not paths.is_writable_dir(candidate):
        raise OSError(f"目录不可写：{candidate}")

    paths.write_settings(download_dir=str(candidate))
    reset_download_dir_cache()
    return candidate


def ensure_download_dir() -> Path:
    """确保下载目录存在并返回它；权限不足等错误转成可读中文提示。"""
    path = get_download_dir()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(
            f"无法创建下载目录 {path}（{exc.strerror or exc}），"
            "请在左侧修改下载目录，或用环境变量 YTDL_DOWNLOAD_DIR 指定一个可写目录。"
        ) from exc
    return path


# --------------------------------------------------------------------------- #
# 3. 输出与格式策略
# --------------------------------------------------------------------------- #
# 文件名模板：限制标题长度，附加视频 ID 避免重名，%(ext)s 由 yt-dlp 填充。
OUTPUT_TEMPLATE = "%(title).120B [%(id)s].%(ext)s"
MERGE_OUTPUT_FORMAT = "mp4"           # 合并后的容器格式
AUDIO_ONLY_FORMAT = "ba/b"            # 仅音频时的兜底选择
AUDIO_MP3_BITRATE = "192"             # 仅音频转 MP3 的码率（kbps）
MAX_HEIGHT_LIMIT = 4320               # 解析结果的高度上限（8K）


def build_output_template(height: int | None = None, *, audio_only: bool = False) -> str:
    """生成文件名模板。

    带上清晰度后缀，避免同一视频的不同清晰度互相覆盖
    （例如先下 144p 再下 1080p，两个文件应共存而不是命中旧文件）。
    """
    if audio_only:
        suffix = "[audio]"
    elif height:
        suffix = f"[{int(height)}p]"
    else:
        suffix = ""
    return f"%(title).120B [%(id)s]{suffix}.%(ext)s"


def build_format_selector(height: int | None = None) -> str:
    """构造 yt-dlp 格式选择串。

    逐级降级，保证"总能选到东西"：
    1. 指定高度：优先 H.264（avc1）—— 兼容性最好，老设备/剪辑软件都能播；
    2. 其次任意 MP4 容器（可能是 AV1 / VP9）；
    3. 再次同高度任意容器（WebM 等）；
    4. 最后放弃高度限制，选最佳音视频流。

    yt-dlp 只负责分别下载视频流与音频流，合并由 FFmpeg 完成。
    """
    if height is None:
        return "bv*+ba/b"
    return (
        f"bv*[height<={height}][vcodec^=avc1]+ba[ext=m4a]/"
        f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/"
        f"bv*[height<={height}]+ba[ext=m4a]/"
        f"bv*[height<={height}]+ba/b[height<={height}]/"
        f"bv*+ba/b"
    )


# --------------------------------------------------------------------------- #
# 4. 链接校验（避免把明显非法的输入丢给 yt-dlp）
# --------------------------------------------------------------------------- #
SUPPORTED_HOSTS = (
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "youtu.be",
)

YOUTUBE_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.|m\.|music\.)?"
    r"(?:youtube\.com|youtube-nocookie\.com|youtu\.be)/\S+$",
    re.IGNORECASE,
)
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def is_supported_url(raw_url: str | None) -> bool:
    """判断输入是否是 YouTube 链接（也接受裸视频 ID）。"""
    if not raw_url:
        return False
    text = raw_url.strip()
    if VIDEO_ID_RE.match(text):
        return True
    if not YOUTUBE_URL_RE.match(text):
        return False
    candidate = text if re.match(r"^https?://", text, re.IGNORECASE) else f"https://{text}"
    host = urlparse(candidate).hostname or ""
    return host.lower() in SUPPORTED_HOSTS


# --------------------------------------------------------------------------- #
# 5. 网络与 yt-dlp 运行参数
# --------------------------------------------------------------------------- #
SOCKET_TIMEOUT = 20      # 秒，单次请求超时
RETRIES = 3              # 网络失败重试次数
PROGRESS_POLL_INTERVAL = 0.4   # 秒，UI 刷新进度的轮询间隔
MAX_LISTED_FORMATS = 40        # 清晰度列表最多展示多少条

COOKIES_ENV_VARS = ("YTDL_COOKIES_FILE", "YOUTUBE_COOKIES_FILE")


def get_cookies_file() -> Path | None:
    """可选：Netscape 格式 cookies 文件，用于年龄限制 / 人机验证场景。"""
    override = _first_env(COOKIES_ENV_VARS)
    if not override:
        return None
    path = Path(os.path.expandvars(override)).expanduser()
    return path if path.is_file() else None


# --------------------------------------------------------------------------- #
# 6. FFmpeg / FFprobe 自动检测
# --------------------------------------------------------------------------- #
FFMPEG_ENV_VARS = ("YTDL_FFMPEG_PATH", "FFMPEG_PATH", "FFMPEG_BINARY", "FFMPEG_DIR")
FFPROBE_ENV_VARS = ("YTDL_FFPROBE_PATH", "FFPROBE_PATH", "FFPROBE_BINARY")
_FFMPEG_PROBE_TIMEOUT = 15  # 秒

# Windows 下调用外部命令不弹出黑窗口
_SUBPROCESS_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class FFmpegNotFoundError(RuntimeError):
    """找不到可用的 FFmpeg 时抛出，携带安装指引。"""

    def __init__(self, message: str, hint: str) -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class FFmpegInfo:
    """检测结果。"""

    ffmpeg: Path
    ffprobe: Path | None
    version: str | None
    source: str  # environment / PATH / candidate:<dir> / same-dir-as-ffmpeg

    @property
    def directory(self) -> Path:
        """传给 yt-dlp ``--ffmpeg-location`` 的目录。"""
        return self.ffmpeg.parent

    @property
    def has_ffprobe(self) -> bool:
        return self.ffprobe is not None

    def describe(self) -> str:
        version = f"v{self.version}" if self.version else "版本未知"
        probe = "已检测到" if self.has_ffprobe else "未检测到（部分功能降级）"
        return f"FFmpeg {version} · {self.ffmpeg} · FFprobe {probe} · 来源 {self.source}"


def _exe_name(stem: str) -> str:
    return f"{stem}.exe" if platform.system() == "Windows" else stem


def _as_executable(value: str, stem: str) -> Path | None:
    """把环境变量值解析成可执行文件路径（允许填目录）。"""
    path = Path(os.path.expandvars(value)).expanduser()
    if path.is_dir():
        candidate = path / _exe_name(stem)
        return candidate if candidate.is_file() else None
    return path if path.is_file() else None


def _registry_path_dirs() -> list[Path]:
    """Windows：从注册表读取用户/系统 PATH。

    本进程的 PATH 可能是启动时的快照，用户刚装完 FFmpeg 时读不到；
    直接读注册表可以让「重新检测」立刻生效，无需重启应用。
    """
    if platform.system() != "Windows":
        return []
    try:
        import winreg
    except ImportError:  # 非 Windows 或精简发行版
        return []

    entries: list[tuple[int, str]] = [
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    ]
    dirs: list[Path] = []
    for hive, subkey in entries:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                raw, _ = winreg.QueryValueEx(key, "Path")
        except OSError:
            continue
        for chunk in str(raw).split(";"):
            text = chunk.strip()
            if not text:
                continue
            try:
                candidate = Path(os.path.expandvars(text)).expanduser()
                if candidate.is_dir():
                    dirs.append(candidate)
            except OSError:
                continue
    return dirs


def _winget_package_dirs() -> list[Path]:
    """winget 的 portable 包会解压到 Packages\\<pkg>\\<ver>\\bin，不在 Links 里。"""
    if platform.system() != "Windows":
        return []
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    root = Path(local) / "Microsoft" / "WinGet" / "Packages"
    if not root.is_dir():
        return []
    dirs: list[Path] = []
    try:
        for entry in root.iterdir():
            if not entry.is_dir() or "ffmpeg" not in entry.name.lower():
                continue
            dirs.append(entry / "bin")
            for sub in entry.iterdir():
                if sub.is_dir():
                    dirs += [sub / "bin", sub]
    except OSError:
        return dirs
    return dirs


def _candidate_directories() -> list[Path]:
    """常见安装位置，按优先级排列。"""
    system = platform.system()
    dirs: list[Path] = []
    env = os.environ

    if system == "Windows":
        for var in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)",
                    "ProgramData", "USERPROFILE"):
            base = env.get(var)
            if not base:
                continue
            root = Path(base)
            dirs += [
                root / "Microsoft" / "WinGet" / "Links",
                root / "Programs" / "ffmpeg" / "bin",
                root / "scoop" / "shims",
                root / "scoop" / "apps" / "ffmpeg" / "current" / "bin",
                root / "chocolatey" / "bin",
                root / "ffmpeg" / "bin",
            ]
        # winget portable 包（Gyan.FFmpeg 等）与注册表里的最新 PATH
        dirs += _winget_package_dirs()
        dirs += _registry_path_dirs()
        dirs += [Path("C:/ffmpeg/bin"), Path("C:/ffmpeg"),
                 Path("C:/Program Files/ffmpeg/bin"), Path("D:/ffmpeg/bin")]
        # 解压式安装：C:\ffmpeg-7.1-full_build\bin 之类
        for drive in ("C:/", "D:/", "E:/"):
            root = Path(drive)
            if not root.is_dir():
                continue
            try:
                for entry in root.glob("ffmpeg*"):
                    if entry.is_dir():
                        dirs += [entry / "bin", entry]
            except OSError:
                continue
    elif system == "Darwin":
        dirs += [Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/opt/local/bin")]
    else:
        dirs += [Path("/usr/bin"), Path("/usr/local/bin"), Path("/snap/bin"), Path("/opt/ffmpeg/bin")]

    dirs.append(Path.home() / "bin")
    return dirs


def _probe_version(executable: Path) -> str | None:
    """实际执行一次 ``ffmpeg -version``，确认二进制可运行并取版本号。"""
    try:
        proc = subprocess.run(
            [str(executable), "-version"],
            capture_output=True, text=True, timeout=_FFMPEG_PROBE_TIMEOUT,
            creationflags=_SUBPROCESS_FLAGS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    output = (proc.stdout or proc.stderr or "").strip()
    if not output:
        return None
    first_line = output.splitlines()[0]
    match = re.search(r"ffmpeg version (\S+)", first_line)
    return match.group(1) if match else first_line[:60]


def _find_ffprobe(ffmpeg_path: Path) -> tuple[Path | None, str]:
    for name in FFPROBE_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            found = _as_executable(value, "ffprobe")
            if found:
                return found, "environment"
    beside = ffmpeg_path.parent / _exe_name("ffprobe")
    if beside.is_file():
        return beside, "same-dir-as-ffmpeg"
    on_path = shutil.which("ffprobe")
    if on_path:
        candidate = Path(on_path)
        if candidate.is_file():
            return candidate, "PATH"
    for directory in _candidate_directories():
        candidate = directory / _exe_name("ffprobe")
        try:
            if candidate.is_file():
                return candidate, f"candidate:{directory}"
        except OSError:
            continue
    return None, ""


def _detect_ffmpeg_uncached() -> FFmpegInfo | None:
    # 1) 显式环境变量
    for name in FFMPEG_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if not value:
            continue
        executable = _as_executable(value, "ffmpeg")
        if executable:
            version = _probe_version(executable)
            if version:
                ffprobe, _ = _find_ffprobe(executable)
                return FFmpegInfo(executable, ffprobe, version, f"environment:{name}")

    # 2) 随 exe 捆绑的 FFmpeg（打包分发的唯一保证；优先于系统 PATH）
    for directory in paths.bundled_bin_dirs():
        candidate = directory / _exe_name("ffmpeg")
        try:
            if not candidate.is_file():
                continue
        except OSError:
            continue
        version = _probe_version(candidate)
        if version:
            ffprobe, _ = _find_ffprobe(candidate)
            return FFmpegInfo(candidate, ffprobe, version, f"bundled:{directory}")

    # 3) 系统 PATH
    on_path = shutil.which("ffmpeg")
    if on_path:
        executable = Path(on_path)
        version = _probe_version(executable)
        if version:
            ffprobe, _ = _find_ffprobe(executable)
            return FFmpegInfo(executable, ffprobe, version, "PATH")

    # 4) 常见安装目录
    for directory in _candidate_directories():
        candidate = directory / _exe_name("ffmpeg")
        try:
            if not candidate.is_file():
                continue
        except OSError:
            continue
        version = _probe_version(candidate)
        if version:
            ffprobe, _ = _find_ffprobe(candidate)
            return FFmpegInfo(candidate, ffprobe, version, f"candidate:{directory}")

    return None


_cached_info: FFmpegInfo | None = None
_cache_ready = False


def detect_ffmpeg(*, refresh: bool = False) -> FFmpegInfo | None:
    """检测 FFmpeg；找不到返回 ``None``。结果带缓存，``refresh=True`` 重新扫描。"""
    global _cached_info, _cache_ready
    if refresh or not _cache_ready:
        _cached_info = _detect_ffmpeg_uncached()
        _cache_ready = True
    return _cached_info


def ffmpeg_available() -> bool:
    return detect_ffmpeg() is not None


def require_ffmpeg() -> FFmpegInfo:
    """取 FFmpeg 信息；不存在时抛 :class:`FFmpegNotFoundError`（含安装指引）。"""
    info = detect_ffmpeg()
    if info is None:
        raise FFmpegNotFoundError(
            "未检测到 FFmpeg，无法合并音视频。", ffmpeg_install_hint()
        )
    return info


def ffmpeg_install_hint(system: str | None = None) -> str:
    """按平台给出的安装指引（Markdown 友好）。

    打包分发的版本自带 FFmpeg；此时提示应指向"安装包不完整"而不是让用户去装。
    """
    system = system or platform.system()
    if paths.is_frozen():
        exe_dir = paths.app_dir()
        return (
            "**未找到随程序捆绑的 FFmpeg**\n\n"
            f"正常安装包里应包含 `{exe_dir / 'bin' / 'ffmpeg.exe'}`。\n\n"
            "1. 请确认整个 `_internal` 目录与 `YouTubeDownloader.exe` 放在同一层"
            "（不要只复制 exe）；\n"
            "2. 也可以手动把 `ffmpeg.exe` 与 `ffprobe.exe` 放到 exe 同级的 `bin\\` 目录；\n"
            "3. 或用环境变量 `YTDL_FFMPEG_PATH` 指向已有的 ffmpeg。\n\n"
            "完成后点界面上的「重新检测」。"
        )
    if system == "Windows":
        return (
            "**Windows 安装 FFmpeg（任选其一）**\n\n"
            "1. winget：`winget install --id Gyan.FFmpeg -e`\n"
            "2. Chocolatey：`choco install ffmpeg -y`\n"
            "3. Scoop：`scoop install ffmpeg`\n"
            "4. 手动：下载 <https://www.gyan.dev/ffmpeg/builds/> 的 release 包，"
            "解压后把 `bin` 目录加入系统 PATH\n\n"
            "已装在其他目录（未进 PATH）时，设置环境变量 "
            "`YTDL_FFMPEG_PATH` 指向 `ffmpeg.exe` 或它的 `bin` 目录，然后重启应用。\n"
            "装好后点界面上的「重新检测」。"
        )
    if system == "Darwin":
        return (
            "**macOS 安装 FFmpeg**\n\n"
            "```\nbrew install ffmpeg\n```\n\n"
            "若使用 Homebrew 在非标准位置，可设置 `YTDL_FFMPEG_PATH` 指向二进制文件。"
        )
    return (
        "**Linux 安装 FFmpeg**\n\n"
        "```\nsudo apt install ffmpeg        # Debian/Ubuntu\n"
        "sudo dnf install ffmpeg        # Fedora\n"
        "sudo pacman -S ffmpeg          # Arch\n```\n\n"
        "或设置 `YTDL_FFMPEG_PATH` 指向已有的二进制文件。"
    )


# --------------------------------------------------------------------------- #
# 7. 环境自检（供 UI 与 CLI 复用）
# --------------------------------------------------------------------------- #
def environment_report() -> dict[str, object]:
    """收集当前运行环境的关键信息。"""
    python_ok = sys.version_info[:2] >= MIN_PYTHON
    download_dir = get_download_dir()
    ffmpeg = detect_ffmpeg()
    return {
        "python_version": platform.python_version(),
        "python_ok": python_ok,
        "platform": f"{platform.system()} {platform.release()}",
        "frozen": paths.is_frozen(),
        "app_dir": str(paths.app_dir()),
        "resource_root": str(paths.resource_root()),
        "download_dir": str(download_dir),
        "download_dir_exists": download_dir.is_dir(),
        "download_dir_writable": paths.is_writable_dir(download_dir),
        "ffmpeg_ok": ffmpeg is not None,
        "ffmpeg_info": ffmpeg.describe() if ffmpeg else None,
        "ffmpeg_source": ffmpeg.source if ffmpeg else None,
        "ffprobe_ok": bool(ffmpeg and ffmpeg.has_ffprobe),
    }


def print_self_check() -> None:
    report = environment_report()
    mode = "打包运行（frozen）" if report["frozen"] else "源码运行（development）"
    lines = [
        f"{APP_NAME} — 环境自检",
        "-" * 52,
        f"运行形态      : {mode}",
        f"Python        : {report['python_version']} {'OK' if report['python_ok'] else '不满足 >= 3.11'}",
        f"操作系统      : {report['platform']}",
        f"程序目录      : {report['app_dir']}",
        f"资源目录      : {report['resource_root']}",
        f"下载目录      : {report['download_dir']}"
        f" ({'可写' if report['download_dir_writable'] else '不存在或不可写'})",
        f"FFmpeg        : {report['ffmpeg_info'] or '未检测到'}",
        f"FFprobe       : {'已检测到' if report['ffprobe_ok'] else '未检测到'}",
    ]
    print("\n".join(lines))
    if not report["ffmpeg_ok"]:
        print("\n" + ffmpeg_install_hint().replace("**", ""))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print_self_check()
