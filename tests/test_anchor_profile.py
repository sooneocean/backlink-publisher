"""Tests for backlink_publisher.anchor_profile."""

from __future__ import annotations

import json
import stat
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from backlink_publisher.anchor_profile import (
    ProfileEntry,
    ProfileState,
    load_profile,
    now_iso,
    record_article,
    recent_degradation_rate,
    recent_secondary_count_split,
    recent_texts,
    recent_type_counts,
    recent_url_category_counts,
)


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def profile_cache(tmp_path):
    """Redirect _cache_dir for anchor_profile + checkpoint imports."""
    fake = tmp_path / "cache"
    with patch("backlink_publisher.anchor_profile._cache_dir", return_value=fake):
        yield fake


def _main(text: str = "51漫画首页", *, ty: str = "branded", deg: bool = False) -> ProfileEntry:
    return ProfileEntry(
        ts=now_iso(),
        link_role="main",
        url_category="home",
        anchor_type=ty,
        anchor_text=text,
        degraded=deg,
    )


def _sec(text: str, *, cat: str = "hot", ty: str = "partial", deg: bool = False) -> ProfileEntry:
    return ProfileEntry(
        ts=now_iso(),
        link_role="secondary",
        url_category=cat,
        anchor_type=ty,
        anchor_text=text,
        degraded=deg,
    )


# ── load_profile ────────────────────────────────────────────────────────────


def test_load_missing_file_returns_empty(profile_cache):
    state = load_profile("https://example.com")
    assert state.entries == []
    assert state.main_domain == "https://example.com"
    assert state.version == 1


def test_record_then_load_roundtrip(profile_cache):
    entries = [_main("51漫画首页"), _sec("热门漫画", cat="hot", ty="exact")]
    record_article("https://51acgs.com", entries)

    state = load_profile("https://51acgs.com")
    assert len(state.entries) == 2
    assert state.entries[0].anchor_text == "51漫画首页"
    assert state.entries[1].url_category == "hot"
    assert state.entries[1].anchor_type == "exact"


def test_load_corrupted_json_returns_empty_with_warning(profile_cache, capsys):
    main_domain = "https://corrupt.example"
    # Force the dir + write garbage
    record_article(main_domain, [_main("seed")])
    # Locate file & corrupt it
    files = list((profile_cache / "anchor-profile").glob("*.json"))
    assert files, "expected a profile file to be present after record_article"
    files[0].write_text("not-json{{{", encoding="utf-8")

    state = load_profile(main_domain)

    assert state.entries == []
    err = capsys.readouterr().err
    assert "anchor_profile_load_failed" in err


def test_load_version_mismatch_returns_empty(profile_cache, capsys):
    profile_cache.mkdir(parents=True, exist_ok=True)
    pdir = profile_cache / "anchor-profile"
    pdir.mkdir(parents=True, exist_ok=True)
    # Find what filename load_profile expects
    from backlink_publisher.anchor_profile import _profile_path
    p = _profile_path("https://example.com")
    p.write_text(
        json.dumps({"version": 99, "main_domain": "https://example.com", "entries": []}),
        encoding="utf-8",
    )

    state = load_profile("https://example.com")
    assert state.entries == []
    err = capsys.readouterr().err
    assert "anchor_profile_version_mismatch" in err


def test_load_skips_malformed_individual_entries(profile_cache):
    main_domain = "https://example.com"
    record_article(main_domain, [_main("good")])
    from backlink_publisher.anchor_profile import _profile_path
    p = _profile_path(main_domain)
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["entries"].append({"this": "is malformed"})  # missing required fields
    p.write_text(json.dumps(raw), encoding="utf-8")

    state = load_profile(main_domain)
    # Only the well-formed entry survives
    assert len(state.entries) == 1
    assert state.entries[0].anchor_text == "good"


# ── sliding window trim ─────────────────────────────────────────────────────


def test_sliding_window_trims_to_100(profile_cache):
    main_domain = "https://trim.example"
    # 35 articles × 3 entries each = 105 → trim to 100, dropping the oldest 5.
    for i in range(35):
        record_article(
            main_domain,
            [
                _main(f"main{i}"),
                _sec(f"sec_a{i}", cat="hot"),
                _sec(f"sec_b{i}", cat="animate"),
            ],
        )

    state = load_profile(main_domain)
    assert len(state.entries) == 100
    # First surviving entry should be a secondary (we trimmed the first main + 2 secs + 2 more)
    # 105 → keep last 100, so we drop entries[0..4]: main0, sec_a0, sec_b0, main1, sec_a1.
    # Surviving first entry = sec_b1.
    assert state.entries[0].anchor_text == "sec_b1"


def test_atomic_record_no_concurrent_loss(profile_cache):
    main_domain = "https://race.example"
    threads = []
    n_threads = 10
    per_thread = 3  # 30 total, under the 100 cap

    def worker(idx):
        record_article(
            main_domain,
            [
                _main(f"m{idx}"),
                _sec(f"s1{idx}", cat="hot"),
                _sec(f"s2{idx}", cat="animate"),
            ],
        )

    for i in range(n_threads):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    state = load_profile(main_domain)
    assert len(state.entries) == n_threads * per_thread


def test_record_empty_entries_is_noop(profile_cache):
    record_article("https://noop.example", [])
    state = load_profile("https://noop.example")
    assert state.entries == []


# ── filename sanitization ───────────────────────────────────────────────────


def test_main_domain_with_scheme_and_slash_yields_safe_filename(profile_cache):
    record_article("https://example.com/", [_main("x")])
    files = list((profile_cache / "anchor-profile").glob("*.json"))
    assert len(files) == 1
    name = files[0].name
    # No "://" or trailing slashes in filename
    assert "://" not in name
    assert "/" not in name
    # Suffix is .json
    assert name.endswith(".json")


def test_filename_safe_for_special_chars(profile_cache):
    record_article("https://path?q=1&r=2.example.com", [_main("x")])
    files = list((profile_cache / "anchor-profile").glob("*.json"))
    assert len(files) == 1
    # Only alnum/dot/underscore/hyphen survive (no ? = & : /)
    stem = files[0].stem
    assert all(c.isalnum() or c in "._-" for c in stem)


# ── file mode 0600 ──────────────────────────────────────────────────────────


@pytest.mark.skipif(False, reason="POSIX file mode check")
def test_record_writes_file_mode_0600(profile_cache):
    main_domain = "https://perm.example"
    record_article(main_domain, [_main("x")])
    files = list((profile_cache / "anchor-profile").glob("*.json"))
    assert files
    mode = stat.S_IMODE(files[0].stat().st_mode)
    # 0600 on POSIX; on Windows we just check the file exists
    import os
    if os.name != "nt":
        assert mode == 0o600


def test_record_write_failure_does_not_raise(profile_cache, capsys):
    """OSError on atomic_write_json must be logged but not re-raised."""
    main_domain = "https://writefail.example"

    with patch(
        "backlink_publisher.anchor_profile.atomic_write_json",
        side_effect=OSError("disk full"),
    ):
        # Should not raise
        record_article(main_domain, [_main("x")])

    err = capsys.readouterr().err
    assert "anchor_profile_write_failed" in err


# ── derived views ───────────────────────────────────────────────────────────


def test_recent_type_counts_returns_all_anchor_types(profile_cache):
    """All four anchor types appear in the result, with zeros for unused ones."""
    main_domain = "https://counts.example"
    entries = [
        _main("a", ty="branded"),
        _main("b", ty="branded"),
        _main("c", ty="partial"),
    ]
    record_article(main_domain, entries)
    state = load_profile(main_domain)
    counts = recent_type_counts(state)
    assert counts == {"branded": 2, "partial": 1, "exact": 0, "lsi": 0}


def test_recent_type_counts_realistic_distribution(profile_cache):
    """Synthesize a 100-entry profile matching Safe SEO and verify counts."""
    main_domain = "https://safe.example"
    entries: list[ProfileEntry] = []
    for _ in range(55):
        entries.append(_main("b", ty="branded"))
    for _ in range(25):
        entries.append(_main("p", ty="partial"))
    for _ in range(10):
        entries.append(_main("e", ty="exact"))
    for _ in range(10):
        entries.append(_main("l", ty="lsi"))
    record_article(main_domain, entries)
    state = load_profile(main_domain)
    counts = recent_type_counts(state)
    assert counts == {"branded": 55, "partial": 25, "exact": 10, "lsi": 10}


def test_recent_url_category_counts(profile_cache):
    main_domain = "https://urls.example"
    entries = [
        _main("h1", ty="branded"),  # url_category=home
        _main("h2", ty="branded"),
        _sec("hot1", cat="hot"),
        _sec("hot2", cat="hot"),
        _sec("ani1", cat="animate"),
    ]
    record_article(main_domain, entries)
    state = load_profile(main_domain)
    counts = recent_url_category_counts(state)
    assert counts == {"home": 2, "hot": 2, "animate": 1}


def test_recent_texts_newest_first(profile_cache):
    main_domain = "https://texts.example"
    record_article(main_domain, [
        _main("first"),
        _sec("second", cat="hot"),
        _main("third"),
    ])
    state = load_profile(main_domain)
    texts = recent_texts(state, n=2)
    assert texts == ["third", "second"]


def test_recent_texts_window_bounds(profile_cache):
    main_domain = "https://texts2.example"
    record_article(main_domain, [_main(f"t{i}") for i in range(30)])
    state = load_profile(main_domain)
    # Default window = 20
    assert len(recent_texts(state)) == 20
    # Smaller window
    assert len(recent_texts(state, n=5)) == 5
    # Window > available entries
    assert len(recent_texts(state, n=100)) == 30


def test_recent_degradation_rate_empty_returns_zero(profile_cache):
    state = ProfileState(main_domain="https://nothing")
    assert recent_degradation_rate(state) == 0.0


def test_recent_degradation_rate_realistic(profile_cache):
    main_domain = "https://degrade.example"
    entries: list[ProfileEntry] = []
    for i in range(100):
        entries.append(_main(f"e{i}", ty="branded", deg=(i < 12)))
    record_article(main_domain, entries)
    state = load_profile(main_domain)
    assert recent_degradation_rate(state) == pytest.approx(0.12)


def test_recent_secondary_count_split_mixed(profile_cache):
    main_domain = "https://sec.example"
    # 12 articles with 1 secondary, 8 articles with 2 secondaries — interleaved
    all_entries: list[ProfileEntry] = []
    for i in range(12):
        all_entries.append(_main(f"m1_{i}"))
        all_entries.append(_sec(f"s_{i}", cat="hot"))
    for i in range(8):
        all_entries.append(_main(f"m2_{i}"))
        all_entries.append(_sec(f"sa_{i}", cat="hot"))
        all_entries.append(_sec(f"sb_{i}", cat="animate"))
    record_article(main_domain, all_entries)
    state = load_profile(main_domain)
    count_1, count_2 = recent_secondary_count_split(state, n=20)
    assert count_1 == 12
    assert count_2 == 8


def test_recent_secondary_count_split_drops_trimmed_remnant(profile_cache):
    """A sliding-window trim that severs an article mid-record should not skew counts."""
    main_domain = "https://trim2.example"
    # 50 articles × 3 entries = 150 → trim to 100. The first 17 articles get
    # fully dropped (51 entries); article 17 might lose its main but keep
    # its 2 secondaries → those leading secondaries should NOT count as an article.
    all_entries: list[ProfileEntry] = []
    for i in range(50):
        all_entries.append(_main(f"m{i}"))
        all_entries.append(_sec(f"sa{i}", cat="hot"))
        all_entries.append(_sec(f"sb{i}", cat="animate"))
    record_article(main_domain, all_entries)
    state = load_profile(main_domain)
    assert len(state.entries) == 100

    count_1, count_2 = recent_secondary_count_split(state, n=50)
    # All surviving full articles have 2 secondaries each
    assert count_1 == 0
    # 100 entries, partial leading article dropped; complete trailing articles have 3 entries
    # The leading remnant could be 1 or 2 secondaries; remaining = 99 or 98 → 33 or 32 full articles
    assert count_2 in (32, 33)


def test_recent_secondary_count_split_empty_profile():
    state = ProfileState(main_domain="https://nothing")
    assert recent_secondary_count_split(state) == (0, 0)
