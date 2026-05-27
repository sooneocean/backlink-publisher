"""Tests for the destination-page preflight fetch routine.

Plan: docs/plans/2026-05-26-008-feat-preflight-targets-verb-plan.md (Unit 1).

The routine reuses the SSRF-guarded urllib opener but exposes the rich facts
the ``preflight-targets`` verb needs (status, final URL, headers, DOM) that
``content.fetch._check_once``'s ``(ok, reason, title)`` tuple cannot carry.

Test seam: these tests patch this module's own ``_PREFLIGHT_OPENER.open`` and
``_check_url_for_ssrf`` references (per feedback_mock_patch_paths_after_extraction).
``_check_url_for_ssrf`` is stubbed so its real ``getaddrinfo`` does not trip
pytest-socket's socket block.
"""

from __future__ import annotations

import email.message
import ssl
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from backlink_publisher.content import _preflight_fetch as pf


def _headers(pairs: dict[str, str] | None = None) -> email.message.Message:
    """Build a case-insensitive header container like ``resp.info()`` returns."""
    msg = email.message.Message()
    for key, value in (pairs or {}).items():
        msg[key] = value
    return msg


def _mock_resp(
    *,
    status: int = 200,
    final_url: str = "https://example.com/page",
    body: bytes = b"<html><head><title>Hi</title></head><body><h1>Heading</h1></body></html>",
    headers: dict[str, str] | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.getcode.return_value = status
    resp.geturl.return_value = final_url
    resp.info.return_value = _headers(headers)
    # read(n) drains the body once then returns b"" (EOF), like a real stream.
    chunks = [body, b""]
    resp.read.side_effect = lambda n=-1: chunks.pop(0) if chunks else b""
    resp.close.return_value = None
    return resp


def _patch(open_return=None, open_side_effect=None, ssrf_side_effect=None, ssrf_return=None):
    """Patch the two network seams. ssrf default = safe (None)."""
    opener = patch.object(pf._PREFLIGHT_OPENER, "open")
    ssrf = patch.object(pf, "_check_url_for_ssrf")
    return opener, ssrf, open_return, open_side_effect, ssrf_side_effect, ssrf_return


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------

def test_healthy_200_title_h1_no_robots():
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open", return_value=_mock_resp()):
        facts = pf.fetch_target("https://example.com/page")
    assert facts.status == 200
    assert facts.has_title is True
    assert facts.has_h1 is True
    assert facts.noindex is False
    assert facts.soft404 is False
    assert facts.tls_unverified is False
    assert facts.reason is None


# --------------------------------------------------------------------------
# noindex (meta + header)
# --------------------------------------------------------------------------

def test_meta_robots_noindex():
    body = b'<html><head><meta name="robots" content="noindex,nofollow"><title>T</title></head><body><h1>H</h1></body></html>'
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open", return_value=_mock_resp(body=body)):
        facts = pf.fetch_target("https://example.com/")
    assert facts.noindex is True


def test_meta_robots_noindex_attr_order_reversed():
    body = b'<html><head><meta content="noindex" name="robots"><title>T</title></head><body><h1>H</h1></body></html>'
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open", return_value=_mock_resp(body=body)):
        facts = pf.fetch_target("https://example.com/")
    assert facts.noindex is True


def test_x_robots_tag_header_noindex():
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open",
                      return_value=_mock_resp(headers={"X-Robots-Tag": "googlebot: noindex"})):
        facts = pf.fetch_target("https://example.com/")
    assert facts.noindex is True
    assert facts.x_robots_tag is not None


def test_x_robots_tag_case_insensitive_and_truncated():
    long_val = "NOINDEX, " + "x" * 5000
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open",
                      return_value=_mock_resp(headers={"x-robots-tag": long_val})):
        facts = pf.fetch_target("https://example.com/")
    assert facts.noindex is True  # case-insensitive match
    assert len(facts.x_robots_tag) <= pf._X_ROBOTS_MAX_LEN  # truncated


# --------------------------------------------------------------------------
# soft-404
# --------------------------------------------------------------------------

def test_soft_404_title():
    body = b"<html><head><title>Page Not Found - Brand</title></head><body><h1>Oops</h1></body></html>"
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open", return_value=_mock_resp(body=body)):
        facts = pf.fetch_target("https://example.com/")
    assert facts.soft404 is True


# --------------------------------------------------------------------------
# Redirect facts
# --------------------------------------------------------------------------

def test_cross_host_redirect_sets_host_diff():
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open",
                      return_value=_mock_resp(final_url="https://elsewhere.example.net/x")):
        facts = pf.fetch_target("https://example.com/page")
    assert facts.redirected is True
    assert facts.host_diff is True
    assert facts.final_url == "https://elsewhere.example.net/x"


def test_same_host_redirect_no_host_diff():
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open",
                      return_value=_mock_resp(final_url="https://example.com/new")):
        facts = pf.fetch_target("https://example.com/old")
    assert facts.redirected is True
    assert facts.host_diff is False


# --------------------------------------------------------------------------
# h1 past the 256 KB head-window boundary (guards against reusing head reader)
# --------------------------------------------------------------------------

def test_h1_past_head_window_is_captured():
    pad = b"<!-- " + b"x" * 300_000 + b" -->"
    body = b"<html><head><title>T</title></head><body>" + pad + b"<h1>DeepHeading</h1></body></html>"
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open", return_value=_mock_resp(body=body)):
        facts = pf.fetch_target("https://example.com/")
    assert facts.has_h1 is True


# --------------------------------------------------------------------------
# Reachability taxonomy (404 vs 5xx vs timeout must be distinguishable)
# --------------------------------------------------------------------------

def test_http_404():
    err = HTTPError("https://example.com/", 404, "Not Found", _headers(), None)
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open", side_effect=err):
        facts = pf.fetch_target("https://example.com/")
    assert facts.status == 404
    assert facts.reason == "http_404"


def test_http_5xx():
    err = HTTPError("https://example.com/", 503, "Unavailable", _headers(), None)
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open", side_effect=err):
        facts = pf.fetch_target("https://example.com/")
    assert facts.status == 503
    assert facts.reason == "http_5xx"


def test_timeout_distinct_from_404():
    import socket
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open", side_effect=socket.timeout()):
        facts = pf.fetch_target("https://example.com/")
    assert facts.reason == "timeout"
    assert facts.status is None


# --------------------------------------------------------------------------
# SSRF (initial + post-redirect re-check), scheme gate
# --------------------------------------------------------------------------

def test_ssrf_blocked_initial_no_fetch():
    with patch.object(pf, "_check_url_for_ssrf", return_value="blocked_ip:10.0.0.0/8"), \
         patch.object(pf._PREFLIGHT_OPENER, "open") as mock_open:
        facts = pf.fetch_target("http://10.0.0.1/")
    assert facts.reason == "ssrf_blocked"
    mock_open.assert_not_called()


def test_ssrf_redirect_hop_blocked():
    err = URLError("ssrf_redirect:blocked_ip:10.0.0.0/8")
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open", side_effect=err):
        facts = pf.fetch_target("https://example.com/")
    assert facts.reason == "ssrf_blocked"


def test_post_redirect_recheck_downgrades_to_ssrf_blocked():
    # First call (initial host) safe; second call (final URL) blocked.
    with patch.object(pf, "_check_url_for_ssrf", side_effect=[None, "blocked_ip:169.254.0.0/16"]), \
         patch.object(pf._PREFLIGHT_OPENER, "open",
                      return_value=_mock_resp(final_url="http://169.254.169.254/latest")):
        facts = pf.fetch_target("https://example.com/page")
    assert facts.reason == "ssrf_blocked"


def test_scheme_gate_rejects_non_http():
    for bad in ("file:///etc/passwd", "ftp://host/x", "gopher://host"):
        with patch.object(pf._PREFLIGHT_OPENER, "open") as mock_open, \
             patch.object(pf, "_check_url_for_ssrf") as mock_ssrf:
            facts = pf.fetch_target(bad)
        assert facts.reason == "invalid_url", bad
        mock_open.assert_not_called()
        mock_ssrf.assert_not_called()  # scheme gate runs before the SSRF DNS check


# --------------------------------------------------------------------------
# TLS classification
# --------------------------------------------------------------------------

def test_tls_cert_failure_sets_tls_unverified():
    err = URLError(ssl.SSLCertVerificationError("bad cert"))
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open", side_effect=err):
        facts = pf.fetch_target("https://badcert.example.com/")
    assert facts.tls_unverified is True
    assert facts.reason == "tls_unverified"


# --------------------------------------------------------------------------
# Redirect cap (footgun guard): cap=5, and an exceeded chain is a distinct fact
# --------------------------------------------------------------------------

def test_redirect_cap_is_five():
    assert pf._MAX_REDIRECTS == 5  # survives apex->TLS->www->slash (4 hops)


def test_redirect_cap_exceeded_is_distinct_fact():
    # When the cap is exceeded, urllib raises HTTPError carrying the last 3xx.
    err = HTTPError("https://example.com/", 302, "Found", _headers(), None)
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open", side_effect=err):
        facts = pf.fetch_target("https://example.com/")
    assert facts.redirect_capped is True
    assert facts.reason == "redirect_capped"


# --------------------------------------------------------------------------
# Never raises; non-ASCII URL normalized before Request
# --------------------------------------------------------------------------

def test_routine_never_raises_on_unexpected_error():
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open", side_effect=RuntimeError("boom")):
        facts = pf.fetch_target("https://example.com/")
    assert facts.reason == "network_error"  # swallowed, not raised


def test_non_ascii_url_does_not_throw():
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open", return_value=_mock_resp()):
        # Korean handle + CJK slug — would crash raw urllib at request-line encoding.
        facts = pf.fetch_target("https://example.com/@한국/슬러그")
    assert facts.status == 200


# --------------------------------------------------------------------------
# Never-raises hardening: malformed IPv6 must not crash _check_url_for_ssrf
# (review finding — urlparse(url).hostname raises ValueError on "http://[invalid")
# --------------------------------------------------------------------------

def test_malformed_ipv6_initial_url_does_not_raise():
    # Real _check_url_for_ssrf (not patched) would raise ValueError on .hostname;
    # _safe_ssrf_check must swallow it. No socket opened (rejected pre-fetch).
    with patch.object(pf._PREFLIGHT_OPENER, "open") as mock_open:
        facts = pf.fetch_target("http://[invalid")
    assert facts.reason == "invalid_url"
    mock_open.assert_not_called()


def test_malformed_ipv6_final_url_does_not_raise():
    # Initial check safe; post-redirect re-check hits a malformed final URL whose
    # real SSRF check would raise — must be swallowed, fetch completes.
    def _ssrf(url):
        if "[bad" in url:
            raise ValueError("Invalid IPv6 URL")
        return None
    with patch.object(pf, "_check_url_for_ssrf", side_effect=_ssrf), \
         patch.object(pf._PREFLIGHT_OPENER, "open",
                      return_value=_mock_resp(final_url="http://[bad/x")):
        facts = pf.fetch_target("https://example.com/page")  # must not raise
    assert facts.status == 200  # completed; malformed final host left to opener guard


# --------------------------------------------------------------------------
# noindex token match, not substring (review finding: "noindexing" false positive)
# --------------------------------------------------------------------------

def test_x_robots_noindexing_is_not_noindex():
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open",
                      return_value=_mock_resp(headers={"X-Robots-Tag": "noindexing-strategy"})):
        facts = pf.fetch_target("https://example.com/")
    assert facts.noindex is False  # "noindexing" is not the "noindex" directive


def test_meta_content_all_noindex_is_noindex():
    body = b'<html><head><meta name="robots" content="all, noindex"><title>T</title></head><body><h1>H</h1></body></html>'
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open", return_value=_mock_resp(body=body)):
        facts = pf.fetch_target("https://example.com/")
    assert facts.noindex is True  # directive token present in a comma list


# --------------------------------------------------------------------------
# Empty / structureless body → has_title / has_h1 False (coverage gap)
# --------------------------------------------------------------------------

def test_empty_body_no_title_no_h1():
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open",
                      return_value=_mock_resp(body=b"<html><body></body></html>")):
        facts = pf.fetch_target("https://example.com/")
    assert facts.status == 200
    assert facts.has_title is False
    assert facts.has_h1 is False
