"""界面样式：一段作用域内的 CSS，让 Streamlit 看起来像正经产品。

原则
- 只做视觉：不改结构、不藏功能性元素，避免版本升级后"功能消失"；
- 选择器用 Streamlit 的稳定 data-testid（匹配不上时只是不生效，不会报错）；
- 浅色卡片风：白底、浅灰描边、红色主色、圆角 16px、克制的阴影。
"""

from __future__ import annotations

import streamlit as st

_CSS = """
<style>
:root {
  --ytdl-accent: #e11d48;
  --ytdl-accent-soft: #fff1f3;
  --ytdl-border: #e6e8ec;
  --ytdl-muted: #6b7280;
  --ytdl-ink: #111827;
  --ytdl-radius: 16px;
}

/* ---------- 页面骨架 ---------- */
.block-container {
  padding-top: 2.4rem;
  padding-bottom: 3rem;
  max-width: 1120px;
}
footer { visibility: hidden; }
#MainMenu { visibility: hidden; }
[data-testid="stHeader"] { background: transparent; }

/* ---------- 顶部 Hero ---------- */
.ytdl-hero {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 22px 26px;
  margin-bottom: 18px;
  border: 1px solid var(--ytdl-border);
  border-radius: 20px;
  background:
    radial-gradient(900px 200px at 0% 0%, #fff1f3 0%, rgba(255,255,255,0) 60%),
    linear-gradient(135deg, #ffffff 0%, #fbfbfd 100%);
  box-shadow: 0 1px 2px rgba(16, 24, 40, .04), 0 18px 40px -32px rgba(16, 24, 40, .45);
}
.ytdl-hero-icon {
  font-size: 2.2rem;
  line-height: 1;
  width: 56px;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 16px;
  background: #fff;
  border: 1px solid var(--ytdl-border);
  box-shadow: 0 6px 16px -12px rgba(225, 29, 72, .6);
}
.ytdl-hero-title {
  margin: 0;
  font-size: 1.85rem;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--ytdl-ink);
  line-height: 1.2;
}
.ytdl-hero-sub {
  margin: 6px 0 0;
  color: var(--ytdl-muted);
  font-size: 0.95rem;
}

/* ---------- 卡片（st.container(border=True)） ---------- */
[data-testid="stVerticalBlockBorderWrapper"] {
  border-radius: var(--ytdl-radius);
  border: 1px solid var(--ytdl-border);
  background: #ffffff;
  box-shadow: 0 1px 2px rgba(16, 24, 40, .03), 0 12px 28px -24px rgba(16, 24, 40, .5);
}
[data-testid="stVerticalBlockBorderWrapper"] > div {
  padding: 1.05rem 1.15rem;
}

/* ---------- 视频信息 ---------- */
[data-testid="stImage"] img {
  border-radius: 14px;
  border: 1px solid var(--ytdl-border);
  box-shadow: 0 10px 24px -20px rgba(16, 24, 40, .7);
}
.ytdl-video-title {
  font-size: 1.18rem;
  font-weight: 650;
  line-height: 1.45;
  color: var(--ytdl-ink);
  margin: 2px 0 12px;
  letter-spacing: -0.01em;
}
.ytdl-chip {
  display: inline-block;
  padding: 3px 11px;
  margin: 0 6px 8px 0;
  border-radius: 999px;
  background: #f4f5f7;
  border: 1px solid #eceef2;
  color: #374151;
  font-size: 0.8rem;
  white-space: nowrap;
}

/* ---------- 清晰度单选项 ---------- */
.ytdl-section-title {
  font-size: 0.82rem;
  font-weight: 650;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--ytdl-muted);
  margin-bottom: 10px;
}
.stRadio [role="radiogroup"] { gap: 8px; }
.stRadio label[data-baseweb="radio"] {
  border: 1px solid var(--ytdl-border);
  border-radius: 12px;
  padding: 10px 14px;
  margin: 0 0 8px 0;
  background: #fff;
  transition: border-color .15s ease, box-shadow .15s ease, background .15s ease;
}
.stRadio label[data-baseweb="radio"]:hover {
  border-color: #f2a0b1;
  background: #fffafb;
}
.stRadio label[data-baseweb="radio"]:has(input:checked) {
  border-color: var(--ytdl-accent);
  background: var(--ytdl-accent-soft);
  box-shadow: 0 0 0 3px rgba(225, 29, 72, .08);
}
.stRadio label[data-baseweb="radio"] p {
  font-weight: 600;
  font-size: 0.94rem;
}
.stRadio label[data-baseweb="radio"] div[data-testid="stMarkdownContainer"] p {
  color: var(--ytdl-ink);
}
/* 选项下方的说明文字 */
.stRadio label[data-baseweb="radio"] small,
.stRadio label[data-baseweb="radio"] span[data-testid="stCaption"] {
  color: var(--ytdl-muted);
}

/* ---------- 按钮 ---------- */
.stButton > button,
[data-testid="stFormSubmitButton"] > button,
.stLinkButton > a {
  border-radius: 10px;
  font-weight: 600;
  padding: 0.5rem 1rem;
  transition: transform .08s ease, box-shadow .15s ease, background .15s ease;
}
.stButton > button:hover,
[data-testid="stFormSubmitButton"] > button:hover {
  transform: translateY(-1px);
}
.stButton > button[kind="primary"],
[data-testid="stFormSubmitButton"] > button[kind="primary"] {
  background: var(--ytdl-accent);
  border: 1px solid var(--ytdl-accent);
  box-shadow: 0 8px 18px -12px rgba(225, 29, 72, .9);
}
.stButton > button[kind="primary"]:hover,
[data-testid="stFormSubmitButton"] > button[kind="primary"]:hover {
  background: #be123c;
  border-color: #be123c;
}

/* ---------- 进度 ---------- */
[data-testid="stProgress"] div[role="progressbar"] > div > div {
  background: linear-gradient(90deg, #f43f5e, #e11d48);
  border-radius: 999px;
}
[data-testid="stProgress"] div[role="progressbar"] {
  border-radius: 999px;
  background: #f3f4f6;
  overflow: hidden;
}
[data-testid="stMetricValue"] {
  font-size: 1.3rem;
  font-weight: 650;
  color: var(--ytdl-ink);
}
[data-testid="stMetricLabel"] p {
  font-size: 0.78rem;
  color: var(--ytdl-muted);
  letter-spacing: .02em;
}

/* ---------- 路径展示 ---------- */
[data-testid="stCodeBlock"] pre {
  border-radius: 12px;
  border: 1px solid var(--ytdl-border);
  background: #f8fafc !important;
  font-size: 0.86rem;
  word-break: break-all;
  white-space: pre-wrap;
}
[data-testid="stCodeBlock"] code {
  color: #0f172a !important;
  font-family: ui-monospace, SFMono-Regular, "Cascadia Code", Consolas, monospace;
}

/* ---------- 侧边栏 ---------- */
[data-testid="stSidebar"] {
  background: #fbfbfd;
  border-right: 1px solid var(--ytdl-border);
}
[data-testid="stSidebar"] [data-testid="stCodeBlock"] pre {
  font-size: 0.78rem;
}

/* ---------- 提示条 ---------- */
[data-testid="stAlert"] {
  border-radius: 12px;
  border: 1px solid var(--ytdl-border);
}

/* ---------- 窄屏适配 ---------- */
@media (max-width: 820px) {
  .block-container { padding-top: 1.4rem; }
  .ytdl-hero { flex-direction: column; align-items: flex-start; gap: 12px; padding: 18px; }
  .ytdl-hero-title { font-size: 1.45rem; }
  .ytdl-hero-icon { width: 46px; height: 46px; font-size: 1.7rem; }
}
</style>
"""


def apply_theme() -> None:
    """注入自定义样式（幂等，可在每次 rerun 调用）。"""
    st.markdown(_CSS, unsafe_allow_html=True)
