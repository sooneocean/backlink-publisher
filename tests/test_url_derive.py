"""Tests for ``_util.url_derive.derive_path_tiers``.

Plan ref: docs/plans/2026-05-20-002-feat-homepage-url-autoderive-v1-plan.md (Unit 1).

Pure-function tests — no network, no fixtures beyond stdlib.
"""

from __future__ import annotations

import pytest

from backlink_publisher._util.url_derive import derive_path_tiers, _CATEGORY_TOKEN


# ── path-depth dispatch ───────────────────────────────────────────────────


def test_depth_2_tail_with_digit_fills_all_tiers():
    """``/comic/6`` — tail has digit, NOT a category token → work=full URL,
    category truncates last segment.
    """
    out = derive_path_tiers("https://51acgs.com/comic/6")
    assert out["main"] == "https://51acgs.com"
    assert out["category"] == "https://51acgs.com/comic"
    assert out["work"] == "https://51acgs.com/comic/6"


def test_depth_1_main_and_category_no_work():
    """One segment → category set, work always None."""
    out = derive_path_tiers("https://51acgs.com/comic")
    assert out["main"] == "https://51acgs.com"
    assert out["category"] == "https://51acgs.com/comic"
    assert out["work"] is None


def test_depth_0_main_only():
    """Origin only → main set, others None."""
    out = derive_path_tiers("https://51acgs.com")
    assert out["main"] == "https://51acgs.com"
    assert out["category"] is None
    assert out["work"] is None


def test_depth_4_tail_with_digit_truncates_to_parent():
    """Deep path, tail has digit → category = path without last segment."""
    out = derive_path_tiers("https://51acgs.com/comic/genre/action/6")
    assert out["main"] == "https://51acgs.com"
    assert out["category"] == "https://51acgs.com/comic/genre/action"
    assert out["work"] == "https://51acgs.com/comic/genre/action/6"


def test_depth_1_pure_letter_about():
    """``/about`` 1 seg → main + category, work None."""
    out = derive_path_tiers("https://example.com/about")
    assert out["main"] == "https://example.com"
    assert out["category"] == "https://example.com/about"
    assert out["work"] is None


def test_medium_handle_with_digit_slug_truncates():
    """Tail ``title-abc123`` has digit → category = parent (``/@author``)."""
    out = derive_path_tiers("https://medium.com/@author/title-abc123")
    assert out["main"] == "https://medium.com"
    assert out["category"] == "https://medium.com/@author"
    assert out["work"] == "https://medium.com/@author/title-abc123"


def test_medium_handle_with_about_category_token_no_work():
    """Tail ``about`` is a pure-letter category token → no work URL."""
    out = derive_path_tiers("https://medium.com/@author/about")
    assert out["main"] == "https://medium.com"
    assert out["category"] == "https://medium.com/@author/about"
    assert out["work"] is None


def test_date_path_with_hyphenated_slug_is_work():
    """``/2024/11/20/post-slug`` — 4 segs, hyphenated tail → work URL.

    ``post-slug`` contains a hyphen, so it does NOT match the (letters-only)
    ``_CATEGORY_TOKEN`` regex. Hyphenated slugs are almost always article
    URLs in the wild, not category landing pages. Therefore category =
    parent path (``/2024/11/20``) and work = full URL.
    """
    out = derive_path_tiers("https://example.com/2024/11/20/post-slug")
    assert out["main"] == "https://example.com"
    assert out["category"] == "https://example.com/2024/11/20"
    assert out["work"] == "https://example.com/2024/11/20/post-slug"


def test_hyphenated_slug_at_depth_2_is_work():
    """``/blog/my-article`` — hyphenated tail at depth 2 → work URL."""
    out = derive_path_tiers("https://example.com/blog/my-article")
    assert out["main"] == "https://example.com"
    assert out["category"] == "https://example.com/blog"
    assert out["work"] == "https://example.com/blog/my-article"


def test_r2_normalization_drops_slash_query_fragment():
    """R2: trailing slash, query, and fragment stripped from category."""
    out = derive_path_tiers("https://x.com/foo/?q=1#frag")
    assert out["main"] == "https://x.com"
    assert out["category"] == "https://x.com/foo"
    assert out["work"] is None


# ── invalid input ─────────────────────────────────────────────────────────


def test_javascript_scheme_returns_all_none():
    assert derive_path_tiers("javascript:alert(1)") == {
        "main": None, "category": None, "work": None,
    }


def test_not_a_url_returns_all_none():
    assert derive_path_tiers("not-a-url") == {
        "main": None, "category": None, "work": None,
    }


def test_ftp_scheme_returns_all_none():
    assert derive_path_tiers("ftp://example.com/x") == {
        "main": None, "category": None, "work": None,
    }


def test_empty_string_returns_all_none():
    assert derive_path_tiers("") == {
        "main": None, "category": None, "work": None,
    }


def test_none_input_returns_all_none():
    assert derive_path_tiers(None) == {  # type: ignore[arg-type]
        "main": None, "category": None, "work": None,
    }


# ── _CATEGORY_TOKEN regex boundary ────────────────────────────────────────


def test_category_token_too_short_does_not_match():
    """``ab`` is 2 chars (< 3) → regex rejects."""
    assert _CATEGORY_TOKEN.match("ab") is None


def test_category_token_3_chars_matches():
    """``abc`` is exactly 3 chars (minimum) → regex accepts."""
    assert _CATEGORY_TOKEN.match("abc") is not None


def test_category_token_with_digit_does_not_match():
    """``abc123`` has digits → regex rejects."""
    assert _CATEGORY_TOKEN.match("abc123") is None


def test_category_token_too_long_does_not_match():
    """``abcdefghijklmnopqr`` is 18 chars (> 15) → regex rejects."""
    assert _CATEGORY_TOKEN.match("abcdefghijklmnopqr") is None


def test_category_token_with_hyphen_does_not_match():
    """Hyphenated slugs (``post-slug``, ``my-article``) → regex rejects."""
    assert _CATEGORY_TOKEN.match("post-slug") is None
    assert _CATEGORY_TOKEN.match("my-article") is None


def test_depth_1_too_short_tail_still_fills_category():
    """At depth 1, any tail shape fills category (no work decision at this
    depth). ``/ab`` rejected by regex but still set as category.
    """
    out = derive_path_tiers("https://example.com/ab")
    assert out["category"] == "https://example.com/ab"
    assert out["work"] is None


def test_depth_2_too_long_tail_fills_work():
    """At depth >=2, a tail that fails the regex → work URL gets populated."""
    out = derive_path_tiers("https://example.com/foo/abcdefghijklmnopqr")
    assert out["category"] == "https://example.com/foo"
    assert out["work"] == "https://example.com/foo/abcdefghijklmnopqr"


# ── R2 normalization details ──────────────────────────────────────────────


def test_http_scheme_forced_to_https():
    """``http://`` input → ``https://`` output (R2 scheme normalization)."""
    out = derive_path_tiers("http://example.com/about")
    assert out["main"] == "https://example.com"
    assert out["category"] == "https://example.com/about"


def test_www_subdomain_preserved():
    """Host preserved verbatim — ``www.`` is kept."""
    out = derive_path_tiers("https://www.example.com/blog")
    assert out["main"] == "https://www.example.com"
    assert out["category"] == "https://www.example.com/blog"
