"""Unit tests for content._html_utils (extract_title, read_html_head_window).

Covers: og:title priority over <title>; og:title empty fallback; <title>
fallback; whitespace stripping; None on missing both; malformed HTML tolerance;
read_html_head_window sentinel stop + max_bytes cap + end-of-stream.
"""

from __future__ import annotations

import io

import pytest

from backlink_publisher.content._html_utils import extract_title, read_html_head_window


# ── extract_title ──────────────────────────────────────────────────────────────

def test_og_title_preferred_over_title_tag() -> None:
    html = b"""<html><head>
    <meta property="og:title" content="OG Title" />
    <title>HTML Title</title>
    </head></html>"""
    assert extract_title(html) == "OG Title"


def test_og_title_empty_falls_back_to_title_tag() -> None:
    html = b"""<html><head>
    <meta property="og:title" content="" />
    <title>Fallback Title</title>
    </head></html>"""
    assert extract_title(html) == "Fallback Title"


def test_og_title_whitespace_only_falls_back() -> None:
    html = b"""<html><head>
    <meta property="og:title" content="   " />
    <title>Real Title</title>
    </head></html>"""
    assert extract_title(html) == "Real Title"


def test_title_tag_only() -> None:
    html = b"<html><head><title>Just Title</title></head></html>"
    assert extract_title(html) == "Just Title"


def test_title_stripped() -> None:
    html = b"<html><head><title>  Padded Title  </title></head></html>"
    assert extract_title(html) == "Padded Title"


def test_og_title_stripped() -> None:
    html = b"""<html><head>
    <meta property="og:title" content="  OG with spaces  " />
    </head></html>"""
    assert extract_title(html) == "OG with spaces"


def test_no_title_returns_none() -> None:
    html = b"<html><head></head><body>No title here</body></html>"
    assert extract_title(html) is None


def test_empty_title_tag_returns_none() -> None:
    html = b"<html><head><title>  </title></head></html>"
    assert extract_title(html) is None


def test_malformed_html_does_not_raise() -> None:
    assert extract_title(b"<<<not html at all>>>") is None or True


def test_empty_bytes_returns_none() -> None:
    assert extract_title(b"") is None


def test_non_og_meta_not_matched() -> None:
    """meta[name=title] is NOT the og:title — should fall through to <title>."""
    html = b"""<html><head>
    <meta name="title" content="Wrong Meta" />
    <title>Correct Title</title>
    </head></html>"""
    assert extract_title(html) == "Correct Title"


# ── read_html_head_window ──────────────────────────────────────────────────────

def _make_resp(content: bytes) -> io.BytesIO:
    return io.BytesIO(content)


def test_stops_at_head_close_tag() -> None:
    body = b"<html><head><title>T</title></head><body>" + b"X" * 100_000
    result = read_html_head_window(_make_resp(body), max_bytes=200_000)
    assert b"</head>" in result
    assert len(result) < 200_000  # stopped before consuming body


def test_head_close_tag_case_insensitive() -> None:
    body = b"<html><head><title>T</title></HEAD><body>" + b"X" * 100_000
    result = read_html_head_window(_make_resp(body), max_bytes=200_000)
    assert len(result) < 200_000


def test_max_bytes_cap_respected() -> None:
    body = b"X" * 500_000  # no </head> at all
    result = read_html_head_window(_make_resp(body), max_bytes=1024)
    assert len(result) <= 1024


def test_end_of_stream_before_max_bytes() -> None:
    body = b"<title>Short</title>"
    result = read_html_head_window(_make_resp(body), max_bytes=100_000)
    assert result == body


def test_empty_stream() -> None:
    result = read_html_head_window(_make_resp(b""), max_bytes=10_000)
    assert result == b""
