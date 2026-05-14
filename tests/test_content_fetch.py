"""Tests for ``content_fetch.verify_url_has_content`` + ``verify_urls_batch``.

Plan ref: docs/plans/2026-05-14-007-feat-url-content-fetch-gate-plan.md (Units 1, 2).

These tests mock ``urlopen`` at the consumer reference
(``backlink_publisher.content_fetch.urlopen``) per
``feedback_python-mock-datetime-patterns.md``. Every test calls
``reset_cache()`` first so module-level state doesn't bleed between
scenarios. The autouse ``disable_socket()`` fixture in ``tests/conftest.py``
ensures any path that escapes the mock would hard-fail rather than touch the
network.
"""

from __future__ import annotations

import socket
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from backlink_publisher.content_fetch import (
    MAX_BODY_BYTES,
    reset_cache,
    verify_url_has_content,
    verify_urls_batch,
)


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
        "backlink_publisher.content_fetch._check_url_for_ssrf",
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
    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
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
    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
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
    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
        ok, _, title = verify_url_has_content("https://example.com/")
    assert ok is True
    assert title == "Fallback Title"


# ── http_200_no_title ──────────────────────────────────────────────────────


def test_200_with_empty_title_tag_fails_gate():
    body = b"<html><head><title></title></head><body>x</body></html>"
    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
        ok, reason, title = verify_url_has_content("https://example.com/")
    assert ok is False
    assert reason == "http_200_no_title"
    assert title is None


def test_200_with_whitespace_only_title_fails_gate():
    body = b"<html><head><title>   \n\t  </title></head><body>x</body></html>"
    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
        ok, reason, _ = verify_url_has_content("https://example.com/")
    assert ok is False
    assert reason == "http_200_no_title"


def test_200_with_no_title_element_at_all_fails_gate():
    body = b"<html><body>just body content, no head/title</body></html>"
    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
        ok, reason, _ = verify_url_has_content("https://example.com/")
    assert ok is False
    assert reason == "http_200_no_title"


# ── body_too_large ─────────────────────────────────────────────────────────


def test_oversized_body_rejected():
    body = b"x" * (MAX_BODY_BYTES + 100)
    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
        ok, reason, title = verify_url_has_content("https://example.com/")
    assert ok is False
    assert reason == "body_too_large"
    assert title is None


# ── http error paths ──────────────────────────────────────────────────────


def test_404_returned_as_http_404_no_retry():
    """4xx is structurally stable — no retry."""
    err = HTTPError("https://example.com/", 404, "Not Found", {}, BytesIO(b""))
    call_count = {"n": 0}

    def _raise(*args, **kwargs):
        call_count["n"] += 1
        raise err

    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=_raise):
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

    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=_raise):
        ok, reason, _ = verify_url_has_content("https://example.com/")
    assert ok is False
    assert reason == "http_5xx"
    assert call_count["n"] == 3, "5xx should retry (1 initial + 2 retries)"


def test_timeout_retried_and_classified():
    call_count = {"n": 0}

    def _raise(*args, **kwargs):
        call_count["n"] += 1
        raise socket.timeout("timed out")

    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=_raise):
        ok, reason, _ = verify_url_has_content("https://example.com/")
    assert ok is False
    assert reason == "timeout"
    assert call_count["n"] == 3


def test_dns_failure_classified_as_network_error():
    err = URLError(socket.gaierror("Name or service not known"))

    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=err):
        ok, reason, _ = verify_url_has_content("https://no-such-host.example/")
    assert ok is False
    assert reason == "network_error"


def test_url_error_with_timeout_reason_classified_as_timeout():
    err = URLError(socket.timeout("read timed out"))
    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=err):
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

    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=_track):
        ok, reason, _ = verify_url_has_content(bad_url)
    assert ok is False
    assert reason == "invalid_url"
    assert call_count["n"] == 0


def test_invalid_url_none_handled_gracefully():
    ok, reason, _ = verify_url_has_content(None)  # type: ignore[arg-type]
    assert ok is False
    assert reason == "invalid_url"


# ── cache behaviour (Unit 2 lives in same module — basic cache cases) ──


def test_cache_hit_skips_second_fetch():
    body = b"<html><head><title>Cached</title></head><body>x</body></html>"
    call_count = {"n": 0}

    def _once(*args, **kwargs):
        call_count["n"] += 1
        return _mock_response(200, body)

    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=_once):
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

    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=_raise):
        verify_url_has_content("https://example.com/missing")
        verify_url_has_content("https://example.com/missing")
    assert call_count["n"] == 1, "failed result must be cached, not re-fetched"


def test_reset_cache_clears_state():
    body = b"<html><head><title>X</title></head><body>x</body></html>"
    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)) as mock:
        verify_url_has_content("https://example.com/")
        reset_cache()
        verify_url_has_content("https://example.com/")
    assert mock.call_count == 2, "after reset, second call must re-fetch"


# ── batch API (Unit 2) ─────────────────────────────────────────────────────


def test_batch_returns_per_url_results():
    body = b"<html><head><title>Title</title></head><body>x</body></html>"
    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
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

    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=_once):
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

    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=_route):
        results = verify_urls_batch(["https://ok.example/", "https://bad.example/"])
    assert results["https://ok.example/"][0] is True
    assert results["https://bad.example/"] == (False, "http_404", None)


def test_batch_hits_cache_on_repeat_call():
    body = b"<html><head><title>X</title></head><body>x</body></html>"
    call_count = {"n": 0}

    def _once(*args, **kwargs):
        call_count["n"] += 1
        return _mock_response(200, body)

    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=_once):
        verify_urls_batch(["https://a.example/", "https://b.example/"])
        verify_urls_batch(["https://a.example/", "https://b.example/"])
    assert call_count["n"] == 2, "second batch hits cache for both URLs"


def test_batch_worker_exception_records_failure_not_crash():
    """A worker raising an unexpected exception still surfaces a result entry
    so the caller doesn't see a partial / missing dict.
    """
    def _explode(*args, **kwargs):
        raise RuntimeError("unexpected")

    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=_explode):
        results = verify_urls_batch(["https://a.example/"])
    assert "https://a.example/" in results
    ok, reason, _ = results["https://a.example/"]
    assert ok is False
    assert reason == "network_error"


# ── redirect handling (urllib follows 301/302 automatically) ──────────────


def test_redirect_to_200_with_title_succeeds():
    """urlopen follows 301/302 by default; final response is what we check."""
    body = b"<html><head><title>Final Page</title></head><body>x</body></html>"
    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
        ok, _, title = verify_url_has_content("https://example.com/redirector")
    assert ok is True
    assert title == "Final Page"


def test_redirect_to_404_classified_as_404():
    err = HTTPError("https://example.com/final", 404, "Not Found", {}, BytesIO(b""))
    with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=err):
        ok, reason, _ = verify_url_has_content("https://example.com/redirect-to-404")
    assert ok is False
    assert reason == "http_404"


# ═════════════════════════════════════════════════════════════════════════════
# Plan 008 Unit 1: cache TTL + stats counters
# ═════════════════════════════════════════════════════════════════════════════


from backlink_publisher.content_fetch import (
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

        with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=_once):
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

        with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=_each):
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
        with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=_each):
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
        with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=_each):
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
        with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=_each):
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
        with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=_each):
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
        with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
            verify_url_has_content("https://example.com/")
        snap = stats_snapshot()
        assert snap["cache_hits"] == 0
        assert snap["cache_misses"] == 1
        assert snap["fetches"] == 1
        assert snap["reason_counts"]["ok"] == 1

    def test_stats_record_cache_hit(self):
        body = b"<html><head><title>X</title></head><body>x</body></html>"
        with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
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

        with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", side_effect=_raise_404):
            verify_url_has_content("https://example.com/missing")
        snap = stats_snapshot()
        assert snap["reason_counts"].get("http_404") == 1
        assert "ok" not in snap["reason_counts"]

    def test_stats_records_latency_for_fetch_not_hit(self):
        body = b"<html><head><title>X</title></head><body>x</body></html>"
        with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
            verify_url_has_content("https://example.com/")
            verify_url_has_content("https://example.com/")  # cache hit, no latency
        snap = stats_snapshot()
        # Latency only counts the actual fetch, not the cache hit.
        # Just assert it's a non-negative integer (mock-driven so likely 0).
        assert snap["total_latency_ms"] >= 0
        assert isinstance(snap["total_latency_ms"], int)

    def test_stats_reset_clears_counters(self):
        body = b"<html><head><title>X</title></head><body>x</body></html>"
        with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
            verify_url_has_content("https://example.com/")
        reset_stats()
        snap = stats_snapshot()
        assert snap["fetches"] == 0
        assert snap["cache_misses"] == 0
        assert snap["reason_counts"] == {}

    def test_stats_snapshot_is_independent_copy(self):
        body = b"<html><head><title>X</title></head><body>x</body></html>"
        with patch("backlink_publisher.content_fetch._SSRF_OPENER.open", return_value=_mock_response(200, body)):
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
        from backlink_publisher.content_fetch import _check_url_for_ssrf
        reason = _check_url_for_ssrf(f"http://{blocked_ip}/")
        assert reason is not None
        assert reason.startswith("blocked_ip:"), reason

    @pytest.mark.parametrize("safe_ip", [
        "8.8.8.8",
        "1.1.1.1",
        "151.101.1.140",
    ])
    def test_literal_public_ip_passes(self, safe_ip):
        from backlink_publisher.content_fetch import _check_url_for_ssrf
        assert _check_url_for_ssrf(f"http://{safe_ip}/") is None

    @pytest.mark.parametrize("ipv6", [
        "::1",
        "fe80::1234",
        "ff02::1",
    ])
    def test_ipv6_blocked_ranges_rejected(self, ipv6):
        from backlink_publisher.content_fetch import _check_url_for_ssrf
        reason = _check_url_for_ssrf(f"http://[{ipv6}]/")
        assert reason is not None
        assert reason.startswith("blocked_ip:")

    def test_hostname_resolving_to_blocked_ip_rejected(self, monkeypatch):
        """An attacker who registers a domain that resolves to 169.254.169.254
        (or whose CDN includes a stale 10.x record) must still be blocked.
        """
        from backlink_publisher.content_fetch import _check_url_for_ssrf

        def _fake_getaddrinfo(host, *args, **kwargs):
            return [(2, 1, 6, "", ("169.254.169.254", 0))]

        monkeypatch.setattr(
            "backlink_publisher.content_fetch.socket.getaddrinfo",
            _fake_getaddrinfo,
        )
        reason = _check_url_for_ssrf("https://evil.example.com/")
        assert reason is not None
        assert reason.startswith("blocked_ip:")

    def test_hostname_resolving_to_public_ip_passes(self, monkeypatch):
        from backlink_publisher.content_fetch import _check_url_for_ssrf

        def _fake_getaddrinfo(host, *args, **kwargs):
            return [(2, 1, 6, "", ("8.8.8.8", 0))]

        monkeypatch.setattr(
            "backlink_publisher.content_fetch.socket.getaddrinfo",
            _fake_getaddrinfo,
        )
        assert _check_url_for_ssrf("https://good.example.com/") is None

    def test_dns_failure_classified_as_network_error(self, monkeypatch):
        from backlink_publisher.content_fetch import _check_url_for_ssrf

        def _fake_getaddrinfo(host, *args, **kwargs):
            raise __import__("socket").gaierror("no such host")

        monkeypatch.setattr(
            "backlink_publisher.content_fetch.socket.getaddrinfo",
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
            "backlink_publisher.content_fetch._SSRF_OPENER.open", _track,
        )
        ok, reason, _ = verify_url_has_content("http://169.254.169.254/")
        assert ok is False
        assert reason == "ssrf_blocked"
        assert call_count["n"] == 0

    def test_verify_url_dns_failure_surfaces_network_error(self, monkeypatch):
        def _fake_getaddrinfo(host, *args, **kwargs):
            raise __import__("socket").gaierror("nope")

        monkeypatch.setattr(
            "backlink_publisher.content_fetch.socket.getaddrinfo",
            _fake_getaddrinfo,
        )
        ok, reason, _ = verify_url_has_content("https://nx.example/")
        assert ok is False
        assert reason == "network_error"

    def test_invalid_host_classified_as_invalid_url(self, monkeypatch):
        from backlink_publisher.content_fetch import _check_url_for_ssrf
        # urlparse with empty netloc → invalid_host. (Note: schemes other
        # than http/https are already rejected upstream as invalid_url, so
        # this path is mostly defence-in-depth.)
        assert _check_url_for_ssrf("http:///path") == "invalid_host"

    def test_redirect_handler_blocks_redirect_to_metadata_ip(self):
        """Construct the redirect handler directly and assert it raises
        URLError on a 302 → metadata-IP redirect target. Uses https→https
        so the downgrade check doesn't preempt the IP check."""
        from backlink_publisher.content_fetch import _SSRFSafeRedirectHandler

        handler = _SSRFSafeRedirectHandler()
        req = Request("https://good.example.com/")
        with pytest.raises(_URLError) as excinfo:
            handler.redirect_request(
                req, None, 302, "Found", {}, "https://169.254.169.254/",
            )
        assert "ssrf_redirect" in str(excinfo.value)

    def test_redirect_handler_blocks_https_to_http_downgrade(self):
        from backlink_publisher.content_fetch import _SSRFSafeRedirectHandler

        handler = _SSRFSafeRedirectHandler()
        req = Request("https://safe.example.com/")
        with pytest.raises(_URLError) as excinfo:
            handler.redirect_request(
                req, None, 302, "Found", {}, "http://safe.example.com/",
            )
        assert "ssrf_https_downgrade" in str(excinfo.value)

    def test_redirect_handler_allows_redirect_to_public_ip(self, monkeypatch):
        from backlink_publisher.content_fetch import _SSRFSafeRedirectHandler

        def _fake_getaddrinfo(host, *args, **kwargs):
            return [(2, 1, 6, "", ("8.8.8.8", 0))]

        monkeypatch.setattr(
            "backlink_publisher.content_fetch.socket.getaddrinfo",
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
