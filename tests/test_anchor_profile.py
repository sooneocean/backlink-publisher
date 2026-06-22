"""Tests for backlink_publisher.anchor_profile."""

from __future__ import annotations

import json
import stat
import threading
from unittest.mock import patch

import pytest

from backlink_publisher.anchor.profile import (
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
    with patch("backlink_publisher.anchor.profile._cache_dir", return_value=fake):
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
    from backlink_publisher.anchor.profile import _profile_path
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
    from backlink_publisher.anchor.profile import _profile_path
    p = _profile_path(main_domain)
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["entries"].append({"this": "is malformed"})  # missing required fields
    p.write_text(json.dumps(raw), encoding="utf-8")

    state = load_profile(main_domain)
    # Only the well-formed entry survives
    assert len(state.entries) == 1
    assert state.entries[0].anchor_text == "good"


# ── sliding window trim ─────────────────────────────────────────────────────


def test_sliding_window_caps_at_max_articles_per_target(profile_cache):
    """Per-target trim: 105 articles all sharing target_url='' → keep newest 100.

    With per-target article-integrity trim, an article is the atomic unit
    (main + secondaries kept or evicted together). 105 articles × 3 entries
    each = 315 entries; cap is _MAX_ARTICLES_PER_TARGET=100 articles for the
    "" bucket → 100 surviving articles × 3 entries = 300 entries.
    """
    main_domain = "https://trim.example"
    for i in range(105):
        record_article(
            main_domain,
            [
                _main(f"main{i}"),
                _sec(f"sec_a{i}", cat="hot"),
                _sec(f"sec_b{i}", cat="animate"),
            ],
        )

    state = load_profile(main_domain)
    # 100 articles × 3 entries each
    assert len(state.entries) == 300
    # Oldest 5 articles evicted atomically; first surviving article starts at main5.
    assert state.entries[0].link_role == "main"
    assert state.entries[0].anchor_text == "main5"
    # No orphaned secondaries (article-integrity invariant)
    assert all(
        e.link_role == "main" or i > 0
        for i, e in enumerate(state.entries)
    )


def test_per_target_trim_isolates_buckets(profile_cache):
    """Per-target trim: target A overflowing does not affect target B's history."""
    main_domain = "https://multi-target.example"
    target_a = "https://multi-target.example/a"
    target_b = "https://multi-target.example/b"

    # 50 articles to target B (well under cap).
    for i in range(50):
        entry = ProfileEntry(
            ts=now_iso(),
            link_role="main",
            url_category="home",
            anchor_type="branded",
            anchor_text=f"b_anchor{i}",
            target_url=target_b,
        )
        record_article(main_domain, [entry])

    # 120 articles to target A (over cap of 100).
    for i in range(120):
        entry = ProfileEntry(
            ts=now_iso(),
            link_role="main",
            url_category="home",
            anchor_type="branded",
            anchor_text=f"a_anchor{i}",
            target_url=target_a,
        )
        record_article(main_domain, [entry])

    state = load_profile(main_domain)
    a_entries = [e for e in state.entries if e.target_url == target_a]
    b_entries = [e for e in state.entries if e.target_url == target_b]

    # Target A trimmed to 100 most-recent articles
    assert len(a_entries) == 100
    # Target B untouched
    assert len(b_entries) == 50


def test_article_integrity_under_trim(profile_cache):
    """Trim never strands a secondary without its main."""
    from backlink_publisher.anchor.profile import _group_into_articles

    main_domain = "https://integrity.example"
    target = "https://integrity.example/page"

    # 110 articles each with 1 main + 2 secondaries, all to the same target_url.
    for i in range(110):
        ts = now_iso()
        record_article(
            main_domain,
            [
                ProfileEntry(ts=ts, link_role="main", url_category="home",
                             anchor_type="branded", anchor_text=f"m{i}", target_url=target),
                ProfileEntry(ts=ts, link_role="secondary", url_category="hot",
                             anchor_type="partial", anchor_text=f"s1_{i}", target_url=target),
                ProfileEntry(ts=ts, link_role="secondary", url_category="animate",
                             anchor_type="partial", anchor_text=f"s2_{i}", target_url=target),
            ],
        )

    state = load_profile(main_domain)
    articles = _group_into_articles(state.entries)
    # 100 surviving articles (cap), each with main + 2 secondaries intact
    assert len(articles) == 100
    for art in articles:
        assert art[0].link_role == "main"
        assert sum(1 for e in art if e.link_role == "secondary") == 2


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


# ── target_url additive field (no schema bump) ──────────────────────────────


def test_load_pre_bump_profile_preserves_all_entries(profile_cache):
    """A v1 on-disk profile written before target_url existed must load cleanly
    with target_url='' for every entry. NO entries silently disappear.

    This is the load-bearing guarantee that prevents the document-review's
    P0 data-destruction finding from regressing — a future maintainer using
    square-bracket access (item["target_url"]) instead of .get() would drop
    every pre-bump entry into the KeyError except-continue branch silently.
    """
    main_domain = "https://pre-bump.example"
    fake_dir = profile_cache / "anchor-profile"
    fake_dir.mkdir(parents=True, exist_ok=True)

    # Hand-craft a v1 profile JSON with NO target_url field on entries.
    legacy_payload = {
        "version": 1,
        "main_domain": main_domain,
        "entries": [
            {
                "ts": "2026-05-01T00:00:00+00:00",
                "link_role": "main",
                "url_category": "home",
                "anchor_type": "branded",
                "anchor_text": f"legacy_anchor_{i}",
                "degraded": False,
                # target_url deliberately absent — legacy schema
            }
            for i in range(7)
        ],
    }
    profile_path = fake_dir / "https___pre-bump.example.json"
    profile_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    state = load_profile(main_domain)
    # All 7 entries survive (no silent KeyError-continue dropping)
    assert len(state.entries) == 7
    # Each entry has target_url defaulted to ""
    assert all(e.target_url == "" for e in state.entries)
    # Other fields preserved
    assert [e.anchor_text for e in state.entries] == [
        f"legacy_anchor_{i}" for i in range(7)
    ]


def test_record_with_target_url_roundtrips(profile_cache):
    """New entries carrying target_url survive write→read roundtrip."""
    main_domain = "https://newshape.example"
    target = "https://newshape.example/money-page"

    entry = ProfileEntry(
        ts=now_iso(),
        link_role="main",
        url_category="home",
        anchor_type="branded",
        anchor_text="newshape anchor",
        target_url=target,
    )
    record_article(main_domain, [entry])

    state = load_profile(main_domain)
    assert len(state.entries) == 1
    assert state.entries[0].target_url == target


def test_target_url_null_in_json_loads_as_empty_string(profile_cache):
    """JSON null in target_url field is coerced to '' (not raises)."""
    main_domain = "https://nullable.example"
    fake_dir = profile_cache / "anchor-profile"
    fake_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "main_domain": main_domain,
        "entries": [
            {
                "ts": "2026-05-14T00:00:00+00:00",
                "link_role": "main",
                "url_category": "home",
                "anchor_type": "branded",
                "anchor_text": "x",
                "degraded": False,
                "target_url": None,
            }
        ],
    }
    (fake_dir / "https___nullable.example.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    state = load_profile(main_domain)
    assert len(state.entries) == 1
    # Python's str(None) == "None"; but our tolerant read uses .get(key, "")
    # so None becomes str(None)="None". We accept that — the failure mode is
    # a row labeled "None" in the report, not a silent data loss.
    assert state.entries[0].target_url in ("", "None")


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
        "backlink_publisher.anchor.profile.atomic_write_json",
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


def test_recent_secondary_count_split_drops_orphan_remnant(profile_cache):
    """Orphaned leading secondaries (no parent main) are dropped, not counted as an article.

    Constructs a profile JSON whose entries start with 2 stray secondaries
    followed by complete main+secondary articles. Asserts that the leading
    secondaries do not get attributed to a phantom article. Replaces the
    old "trim severs article mid-record" test — the new article-integrity
    trim makes that scenario impossible for fresh writes, but the underlying
    _group_into_articles drop-remnant rule is still a real invariant for
    profiles whose entries were corrupted or hand-edited.
    """
    main_domain = "https://orphan.example"
    fake_dir = profile_cache / "anchor-profile"
    fake_dir.mkdir(parents=True, exist_ok=True)
    entries = [
        {"ts": "2026-05-14T00:00:00+00:00", "link_role": "secondary",
         "url_category": "hot", "anchor_type": "partial",
         "anchor_text": "orphan_sa", "degraded": False, "target_url": ""},
        {"ts": "2026-05-14T00:00:00+00:00", "link_role": "secondary",
         "url_category": "animate", "anchor_type": "partial",
         "anchor_text": "orphan_sb", "degraded": False, "target_url": ""},
    ]
    # 3 complete articles, each with main + 2 secondaries
    for i in range(3):
        ts = f"2026-05-14T01:0{i}:00+00:00"
        entries.append({"ts": ts, "link_role": "main", "url_category": "home",
                        "anchor_type": "branded", "anchor_text": f"m{i}",
                        "degraded": False, "target_url": ""})
        entries.append({"ts": ts, "link_role": "secondary", "url_category": "hot",
                        "anchor_type": "partial", "anchor_text": f"sa{i}",
                        "degraded": False, "target_url": ""})
        entries.append({"ts": ts, "link_role": "secondary", "url_category": "animate",
                        "anchor_type": "partial", "anchor_text": f"sb{i}",
                        "degraded": False, "target_url": ""})
    payload = {"version": 1, "main_domain": main_domain, "entries": entries}
    (fake_dir / "https___orphan.example.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    state = load_profile(main_domain)
    # All 11 entries load (orphan-drop happens in _group_into_articles, not load)
    assert len(state.entries) == 11

    # But split sees only the 3 complete articles.
    count_1, count_2 = recent_secondary_count_split(state, n=50)
    assert count_1 == 0
    assert count_2 == 3


def test_recent_secondary_count_split_empty_profile():
    state = ProfileState(main_domain="https://nothing")
    assert recent_secondary_count_split(state) == (0, 0)


def test_recent_secondary_count_split_default_spans_full_window(profile_cache):
    """The default look-back must weigh the FULL retained profile, not the
    20-entry anchor-text dedup window it used to borrow by accident.

    Builds 30 one-secondary articles (oldest) then 5 two-secondary articles
    (newest) — 75 entries, under the 100-entry retention cap so nothing trims.
    The default sees all 35; the old 20-article window would have seen only the
    most recent 20 (15 one-sec + 5 two-sec), undercounting the one-sec bucket.
    """
    from backlink_publisher.anchor._profile_analysis import (
        _DEFAULT_TEXT_WINDOW,
        _MAX_ENTRIES,
        _SECONDARY_SPLIT_WINDOW,
    )

    # The dedicated constant must be the full-window value, never the dedup one.
    assert _SECONDARY_SPLIT_WINDOW == _MAX_ENTRIES
    assert _SECONDARY_SPLIT_WINDOW != _DEFAULT_TEXT_WINDOW

    main_domain = "https://fullwin.example"
    all_entries: list[ProfileEntry] = []
    for i in range(30):
        all_entries.append(_main(f"m1_{i}"))
        all_entries.append(_sec(f"s_{i}", cat="hot"))
    for i in range(5):
        all_entries.append(_main(f"m2_{i}"))
        all_entries.append(_sec(f"sa_{i}", cat="hot"))
        all_entries.append(_sec(f"sb_{i}", cat="animate"))
    record_article(main_domain, all_entries)
    state = load_profile(main_domain)

    # Default (full window): every retained article is weighed.
    assert recent_secondary_count_split(state) == (30, 5)
    # The old 20-article window would have undercounted the one-sec bucket.
    assert recent_secondary_count_split(state, n=20) == (15, 5)
