"""YouTube 视频下载器 —— Streamlit 入口。

职责：状态编排与组件调用，不含解析/下载细节。
- ``core/extractor.py`` 解析链接
- ``core/downloader.py`` 下载 + FFmpeg 合并
- ``ui/components.py`` 渲染
- ``ui/styles.py`` 视觉
"""

from __future__ import annotations

import streamlit as st

import config
from core.downloader import DownloadJob
from core.extractor import ExtractionError, VideoInfo, extract_video_info
from ui.components import (
    download_row,
    page_header,
    progress_view,
    quality_selector,
    sidebar_panel,
    video_card,
)
from ui.styles import apply_theme

st.set_page_config(
    page_title=config.APP_TITLE,
    page_icon=config.PAGE_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)

URL_PLACEHOLDER = "https://www.youtube.com/watch?v=…"


# --------------------------------------------------------------------------- #
# 会话状态
# --------------------------------------------------------------------------- #
def _init_state() -> None:
    st.session_state.setdefault("parsed", None)
    st.session_state.setdefault("parse_error", None)
    st.session_state.setdefault("job", None)


def _ffmpeg_ok() -> bool:
    return config.detect_ffmpeg() is not None


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def _render_input() -> None:
    with st.form("parse_form", border=False):
        input_col, button_col = st.columns([5, 1], vertical_alignment="bottom")
        url = input_col.text_input(
            "YouTube 链接",
            placeholder=URL_PLACEHOLDER,
            key="url_input",
            help="支持 youtube.com/watch、youtu.be、/shorts/ 以及 11 位视频 ID",
        )
        submitted = button_col.form_submit_button(
            "解析", type="primary", use_container_width=True
        )

    if submitted:
        _handle_parse(url)


def _handle_parse(url: str) -> None:
    st.session_state.job = None
    st.session_state.parsed = None
    st.session_state.parse_error = None

    if not config.is_supported_url(url):
        st.session_state.parse_error = (
            "链接无效：请粘贴 YouTube 视频链接。",
            "例如 https://www.youtube.com/watch?v=… 或 https://youtu.be/…",
        )
        return

    try:
        with st.spinner("正在解析视频信息…"):
            st.session_state.parsed = extract_video_info(url)
    except ExtractionError as exc:
        st.session_state.parse_error = (str(exc), exc.hint)
    except Exception as exc:  # 兜底：任何意外都不应让页面崩掉
        st.session_state.parse_error = (f"解析失败：{exc}", None)


def _render_parse_error() -> None:
    error = st.session_state.get("parse_error")
    if not error:
        return
    message, hint = error
    st.error(message)
    if hint:
        st.info(hint)


# --------------------------------------------------------------------------- #
# 下载区
# --------------------------------------------------------------------------- #
def _render_download(info: VideoInfo) -> None:
    with st.container(border=True):
        selected = quality_selector(info)

        job: DownloadJob | None = st.session_state.job
        active = job is not None and job.snapshot().is_active

        if download_row(selected, disabled=active or not _ffmpeg_ok()):
            st.session_state.job = DownloadJob(info, selected)
            st.session_state.job.start()
            st.rerun()

        job = st.session_state.job
        if job is not None:
            st.divider()
            if progress_view(job) == "reset":
                st.session_state.job = None
                st.rerun()


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> None:
    _init_state()
    apply_theme()
    sidebar_panel()

    page_header()

    if not _ffmpeg_ok():
        st.warning("未检测到 FFmpeg：可以解析视频，但无法合并下载。请看左侧安装指引。")

    _render_input()
    _render_parse_error()

    info: VideoInfo | None = st.session_state.get("parsed")
    if info is not None:
        video_card(info)
        _render_download(info)
    elif not st.session_state.get("parse_error"):
        st.caption("粘贴链接后点击「解析」，即可查看清晰度列表。")


main()
