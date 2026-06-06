"""Tests for zh-short citability exemption (U8 R11).

zh-short payloads receive ONLY zero-cost levers: freshness + entity_claim.
They must NOT receive FAQ or stats blocks. The char envelope must not be
extended beyond reasonable bounds (zero-cost means no heavy injection).
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest

from backlink_publisher.cli.plan_backlinks._citability import apply_zero_cost_levers
from backlink_publisher.cli.plan_backlinks._engine import _apply_zero_cost_and_emit


# ── _apply_zero_cost_and_emit ─────────────────────────────────────────────────


def _make_zh_short_payload(
    main_domain: str = "https://example.com",
    content: str = "短文内容示例。",
) -> dict:
    """Minimal zh-short-like payload."""
    return {
        "id": "test123",
        "platform": "medium",
        "language": "zh-CN",
        "main_domain": main_domain + "/",
        "content_markdown": content,
        "links": [],
    }


def _make_row(main_domain: str = "https://example.com") -> dict:
    return {
        "target_url": main_domain + "/article",
        "main_domain": main_domain,
        "language": "zh-CN",
        "platform": "medium",
    }


def test_zh_short_gets_freshness_and_entity_claim():
    today = datetime.date.today().isoformat()
    payload = _make_zh_short_payload()
    row = _make_row()
    result = _apply_zero_cost_and_emit(payload, row, "zh_short")

    md = result["content_markdown"]
    assert today in md
    # entity_claim should name the domain
    assert "example.com" in md


def test_zh_short_no_faq_block():
    payload = _make_zh_short_payload()
    row = _make_row()
    result = _apply_zero_cost_and_emit(payload, row, "zh_short")

    md = result["content_markdown"]
    # FAQ markers must NOT appear
    assert "常见问题" not in md
    assert "FAQ" not in md
    assert "Frequently" not in md


def test_zh_short_no_stat_block():
    payload = _make_zh_short_payload()
    row = _make_row()
    result = _apply_zero_cost_and_emit(payload, row, "zh_short")

    md = result["content_markdown"]
    # Stat markers must NOT appear
    assert "数据参考" not in md
    assert "By the numbers" not in md
    assert "Данные" not in md


def test_zh_short_levers_are_only_zero_cost():
    payload = _make_zh_short_payload()
    row = _make_row()
    result = _apply_zero_cost_and_emit(payload, row, "zh_short")

    levers = result["_citability_levers"]
    assert set(levers) == {"entity_claim", "freshness"}
    assert len(levers) == 2


def test_zh_short_content_length_growth_is_small():
    """Zero-cost levers add only two short sentences — not a bulky injection."""
    original_content = "短文内容示例。" * 5  # ~35 chars
    payload = _make_zh_short_payload(content=original_content)
    row = _make_row()
    result = _apply_zero_cost_and_emit(payload, row, "zh_short")

    added = len(result["content_markdown"]) - len(original_content)
    # Two levers (entity claim ~30 chars + freshness ~25 chars + newlines)
    # should add well under 200 chars
    assert added < 200, f"Zero-cost levers added too much: {added} chars"


def test_zh_short_apply_zero_cost_levers_directly():
    """apply_zero_cost_levers returns (augmented, [entity_claim, freshness])."""
    body = "Some content."
    augmented, levers = apply_zero_cost_levers(body, "example.com", language="zh-CN")
    assert "entity_claim" in levers
    assert "freshness" in levers
    assert len(levers) == 2
    # Chinese freshness marker
    assert "更新于" in augmented
    # Entity claim
    assert "example.com" in augmented


def test_no_warn_emitted_for_zh_short():
    """Zero-cost levers never trigger the stat-degradation WARN."""
    with patch(
        "backlink_publisher.cli.plan_backlinks._citability.plan_logger"
    ) as mock_logger:
        apply_zero_cost_levers("body", "example.com", language="zh-CN")
    mock_logger.warn.assert_not_called()
