"""可复用界面组件：顶部说明、环境面板、信息卡、清晰度选择、进度与结果。

约定
- 只负责渲染与收集用户动作，不持有业务状态；
- 需要改变会话状态的动作以返回值交给 ``app.py`` 处理（例如 ``"reset"``）；
- 不 import 任何网络/下载实现细节以外的模块，保证可被 AppTest 直接驱动。
"""

from __future__ import annotations

import html
from pathlib import Path

import streamlit as st

import config
import desktop
import paths
from core.downloader import DownloadCancelledError, DownloadFailed, DownloadJob, DownloadProgress
from core.extractor import QualityOption, VideoInfo
from core.utils import format_count, format_eta, format_filesize, format_speed

__all__ = [
    "page_header",
    "sidebar_panel",
    "video_card",
    "quality_selector",
    "download_row",
    "progress_view",
]


# --------------------------------------------------------------------------- #
# 顶部与侧栏
# --------------------------------------------------------------------------- #
def page_header() -> None:
    st.markdown(
        f"""
<div class="ytdl-hero">
  <div class="ytdl-hero-icon">{config.PAGE_ICON}</div>
  <div>
    <h1 class="ytdl-hero-title">{html.escape(config.APP_TITLE)}</h1>
    <p class="ytdl-hero-sub">{html.escape(config.APP_TAGLINE)}</p>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


def sidebar_panel() -> None:
    """环境状态、下载目录（可修改）、使用步骤与合规提示。"""
    with st.sidebar:
        flash = st.session_state.pop("_download_dir_flash", None)
        if flash:
            st.success(flash)

        st.markdown("#### 环境状态")

        ffmpeg = config.detect_ffmpeg()
        if ffmpeg is not None:
            version = f" {ffmpeg.version}" if ffmpeg.version else ""
            st.success(f"FFmpeg{version} 已就绪")
            st.caption(str(ffmpeg.ffmpeg))
            source = ffmpeg.source or ""
            if source.startswith("bundled"):
                st.caption("来源：随程序捆绑")
            elif not ffmpeg.has_ffprobe:
                st.caption("未检测到 FFprobe（不影响合并）")
        else:
            st.error("未检测到 FFmpeg，无法合并音视频")
            st.markdown(config.ffmpeg_install_hint())
            if st.button("重新检测 FFmpeg", use_container_width=True):
                config.detect_ffmpeg(refresh=True)
                st.rerun()

        st.divider()
        st.caption("下载目录")
        st.code(str(config.get_download_dir()), language=None)

        with st.expander("修改下载目录"):
            target = st.text_input(
                "新路径",
                value=str(config.get_download_dir()),
                key="download_dir_input",
                help="保存后写入程序目录下的 settings.json，下次启动依然生效。",
            )
            if st.button("保存并切换", use_container_width=True, key="save_download_dir"):
                try:
                    saved = config.set_download_dir(target)
                except (OSError, ValueError) as exc:
                    st.error(f"无法使用该目录：{exc}")
                else:
                    st.session_state["_download_dir_flash"] = f"已切换到 {saved}"
                    st.rerun()

        st.divider()
        exe = paths.executable_path()
        if exe is None:
            st.caption("桌面快捷方式：打包版本可用")
        elif st.button("创建桌面快捷方式", use_container_width=True, key="create_shortcut"):
            try:
                link = desktop.create_shortcut(exe, force=True)
            except desktop.ShortcutError as exc:
                st.error(f"创建失败：{exc}")
            else:
                st.success(f"已创建：{link}")

        st.divider()
        st.markdown("#### 使用步骤")
        st.markdown(
            "1. 粘贴链接并解析\n"
            "2. 选择清晰度（末尾含「仅音频」）\n"
            "3. 点击下载，等待 FFmpeg 合并\n"
            "4. 复制完成后显示的完整路径"
        )

        st.divider()
        st.caption("请仅下载你有权保存的内容，并遵守 YouTube 服务条款。")


# --------------------------------------------------------------------------- #
# 视频信息卡
# --------------------------------------------------------------------------- #
def video_card(info: VideoInfo) -> None:
    with st.container(border=True):
        thumb_col, meta_col = st.columns([1, 2.4], gap="large")

        with thumb_col:
            if info.thumbnail:
                st.image(info.thumbnail, width="stretch")
            else:
                st.caption("没有可用封面")

        with meta_col:
            st.markdown(
                f'<div class="ytdl-video-title">{html.escape(info.title)}</div>',
                unsafe_allow_html=True,
            )

            chips: list[str] = []
            if info.uploader:
                chips.append(f"👤 {info.uploader}")
            chips.append(f"⏱ {info.duration_text}")
            if info.upload_date:
                chips.append(f"📅 {info.upload_date}")
            if info.view_count is not None:
                chips.append(f"👁 {format_count(info.view_count)} 次观看")
            st.markdown(
                "".join(f'<span class="ytdl-chip">{html.escape(chip)}</span>' for chip in chips),
                unsafe_allow_html=True,
            )

            if info.webpage_url:
                st.link_button("在 YouTube 打开", info.webpage_url)


# --------------------------------------------------------------------------- #
# 清晰度与下载按钮
# --------------------------------------------------------------------------- #
def quality_selector(info: VideoInfo) -> QualityOption:
    options = info.qualities
    st.markdown(
        '<div class="ytdl-section-title">选择清晰度</div>', unsafe_allow_html=True
    )
    index = st.radio(
        "清晰度",
        options=list(range(len(options))),
        format_func=lambda i: options[i].label,
        captions=[option.detail for option in options],
        key=f"quality_{info.video_id}",
        label_visibility="collapsed",
    )
    return options[index]


def download_row(quality: QualityOption, *, disabled: bool) -> bool:
    """渲染「开始下载 + 预计体积」一行，返回是否点击。"""
    action_col, hint_col = st.columns([1, 2.6], vertical_alignment="center")
    clicked = action_col.button(
        "开始下载", type="primary", use_container_width=True, disabled=disabled
    )
    hint_col.caption(
        f"预计体积 {format_filesize(quality.approx_size)} · "
        f"保存到 {config.get_download_dir()}"
    )
    return clicked


# --------------------------------------------------------------------------- #
# 进度与结果
# --------------------------------------------------------------------------- #
def progress_view(job: DownloadJob) -> str | None:
    """渲染进度/结果，用户点击需要重置时返回 ``"reset"``。"""
    progress = job.snapshot()
    if progress.is_active:
        _live_progress(job)
        return None
    return _final_progress(job, progress)


@st.fragment(run_every=config.PROGRESS_POLL_INTERVAL)
def _live_progress(job: DownloadJob) -> None:
    """下载中：每 0.4s 自动刷新，可随时取消。"""
    progress = job.snapshot()

    st.progress(
        min(max(progress.percent / 100.0, 0.0), 1.0),
        text=f"{progress.percent:.1f}% · {progress.phase}",
    )
    total_text = format_filesize(progress.total_bytes) if progress.total_bytes else "未知"
    st.caption(f"阶段：{progress.phase} · 共 {total_text}")

    col_done, col_speed, col_eta = st.columns(3)
    col_done.metric("已下载", format_filesize(progress.downloaded_bytes))
    col_speed.metric("速度", format_speed(progress.speed))
    col_eta.metric("剩余", format_eta(progress.eta))

    cancel_col, _ = st.columns([1, 3])
    if cancel_col.button("取消下载", use_container_width=True):
        job.cancel()

    if not progress.is_active:
        st.rerun()


def _final_progress(job: DownloadJob, progress: DownloadProgress) -> str | None:
    if progress.is_finished and progress.output_path:
        path = Path(progress.output_path)
        st.success("下载完成")
        st.markdown("**文件完整路径**")
        st.code(str(path), language=None)

        details: list[str] = []
        try:
            details.append(f"大小 {format_filesize(path.stat().st_size)}")
        except OSError:
            pass
        if job.info.duration_text:
            details.append(f"时长 {job.info.duration_text}")
        if job.quality is not None:
            details.append(job.quality.label)
        if details:
            st.caption(" · ".join(details))

        if st.button("继续下载其他清晰度"):
            return "reset"
        return None

    if progress.status == "cancelled":
        st.warning("下载已取消，未生成文件。")
        if st.button("知道了"):
            return "reset"
        return None

    st.error(progress.message or "下载失败")
    if isinstance(job.error, DownloadFailed) and job.error.hint:
        st.info(job.error.hint)
    elif isinstance(job.error, DownloadCancelledError):
        st.caption("已取消。")
    if st.button("重试"):
        return "reset"
    return None
