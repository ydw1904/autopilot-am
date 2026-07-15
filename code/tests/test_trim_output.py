"""mcp_server._trim_output — keeps subprocess output under the MCP payload cap.

Importing mcp_server pulls in cdp, which only strips env vars at import time,
so this needs no Chrome.
"""

import pytest

from mcp_server import _trim_output


def test_under_limit_is_returned_unchanged():
    text = "x" * 100
    out, truncated = _trim_output(text, limit=200)

    assert out == text
    assert truncated is False


def test_exactly_at_limit_is_returned_unchanged():
    text = "x" * 200
    out, truncated = _trim_output(text, limit=200)

    assert out == text
    assert truncated is False


def test_over_limit_keeps_head_and_tail_and_marks_truncation():
    head = "H" * 100
    middle = "M" * 500
    tail = "T" * 100
    out, truncated = _trim_output(head + middle + tail, limit=200)

    assert truncated is True
    assert out.startswith("H" * 100)   # limit//2 chars of head
    assert out.endswith("T" * 100)     # limit//2 chars of tail
    assert "MMM" not in out            # the middle is what got dropped
    assert "[truncated 500 chars]" in out


def test_truncation_marker_reports_dropped_char_count():
    text = "a" * 1000
    out, truncated = _trim_output(text, limit=200)

    assert truncated is True
    # 1000 total - 100 head - 100 tail = 800 dropped
    assert "[truncated 800 chars]" in out


@pytest.mark.parametrize("value", ["", None])
def test_empty_and_none_are_handled(value):
    out, truncated = _trim_output(value, limit=200)

    assert out == ""
    assert truncated is False
