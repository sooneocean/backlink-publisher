"""report-anchors JSONL-path dofollow-tier segmentation (Plan 2026-05-25-001 Unit 3 / R3).

Verifies the tier breakdown that ``report-anchors`` appends to its
JSONL-aggregate output: per-tier article/anchor counts, the referral
high/low sub-split inside nofollow-signal, the reserved-key JSON contract,
and graceful handling of rows missing platform.
"""

from __future__ import annotations

import collections
import json

from backlink_publisher.cli._report_format import (
    _build_tier_summary,
    _json_output,
    _markdown_table,
)


def _row(platform: str | None, *, anchors: int = 1, metadata: dict | None = None) -> dict:
    links = [
        {"kind": "main_domain", "anchor": f"kw{i}"} for i in range(anchors)
    ]
    row: dict = {"main_domain": "https://a.com", "links": links}
    if platform is not None:
        row["platform"] = platform
    if metadata is not None:
        row["metadata"] = metadata
    return row


def test_tier_summary_buckets_by_registry_status() -> None:
    # blogger=dofollow, devto=nofollow-signal(high) in production registry.
    rows = [_row("blogger", anchors=2), _row("devto", anchors=3)]
    summary = _build_tier_summary(rows)
    assert summary["dofollow"] == {"articles": 1, "anchors": 2}
    assert summary["nofollow-signal"]["articles"] == 1
    assert summary["nofollow-signal"]["anchors"] == 3
    assert summary["nofollow-signal"]["referral"]["high"] == {"articles": 1, "anchors": 3}
    assert summary["nofollow-signal"]["referral"]["low"] == {"articles": 0, "anchors": 0}


def test_tier_summary_prefers_metadata_mark() -> None:
    # A row carrying the plan-backlinks metadata mark is bucketed by it
    # even without a registry lookup needed.
    rows = [
        _row(
            "anyplatform",
            anchors=1,
            metadata={"dofollow_tier": "nofollow-signal", "referral_value": "low"},
        )
    ]
    summary = _build_tier_summary(rows)
    assert summary["nofollow-signal"]["referral"]["low"]["articles"] == 1


def test_tier_summary_unknown_platform_bucket() -> None:
    rows = [_row(None, anchors=1), _row("unregistered_xyz", anchors=2)]
    summary = _build_tier_summary(rows)
    assert summary["unknown"]["articles"] == 2
    assert summary["unknown"]["anchors"] == 3


def test_json_output_adds_reserved_tier_key_without_breaking_domains() -> None:
    rows = [_row("blogger")]
    stats = {"https://a.com": {"total_articles": 1, "anchors": {"kw0": 1}, "fallback_count": 0}}
    summary = _build_tier_summary(rows)
    data = json.loads(_json_output(stats, tier_summary=summary))
    # Per-domain contract intact at top level.
    assert data["https://a.com"]["total_articles"] == 1
    # Tier data under the reserved key.
    assert data["_dofollow_tiers"]["dofollow"]["articles"] == 1


def test_json_output_omits_tier_key_when_summary_none() -> None:
    # Backward-compat: formatter called without a summary emits no tier key.
    stats = {"https://a.com": {"total_articles": 1, "anchors": {}, "fallback_count": 0}}
    data = json.loads(_json_output(stats))
    assert "_dofollow_tiers" not in data


def test_nofollow_signal_with_no_referral_falls_into_unclassified() -> None:
    # A nofollow-signal row whose referral_value is unset (neither high nor
    # low) must land in the "unclassified" sub-bucket, not high/low.
    rows = [
        _row(
            "anyplatform",
            anchors=2,
            metadata={"dofollow_tier": "nofollow-signal", "referral_value": None},
        )
    ]
    summary = _build_tier_summary(rows)
    ref = summary["nofollow-signal"]["referral"]
    assert ref["unclassified"] == {"articles": 1, "anchors": 2}
    assert ref["high"]["articles"] == 0 and ref["low"]["articles"] == 0


def test_markdown_omits_unknown_row_when_zero_unknown_articles() -> None:
    # The unknown row is suppressed when no rows fell into the unknown tier.
    rows = [_row("blogger")]
    stats = {
        "https://a.com": {
            "total_articles": 1,
            "anchors": collections.Counter({"kw0": 1}),
            "fallback_count": 0,
        }
    }
    md = _markdown_table(stats, top_n=5, tier_summary=_build_tier_summary(rows))
    assert "| unknown |" not in md


def test_markdown_shows_unknown_row_when_unknown_present() -> None:
    rows = [_row(None, anchors=1)]
    stats = {
        "https://a.com": {
            "total_articles": 1,
            "anchors": collections.Counter({"kw0": 1}),
            "fallback_count": 0,
        }
    }
    md = _markdown_table(stats, top_n=5, tier_summary=_build_tier_summary(rows))
    assert "| unknown |" in md


def test_markdown_omits_tier_section_when_summary_none() -> None:
    # Backward-compat: no tier_summary → no breakdown section appended.
    stats = {
        "https://a.com": {
            "total_articles": 1,
            "anchors": collections.Counter({"kw0": 1}),
            "fallback_count": 0,
        }
    }
    md = _markdown_table(stats, top_n=5)
    assert "Dofollow tier breakdown" not in md


def test_markdown_appends_tier_breakdown_section() -> None:
    rows = [_row("blogger"), _row("devto")]
    stats = {
        "https://a.com": {
            "total_articles": 2,
            "anchors": collections.Counter({"kw0": 2}),
            "fallback_count": 0,
        }
    }
    summary = _build_tier_summary(rows)
    md = _markdown_table(stats, top_n=5, tier_summary=summary)
    assert "Dofollow tier breakdown" in md
    assert "nofollow-signal" in md
    assert "referral=high" in md
