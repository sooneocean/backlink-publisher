"""Tests for citability lever injection in long-form articles (U8).

Covers:
- Happy path: long-form payload contains FAQ block, >=1 entity-naming claim,
  a freshness date, and a stat paragraph.
- apply_long_form_levers returns (augmented_body, applied_levers) with all 4
  lever names recorded.
- Lever names are deterministic (stat_numeric vs stat_assertion tracked correctly).
- apply_zero_cost_levers returns only freshness + entity_claim.
- _generate_payload sets _citability_levers on the returned payload (before
  plan_rows moves it into metadata).
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest

from backlink_publisher.cli.plan_backlinks._citability import (
    apply_long_form_levers,
    apply_zero_cost_levers,
    build_entity_claim,
    build_faq_block,
    build_freshness_line,
    build_stat_paragraph,
)


# ── build_freshness_line ──────────────────────────────────────────────────────


def test_freshness_line_contains_today():
    today = datetime.date.today().isoformat()
    line = build_freshness_line(language="en")
    assert today in line


def test_freshness_line_zh():
    line = build_freshness_line(language="zh-CN")
    assert "更新于" in line


def test_freshness_line_ko():
    line = build_freshness_line(language="ko")
    assert "업데이트" in line


def test_freshness_line_ru():
    line = build_freshness_line(language="ru")
    assert "актуален" in line


# ── build_entity_claim ────────────────────────────────────────────────────────


def test_entity_claim_names_domain_en():
    claim = build_entity_claim("example.com", language="en")
    assert "example.com" in claim


def test_entity_claim_names_domain_zh():
    claim = build_entity_claim("example.com", language="zh-CN")
    assert "example.com" in claim


def test_entity_claim_is_self_contained():
    claim = build_entity_claim("mysite.io", language="en")
    # Should be a sentence that stands alone
    assert "mysite.io" in claim
    assert len(claim) > 20


# ── build_faq_block ───────────────────────────────────────────────────────────


def test_faq_block_uses_topic():
    block = build_faq_block("example.com", {"topic": "anime streaming"}, language="en")
    assert "anime streaming" in block
    assert "example.com" in block


def test_faq_block_falls_back_to_seed_keywords():
    block = build_faq_block(
        "example.com", {"seed_keywords": ["manga", "comics"]}, language="en"
    )
    assert "manga" in block


def test_faq_block_falls_back_to_domain():
    block = build_faq_block("example.com", {}, language="en")
    assert "example.com" in block
    assert "FAQ" in block or "Frequently" in block


def test_faq_block_zh():
    block = build_faq_block("example.com", {"topic": "动漫"}, language="zh-CN")
    assert "常见问题" in block
    assert "动漫" in block


# ── build_stat_paragraph ──────────────────────────────────────────────────────


def test_stat_numeric_when_data_source_present():
    row = {"data_source": "Statista 2024", "stat_claim": "a top-tier platform"}
    text, is_numeric = build_stat_paragraph("example.com", row, language="en")
    assert is_numeric is True
    assert "Statista 2024" in text
    assert "example.com" in text


def test_stat_no_number_when_no_data_source(capfd):
    """No data_source → non-numeric assertion, no numeric stat, returns is_numeric=False."""
    row = {}
    text, is_numeric = build_stat_paragraph("example.com", row, language="en")
    assert is_numeric is False
    # Must not contain any digits that look like fabricated statistics
    # (assertion text may have none, but we verify no numeric patterns like "3×")
    assert "%" not in text
    assert "×" not in text


def test_stat_empty_data_source_treated_as_absent():
    row = {"data_source": "   "}
    _, is_numeric = build_stat_paragraph("example.com", row, language="en")
    assert is_numeric is False


def test_stat_numeric_zh():
    row = {"data_source": "中国网络视听协会"}
    text, is_numeric = build_stat_paragraph("example.com", row, language="zh-CN")
    assert is_numeric is True
    assert "中国网络视听协会" in text


# ── apply_long_form_levers ────────────────────────────────────────────────────


def test_long_form_levers_all_four_applied():
    body = "Initial body text."
    row = {"topic": "streaming", "data_source": "Alexa 2024"}
    augmented, levers = apply_long_form_levers(
        body, "example.com", row, language="en"
    )
    assert set(levers) >= {"stat_numeric", "faq_block", "entity_claim", "freshness"}
    assert len(levers) == 4


def test_long_form_levers_stat_assertion_recorded_without_data_source():
    body = "Body."
    row = {}
    _, levers = apply_long_form_levers(body, "example.com", row, language="en")
    assert "stat_assertion" in levers
    assert "stat_numeric" not in levers
    assert "faq_block" in levers
    assert "entity_claim" in levers
    assert "freshness" in levers


def test_long_form_levers_body_contains_faq():
    body = "Body."
    row = {"topic": "comics"}
    augmented, _ = apply_long_form_levers(body, "example.com", row, language="en")
    assert "FAQ" in augmented or "Frequently" in augmented


def test_long_form_levers_body_contains_entity_name():
    body = "Body."
    row = {}
    augmented, _ = apply_long_form_levers(body, "mysite.io", row, language="en")
    # entity_claim names the domain
    assert "mysite.io" in augmented


def test_long_form_levers_body_contains_freshness_date():
    today = datetime.date.today().isoformat()
    body = "Body."
    row = {}
    augmented, _ = apply_long_form_levers(body, "example.com", row, language="en")
    assert today in augmented


# ── apply_zero_cost_levers ────────────────────────────────────────────────────


def test_zero_cost_levers_returns_only_two():
    body = "Short body."
    augmented, levers = apply_zero_cost_levers(body, "example.com", language="en")
    assert set(levers) == {"entity_claim", "freshness"}
    assert len(levers) == 2


def test_zero_cost_levers_no_faq_no_stat():
    body = "Short body."
    augmented, levers = apply_zero_cost_levers(body, "example.com", language="en")
    assert "faq_block" not in levers
    assert "stat_numeric" not in levers
    assert "stat_assertion" not in levers
    assert "FAQ" not in augmented
    assert "Frequently" not in augmented


def test_zero_cost_levers_body_contains_domain():
    body = "Short."
    augmented, _ = apply_zero_cost_levers(body, "mysite.io", language="en")
    assert "mysite.io" in augmented


# ── _generate_payload sets _citability_levers ─────────────────────────────────


def test_generate_payload_includes_citability_levers():
    """_generate_payload should set _citability_levers with 4 entries."""
    from backlink_publisher.cli.plan_backlinks._payload import _generate_payload

    row = {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "medium",
        "url_mode": "A",
        "publish_mode": "draft",
        "topic": "test",
    }
    # fetch_verify_enabled=False skips the lazy import of verify_url_has_content
    payload = _generate_payload(row, fetch_verify_enabled=False)

    assert "_citability_levers" in payload
    levers = payload["_citability_levers"]
    assert isinstance(levers, list)
    assert len(levers) == 4
    assert "freshness" in levers
    assert "faq_block" in levers
    assert "entity_claim" in levers


def test_generate_payload_content_has_faq_and_freshness():
    """Content markdown should contain the FAQ block and freshness line."""
    from backlink_publisher.cli.plan_backlinks._payload import _generate_payload

    today = datetime.date.today().isoformat()
    row = {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "medium",
        "url_mode": "A",
        "publish_mode": "draft",
        "topic": "streaming",
    }
    # fetch_verify_enabled=False skips the URL health check
    payload = _generate_payload(row, fetch_verify_enabled=False)
    md = payload["content_markdown"]
    assert today in md
    assert "FAQ" in md or "Frequently" in md
    assert "example.com" in md
