"""yt-dlp 下载封装：下载 → FFmpeg 合并 → 返回本地完整路径。

职责边界
- 只负责"下载 + 后处理"，解析在 ``core/extractor.py``；
- 进度通过 :class:`DownloadProgress` 快照对外暴露，界面层轮询即可；
- 失败收敛为 :class:`DownloadFailed`（中文提示 + 可选 hint），取消为
  :class:`DownloadCancelledError`；两者都不会让进程崩溃；
- 不 import streamlit，可独立测试。

线程模型
``DownloadJob.execute()`` 是阻塞调用。界面层用 ``start()`` 放到后台线程，
主线程 ``snapshot()`` 轮询进度；``cancel()`` 置位后由 yt-dlp 的 progress hook
抛出取消异常，从而中断下载。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

import yt_dlp

import config
from core.extractor import QualityOption, VideoInfo, classify_error
from core.utils import QuietLogger

try:  # 兜底：极旧版本没有该异常时也能运行
    from yt_dlp.utils import DownloadCancelled
except ImportError:  # pragma: no cover
    class DownloadCancelled(Exception):  # type: ignore[no-redef]
        """旧版 yt-dlp 的取消异常占位。"""

__all__ = [
    "DownloadProgress",
    "DownloadJob",
    "DownloadFailed",
    "DownloadCancelledError",
    "download",
]

_MERGE_PHASE = "正在合并音视频（FFmpeg）…"
_AUDIO_PHASE = "正在转换为 MP3…"


class DownloadFailed(RuntimeError):
    """下载失败。``message`` 面向用户，``hint`` 是可选建议。"""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


class DownloadCancelledError(RuntimeError):
    """用户主动取消。"""


@dataclass(frozen=True)
class DownloadProgress:
    """一次下载的只读进度快照。"""

    status: str = "pending"      # pending / downloading / merging / finished / failed / cancelled
    percent: float = 0.0
    phase: str = "准备中"
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    speed: float | None = None
    eta: int | None = None
    message: str = ""
    output_path: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status in {"pending", "downloading", "merging"}

    @property
    def is_finished(self) -> bool:
        return self.status == "finished"


class DownloadJob:
    """一次下载任务：构造后 ``execute()`` 阻塞执行，或 ``start()`` 后台执行。"""

    def __init__(
        self,
        info: VideoInfo,
        quality: QualityOption,
        *,
        download_dir: Path | str | None = None,
        on_update: Callable[[DownloadProgress], None] | None = None,
    ) -> None:
        self.info = info
        self.quality = quality
        self.download_dir = Path(download_dir) if download_dir else config.get_download_dir()
        self._on_update = on_update

        self._lock = threading.RLock()
        self._cancel_event = threading.Event()
        self._progress = DownloadProgress()
        self._result: Path | None = None
        self._error: Exception | None = None
        self._thread: threading.Thread | None = None

        # 内部统计：用来把多路流（视频 + 音频）合成一个总进度
        self._expected_streams: int | None = None
        self._finished_streams = 0
        self._streams: dict[str, tuple[int, int]] = {}

    # ------------------------------------------------------------------ #
    # 对外状态
    # ------------------------------------------------------------------ #
    def snapshot(self) -> DownloadProgress:
        with self._lock:
            return self._progress

    @property
    def progress(self) -> DownloadProgress:
        return self.snapshot()

    @property
    def result(self) -> Path | None:
        return self._result

    @property
    def error(self) -> Exception | None:
        return self._error

    def cancel(self) -> None:
        """请求取消；真正中断发生在下一个进度回调。"""
        self._cancel_event.set()
        if self.snapshot().is_active:
            self._publish(phase="正在取消…", message="等待当前分片结束")

    # ------------------------------------------------------------------ #
    # 执行
    # ------------------------------------------------------------------ #
    def execute(self) -> Path:
        """阻塞执行下载。成功返回本地完整路径，失败抛异常。"""
        ffmpeg = self._require_ffmpeg()
        self._ensure_download_dir()

        self._publish(status="downloading", phase="正在连接 YouTube…",
                      percent=0.0, message="")
        try:
            with yt_dlp.YoutubeDL(self._build_options(ffmpeg)) as ydl:
                raw = ydl.extract_info(self._url, download=True)
        except Exception as exc:  # noqa: BLE001 - 统一翻译后再抛出
            raise self._translate(exc) from exc

        path = self._resolve_output(raw)
        self._result = path
        self._publish(status="finished", percent=100.0, phase="已完成",
                      message="下载完成", output_path=str(path))
        return path

    def start(self) -> threading.Thread:
        """后台线程执行，返回线程对象（daemon）。"""
        thread = threading.Thread(target=self._run, name="ytdl-download", daemon=True)
        self._thread = thread
        thread.start()
        return thread

    def wait(self, timeout: float | None = None) -> bool:
        """等待结束，返回是否已结束。"""
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def _run(self) -> None:
        try:
            self.execute()
        except (DownloadFailed, DownloadCancelledError) as exc:
            self._error = exc
        except Exception as exc:  # noqa: BLE001 - 线程内兜底，绝不让异常静默消失
            failure = DownloadFailed(f"下载失败：{exc}")
            self._error = failure
            self._publish(status="failed", phase="失败", message=str(failure))

    # ------------------------------------------------------------------ #
    # 内部：环境与选项
    # ------------------------------------------------------------------ #
    @property
    def _url(self) -> str:
        return self.info.webpage_url or f"https://www.youtube.com/watch?v={self.info.video_id}"

    def _require_ffmpeg(self) -> config.FFmpegInfo:
        try:
            return config.require_ffmpeg()
        except config.FFmpegNotFoundError as exc:
            self._publish(status="failed", phase="缺少 FFmpeg", message=f"{exc}")
            raise DownloadFailed(str(exc), hint=exc.hint) from exc

    def _ensure_download_dir(self) -> None:
        try:
            self.download_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            message = f"无法创建下载目录 {self.download_dir}（{exc.strerror or exc}）"
            self._publish(status="failed", phase="失败", message=message)
            raise DownloadFailed(
                message, hint="用环境变量 YTDL_DOWNLOAD_DIR 指定一个可写目录后重试。"
            ) from exc

    def _build_options(self, ffmpeg: config.FFmpegInfo) -> dict[str, Any]:
        options: dict[str, Any] = {
            "format": self.quality.selector,
            "paths": {"home": str(self.download_dir)},
            "outtmpl": config.build_output_template(
                self.quality.height, audio_only=self.quality.is_audio_only
            ),
            "noplaylist": True,
            "merge_output_format": config.MERGE_OUTPUT_FORMAT,
            # 合并/转码必须由 FFmpeg 完成，路径交给 yt-dlp
            "ffmpeg_location": str(ffmpeg.directory),
            "progress_hooks": [self._on_progress],
            "postprocessor_hooks": [self._on_postprocessor],
            "socket_timeout": config.SOCKET_TIMEOUT,
            "retries": config.RETRIES,
            "fragment_retries": config.RETRIES,
            "concurrent_fragment_downloads": 4,
            "overwrites": False,
            "noprogress": True,
            "quiet": True,
            "no_warnings": True,
            "logger": QuietLogger(),
        }
        cookie_opts = config.cookie_options()
        if cookie_opts:
            options.update(cookie_opts)

        if self.quality.is_audio_only:
            options["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": config.AUDIO_MP3_BITRATE,
                }
            ]
        return options

    # ------------------------------------------------------------------ #
    # 内部：进度
    # ------------------------------------------------------------------ #
    def _publish(self, **changes: Any) -> None:
        with self._lock:
            self._progress = replace(self._progress, **changes)
            snapshot = self._progress
        callback = self._on_update
        if callback is not None:
            try:
                callback(snapshot)
            except Exception:  # 界面回调出错不应影响下载
                pass

    def _on_progress(self, data: dict[str, Any]) -> None:
        if self._cancel_event.is_set():
            raise DownloadCancelled("用户已取消下载")

        status = data.get("status")
        info_dict = data.get("info_dict") or {}
        if status == "downloading":
            self._update_downloading(data, info_dict)
        elif status == "finished":
            self._update_stream_finished(info_dict)
        elif status == "error":
            self._publish(status="failed", phase="失败", message="下载媒体流时出错")

    def _update_downloading(self, data: dict[str, Any],
                            info_dict: dict[str, Any]) -> None:
        downloaded = int(data.get("downloaded_bytes") or 0)
        total_raw = data.get("total_bytes") or data.get("total_bytes_estimate")
        total = int(total_raw) if total_raw else 0
        stream_key = str(info_dict.get("format_id") or f"stream{len(self._streams)}")

        has_video = (info_dict.get("vcodec") or "none") != "none"
        has_audio = (info_dict.get("acodec") or "none") != "none"

        with self._lock:
            if self._expected_streams is None:
                single_file = self.quality.is_audio_only or (has_video and has_audio)
                self._expected_streams = 1 if single_file else 2

            self._streams[stream_key] = (downloaded, total)
            fraction = (downloaded / total) if total else 0.0
            expected = max(self._expected_streams or 1, 1)
            ratio = (self._finished_streams + min(fraction, 1.0)) / expected
            percent = min(ratio * 100.0, 99.0)

            sum_downloaded = sum(value[0] for value in self._streams.values())
            totals = [value[1] for value in self._streams.values()]
            sum_total = sum(totals) if totals and all(t > 0 for t in totals) else None

        if self._expected_streams == 1:
            phase = "正在下载"
        else:
            phase = "正在下载音频流" if has_audio and not has_video else "正在下载视频流"

        self._publish(
            status="downloading",
            percent=percent,
            phase=phase,
            downloaded_bytes=sum_downloaded,
            total_bytes=sum_total,
            speed=data.get("speed"),
            eta=data.get("eta"),
        )

    def _update_stream_finished(self, info_dict: dict[str, Any]) -> None:
        with self._lock:
            self._finished_streams += 1
            expected = max(self._expected_streams or 1, 1)
            percent = min(self._finished_streams / expected * 100.0, 99.0)

        if self._finished_streams >= expected:
            self._publish(status="merging", percent=percent, phase="准备后处理…")
        else:
            self._publish(status="downloading", percent=percent)

    def _on_postprocessor(self, data: dict[str, Any]) -> None:
        if self._cancel_event.is_set():
            raise DownloadCancelled("用户已取消下载")
        if data.get("status") != "started":
            return

        name = str(data.get("postprocessor") or "")
        if "Merger" in name:
            phase = _MERGE_PHASE
        elif "ExtractAudio" in name:
            phase = _AUDIO_PHASE
        else:
            phase = f"正在后处理（{name or 'FFmpeg'}）…"

        with self._lock:
            percent = max(self._progress.percent, 95.0)
        self._publish(status="merging", phase=phase, percent=percent)

    # ------------------------------------------------------------------ #
    # 内部：结果与异常
    # ------------------------------------------------------------------ #
    def _resolve_output(self, raw: Any) -> Path:
        candidates: list[Path] = []
        if isinstance(raw, dict):
            filepath = raw.get("filepath")
            if filepath:
                candidates.append(Path(filepath))
            for entry in raw.get("requested_downloads") or []:
                entry_path = entry.get("filepath") if isinstance(entry, dict) else None
                if entry_path:
                    candidates.append(Path(entry_path))

        for path in candidates:
            try:
                if path.is_file():
                    return path
            except OSError:
                continue

        # 回退：合并后 yt-dlp 可能没把最终路径写回 info，按视频 ID 在目录里找最新的成品
        expected_suffix = f".{self.quality.ext.lower()}"
        try:
            matches = [
                item for item in self.download_dir.glob(f"*[{self.info.video_id}]*")
                if item.is_file() and item.suffix.lower() not in {".part", ".ytdl", ".temp"}
            ]
        except OSError:
            matches = []

        def _mtime(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0.0

        matches.sort(key=_mtime, reverse=True)
        preferred = [item for item in matches if item.suffix.lower() == expected_suffix]
        for path in preferred or matches:
            return path

        raise DownloadFailed(
            "下载流程结束，但没有在下载目录中找到成品文件。",
            hint=f"请检查 {self.download_dir} 是否可写，或查看是否有残留的 .part 文件。",
        )

    def _translate(self, exc: Exception) -> Exception:
        if self._cancel_event.is_set() or isinstance(exc, DownloadCancelled) \
                or "cancel" in type(exc).__name__.lower():
            self._cleanup_partial()
            self._publish(status="cancelled", phase="已取消", message="下载已取消")
            return DownloadCancelledError("下载已取消")

        friendly, hint = classify_error(str(exc))
        if "ffmpeg" in str(exc).lower():
            friendly = f"{friendly}（FFmpeg 合并失败）"
            hint = hint or config.ffmpeg_install_hint()
        self._publish(status="failed", phase="失败", message=friendly)
        return DownloadFailed(friendly, hint=hint)

    def _cleanup_partial(self) -> None:
        """取消后清理本视频的分片残留（失败时保留，便于 yt-dlp 断点续传）。"""
        try:
            leftovers = list(self.download_dir.glob(f"*[{self.info.video_id}]*"))
        except OSError:
            return
        for item in leftovers:
            name = item.name.lower()
            if ".part" in name or name.endswith(".ytdl") or name.endswith(".temp"):
                try:
                    item.unlink()
                except OSError:
                    continue


def download(
    info: VideoInfo,
    quality: QualityOption,
    *,
    download_dir: Path | str | None = None,
    on_update: Callable[[DownloadProgress], None] | None = None,
) -> Path:
    """阻塞式便捷入口：下载并返回本地完整路径。"""
    job = DownloadJob(info, quality, download_dir=download_dir, on_update=on_update)
    return job.execute()
