"""Tests for report-anchors CLI."""

from __future__ import annotations

import json
import sys
from io import StringIO
from unittest.mock import patch

import pytest

from backlink_publisher.anchor_profile import (
    ProfileEntry,
    ProfileState,
    now_iso,
    record_article,
)
from backlink_publisher.cli.report_anchors import (
    _build_profile_report,
    _build_report,
    _format_profile_report_markdown,
    _markdown_table,
    main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _payload(
    main_domain: str,
    url_mode: str = "A",
    anchors: tuple[str, str] = ("brand-kw", "head-kw"),
) -> dict:
    """Minimal payload dict for testing."""
    return {
        "main_domain": main_domain.rstrip("/") + "/",
        "url_mode": url_mode,
        "links": [
            {"kind": "main_domain", "url": main_domain.rstrip("/"), "anchor": anchors[0]},
            {"kind": "target", "url": main_domain.rstrip("/") + "/page", "anchor": anchors[1]},
            {"kind": "supporting", "url": "https://en.wikipedia.org", "anchor": "Wikipedia"},
        ],
    }


def _run_main(input_data: str, extra_args: list[str] | None = None) -> tuple[str, str, int]:
    old_stdin, old_stdout, old_stderr = sys.stdin, sys.stdout, sys.stderr
    try:
        sys.stdin = StringIO(input_data)
        out = StringIO()
        err = StringIO()
        sys.stdout = out
        sys.stderr = err
        try:
            main(extra_args or [])
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return out.getvalue(), err.getvalue(), code
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_stdin, old_stdout, old_stderr


# ---------------------------------------------------------------------------
# _build_report unit tests
# ---------------------------------------------------------------------------

def test_build_report_basic_two_domains():
    rows = [
        _payload("https://a.com"),
        _payload("https://b.org"),
        _payload("https://a.com", anchors=("other-kw", "head-kw")),
    ]
    stats = _build_report(rows)
    assert set(stats.keys()) == {"https://a.com", "https://b.org"}
    a = stats["https://a.com"]
    assert a["total_articles"] == 2
    assert a["anchors"]["brand-kw"] == 1
    assert a["anchors"]["head-kw"] == 2  # appears in both articles
    assert a["anchors"]["other-kw"] == 1


def test_build_report_supporting_links_excluded():
    rows = [_payload("https://a.com")]
    stats = _build_report(rows)
    # Wikipedia is a supporting link — must not appear in anchor counts
    assert "Wikipedia" not in stats["https://a.com"]["anchors"]


def test_build_report_fallback_detection():
    rows = [
        _payload("https://a.com", anchors=("a.com", "head-kw")),  # fallback anchor
        _payload("https://a.com", anchors=("brand", "head-kw")),   # not fallback
    ]
    stats = _build_report(rows)
    assert stats["https://a.com"]["fallback_count"] == 1


def test_build_report_all_fallback():
    rows = [
        _payload("https://site.com", anchors=("site.com", "site.com")),
        _payload("https://site.com", anchors=("site.com", "site.com")),
    ]
    stats = _build_report(rows)
    assert stats["https://site.com"]["fallback_count"] == 2
    assert stats["https://site.com"]["total_articles"] == 2


def test_build_report_empty_input():
    assert _build_report([]) == {}


def test_build_report_missing_links_field_skipped():
    rows = [{"main_domain": "https://a.com"}]  # no 'links' key
    stats = _build_report(rows)
    assert stats["https://a.com"]["total_articles"] == 1
    assert len(stats["https://a.com"]["anchors"]) == 0


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------

def test_cli_markdown_output_basic():
    row = json.dumps(_payload("https://a.com"))
    stdout, _, code = _run_main(row)
    assert code == 0
    assert "https://a.com" in stdout
    assert "|" in stdout  # markdown table
    assert "brand-kw" in stdout


def test_cli_json_flag():
    row = json.dumps(_payload("https://a.com"))
    stdout, _, code = _run_main(row, ["--json"])
    assert code == 0
    data = json.loads(stdout)
    assert "https://a.com" in data
    entry = data["https://a.com"]
    assert entry["total_articles"] == 1
    assert "brand-kw" in entry["anchors"]


def test_cli_empty_input():
    stdout, stderr, code = _run_main("")
    assert code == 0
    # No crash; markdown table with just header lines
    assert "target" in stdout


def test_cli_malformed_json_line_warns_and_skips():
    data = "not-json\n" + json.dumps(_payload("https://a.com"))
    stdout, stderr, code = _run_main(data)
    assert code == 0
    assert "WARN" in stderr
    assert "https://a.com" in stdout


def test_cli_top_anchors_limit():
    # 6 distinct anchors for same domain, limit to 2
    rows = "\n".join(
        json.dumps(_payload("https://a.com", anchors=(f"kw{i}", f"kw{i+1}")))
        for i in range(6)
    )
    stdout, _, code = _run_main(rows, ["--top-anchors", "2"])
    assert code == 0
    # At most 2 top-anchor entries per row in the table
    # Just check the row isn't showing more than 2 (kw, count) pairs
    a_row = [line for line in stdout.splitlines() if "https://a.com" in line][0]
    assert a_row.count("('") <= 2 or a_row.count("'kw") <= 2


def test_cli_multiple_url_modes_show_distinct_anchors():
    rows = "\n".join(
        json.dumps(_payload("https://a.com", url_mode=mode, anchors=(f"kw-{mode}", f"kw2-{mode}")))
        for mode in ("A", "B", "C")
    )
    stdout, _, code = _run_main(rows, ["--json"])
    assert code == 0
    data = json.loads(stdout)
    distinct = len(data["https://a.com"]["anchors"])
    assert distinct >= 3


# ─── Unit 9: --from-profile path tests ──────────────────────────────────────


SAFE_SEO = {"branded": 0.55, "partial": 0.25, "exact": 0.10, "lsi": 0.10}


@pytest.fixture()
def profile_cache(tmp_path):
    """Redirect anchor_profile to write under tmp_path."""
    with patch("backlink_publisher.anchor_profile._cache_dir", return_value=tmp_path / "cache"):
        yield tmp_path / "cache"


def _entry(role="main", cat="home", ty="branded", text="x", degraded=False):
    return ProfileEntry(
        ts=now_iso(),
        link_role=role,
        url_category=cat,
        anchor_type=ty,
        anchor_text=text,
        degraded=degraded,
    )


def _build_safe_seo_profile() -> ProfileState:
    """100-entry profile matching Safe SEO exactly."""
    entries = (
        [_entry(ty="branded", text=f"b{i}") for i in range(55)]
        + [_entry(ty="partial", text=f"p{i}") for i in range(25)]
        + [_entry(ty="exact", text=f"e{i}") for i in range(10)]
        + [_entry(ty="lsi", text=f"l{i}") for i in range(10)]
    )
    return ProfileState(main_domain="https://51acgs.com", entries=entries)


# ── _build_profile_report unit tests ────────────────────────────────────────


def test_profile_report_safe_seo_distribution_zero_deviation():
    report = _build_profile_report(_build_safe_seo_profile(), SAFE_SEO)
    assert report["total_entries"] == 100
    for t, expected in [("branded", 55), ("partial", 25), ("exact", 10), ("lsi", 10)]:
        s = report["type_stats"][t]
        assert s["count"] == expected
        assert abs(s["deviation_pp"]) < 0.01


def test_profile_report_all_branded_yields_45pp_deviation():
    entries = [_entry(ty="branded", text=f"b{i}") for i in range(100)]
    profile = ProfileState(main_domain="https://x.com", entries=entries)
    report = _build_profile_report(profile, SAFE_SEO)
    assert report["type_stats"]["branded"]["deviation_pp"] == pytest.approx(45.0)
    assert report["type_stats"]["partial"]["deviation_pp"] == pytest.approx(-25.0)
    assert report["type_stats"]["exact"]["deviation_pp"] == pytest.approx(-10.0)
    assert report["type_stats"]["lsi"]["deviation_pp"] == pytest.approx(-10.0)


def test_profile_report_degradation_rate_above_threshold():
    """15 degraded out of 100 should be reported as 15.0."""
    entries = [
        _entry(ty="branded", text=f"x{i}", degraded=(i < 15)) for i in range(100)
    ]
    profile = ProfileState(main_domain="https://x.com", entries=entries)
    report = _build_profile_report(profile, SAFE_SEO)
    assert report["degradation_rate_pct"] == pytest.approx(15.0)


def test_profile_report_zero_degradation_rate_when_empty():
    profile = ProfileState(main_domain="https://nothing.example")
    report = _build_profile_report(profile, SAFE_SEO)
    assert report["degradation_rate_pct"] == 0.0
    assert report["total_entries"] == 0


def test_profile_report_top_texts_counts_duplicates():
    entries = (
        [_entry(text="重复") for _ in range(4)]
        + [_entry(text="单次") for _ in range(1)]
    )
    profile = ProfileState(main_domain="https://x.com", entries=entries)
    report = _build_profile_report(profile, SAFE_SEO)
    top = dict(report["top_texts"])
    assert top["重复"] == 4
    assert top["单次"] == 1


def test_profile_report_url_category_cross_tab():
    entries = [
        _entry(role="main", cat="home", ty="branded"),
        _entry(role="secondary", cat="hot", ty="exact"),
        _entry(role="secondary", cat="hot", ty="exact"),
        _entry(role="secondary", cat="animate", ty="lsi"),
    ]
    profile = ProfileState(main_domain="https://x.com", entries=entries)
    report = _build_profile_report(profile, SAFE_SEO)
    cross = report["url_cat_cross"]
    assert cross["home"]["branded"] == 1
    assert cross["hot"]["exact"] == 2
    assert cross["animate"]["lsi"] == 1


# ── Markdown formatter ──────────────────────────────────────────────────────


def test_markdown_format_includes_all_sections():
    report = _build_profile_report(_build_safe_seo_profile(), SAFE_SEO)
    md = _format_profile_report_markdown(report)
    assert "Anchor Profile Report" in md
    assert "Total entries (rolling window): **100**" in md
    assert "Anchor Type Distribution" in md
    assert "URL Category × Anchor Type" in md
    assert "Top 20 Most-Used Anchor Texts" in md
    # No sample-size warning at 100 entries
    assert "below 50" not in md


def test_markdown_emits_sample_size_warning_under_50():
    entries = [_entry(text=f"e{i}") for i in range(30)]
    profile = ProfileState(main_domain="https://x.com", entries=entries)
    report = _build_profile_report(profile, SAFE_SEO)
    md = _format_profile_report_markdown(report)
    assert "⚠️" in md
    assert "below" in md


def test_markdown_flags_high_degradation_rate():
    entries = [_entry(degraded=(i < 12), text=f"e{i}") for i in range(100)]
    profile = ProfileState(main_domain="https://x.com", entries=entries)
    report = _build_profile_report(profile, SAFE_SEO)
    md = _format_profile_report_markdown(report)
    assert "Degradation Rate" in md
    assert "12.0%" in md
    # Should carry the alarm marker since 12% > 10%
    assert "⚠️" in md
    assert "investigate" in md


def test_markdown_no_alarm_below_threshold():
    entries = [_entry(degraded=(i < 5), text=f"e{i}") for i in range(100)]
    profile = ProfileState(main_domain="https://x.com", entries=entries)
    report = _build_profile_report(profile, SAFE_SEO)
    md = _format_profile_report_markdown(report)
    assert "5.0%" in md
    # No alarm marker on the degradation row
    assert "investigate" not in md


def test_markdown_handles_empty_profile_gracefully():
    profile = ProfileState(main_domain="https://nothing.example")
    report = _build_profile_report(profile, SAFE_SEO)
    md = _format_profile_report_markdown(report)
    assert "Total entries (rolling window): **0**" in md
    assert "(no entries)" in md


# ── CLI integration via --from-profile ──────────────────────────────────────


def test_cli_from_profile_renders_markdown(profile_cache):
    record_article("https://51acgs.com", [
        _entry(ty="branded", text="51漫画首页"),
        _entry(role="secondary", cat="hot", ty="exact", text="热门漫画"),
    ])
    # Empty stdin since --from-profile reads the file, not stdin
    stdout, stderr, code = _run_main("", ["--from-profile", "https://51acgs.com"])
    assert code == 0, f"stderr={stderr}"
    assert "Anchor Profile Report" in stdout
    assert "51漫画首页" in stdout
    assert "热门漫画" in stdout


def test_cli_from_profile_json(profile_cache):
    record_article("https://51acgs.com", [
        _entry(ty="branded", text="51漫画首页"),
        _entry(role="secondary", cat="hot", ty="exact", text="热门漫画"),
    ])
    stdout, stderr, code = _run_main("", ["--from-profile", "https://51acgs.com", "--json"])
    assert code == 0, f"stderr={stderr}"
    data = json.loads(stdout)
    assert data["total_entries"] == 2
    assert "type_stats" in data
    assert any(item[0] == "51漫画首页" for item in data["top_texts"])


def test_cli_from_profile_missing_file_returns_empty(profile_cache):
    """No profile file yet → empty report, not an error."""
    stdout, stderr, code = _run_main("", ["--from-profile", "https://nothing.example"])
    assert code == 0
    assert "Total entries" in stdout
    # Sample-size warning is expected for an empty profile
    assert "⚠️" in stdout


def test_cli_existing_jsonl_path_still_works():
    """--from-profile must not break the original --from-jsonl behavior."""
    rows_json = json.dumps(_payload("https://a.com"))
    stdout, _, code = _run_main(rows_json)
    assert code == 0
    # The original markdown table header should be present
    assert "| target | articles" in stdout


# ─── Anchor distribution alarm integration ──────────────────────────────────


def _breach_profile_entries(target_url: str) -> list[ProfileEntry]:
    """Synthesize a profile that breaches exact_ratio_ceiling.

    25 entries to one target_url: 15 exact-match + 10 branded → 60% exact
    ratio, well above the 10% default ceiling. Sample size ≥ 20 so the
    alarm-floor gate does not suppress.
    """
    return [
        ProfileEntry(
            ts=now_iso(), link_role="main", url_category="home",
            anchor_type="exact", anchor_text=f"exact_{i}", target_url=target_url,
        )
        for i in range(15)
    ] + [
        ProfileEntry(
            ts=now_iso(), link_role="main", url_category="home",
            anchor_type="branded", anchor_text=f"brand_{i}", target_url=target_url,
        )
        for i in range(10)
    ]


def test_cli_from_profile_clean_distribution_exits_zero(profile_cache):
    """Empty profile → alarm.any_breach = False, exit 0."""
    stdout, stderr, code = _run_main(
        "", ["--from-profile", "https://clean.example", "--json"],
    )
    assert code == 0, f"stderr={stderr}"
    data = json.loads(stdout)
    assert "alarm" in data
    assert data["alarm"]["any_breach"] is False
    # Stderr should NOT contain any anchor_alarm WARN lines
    assert "anchor_alarm" not in stderr


def test_cli_from_profile_breach_exits_6(profile_cache):
    """Synthetic exact-ratio breach → exit code 6 + stderr WARN + JSON breaches."""
    target = "https://breach.example/money-page"
    record_article("https://breach.example", _breach_profile_entries(target))

    stdout, stderr, code = _run_main(
        "", ["--from-profile", "https://breach.example", "--json"],
    )
    assert code == 6, f"expected exit 6 (alarm); got {code}; stderr={stderr}"
    data = json.loads(stdout)
    assert data["alarm"]["any_breach"] is True

    target_entry = data["alarm"]["targets"][target]
    assert target_entry["granularity"] == "url"
    assert "exact_ratio_ceiling" in target_entry["breaches"]

    # 90d window contains all 25 entries (synthesized at now_iso())
    assert target_entry["metrics"]["90d"]["sample_size"] == 25

    # Stderr has the human-readable WARN line
    assert "WARN [anchor_alarm]" in stderr
    assert target in stderr
    assert "exact_ratio_ceiling" in stderr


def test_cli_from_profile_breach_markdown_includes_alarm_section(profile_cache):
    """Markdown output appends a Distribution Alarm subsection on breach."""
    target = "https://md-breach.example/page"
    record_article("https://md-breach.example", _breach_profile_entries(target))

    stdout, stderr, code = _run_main(
        "", ["--from-profile", "https://md-breach.example"],
    )
    assert code == 6
    assert "## ⚠️ Anchor Distribution Alarm" in stdout
    assert target in stdout
    assert "exact_ratio_ceiling" in stdout


def test_cli_from_profile_low_sample_suppresses_alarm(profile_cache):
    """Sample < 20 → metrics computed, but no breach + exit 0."""
    target = "https://low-n.example/page"
    # Only 10 entries — below _ALARM_SAMPLE_MIN_PER_TARGET=20
    record_article(
        "https://low-n.example",
        [
            ProfileEntry(
                ts=now_iso(), link_role="main", url_category="home",
                anchor_type="exact", anchor_text=f"e{i}", target_url=target,
            )
            for i in range(10)
        ],
    )
    stdout, stderr, code = _run_main(
        "", ["--from-profile", "https://low-n.example", "--json"],
    )
    assert code == 0, f"low-sample should suppress; stderr={stderr}"
    data = json.loads(stdout)
    assert data["alarm"]["any_breach"] is False
    target_entry = data["alarm"]["targets"][target]
    assert target_entry["breaches"] == []
    assert target_entry["metrics"]["90d"]["sample_size"] == 10
    # Metric still computed (exact_ratio = 1.0)
    assert target_entry["metrics"]["90d"]["exact_ratio"] == 1.0


def test_cli_from_profile_per_domain_override_loosens_threshold(
    profile_cache, tmp_path, monkeypatch,
):
    """A loose per-domain override on exact_ratio_ceiling suppresses what
    would otherwise breach with default thresholds."""
    target = "https://override.example/page"
    record_article("https://override.example", _breach_profile_entries(target))

    # Write a config.toml that loosens exact_ratio_ceiling for this domain.
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "config.toml"
    config_path.write_text(
        "[[anchor_alarm.override]]\n"
        "match = 'override.example'\n"
        "scope = 'domain'\n"
        "exact_ratio_ceiling = 0.80\n",  # loose: 80% ceiling, fixture is 60%
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "backlink_publisher.config._config_dir",
        lambda: config_dir,
    )

    stdout, stderr, code = _run_main(
        "", ["--from-profile", "https://override.example", "--json"],
    )
    assert code == 0, f"override should suppress breach; stderr={stderr}"
    data = json.loads(stdout)
    target_entry = data["alarm"]["targets"][target]
    # Resolved threshold reflects the override
    assert target_entry["thresholds_applied"]["exact_ratio_ceiling"] == 0.80
    assert target_entry["breaches"] == []


def test_cli_stdin_path_emits_hint_about_from_profile():
    """Operator running cat payloads.jsonl | report-anchors gets a stderr
    hint that the distribution alarm requires --from-profile. Prevents the
    false-safety failure mode flagged by document-review F6."""
    row = json.dumps(_payload("https://hint.example"))
    stdout, stderr, code = _run_main(row)
    assert code == 0
    assert "anchor distribution alarm requires --from-profile" in stderr


def test_cli_pre_bump_rollup_target(profile_cache):
    """Pre-bump entries (target_url='') surface as a 'domain-rollup' bucket."""
    # Write entries via load_profile path: use raw JSON to skip the new
    # write-time mechanism and simulate a profile written before target_url existed.
    main_domain = "https://prebump.example"
    fake_dir = profile_cache / "anchor-profile"
    fake_dir.mkdir(parents=True, exist_ok=True)
    legacy_payload = {
        "version": 1,
        "main_domain": main_domain,
        "entries": [
            {
                "ts": now_iso(),
                "link_role": "main",
                "url_category": "home",
                "anchor_type": "branded",
                "anchor_text": f"old{i}",
                "degraded": False,
            }
            for i in range(5)
        ],
    }
    (fake_dir / "https___prebump.example.json").write_text(
        json.dumps(legacy_payload), encoding="utf-8"
    )

    stdout, stderr, code = _run_main(
        "", ["--from-profile", main_domain, "--json"],
    )
    assert code == 0
    data = json.loads(stdout)
    # Pre-bump bucket keyed by ""; granularity labeled "domain-rollup"
    assert "" in data["alarm"]["targets"]
    assert data["alarm"]["targets"][""]["granularity"] == "domain-rollup"
