"""测试公用夹具。

- 不使用 pytest 内置 ``tmp_path``：它对目录显式设置 0o700 权限，
  在受限环境（例如本机的 DSH 工作区沙箱）下新目录不继承可写 ACL，
  会抛 ``PermissionError``。这里改为在 ``tests/.tmp`` 下用默认权限建目录。
- 自动隔离用户状态（settings.json、相关环境变量、下载目录缓存），
  避免用例之间互相污染，也不污染真实的项目目录。
"""

from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path
from typing import Iterator

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402  （必须在 sys.path 调整之后导入）

_TMP_ROOT = Path(__file__).resolve().parent / ".tmp"

_USER_ENV_VARS = (
    "YTDL_DOWNLOAD_DIR",
    "YOUTUBE_DOWNLOAD_DIR",
    "YTDL_FFMPEG_PATH",
    "FFMPEG_PATH",
    "FFMPEG_BINARY",
    "FFMPEG_DIR",
    "YTDL_FFPROBE_PATH",
)


@pytest.fixture
def tmp_workdir() -> Iterator[Path]:
    """一次性的临时工作目录，用后删除。"""
    _TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = _TMP_ROOT / uuid.uuid4().hex
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolate_user_state(monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path) -> Iterator[None]:
    """每个用例都在独立的 settings.json / cookies 文件 + 干净环境变量下运行。"""
    monkeypatch.setenv("YTDL_SETTINGS_FILE", str(tmp_workdir / "settings.json"))
    # cookies 文件默认落在 data_dir（开发模式下就是项目根），必须隔离；
    # 否则某个用例写下的 cookies.txt 会让后续用例误判"已配置 cookies"。
    monkeypatch.setattr(config, "cookies_file_path", lambda: tmp_workdir / "cookies.txt")
    for name in _USER_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    config.reset_download_dir_cache()
    try:
        yield
    finally:
        config.reset_download_dir_cache()
