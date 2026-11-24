"""desktop_entry.py 的单元测试：窗口模式失败记忆逻辑。

不触碰 GUI：只测"是否尝试窗口模式"的判定与 settings.json 标记的读写。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import desktop_entry  # noqa: E402
import paths  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_flag() -> None:
    paths.write_settings(webview_unavailable=None)
    yield
    paths.write_settings(webview_unavailable=None)


def test_tries_webview_by_default() -> None:
    assert desktop_entry._should_try_webview("auto") is True


def test_skips_webview_after_failure_is_remembered() -> None:
    desktop_entry._mark_webview_unavailable()

    assert paths.read_settings().get("webview_unavailable") is True
    assert desktop_entry._should_try_webview("auto") is False


def test_explicit_flags_override_the_memory() -> None:
    desktop_entry._mark_webview_unavailable()

    # --webview 允许用户手动重试，--browser 永远不试
    assert desktop_entry._should_try_webview("webview") is True
    assert desktop_entry._should_try_webview("browser") is False


def test_flag_is_cleared_when_webview_works() -> None:
    desktop_entry._mark_webview_unavailable()
    assert "webview_unavailable" in paths.read_settings()

    desktop_entry._clear_webview_unavailable()

    assert "webview_unavailable" not in paths.read_settings()
    assert desktop_entry._should_try_webview("auto") is True


def test_import_check_passes_in_source_mode() -> None:
    """打包后必跑的自检：确认 ui/core 等模块真的在产物里。

    源码模式下这些模块本来就在，这里主要防止自检函数本身写错
    （例如模块名拼错导致误报）。
    """
    assert desktop_entry._run_import_check() == 0


def test_survives_broken_settings_file(monkeypatch: pytest.MonkeyPatch, tmp_workdir: Path) -> None:
    broken = tmp_workdir / "settings.json"
    broken.write_text("not json at all", encoding="utf-8")
    monkeypatch.setenv(paths.SETTINGS_FILE_ENV, str(broken))
    paths.reload_settings()

    # 读不出来时按"可以试一下"处理，不能因为配置坏了就永久跳过窗口模式
    assert desktop_entry._should_try_webview("auto") is True
