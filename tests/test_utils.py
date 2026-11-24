"""core/utils.py 的单元测试：格式化边界值。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.utils import (  # noqa: E402
    format_count,
    format_duration,
    format_eta,
    format_filesize,
    format_speed,
    sanitize_filename,
    truncate_text,
)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (None, "--:--"),
        (0, "0:00"),
        (59, "0:59"),
        (60, "1:00"),
        (635, "10:35"),
        (3600, "1:00:00"),
        (3725, "1:02:05"),
        (-5, "--:--"),
        ("abc", "--:--"),
        (59.6, "1:00"),
    ],
)
def test_format_duration(seconds: float | int | str | None, expected: str) -> None:
    assert format_duration(seconds) == expected


@pytest.mark.parametrize(
    ("num_bytes", "expected"),
    [
        (None, "未知"),
        (0, "0 B"),
        (999, "999 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (2000, "2.0 KB"),
        (1024 * 1024 * 215.3, "215 MB"),
        (1024 ** 3 * 1.2, "1.2 GB"),
        (-1, "未知"),
        (float("inf"), "未知"),
        ("x", "未知"),
    ],
)
def test_format_filesize(num_bytes: float | str | None, expected: str) -> None:
    assert format_filesize(num_bytes) == expected


@pytest.mark.parametrize(
    ("speed", "expected"),
    [(None, "—"), (0, "—"), (-3, "—"), (3_500_000, "3.3 MB/s")],
)
def test_format_speed(speed: float | None, expected: str) -> None:
    assert format_speed(speed) == expected


def test_format_eta() -> None:
    assert format_eta(None) == "未知"
    assert format_eta(42) == "0:42"
    assert format_eta(3725) == "1:02:05"


@pytest.mark.parametrize(
    ("count", "expected"),
    [(None, "—"), (999, "999"), (9999, "9999"), (10000, "1 万"),
     (12345, "1.2 万"), (200_000_000, "2 亿"), (123_456_789, "1.2 亿")],
)
def test_format_count(count: int | None, expected: str) -> None:
    assert format_count(count) == expected


def test_sanitize_filename_removes_illegal_chars() -> None:
    assert sanitize_filename('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert sanitize_filename("  多余   空格  ") == "多余 空格"
    assert sanitize_filename("...尾随点...") == "尾随点"
    assert sanitize_filename("") == "video"
    assert sanitize_filename("   ") == "video"
    assert sanitize_filename("line\nbreak") == "line_break"


def test_sanitize_filename_truncates() -> None:
    result = sanitize_filename("字" * 300, max_length=20)
    assert len(result) == 20


def test_truncate_text() -> None:
    assert truncate_text("abc", 10) == "abc"
    assert truncate_text("abcdefghij", 5) == "abcd…"
    assert truncate_text("", 5) == ""
