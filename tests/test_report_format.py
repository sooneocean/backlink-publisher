"""Unit tests for cli/_report_format.py.

All functions in the module are documented as pure — no I/O, no network,
no DB. Tests construct minimal in-memory inputs and assert on output structure
or spot-check key values.
"""

from __future__ import annotations

import json

import backlink_publisher.publishing.adapters  # registers platforms before registry reads

from backlink_publisher.cli._report_format import (
    _build_report,
    _build_tier_summary,
    _count_qualifying_anchors,
    _domain_label,
    _format_profile_report_json,
    _format_profile_report_markdown,
    _json_output,
    _markdown_table,
    _resolve_row_tier,
    _tier_markdown,
)
from backlink_publisher.config import ANCHOR_TYPES


# ── Helpers ────────────────────────────────────────────────────────────────────


def _link(kind: str = "main_domain", anchor: str = "example") -> dict:
    return {"kind": kind, "anchor": anchor}


def _row(
    main_domain: str = "https://example.com",
    links: list | None = None,
    platform: str = "velog",
    metadata: dict | None = None,
) -> dict:
    r: dict = {"main_domain": main_domain, "platform": platform}
    if links is not None:
        r["links"] = links
    if metadata is not None:
        r["metadata"] = metadata
    return r


def _make_report(
    *,
    main_domain: str = "example.com",
    total_entries: int = 100,
    degradation_rate_pct: float = 0.0,
    url_cat_cross: dict | None = None,
    top_texts: list | None = None,
) -> dict:
    type_stats = {
        t: {"count": 25, "actual_pct": 25.0, "target_pct": 25.0, "deviation_pp": 0.0}
        for t in ANCHOR_TYPES
    }
    return {
        "main_domain": main_domain,
        "total_entries": total_entries,
        "type_stats": type_stats,
        "url_cat_cross": url_cat_cross or {},
        "degradation_rate_pct": degradation_rate_pct,
        "top_texts": top_texts or [("buy cheap", 5), ("click here", 3)],
    }


def _make_tier_summary(
    *,
    df_articles: int = 2,
    nf_articles: int = 3,
    unk_articles: int = 0,
) -> dict:
    return {
        "dofollow": {"articles": df_articles, "anchors": 10},
        "nofollow-signal": {
            "articles": nf_articles,
            "anchors": 15,
            "referral": {
                "high": {"articles": 1, "anchors": 5},
                "low": {"articles": 2, "anchors": 10},
                "unclassified": {"articles": 0, "anchors": 0},
            },
        },
        "unknown": {"articles": unk_articles, "anchors": 0},
    }


# ── _domain_label ──────────────────────────────────────────────────────────────


class TestDomainLabel:
    def test_strips_https(self) -> None:
        assert _domain_label("https://example.com") == "example.com"

    def test_strips_http(self) -> None:
        assert _domain_label("http://example.com") == "example.com"

    def test_strips_trailing_slash(self) -> None:
        assert _domain_label("https://example.com/") == "example.com"

    def test_bare_domain_unchanged(self) -> None:
        assert _domain_label("example.com") == "example.com"


# ── _build_report ──────────────────────────────────────────────────────────────


class TestBuildReport:
    def test_empty_rows_returns_empty(self) -> None:
        assert _build_report([]) == {}

    def test_row_without_main_domain_skipped(self) -> None:
        assert _build_report([{"links": [_link()]}]) == {}

    def test_link_counts_accumulated(self) -> None:
        rows = [_row(links=[_link(anchor="foo"), _link(anchor="foo"), _link(anchor="bar")])]
        stats = _build_report(rows)
        assert stats["https://example.com"]["anchors"]["foo"] == 2
        assert stats["https://example.com"]["anchors"]["bar"] == 1

    def test_fallback_detected_when_anchor_matches_domain(self) -> None:
        rows = [_row(main_domain="https://example.com", links=[_link(anchor="example.com")])]
        stats = _build_report(rows)
        assert stats["https://example.com"]["fallback_count"] == 1

    def test_non_qualifying_link_kind_ignored(self) -> None:
        rows = [_row(links=[_link(kind="other", anchor="x")])]
        stats = _build_report(rows)
        assert stats["https://example.com"]["anchors"]["x"] == 0  # Counter returns 0 for missing

    def test_non_list_links_row_counted_but_no_anchors(self) -> None:
        rows = [_row(links=None)]
        rows[0]["links"] = "bad"
        stats = _build_report(rows)
        # Non-list links → row is skipped entirely (continue in loop)
        assert "https://example.com" not in stats

    def test_total_articles_increments(self) -> None:
        rows = [_row(), _row()]
        stats = _build_report(rows)
        assert stats["https://example.com"]["total_articles"] == 2


# ── _count_qualifying_anchors ──────────────────────────────────────────────────


class TestCountQualifyingAnchors:
    def test_empty_links(self) -> None:
        assert _count_qualifying_anchors({"links": []}) == 0

    def test_non_list_links(self) -> None:
        assert _count_qualifying_anchors({"links": "bad"}) == 0

    def test_qualifying_links_counted(self) -> None:
        row = {"links": [_link("main_domain", "foo"), _link("target", "bar")]}
        assert _count_qualifying_anchors(row) == 2

    def test_non_qualifying_kind_excluded(self) -> None:
        row = {"links": [_link("other", "x"), _link("main_domain", "y")]}
        assert _count_qualifying_anchors(row) == 1

    def test_empty_anchor_excluded(self) -> None:
        row = {"links": [{"kind": "main_domain", "anchor": ""}]}
        assert _count_qualifying_anchors(row) == 0


# ── _resolve_row_tier ──────────────────────────────────────────────────────────


class TestResolveRowTier:
    def test_metadata_tier_wins(self) -> None:
        row = _row(metadata={"dofollow_tier": "dofollow", "referral_value": "high"})
        tier, referral = _resolve_row_tier(row)
        assert tier == "dofollow"
        assert referral == "high"

    def test_nofollow_signal_from_metadata(self) -> None:
        row = _row(metadata={"dofollow_tier": "nofollow-signal", "referral_value": "low"})
        tier, referral = _resolve_row_tier(row)
        assert tier == "nofollow-signal"
        assert referral == "low"

    def test_falls_back_to_registry_for_known_dofollow_platform(self) -> None:
        row = _row(platform="telegraph")
        tier, _ = _resolve_row_tier(row)
        assert tier == "dofollow"

    def test_no_platform_no_metadata_gives_unknown(self) -> None:
        row: dict = {}
        tier, referral = _resolve_row_tier(row)
        assert tier == "unknown"

    def test_unregistered_platform_gives_unknown(self) -> None:
        row = _row(platform="no_such_platform_xyz")
        tier, _ = _resolve_row_tier(row)
        assert tier == "unknown"


# ── _build_tier_summary ────────────────────────────────────────────────────────


class TestBuildTierSummary:
    def test_empty_rows(self) -> None:
        s = _build_tier_summary([])
        assert s["dofollow"]["articles"] == 0
        assert s["nofollow-signal"]["articles"] == 0
        assert s["unknown"]["articles"] == 0

    def test_dofollow_row_counted(self) -> None:
        rows = [_row(metadata={"dofollow_tier": "dofollow"}, links=[_link()])]
        s = _build_tier_summary(rows)
        assert s["dofollow"]["articles"] == 1
        assert s["dofollow"]["anchors"] == 1

    def test_nofollow_referral_sub_split(self) -> None:
        rows = [
            _row(metadata={"dofollow_tier": "nofollow-signal", "referral_value": "high"}),
            _row(metadata={"dofollow_tier": "nofollow-signal", "referral_value": "low"}),
        ]
        s = _build_tier_summary(rows)
        assert s["nofollow-signal"]["articles"] == 2
        assert s["nofollow-signal"]["referral"]["high"]["articles"] == 1
        assert s["nofollow-signal"]["referral"]["low"]["articles"] == 1

    def test_unclassified_referral_bucket(self) -> None:
        rows = [_row(metadata={"dofollow_tier": "nofollow-signal", "referral_value": None})]
        s = _build_tier_summary(rows)
        assert s["nofollow-signal"]["referral"]["unclassified"]["articles"] == 1


# ── _tier_markdown ─────────────────────────────────────────────────────────────


class TestTierMarkdown:
    def test_contains_header(self) -> None:
        md = _tier_markdown(_make_tier_summary())
        assert "Dofollow tier breakdown" in md

    def test_dofollow_row_present(self) -> None:
        md = _tier_markdown(_make_tier_summary(df_articles=5))
        assert "dofollow" in md
        assert "5" in md

    def test_unknown_row_omitted_when_zero(self) -> None:
        md = _tier_markdown(_make_tier_summary(unk_articles=0))
        lines = [l for l in md.splitlines() if "unknown" in l]
        assert not lines

    def test_unknown_row_present_when_nonzero(self) -> None:
        md = _tier_markdown(_make_tier_summary(unk_articles=2))
        assert "unknown" in md


# ── _markdown_table ────────────────────────────────────────────────────────────


class TestMarkdownTable:
    def _stats(self) -> dict:
        import collections
        c = collections.Counter({"branded": 3, "exact": 1})
        return {"example.com": {"total_articles": 4, "anchors": c, "fallback_count": 1}}

    def test_contains_header(self) -> None:
        md = _markdown_table(self._stats(), top_n=5)
        assert "target" in md
        assert "articles" in md

    def test_domain_row_present(self) -> None:
        md = _markdown_table(self._stats(), top_n=5)
        assert "example.com" in md

    def test_with_tier_summary_appended(self) -> None:
        md = _markdown_table(self._stats(), top_n=5, tier_summary=_make_tier_summary())
        assert "Dofollow tier breakdown" in md


# ── _json_output ───────────────────────────────────────────────────────────────


class TestJsonOutput:
    def _stats(self) -> dict:
        import collections
        c = collections.Counter({"branded": 2})
        return {"example.com": {"total_articles": 2, "anchors": c, "fallback_count": 0}}

    def test_valid_json(self) -> None:
        out = _json_output(self._stats())
        parsed = json.loads(out)
        assert "example.com" in parsed

    def test_anchors_are_plain_dict(self) -> None:
        out = _json_output(self._stats())
        parsed = json.loads(out)
        assert isinstance(parsed["example.com"]["anchors"], dict)

    def test_tier_summary_embedded(self) -> None:
        out = _json_output(self._stats(), tier_summary={"dofollow": {"articles": 1}})
        parsed = json.loads(out)
        assert "_dofollow_tiers" in parsed


# ── _format_profile_report_markdown ───────────────────────────────────────────


class TestFormatProfileReportMarkdown:
    def test_main_domain_in_header(self) -> None:
        md = _format_profile_report_markdown(_make_report(main_domain="foo.com"))
        assert "foo.com" in md

    def test_small_sample_warning(self) -> None:
        md = _format_profile_report_markdown(_make_report(total_entries=10))
        assert "below" in md or "not yet reliable" in md

    def test_no_small_sample_warning_for_large_sample(self) -> None:
        md = _format_profile_report_markdown(_make_report(total_entries=200))
        assert "not yet reliable" not in md

    def test_degradation_alarm_marker(self) -> None:
        md = _format_profile_report_markdown(_make_report(degradation_rate_pct=15.0))
        assert "⚠️" in md or "Degradation rate exceeds" in md

    def test_anchor_type_table_contains_all_types(self) -> None:
        md = _format_profile_report_markdown(_make_report())
        for t in ANCHOR_TYPES:
            assert t in md

    def test_empty_url_cat_cross_shows_no_entries(self) -> None:
        md = _format_profile_report_markdown(_make_report(url_cat_cross={}))
        assert "no entries" in md

    def test_url_cat_cross_table_rendered(self) -> None:
        cross = {"blog": {"branded": 3, "exact": 1}}
        md = _format_profile_report_markdown(_make_report(url_cat_cross=cross))
        assert "blog" in md

    def test_top_texts_table_present(self) -> None:
        md = _format_profile_report_markdown(_make_report(top_texts=[("buy now", 7)]))
        assert "buy now" in md
        assert "7" in md


# ── _format_profile_report_json ───────────────────────────────────────────────


class TestFormatProfileReportJson:
    def test_top_texts_serialized_as_lists(self) -> None:
        report = _make_report(top_texts=[("anchor text", 5)])
        parsed = json.loads(_format_profile_report_json(report))
        assert parsed["top_texts"] == [["anchor text", 5]]

    def test_valid_json_roundtrip(self) -> None:
        report = _make_report()
        parsed = json.loads(_format_profile_report_json(report))
        assert parsed["main_domain"] == "example.com"
        assert parsed["total_entries"] == 100
