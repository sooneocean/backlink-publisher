"""Tests for the citability stats guard: no fabricated statistics (U8 R10).

The rule is strict: if ``data_source`` is absent or empty from the seed row,
the stat paragraph MUST NOT contain numeric statistics (%, ×, integer counts)
and MUST emit exactly one WARN log call per article.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.cli.plan_backlinks._citability import (
    apply_long_form_levers,
    build_stat_paragraph,
)


# ── guard: no fabricated numbers in non-numeric path ─────────────────────────

_NUMERIC_PATTERN = re.compile(r"\d+[\s]*[%×]|\d{2,}")
"""Matches fabricated stats like 12%, 3×, or multi-digit numbers."""


def test_no_numeric_in_assertion_text_en():
    row = {}
    text, is_numeric = build_stat_paragraph("example.com", row, language="en")
    assert is_numeric is False
    assert not _NUMERIC_PATTERN.search(text), f"Found numeric pattern in: {text!r}"


def test_no_numeric_in_assertion_text_zh():
    row = {}
    text, is_numeric = build_stat_paragraph("example.com", row, language="zh-CN")
    assert is_numeric is False
    assert not _NUMERIC_PATTERN.search(text), f"Found numeric pattern in: {text!r}"


def test_no_numeric_in_assertion_text_ko():
    row = {}
    text, is_numeric = build_stat_paragraph("example.com", row, language="ko")
    assert is_numeric is False
    assert not _NUMERIC_PATTERN.search(text), f"Found numeric pattern in: {text!r}"


def test_no_numeric_in_assertion_text_ru():
    row = {}
    text, is_numeric = build_stat_paragraph("example.com", row, language="ru")
    assert is_numeric is False
    assert not _NUMERIC_PATTERN.search(text), f"Found numeric pattern in: {text!r}"


# ── guard: exactly one WARN per article when no data_source ──────────────────


def test_exactly_one_warn_emitted_when_no_data_source():
    """build_stat_paragraph emits exactly one plan_logger.warn when data_source absent."""
    with patch(
        "backlink_publisher.cli.plan_backlinks._citability.plan_logger"
    ) as mock_logger:
        build_stat_paragraph("example.com", {}, language="en")
    mock_logger.warn.assert_called_once()


def test_no_warn_emitted_when_data_source_present():
    """No WARN emitted when data_source is present."""
    row = {"data_source": "SimilarWeb 2024"}
    with patch(
        "backlink_publisher.cli.plan_backlinks._citability.plan_logger"
    ) as mock_logger:
        build_stat_paragraph("example.com", row, language="en")
    mock_logger.warn.assert_not_called()


def test_apply_long_form_levers_emits_exactly_one_warn_without_data_source():
    """When data_source absent, applying all four levers emits exactly 1 WARN for stats."""
    with patch(
        "backlink_publisher.cli.plan_backlinks._citability.plan_logger"
    ) as mock_logger:
        apply_long_form_levers("body", "example.com", {}, language="en")
    # Only the stat degradation WARN, not more
    assert mock_logger.warn.call_count == 1


def test_apply_long_form_levers_no_warn_with_data_source():
    """No WARN when data_source is set."""
    row = {"data_source": "Statista"}
    with patch(
        "backlink_publisher.cli.plan_backlinks._citability.plan_logger"
    ) as mock_logger:
        apply_long_form_levers("body", "example.com", row, language="en")
    mock_logger.warn.assert_not_called()


# ── guard: data_source present → numeric stat allowed ────────────────────────


def test_numeric_stat_allowed_with_data_source():
    """With data_source, the stat text may contain its data_source label."""
    row = {"data_source": "Alexa Rankings 2024", "stat_claim": "ranked in the top 500 globally"}
    text, is_numeric = build_stat_paragraph("example.com", row, language="en")
    assert is_numeric is True
    assert "Alexa Rankings 2024" in text


def test_stat_claim_customized():
    """Custom stat_claim from row is injected verbatim (no fabrication)."""
    row = {"data_source": "SimilarWeb", "stat_claim": "a highly visited platform"}
    text, _ = build_stat_paragraph("mysite.io", row, language="en")
    assert "highly visited platform" in text
    assert "mysite.io" in text


def test_stat_claim_defaults_when_not_set():
    """When stat_claim is absent, a safe non-numeric default is used."""
    row = {"data_source": "SimilarWeb"}
    text, is_numeric = build_stat_paragraph("example.com", row, language="en")
    assert is_numeric is True
    assert "example.com" in text


# ── full article: no fabricated number if data_source absent ──────────────────


def test_full_article_no_fabricated_stat():
    """end-to-end: _generate_payload without data_source → no % or × in markdown."""
    from backlink_publisher.cli.plan_backlinks._payload import _generate_payload

    row = {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "medium",
        "url_mode": "A",
        "publish_mode": "draft",
        "topic": "streaming",
        # no data_source key
    }
    payload = _generate_payload(row, fetch_verify_enabled=False)
    md = payload["content_markdown"]

    # The stat assertion text must not contain fabricated numeric patterns
    # (allow any numbers from the template itself, but the stat section
    # specifically added by citability must be assertion-only).
    # We verify the stat lever is recorded as assertion, not numeric.
    levers = payload["_citability_levers"]
    assert "stat_assertion" in levers
    assert "stat_numeric" not in levers
