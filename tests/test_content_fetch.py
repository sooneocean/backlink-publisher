"""Tests for ``content_fetch.verify_url_has_content`` + ``verify_urls_batch``.

Plan ref: docs/plans/2026-05-14-007-feat-url-content-fetch-gate-plan.md (Units 1, 2).

These tests mock ``urlopen`` at the consumer reference
(``backlink_publisher.content_fetch.urlopen``) per
``feedback_python-mock-datetime-patterns.md``. Every test calls
``reset_cache()`` first so module-level state doesn't bleed between
scenarios. The autouse ``disable_socket()`` fixture in ``tests/conftest.py``
ensures any path that escapes the mock would hard-fail rather than touch the
network.

The module-level ``pytestmark = pytest.mark.real_content_fetch`` opts every
test in this file out of the conftest ``_mock_content_fetch`` autouse
default-pass mock so the production ``verify_urls_batch`` /
``verify_url_has_content`` code paths actually run. Marker is registered in
``pyproject.toml [tool.pytest.ini_options] markers``.
"""

from __future__ import annotations

import socket
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from backlink_publisher.content.fetch import (
    HEAD_SCAN_BYTES,
    reset_cache,
    verify_url_has_content,
    verify_urls_batch,
)

pytestmark = pytest.mark.real_content_fetch


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_cache()
    yield
    reset_cache()


@pytest.fixture(autouse=True)
def _bypass_ssrf_check(monkeypatch, request):
    """Default-bypass the SSRF defence for every test that exercises the
    real ``_check_once`` path. The pytest-socket autouse fixture in
    ``tests/conftest.py`` blocks all sockets, so ``_check_url_for_ssrf``'s
    real DNS resolution would surface as ``dns_failure`` and turn every
    happy-path test into a network_error.

    Tests in ``TestSSRFDefense`` opt out by setting a marker so they
    exercise the real SSRF code path with mocked DNS instead.
    """
    if request.node.get_closest_marker("real_ssrf_check"):
        return
    monkeypatch.setattr(
        "backlink_publisher.content.fetch._check_url_for_ssrf",
        lambda _url: None,
    )


def _mock_response(status: int, body: bytes) -> MagicMock:
    """Build a urlopen() return value with .getcode() and .read()."""
    resp = MagicMock()
    resp.getcode.return_value = status
    resp.read.side_effect = lambda *args: body[: args[0]] if args else body
    resp.close = MagicMock()
    return resp


# ── happy paths ────────────────────────────────────────────────────────────


def test_happy_path_title_tag_returns_extracted_title():
    body = b"<html><head><title>Real Page</title></head><body>x</body></html>"
    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
        ok, reason, title = verify_url_has_content("https://example.com/")
    assert ok is True
    assert reason is None
    assert title == "Real Page"


def test_happy_path_og_title_preferred_over_title_tag():
    body = (
        b'<html><head>'
        b'<meta property="og:title" content="OG Title Wins">'
        b'<title>Bare Title Loses</title>'
        b"</head><body>x</body></html>"
    )
    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
        ok, _, title = verify_url_has_content("https://example.com/")
    assert ok is True
    assert title == "OG Title Wins"


def test_happy_path_og_title_empty_falls_back_to_title_tag():
    body = (
        b'<html><head>'
        b'<meta property="og:title" content="   ">'
        b"<title>Fallback Title</title>"
        b"</head><body>x</body></html>"
    )
    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
        ok, _, title = verify_url_has_content("https://example.com/")
    assert ok is True
    assert title == "Fallback Title"


# ── http_200_no_title ──────────────────────────────────────────────────────


def test_200_with_empty_title_tag_fails_gate():
    body = b"<html><head><title></title></head><body>x</body></html>"
    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
        ok, reason, title = verify_url_has_content("https://example.com/")
    assert ok is False
    assert reason == "http_200_no_title"
    assert title is None


def test_200_with_whitespace_only_title_fails_gate():
    body = b"<html><head><title>   \n\t  </title></head><body>x</body></html>"
    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
        ok, reason, _ = verify_url_has_content("https://example.com/")
    assert ok is False
    assert reason == "http_200_no_title"


def test_200_with_no_title_element_at_all_fails_gate():
    body = b"<html><body>just body content, no head/title</body></html>"
    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
        ok, reason, _ = verify_url_has_content("https://example.com/")
    assert ok is False
    assert reason == "http_200_no_title"


# ── head-window streaming (replaces former body_too_large path) ───────────


def test_oversized_body_no_title_resolves_as_http_200_no_title():
    """A giant body with no <head>/<title> is no longer rejected as
    body_too_large — the streaming reader caps at HEAD_SCAN_BYTES and the
    title extractor returns None on a body of just filler bytes.
    """
    body = b"x" * (HEAD_SCAN_BYTES * 4)
    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
        ok, reason, title = verify_url_has_content("https://example.com/")
    assert ok is False
    assert reason == "http_200_no_title"
    assert title is None


def test_head_window_stops_at_head_close_even_when_body_is_huge():
    """Title-bearing <head> followed by a giant body succeeds: the streamer
    stops at </head> and never reads the bloated body that used to trip
    body_too_large. Verifies the root fix for HTML pages that exceed the
    old 1MB body cap due to inlined CSS/JS or large nav structures.
    """
    head = b"<html><head><title>OK</title></head>"
    huge_body = b"<body>" + b"x" * (HEAD_SCAN_BYTES * 4) + b"</body></html>"
    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", return_value=_mock_response(200, head + huge_body)):
        ok, reason, title = verify_url_has_content("https://example.com/")
    assert ok is True
    assert reason is None
    assert title == "OK"


# ── http error paths ──────────────────────────────────────────────────────


def test_404_returned_as_http_404_no_retry():
    """4xx is structurally stable — no retry."""
    err = HTTPError("https://example.com/", 404, "Not Found", {}, BytesIO(b""))
    call_count = {"n": 0}

    def _raise(*args, **kwargs):
        call_count["n"] += 1
        raise err

    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_raise):
        ok, reason, _ = verify_url_has_content("https://example.com/missing")
    assert ok is False
    assert reason == "http_404"
    assert call_count["n"] == 1, "4xx should not retry"


def test_500_retried_and_classified_as_http_5xx():
    err = HTTPError("https://example.com/", 503, "Service Unavailable", {}, BytesIO(b""))
    call_count = {"n": 0}

    def _raise(*args, **kwargs):
        call_count["n"] += 1
        raise err

    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_raise):
        ok, reason, _ = verify_url_has_content("https://example.com/")
    assert ok is False
    assert reason == "http_5xx"
    assert call_count["n"] == 3, "5xx should retry (1 initial + 2 retries)"


def test_timeout_retried_and_classified():
    call_count = {"n": 0}

    def _raise(*args, **kwargs):
        call_count["n"] += 1
        raise socket.timeout("timed out")

    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_raise):
        ok, reason, _ = verify_url_has_content("https://example.com/")
    assert ok is False
    assert reason == "timeout"
    assert call_count["n"] == 3


def test_dns_failure_classified_as_network_error():
    err = URLError(socket.gaierror("Name or service not known"))

    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=err):
        ok, reason, _ = verify_url_has_content("https://no-such-host.example/")
    assert ok is False
    assert reason == "network_error"


def test_url_error_with_timeout_reason_classified_as_timeout():
    err = URLError(socket.timeout("read timed out"))
    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=err):
        ok, reason, _ = verify_url_has_content("https://example.com/")
    assert ok is False
    assert reason == "timeout"


# ── invalid URLs ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_url",
    [
        "",
        "not-a-url",
        "ftp://example.com/file",
        "/relative/path",
        "javascript:alert(1)",
    ],
)
def test_invalid_url_returns_invalid_url_without_network(bad_url):
    """Structurally bad URLs short-circuit before any HTTP attempt."""
    call_count = {"n": 0}

    def _track(*args, **kwargs):
        call_count["n"] += 1
        raise AssertionError("urlopen should not be called for invalid URLs")

    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_track):
        ok, reason, _ = verify_url_has_content(bad_url)
    assert ok is False
    assert reason == "invalid_url"
    assert call_count["n"] == 0


def test_invalid_url_none_handled_gracefully():
    ok, reason, _ = verify_url_has_content(None)  # type: ignore[arg-type]
    assert ok is False
    assert reason == "invalid_url"


@pytest.mark.parametrize("bad", ["http://[invalid", "http://[::1", "http://["])
def test_is_valid_http_url_malformed_ipv6_returns_false_not_raises(bad):
    """_is_valid_http_url must return False on malformed IPv6, never raise —
    its contract is a deterministic invalid verdict before any network attempt
    (Plan 2026-05-27-006 R4)."""
    from backlink_publisher.content.fetch import _is_valid_http_url
    assert _is_valid_http_url(bad) is False


# ── cache behaviour (Unit 2 lives in same module — basic cache cases) ──


def test_cache_hit_skips_second_fetch():
    body = b"<html><head><title>Cached</title></head><body>x</body></html>"
    call_count = {"n": 0}

    def _once(*args, **kwargs):
        call_count["n"] += 1
        return _mock_response(200, body)

    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_once):
        ok1, _, t1 = verify_url_has_content("https://example.com/cached")
        ok2, _, t2 = verify_url_has_content("https://example.com/cached")

    assert (ok1, t1) == (True, "Cached")
    assert (ok2, t2) == (True, "Cached")
    assert call_count["n"] == 1, "second call should hit cache, not network"


def test_cache_stores_failures_too():
    err = HTTPError("https://example.com/", 404, "Not Found", {}, BytesIO(b""))
    call_count = {"n": 0}

    def _raise(*args, **kwargs):
        call_count["n"] += 1
        raise err

    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_raise):
        verify_url_has_content("https://example.com/missing")
        verify_url_has_content("https://example.com/missing")
    assert call_count["n"] == 1, "failed result must be cached, not re-fetched"


def test_reset_cache_clears_state():
    body = b"<html><head><title>X</title></head><body>x</body></html>"
    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)) as mock:
        verify_url_has_content("https://example.com/")
        reset_cache()
        verify_url_has_content("https://example.com/")
    assert mock.call_count == 2, "after reset, second call must re-fetch"


# ── batch API (Unit 2) ─────────────────────────────────────────────────────


def test_batch_returns_per_url_results():
    body = b"<html><head><title>Title</title></head><body>x</body></html>"
    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
        results = verify_urls_batch(
            ["https://a.example/", "https://b.example/", "https://c.example/"]
        )
    assert set(results) == {"https://a.example/", "https://b.example/", "https://c.example/"}
    assert all(ok for ok, _, _ in results.values())


def test_batch_deduplicates_input():
    body = b"<html><head><title>X</title></head><body>x</body></html>"
    call_count = {"n": 0}

    def _once(*args, **kwargs):
        call_count["n"] += 1
        return _mock_response(200, body)

    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_once):
        results = verify_urls_batch(
            ["https://a.example/", "https://a.example/", "https://a.example/"]
        )
    assert len(results) == 1
    assert call_count["n"] == 1


def test_batch_empty_input_returns_empty_dict():
    results = verify_urls_batch([])
    assert results == {}


def test_batch_mixed_outcomes():
    """One URL succeeds, one 404s — both surface as their own results."""
    ok_body = b"<html><head><title>OK</title></head><body>x</body></html>"
    err = HTTPError("https://example.com/", 404, "Not Found", {}, BytesIO(b""))

    def _route(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "ok" in url:
            return _mock_response(200, ok_body)
        raise err

    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_route):
        results = verify_urls_batch(["https://ok.example/", "https://bad.example/"])
    assert results["https://ok.example/"][0] is True
    assert results["https://bad.example/"] == (False, "http_404", None)


def test_batch_hits_cache_on_repeat_call():
    body = b"<html><head><title>X</title></head><body>x</body></html>"
    call_count = {"n": 0}

    def _once(*args, **kwargs):
        call_count["n"] += 1
        return _mock_response(200, body)

    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_once):
        verify_urls_batch(["https://a.example/", "https://b.example/"])
        verify_urls_batch(["https://a.example/", "https://b.example/"])
    assert call_count["n"] == 2, "second batch hits cache for both URLs"


def test_batch_worker_exception_records_failure_not_crash():
    """A worker raising an unexpected exception still surfaces a result entry
    so the caller doesn't see a partial / missing dict.
    """
    def _explode(*args, **kwargs):
        raise RuntimeError("unexpected")

    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_explode):
        results = verify_urls_batch(["https://a.example/"])
    assert "https://a.example/" in results
    ok, reason, _ = results["https://a.example/"]
    assert ok is False
    assert reason == "network_error"


# ── canonical cache key (collapses equivalent URL representations) ─────────


def test_cache_key_collapses_utm_params():
    """Two URLs differing only by utm_* tracking params share one cache entry,
    so the second call is served from cache instead of re-fetching."""
    body = b"<html><head><title>X</title></head><body>x</body></html>"
    call_count = {"n": 0}

    def _once(*args, **kwargs):
        call_count["n"] += 1
        return _mock_response(200, body)

    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_once):
        verify_url_has_content("https://example.com/post?utm_source=newsletter")
        ok, _, title = verify_url_has_content("https://example.com/post")

    assert (ok, title) == (True, "X")
    assert call_count["n"] == 1, "utm-only variant must hit the canonical cache key"


def test_cache_key_collapses_fragment_and_trailing_slash():
    """Fragment and trailing slash are dropped by canonicalization, so these
    representations collapse to a single cache entry."""
    body = b"<html><head><title>X</title></head><body>x</body></html>"
    call_count = {"n": 0}

    def _once(*args, **kwargs):
        call_count["n"] += 1
        return _mock_response(200, body)

    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_once):
        verify_url_has_content("https://example.com/page/#section")
        verify_url_has_content("https://example.com/page")

    assert call_count["n"] == 1, "fragment/trailing-slash variants share a cache key"


def test_batch_collapses_equivalent_urls_to_single_fetch():
    """A batch containing equivalent representations fetches once but returns a
    result entry keyed by every original input URL."""
    body = b"<html><head><title>X</title></head><body>x</body></html>"
    call_count = {"n": 0}

    def _once(*args, **kwargs):
        call_count["n"] += 1
        return _mock_response(200, body)

    originals = [
        "https://a.example/p?utm_source=x",
        "https://a.example/p",
        "https://a.example/p#frag",
    ]
    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_once):
        results = verify_urls_batch(originals)

    assert set(results) == set(originals), "every original URL must get its own entry"
    assert all(results[u][0] is True for u in originals)
    assert call_count["n"] == 1, "equivalent URLs collapse to a single fetch"


def test_cache_key_falls_back_on_malformed_url_without_raising():
    """``_cache_key`` must never raise — malformed input that breaks ``urlsplit``
    falls back to the raw string so the fail-closed invalid_url path is reached
    instead of a bare ValueError crashing the gate."""
    from backlink_publisher.content.fetch import _cache_key

    assert _cache_key("http://[invalid") == "http://[invalid"
    # End-to-end: the malformed URL still resolves as invalid_url, no network.
    ok, reason, _ = verify_url_has_content("http://[invalid")
    assert (ok, reason) == (False, "invalid_url")


def test_concurrent_verify_writes_cache_without_corruption():
    """Many threads writing distinct cache entries concurrently must not raise
    or corrupt the shared dict — the ``_CACHE_LOCK`` serializes mutation."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from backlink_publisher.content.fetch import _CACHE

    body = b"<html><head><title>X</title></head><body>x</body></html>"
    urls = [f"https://host{i}.example/p" for i in range(48)]

    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(verify_url_has_content, u) for u in urls]
            results = [f.result() for f in as_completed(futures)]

    assert all(ok for ok, _, _ in results)
    assert len(_CACHE) == len(urls), "each distinct URL cached exactly once, no lost writes"


def test_batch_larger_than_cache_cap_keeps_true_results(monkeypatch):
    """A batch with more distinct URLs than the LRU cap must still report every
    succeeding URL as live. Regression: results were built by re-reading the
    cache *after* per-write eviction, so early successes surfaced as a spurious
    'network_error'."""
    monkeypatch.setattr("backlink_publisher.content.fetch._MAX_CACHE_ENTRIES", 8)
    body = b"<html><head><title>X</title></head><body>x</body></html>"
    urls = [f"https://host{i}.example/p" for i in range(20)]  # 20 > cap 8

    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
        results = verify_urls_batch(urls)

    assert set(results) == set(urls)
    bad = {u: r for u, r in results.items() if r != (True, None, "X")}
    assert not bad, f"evicted successes must not become network_error: {bad}"


@pytest.mark.parametrize("bad", [123, 1.5, True])
def test_non_str_url_returns_invalid_without_crash(bad):
    """A non-string *scalar* (the realistic accidental input — e.g. a numeric ID
    or bool read from a payload) must resolve as invalid_url (fail-closed), never
    crash. ``_cache_key`` runs before the type guard and ``canonicalize_url``
    would raise AttributeError/TypeError on non-str. (Unhashable inputs like
    ``list`` are out of contract and crash at the dict lookup, same as before.)"""
    ok, reason, _ = verify_url_has_content(bad)
    assert (ok, reason) == (False, "invalid_url")


def test_batch_with_non_str_element_does_not_crash_whole_batch():
    """One bad (non-str) element must not take down results for the valid URLs."""
    body = b"<html><head><title>OK</title></head><body>x</body></html>"
    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
        results = verify_urls_batch(["https://ok.example/", 123])
    assert results["https://ok.example/"][0] is True
    assert results[123] == (False, "invalid_url", None)


# ── redirect handling (urllib follows 301/302 automatically) ──────────────


def test_redirect_to_200_with_title_succeeds():
    """urlopen follows 301/302 by default; final response is what we check."""
    body = b"<html><head><title>Final Page</title></head><body>x</body></html>"
    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
        ok, _, title = verify_url_has_content("https://example.com/redirector")
    assert ok is True
    assert title == "Final Page"


def test_redirect_to_404_classified_as_404():
    err = HTTPError("https://example.com/final", 404, "Not Found", {}, BytesIO(b""))
    with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=err):
        ok, reason, _ = verify_url_has_content("https://example.com/redirect-to-404")
    assert ok is False
    assert reason == "http_404"


# ═════════════════════════════════════════════════════════════════════════════
# Plan 008 Unit 1: cache TTL + stats counters
# ═════════════════════════════════════════════════════════════════════════════


from backlink_publisher.content.fetch import (
    reset_stats,
    set_default_max_age,
    stats_snapshot,
)


@pytest.fixture(autouse=True)
def _clear_stats_and_ttl():
    """Reset module-level TTL + stats so each test is isolated."""
    reset_stats()
    set_default_max_age(None)
    yield
    reset_stats()
    set_default_max_age(None)


class TestCacheTTL:
    def test_default_no_ttl_keeps_cache_forever(self):
        body = b"<html><head><title>X</title></head><body>x</body></html>"
        call_count = {"n": 0}

        def _once(*args, **kwargs):
            call_count["n"] += 1
            return _mock_response(200, body)

        with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_once):
            verify_url_has_content("https://example.com/")
            import time as _time
            _time.sleep(0.05)
            verify_url_has_content("https://example.com/")
        # No TTL set → second call hits cache.
        assert call_count["n"] == 1

    def test_per_call_max_age_zero_forces_refetch(self):
        body = b"<html><head><title>X</title></head><body>x</body></html>"
        call_count = {"n": 0}

        def _each(*args, **kwargs):
            call_count["n"] += 1
            return _mock_response(200, body)

        with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_each):
            verify_url_has_content("https://example.com/")
            verify_url_has_content("https://example.com/", max_age_seconds=0)
        assert call_count["n"] == 2, "max_age_seconds=0 must force a fresh fetch"

    def test_module_default_ttl_expires_cache(self):
        """Set a tiny TTL, sleep past it, expect a re-fetch."""
        body = b"<html><head><title>X</title></head><body>x</body></html>"
        call_count = {"n": 0}

        def _each(*args, **kwargs):
            call_count["n"] += 1
            return _mock_response(200, body)

        set_default_max_age(0.05)  # 50 ms
        with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_each):
            verify_url_has_content("https://example.com/")
            import time as _time
            _time.sleep(0.1)  # past the 50 ms TTL
            verify_url_has_content("https://example.com/")
        assert call_count["n"] == 2

    def test_set_default_max_age_none_disables_ttl(self):
        """Set TTL, then clear it back to None — cache becomes immortal again."""
        body = b"<html><head><title>X</title></head><body>x</body></html>"
        call_count = {"n": 0}

        def _each(*args, **kwargs):
            call_count["n"] += 1
            return _mock_response(200, body)

        set_default_max_age(0.01)
        with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_each):
            verify_url_has_content("https://example.com/")
            set_default_max_age(None)
            import time as _time
            _time.sleep(0.05)
            verify_url_has_content("https://example.com/")
        assert call_count["n"] == 1

    def test_explicit_max_age_overrides_module_default(self):
        body = b"<html><head><title>X</title></head><body>x</body></html>"
        call_count = {"n": 0}

        def _each(*args, **kwargs):
            call_count["n"] += 1
            return _mock_response(200, body)

        set_default_max_age(60.0)  # generous module default
        with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_each):
            verify_url_has_content("https://example.com/")
            # Per-call override forces refetch despite the 60s module default.
            verify_url_has_content("https://example.com/", max_age_seconds=0)
        assert call_count["n"] == 2

    def test_batch_respects_module_ttl_for_expired_entries(self):
        """verify_urls_batch must re-fetch URLs whose cached entry has aged
        past the module default TTL, not just URLs absent from the cache."""
        body = b"<html><head><title>X</title></head><body>x</body></html>"
        call_count = {"n": 0}

        def _each(*args, **kwargs):
            call_count["n"] += 1
            return _mock_response(200, body)

        set_default_max_age(0.05)
        with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_each):
            verify_urls_batch(["https://a.example/"])
            import time as _time
            _time.sleep(0.1)
            verify_urls_batch(["https://a.example/"])
        assert call_count["n"] == 2


class TestStats:
    def test_stats_zero_at_start(self):
        snap = stats_snapshot()
        assert snap == {
            "cache_hits": 0,
            "cache_misses": 0,
            "fetches": 0,
            "total_latency_ms": 0,
            "reason_counts": {},
        }

    def test_stats_record_success_and_miss(self):
        body = b"<html><head><title>X</title></head><body>x</body></html>"
        with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
            verify_url_has_content("https://example.com/")
        snap = stats_snapshot()
        assert snap["cache_hits"] == 0
        assert snap["cache_misses"] == 1
        assert snap["fetches"] == 1
        assert snap["reason_counts"]["ok"] == 1

    def test_stats_record_cache_hit(self):
        body = b"<html><head><title>X</title></head><body>x</body></html>"
        with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
            verify_url_has_content("https://example.com/")
            verify_url_has_content("https://example.com/")  # cache hit
        snap = stats_snapshot()
        assert snap["cache_hits"] == 1
        assert snap["cache_misses"] == 1
        assert snap["fetches"] == 1
        assert snap["reason_counts"]["ok"] == 1

    def test_stats_record_failure_reasons(self):
        from urllib.error import HTTPError
        from io import BytesIO

        def _raise_404(*args, **kwargs):
            raise HTTPError("https://example.com/", 404, "NF", {}, BytesIO(b""))

        with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=_raise_404):
            verify_url_has_content("https://example.com/missing")
        snap = stats_snapshot()
        assert snap["reason_counts"].get("http_404") == 1
        assert "ok" not in snap["reason_counts"]

    def test_stats_records_latency_for_fetch_not_hit(self):
        body = b"<html><head><title>X</title></head><body>x</body></html>"
        with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
            verify_url_has_content("https://example.com/")
            verify_url_has_content("https://example.com/")  # cache hit, no latency
        snap = stats_snapshot()
        # Latency only counts the actual fetch, not the cache hit.
        # Just assert it's a non-negative integer (mock-driven so likely 0).
        assert snap["total_latency_ms"] >= 0
        assert isinstance(snap["total_latency_ms"], int)

    def test_stats_reset_clears_counters(self):
        body = b"<html><head><title>X</title></head><body>x</body></html>"
        with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
            verify_url_has_content("https://example.com/")
        reset_stats()
        snap = stats_snapshot()
        assert snap["fetches"] == 0
        assert snap["cache_misses"] == 0
        assert snap["reason_counts"] == {}

    def test_stats_snapshot_is_independent_copy(self):
        body = b"<html><head><title>X</title></head><body>x</body></html>"
        with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
            verify_url_has_content("https://example.com/")
        snap1 = stats_snapshot()
        # Mutate snapshot — must not affect module state nor a second snap.
        snap1["fetches"] = 999
        snap1["reason_counts"]["ok"] = 42
        snap2 = stats_snapshot()
        assert snap2["fetches"] == 1
        assert snap2["reason_counts"]["ok"] == 1

    def test_stats_invalid_url_counted_as_invalid_url(self):
        verify_url_has_content("not-a-url")
        snap = stats_snapshot()
        assert snap["reason_counts"].get("invalid_url") == 1
        assert snap["fetches"] == 0  # invalid URLs short-circuit without HTTP


# ═════════════════════════════════════════════════════════════════════════════
# SSRF defence (port of plan 005 Unit 1 into content_fetch directly)
# ═════════════════════════════════════════════════════════════════════════════


from urllib.error import URLError as _URLError
from urllib.request import Request


@pytest.mark.real_ssrf_check
class TestSSRFDefense:
    """Verify _check_url_for_ssrf + _SSRFSafeRedirectHandler reject
    requests targeting RFC1918 / loopback / link-local / cloud-metadata
    /  CGNAT / IPv6-tunnel destinations, plus per-redirect-hop
    re-checks and HTTPS→HTTP downgrade refusal."""

    @pytest.mark.parametrize("blocked_ip", [
        "127.0.0.1",
        "127.0.0.53",
        "10.0.0.5",
        "10.255.255.1",
        "172.16.5.10",
        "172.31.0.1",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "100.64.1.2",       # CGNAT
        "0.0.0.0",
    ])
    def test_literal_blocked_ip_in_url_rejected(self, blocked_ip):
        from backlink_publisher.content.fetch import _check_url_for_ssrf
        reason = _check_url_for_ssrf(f"http://{blocked_ip}/")
        assert reason is not None
        assert reason.startswith("blocked_ip:"), reason

    @pytest.mark.parametrize("safe_ip", [
        "8.8.8.8",
        "1.1.1.1",
        "151.101.1.140",
    ])
    def test_literal_public_ip_passes(self, safe_ip):
        from backlink_publisher.content.fetch import _check_url_for_ssrf
        assert _check_url_for_ssrf(f"http://{safe_ip}/") is None

    @pytest.mark.parametrize("ipv6", [
        "::1",
        "fe80::1234",
        "ff02::1",
    ])
    def test_ipv6_blocked_ranges_rejected(self, ipv6):
        from backlink_publisher.content.fetch import _check_url_for_ssrf
        reason = _check_url_for_ssrf(f"http://[{ipv6}]/")
        assert reason is not None
        assert reason.startswith("blocked_ip:")

    def test_hostname_resolving_to_blocked_ip_rejected(self, monkeypatch):
        """An attacker who registers a domain that resolves to 169.254.169.254
        (or whose CDN includes a stale 10.x record) must still be blocked.
        """
        from backlink_publisher.content.fetch import _check_url_for_ssrf

        def _fake_getaddrinfo(host, *args, **kwargs):
            return [(2, 1, 6, "", ("169.254.169.254", 0))]

        monkeypatch.setattr(
            "backlink_publisher.content.fetch.socket.getaddrinfo",
            _fake_getaddrinfo,
        )
        reason = _check_url_for_ssrf("https://evil.example.com/")
        assert reason is not None
        assert reason.startswith("blocked_ip:")

    def test_hostname_resolving_to_public_ip_passes(self, monkeypatch):
        from backlink_publisher.content.fetch import _check_url_for_ssrf

        def _fake_getaddrinfo(host, *args, **kwargs):
            return [(2, 1, 6, "", ("8.8.8.8", 0))]

        monkeypatch.setattr(
            "backlink_publisher.content.fetch.socket.getaddrinfo",
            _fake_getaddrinfo,
        )
        assert _check_url_for_ssrf("https://good.example.com/") is None

    def test_dns_failure_classified_as_network_error(self, monkeypatch):
        from backlink_publisher.content.fetch import _check_url_for_ssrf

        def _fake_getaddrinfo(host, *args, **kwargs):
            raise __import__("socket").gaierror("no such host")

        monkeypatch.setattr(
            "backlink_publisher.content.fetch.socket.getaddrinfo",
            _fake_getaddrinfo,
        )
        assert _check_url_for_ssrf("https://nx.example/") == "dns_failure"

    def test_verify_url_blocked_ssrf_returns_ssrf_blocked(self, monkeypatch):
        """End-to-end via verify_url_has_content: a literal-IP URL whose IP
        is in the block list short-circuits before any HTTP attempt and
        surfaces reason=ssrf_blocked."""
        # _SSRF_OPENER.open should NOT be invoked — block fires earlier.
        call_count = {"n": 0}

        def _track(*args, **kwargs):
            call_count["n"] += 1
            raise AssertionError("opener must not be reached")

        monkeypatch.setattr(
            "backlink_publisher.content.fetch._SSRF_OPENER.open", _track,
        )
        ok, reason, _ = verify_url_has_content("http://169.254.169.254/")
        assert ok is False
        assert reason == "ssrf_blocked"
        assert call_count["n"] == 0

    def test_verify_url_dns_failure_surfaces_network_error(self, monkeypatch):
        def _fake_getaddrinfo(host, *args, **kwargs):
            raise __import__("socket").gaierror("nope")

        monkeypatch.setattr(
            "backlink_publisher.content.fetch.socket.getaddrinfo",
            _fake_getaddrinfo,
        )
        ok, reason, _ = verify_url_has_content("https://nx.example/")
        assert ok is False
        assert reason == "network_error"

    def test_invalid_host_classified_as_invalid_url(self, monkeypatch):
        from backlink_publisher.content.fetch import _check_url_for_ssrf
        # urlparse with empty netloc → invalid_host. (Note: schemes other
        # than http/https are already rejected upstream as invalid_url, so
        # this path is mostly defence-in-depth.)
        assert _check_url_for_ssrf("http:///path") == "invalid_host"

    def test_redirect_handler_blocks_redirect_to_metadata_ip(self):
        """Construct the redirect handler directly and assert it raises
        URLError on a 302 → metadata-IP redirect target. Uses https→https
        so the downgrade check doesn't preempt the IP check."""
        from backlink_publisher._util.net_safety import _SSRFSafeRedirectHandler

        handler = _SSRFSafeRedirectHandler()
        req = Request("https://good.example.com/")
        with pytest.raises(_URLError) as excinfo:
            handler.redirect_request(
                req, None, 302, "Found", {}, "https://169.254.169.254/",
            )
        assert "ssrf_redirect" in str(excinfo.value)

    def test_redirect_handler_blocks_https_to_http_downgrade(self):
        from backlink_publisher._util.net_safety import _SSRFSafeRedirectHandler

        handler = _SSRFSafeRedirectHandler()
        req = Request("https://safe.example.com/")
        with pytest.raises(_URLError) as excinfo:
            handler.redirect_request(
                req, None, 302, "Found", {}, "http://safe.example.com/",
            )
        assert "ssrf_https_downgrade" in str(excinfo.value)

    def test_redirect_handler_allows_redirect_to_public_ip(self, monkeypatch):
        from backlink_publisher._util.net_safety import _SSRFSafeRedirectHandler

        def _fake_getaddrinfo(host, *args, **kwargs):
            return [(2, 1, 6, "", ("8.8.8.8", 0))]

        monkeypatch.setattr(
            "backlink_publisher._util.net_safety.socket.getaddrinfo",
            _fake_getaddrinfo,
        )
        handler = _SSRFSafeRedirectHandler()
        req = Request("https://from.example/")
        # Should not raise — falls through to base class. Base class would
        # build a redirect Request; we only care that no SSRF exception
        # was raised from our subclass.
        result = handler.redirect_request(
            req, None, 302, "Found", {"location": "https://to.example/"},
            "https://to.example/",
        )
        # Base class returns a Request object on success.
        assert result is not None

    # ── malformed-IPv6 never-raises (Plan 2026-05-27-006 Unit 4, R3b/R3c) ──

    @pytest.mark.parametrize("bad", ["http://[invalid", "http://[::1", "http://["])
    def test_check_url_for_ssrf_malformed_returns_invalid_host_not_raises(self, bad, monkeypatch):
        """The urllib SSRF gate must return 'invalid_host' (blocked) on malformed
        IPv6, never leak ValueError, and never reach DNS. _check_once calls this
        on untrusted URLs and is contractually never-raises."""
        from backlink_publisher.content.fetch import _check_url_for_ssrf

        def _boom(*a, **k):
            raise AssertionError("getaddrinfo must not be called for malformed input")

        monkeypatch.setattr(
            "backlink_publisher._util.net_safety.socket.getaddrinfo", _boom,
        )
        assert _check_url_for_ssrf(bad) == "invalid_host"

    @pytest.mark.parametrize("bad", ["http://[invalid", "http://[::1"])
    def test_verify_url_malformed_ipv6_returns_invalid_without_network(self, bad, monkeypatch):
        """End-to-end: a malformed-IPv6 URL short-circuits to invalid_url before
        any HTTP attempt (fail-closed, never crashes the fetch gate)."""
        def _track(*a, **k):
            raise AssertionError("opener must not be reached for malformed input")

        monkeypatch.setattr(
            "backlink_publisher.content.fetch._SSRF_OPENER.open", _track,
        )
        ok, reason, _ = verify_url_has_content(bad)
        assert ok is False
        assert reason == "invalid_url"

    @pytest.mark.parametrize("bad", ["http://[invalid", "http://[::1", "http://["])
    def test_redirect_handler_malformed_location_blocks_not_raises(self, bad, monkeypatch):
        """A malformed (server-controlled) Location must be a blocked redirect
        (URLError), never a bare ValueError, never followed, never reaching DNS."""
        from backlink_publisher._util.net_safety import _SSRFSafeRedirectHandler

        def _boom(*a, **k):
            raise AssertionError("getaddrinfo must not be called for malformed redirect")

        monkeypatch.setattr(
            "backlink_publisher._util.net_safety.socket.getaddrinfo", _boom,
        )
        handler = _SSRFSafeRedirectHandler()
        req = Request("https://from.example/")
        with pytest.raises(_URLError):
            handler.redirect_request(req, None, 302, "Found", {}, bad)
        # Note: req.full_url itself can never be malformed here — urllib's
        # Request(...) raises at construction on a malformed URL, and the
        # original URL already passed _check_url_for_ssrf. So only `newurl`
        # (the raw server Location string) is a real malformed-input vector.


# ═════════════════════════════════════════════════════════════════════════════
# Soft-404 title detection
# ═════════════════════════════════════════════════════════════════════════════


class TestSoftFourOhFour:
    """Sites that serve HTTP 200 + a "Page Not Found"-style title pass the
    plan-007 gate because the gate only checked HTTP status + non-empty
    title. This class verifies the soft-404 title-pattern blocker catches
    them without trashing legitimate titles that happen to contain a
    blocked phrase mid-string."""

    @pytest.mark.parametrize("bad_title", [
        "404",
        "404 Not Found",
        "404 - Site Name",
        "Page Not Found",
        "Page Not Found | My Site",
        "page not found - awesome site",
        "Not Found",
        "Page does not exist",
        "Error 404",
        "Error 404 — taiwanmanga",
        "This page can't be found",
        "This page cannot be found - Bingo",
        "This page could not be found",
        # Chinese
        "页面不存在",
        "页面不存在 - 51漫画",
        "页面未找到",
        "找不到页面",
        "頁面不存在",
        "頁面未找到",
        "找不到頁面",
        "404错误",
        "404 错误",
        # Japanese
        "ページが見つかりません",
        "お探しのページは見つかりません",
        # Russian
        "Страница не найдена",
    ])
    def test_soft_404_title_pattern_caught(self, bad_title):
        from backlink_publisher.content.fetch import _is_soft_404_title
        assert _is_soft_404_title(bad_title) is True, (
            f"expected {bad_title!r} to trip the soft-404 guard"
        )

    @pytest.mark.parametrize("good_title", [
        # Mid-string occurrence — must NOT match
        "What's Not Found in the Manuscript",
        "Apartment 404: A Survival Story",
        "Lessons from page 404 of the codex",
        "How to make your site's 404 page useful",
        # Articles whose title mentions "404" but isn't itself a 404 page
        "404 Apartments Review",
        # Real titles
        "Best Laptops 2026 — comprehensive buying guide",
        "51漫画 - 成人ACG漫画 / 同人本免费在线阅读",
        "ACG资源",
        "OG Title Wins",
    ])
    def test_legitimate_title_passes(self, good_title):
        from backlink_publisher.content.fetch import _is_soft_404_title
        assert _is_soft_404_title(good_title) is False, (
            f"false positive: {good_title!r} should NOT trip the guard"
        )

    def test_empty_title_returns_false(self):
        from backlink_publisher.content.fetch import _is_soft_404_title
        assert _is_soft_404_title("") is False
        assert _is_soft_404_title("   ") is False

    def test_verify_url_soft_404_returns_distinct_reason(self):
        body = (
            b"<html><head><title>Page Not Found - Example</title></head>"
            b"<body>404 stub</body></html>"
        )
        with patch(
            "backlink_publisher.content.fetch._SSRF_OPENER.open",
            return_value=_mock_response(200, body),
        ):
            ok, reason, title = verify_url_has_content("https://example.com/oops")
        assert ok is False
        assert reason == "soft_404_title", (
            "expected soft_404_title reason, not http_200_no_title"
        )
        assert title is None

    def test_stats_records_soft_404_reason(self):
        reset_stats()
        body = b"<html><head><title>404</title></head><body>nope</body></html>"
        with patch(
            "backlink_publisher.content.fetch._SSRF_OPENER.open",
            return_value=_mock_response(200, body),
        ):
            verify_url_has_content("https://example.com/missing")
        snap = stats_snapshot()
        assert snap["reason_counts"].get("soft_404_title") == 1


# ── Unit 1 (autoderive v1): timeout/redirect kwargs + body_too_small ──────

from backlink_publisher._util.net_safety import _make_ssrf_opener
from urllib.request import OpenerDirector


class TestVerifyKwargs:
    """``verify_url_has_content`` accepts ``timeout_seconds`` / ``max_redirects``
    without breaking back-compat. None preserves the historical defaults
    (10s timeout / 10 max redirects).
    """

    def test_explicit_kwargs_accepted_and_succeed(self):
        body = b"<html><head><title>OK</title></head></html>"
        fake_opener = MagicMock()
        fake_opener.open.return_value = _mock_response(200, body)
        with patch(
            "backlink_publisher.content.fetch._make_ssrf_opener",
            return_value=fake_opener,
        ) as factory:
            ok, reason, title = verify_url_has_content(
                "https://example.com/",
                timeout_seconds=5,
                max_redirects=3,
            )
        assert ok is True
        assert reason is None
        assert title == "OK"
        # Custom max_redirects routed through the factory with the right cap.
        factory.assert_called_once_with(3)

    def test_default_kwargs_preserve_unchanged_behavior(self):
        """``timeout_seconds=None`` and ``max_redirects=None`` (or omitted)
        keep the legacy behaviour — same call shape as before this Unit.
        """
        body = b"<html><head><title>Default</title></head></html>"
        with patch(
            "backlink_publisher.content.fetch._SSRF_OPENER.open",
            return_value=_mock_response(200, body),
        ) as mocked:
            ok, _, title = verify_url_has_content(
                "https://example.com/",
                timeout_seconds=None,
                max_redirects=None,
            )
        assert ok is True
        assert title == "Default"
        # Confirm the shared opener was used (max_redirects=None branch).
        assert mocked.called

    def test_custom_timeout_threaded_to_opener(self):
        """Custom ``timeout_seconds`` ends up in the ``timeout=`` kwarg
        passed to opener.open — confirms thread-through.
        """
        body = b"<html><head><title>T</title></head></html>"
        with patch(
            "backlink_publisher.content.fetch._SSRF_OPENER.open",
            return_value=_mock_response(200, body),
        ) as mocked:
            verify_url_has_content("https://example.com/", timeout_seconds=2.5)
        # Inspect the timeout kwarg on the actual open() call.
        _, kwargs = mocked.call_args
        assert kwargs.get("timeout") == 2.5


class TestBodyTooSmall:
    """``body_too_small`` fires only when ALL three conditions hold:
    HTTP 200, no ``</head>`` in body, no ``<title>`` extracted, AND
    total bytes < 2048. Legitimate short pages with a title still pass.
    """

    def test_200_short_body_no_title_returns_body_too_small(self):
        body = b"x" * 1024  # no </head>, no <title>, well under 2048
        # Streaming-accurate mock: read() drains a BytesIO so subsequent
        # reads return b"" — needed because the deduper otherwise yields
        # the same chunk forever and inflates buf past 2048.
        resp = MagicMock()
        resp.getcode.return_value = 200
        buf = BytesIO(body)
        resp.read.side_effect = lambda *args: buf.read(*args)
        resp.close = MagicMock()
        with patch(
            "backlink_publisher.content.fetch._SSRF_OPENER.open",
            return_value=resp,
        ):
            ok, reason, title = verify_url_has_content("https://example.com/")
        assert ok is False
        assert reason == "body_too_small"
        assert title is None

    def test_200_short_body_with_title_still_passes(self):
        """Legitimate brevity — 60-byte page with a valid <title> succeeds."""
        body = b"<html><head><title>X</title></head><body></body></html>"
        assert len(body) < 2048
        with patch(
            "backlink_publisher.content.fetch._SSRF_OPENER.open",
            return_value=_mock_response(200, body),
        ):
            ok, reason, title = verify_url_has_content("https://example.com/")
        assert ok is True
        assert reason is None
        assert title == "X"

    def test_200_large_body_no_title_remains_http_200_no_title(self):
        """Large body (>=2048) without title → unchanged ``http_200_no_title``."""
        body = b"<html><body>" + b"x" * 4096 + b"</body></html>"
        with patch(
            "backlink_publisher.content.fetch._SSRF_OPENER.open",
            return_value=_mock_response(200, body),
        ):
            ok, reason, _ = verify_url_has_content("https://example.com/")
        assert ok is False
        assert reason == "http_200_no_title"

    def test_200_short_body_with_head_close_but_no_title_is_not_too_small(self):
        """If ``</head>`` parsed but no title → unchanged ``http_200_no_title``,
        NOT ``body_too_small``. body_too_small is the tighter subset.
        """
        body = b"<html><head></head><body>x</body></html>"
        assert len(body) < 2048
        assert b"</head>" in body
        with patch(
            "backlink_publisher.content.fetch._SSRF_OPENER.open",
            return_value=_mock_response(200, body),
        ):
            ok, reason, _ = verify_url_has_content("https://example.com/")
        assert ok is False
        assert reason == "http_200_no_title"


class TestMakeSSRFOpener:
    def test_factory_default_is_10_redirects(self):
        opener = _make_ssrf_opener()
        assert isinstance(opener, OpenerDirector)
        # Find the redirect handler on the opener and inspect its cap.
        handlers = [h for h in opener.handlers if hasattr(h, "max_redirections")]
        assert handlers, "expected an SSRF redirect handler on the opener"
        assert any(h.max_redirections == 10 for h in handlers)

    def test_factory_custom_cap(self):
        opener = _make_ssrf_opener(3)
        assert isinstance(opener, OpenerDirector)
        handlers = [h for h in opener.handlers if hasattr(h, "max_redirections")]
        assert any(h.max_redirections == 3 for h in handlers)

    def test_factory_returns_fresh_instances(self):
        a = _make_ssrf_opener(2)
        b = _make_ssrf_opener(5)
        assert a is not b
        a_caps = [h.max_redirections for h in a.handlers if hasattr(h, "max_redirections")]
        b_caps = [h.max_redirections for h in b.handlers if hasattr(h, "max_redirections")]
        assert 2 in a_caps and 5 in b_caps


# ── Plan 2026-05-21-005: non-ASCII URLs must not crash content/fetch ──────────


class TestNonAsciiUrlFetch:
    """Regression: _check_once passes raw URLs to urllib.request.Request()
    which crashes with 'ascii' codec can't encode characters when the URL
    carries non-ASCII bytes (CJK list_url or work_url). Plan 2026-05-21-005.
    """

    def test_cjk_url_does_not_crash_check_once(self):
        """CJK path in list_url/work_url goes through normalize_url_for_fetch
        before reaching urllib; opener sees only ASCII bytes."""
        body = b"<html><head><title>Content</title></head><body>x</body></html>"
        captured: list[str] = []

        def mock_open(req, **kw):
            captured.append(req.full_url)
            return _mock_response(200, body)

        with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=mock_open):
            ok, reason, _ = verify_url_has_content("https://example.com/한글-목록/")

        assert ok is True
        assert len(captured) == 1
        captured[0].encode("ascii")  # would raise if non-ASCII slipped through
        assert "%" in captured[0]

    def test_ascii_url_passes_through_unchanged(self):
        body = b"<html><head><title>T</title></head><body>x</body></html>"
        captured: list[str] = []

        def mock_open(req, **kw):
            captured.append(req.full_url)
            return _mock_response(200, body)

        with patch("backlink_publisher.content.fetch._SSRF_OPENER.open", side_effect=mock_open):
            verify_url_has_content("https://example.com/path?q=1")

        assert captured == ["https://example.com/path?q=1"]
