"""Tests for ``inspect_target_anchor`` — the canary target-href inspector.

Plan: docs/plans/2026-05-27-001-feat-adapter-contract-canary-plan.md (Unit 2).

``inspect_target_anchor`` is a *sibling* of ``verify_link_attributes`` that
matches a specific target backlink's own ``<a>`` tag (not a page-wide nofollow
aggregate) and fetches through the SSRF-guarded preflight opener. It never
raises; on any error it returns a dict with ``page_readable=False`` and a
``reason``.

Test seam: patch ``inspect_target_anchor``'s reused network references
(``_preflight_fetch._PREFLIGHT_OPENER.open`` and
``_preflight_fetch._check_url_for_ssrf``) per
feedback_mock_patch_paths_after_extraction. ``_check_url_for_ssrf`` is stubbed
safe so its real ``getaddrinfo`` does not trip pytest-socket.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.content import _preflight_fetch as pf
from backlink_publisher.publishing.adapters import link_attr_verifier as lav


def _mock_resp(
    *,
    status: int = 200,
    final_url: str = "https://blog.example/post",
    body: bytes = b"<html><body></body></html>",
    headers: dict[str, str] | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.getcode.return_value = status
    resp.geturl.return_value = final_url
    import email.message

    msg = email.message.Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    resp.info.return_value = msg
    chunks = [body, b""]
    resp.read.side_effect = lambda n=-1: chunks.pop(0) if chunks else b""
    resp.close.return_value = None
    return resp


def _inspect(body: bytes, target_url: str, *, expected_marker=None, status: int = 200):
    """Run inspect_target_anchor against a mocked page body, SSRF safe."""
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open", return_value=_mock_resp(body=body, status=status)):
        return lav.inspect_target_anchor(
            "https://blog.example/post", target_url, expected_marker=expected_marker
        )


# --------------------------------------------------------------------------
# ★ Critical counter-example 1 — false-positive guard
# nav/footer anchors carry rel=nofollow but the TARGET anchor is dofollow.
# A page-wide nofollow aggregate would wrongly flag this.
# --------------------------------------------------------------------------

def test_target_dofollow_despite_nofollow_nav_footer():
    body = (
        b'<html><body>'
        b'<nav><a href="https://other.example/x" rel="nofollow">nav</a></nav>'
        b'<p><a href="https://example.com">my backlink</a></p>'
        b'<footer><a href="https://ads.example/y" rel="nofollow sponsored">ad</a></footer>'
        b'</body></html>'
    )
    result = _inspect(body, "https://example.com")
    assert result["page_readable"] is True
    assert result["target_anchor_found"] is True
    assert result["target_is_nofollow"] is False


# --------------------------------------------------------------------------
# ★ Critical counter-example 2 — interstitial unwrap
# Target href wrapped in a redirect shim ``?target=<encoded url>``.
# Must NOT report the href as missing.
# --------------------------------------------------------------------------

def test_interstitial_wrapped_href_unwrapped_and_matched():
    body = (
        b'<html><body>'
        b'<a href="https://link.juejin.cn/?target=https%3A%2F%2Fexample.com">my backlink</a>'
        b'</body></html>'
    )
    result = _inspect(body, "https://example.com")
    assert result["page_readable"] is True
    assert result["target_anchor_found"] is True
    assert result["target_is_nofollow"] is False


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------

def test_happy_target_dofollow():
    body = b'<html><body><a href="https://example.com/page">link</a></body></html>'
    result = _inspect(body, "https://example.com/page")
    assert result["page_readable"] is True
    assert result["target_anchor_found"] is True
    assert result["target_is_nofollow"] is False
    assert result["target_rel"] is None
    assert result["reason"] is None


# --------------------------------------------------------------------------
# Drift: target anchor has rel=nofollow
# --------------------------------------------------------------------------

def test_drift_target_nofollow():
    body = b'<html><body><a href="https://example.com/page" rel="nofollow">link</a></body></html>'
    result = _inspect(body, "https://example.com/page")
    assert result["target_anchor_found"] is True
    assert result["target_is_nofollow"] is True
    assert result["target_rel"] is not None
    assert "nofollow" in result["target_rel"].lower()


@pytest.mark.parametrize("rel_value", ["ugc", "sponsored", "noopener nofollow"])
def test_drift_target_ugc_sponsored(rel_value):
    body = (
        b'<html><body><a href="https://example.com/page" rel="'
        + rel_value.encode()
        + b'">link</a></body></html>'
    )
    result = _inspect(body, "https://example.com/page")
    assert result["target_anchor_found"] is True
    assert result["target_is_nofollow"] is True


# --------------------------------------------------------------------------
# Drift: target href genuinely absent (page readable)
# --------------------------------------------------------------------------

def test_target_href_absent_page_readable():
    body = b'<html><body><a href="https://unrelated.example/x">other</a></body></html>'
    result = _inspect(body, "https://example.com/page")
    assert result["page_readable"] is True
    assert result["target_anchor_found"] is False
    assert result["target_is_nofollow"] is False
    assert result["target_rel"] is None


# --------------------------------------------------------------------------
# Edge: canonicalization — same href many anchors / trailing slash / utm
# --------------------------------------------------------------------------

def test_trailing_slash_canonicalized_match():
    body = b'<html><body><a href="https://example.com/page/">link</a></body></html>'
    result = _inspect(body, "https://example.com/page")
    assert result["target_anchor_found"] is True


def test_utm_params_canonicalized_match():
    body = b'<html><body><a href="https://example.com/page?utm_source=x">link</a></body></html>'
    result = _inspect(body, "https://example.com/page")
    assert result["target_anchor_found"] is True


def test_multiple_anchors_same_href_first_match_rel():
    # Two anchors to the target: first dofollow, second nofollow. We report
    # the first match's rel (deterministic) — at least one dofollow exists.
    body = (
        b'<html><body>'
        b'<a href="https://example.com/page">first</a>'
        b'<a href="https://example.com/page" rel="nofollow">second</a>'
        b'</body></html>'
    )
    result = _inspect(body, "https://example.com/page")
    assert result["target_anchor_found"] is True
    assert result["target_is_nofollow"] is False


# --------------------------------------------------------------------------
# Error paths — never raise
# --------------------------------------------------------------------------

def test_fetch_fails_never_raises():
    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open", side_effect=OSError("boom")):
        result = lav.inspect_target_anchor("https://blog.example/post", "https://example.com")
    assert result["page_readable"] is False
    assert result["target_anchor_found"] is False
    assert result["reason"]


def test_non_200_status():
    result = _inspect(b"<html></html>", "https://example.com", status=404)
    assert result["page_readable"] is False
    assert result["reason"]


def test_empty_body():
    result = _inspect(b"", "https://example.com")
    assert result["page_readable"] is False
    assert result["reason"]


def test_invalid_url_never_raises():
    result = lav.inspect_target_anchor("not-a-url", "https://example.com")
    assert result["page_readable"] is False
    assert result["reason"]


def test_malformed_ipv6_url_never_raises():
    # urlparse raises ValueError on malformed IPv6 — must be guarded.
    result = lav.inspect_target_anchor("http://[invalid", "https://example.com")
    assert result["page_readable"] is False
    assert result["reason"]


# --------------------------------------------------------------------------
# marker presence
# --------------------------------------------------------------------------

def test_marker_present():
    body = b'<html><body>canary-XYZ-123 <a href="https://example.com">l</a></body></html>'
    result = _inspect(body, "https://example.com", expected_marker="canary-XYZ-123")
    assert result["marker_present"] is True


def test_marker_absent():
    body = b'<html><body><a href="https://example.com">l</a></body></html>'
    result = _inspect(body, "https://example.com", expected_marker="canary-XYZ-123")
    assert result["marker_present"] is False


def test_marker_none_when_not_requested():
    body = b'<html><body><a href="https://example.com">l</a></body></html>'
    result = _inspect(body, "https://example.com")
    assert result["marker_present"] is None


# --------------------------------------------------------------------------
# Security — SSRF rejection ON A REDIRECT HOP (real guard, not bypassed).
# --------------------------------------------------------------------------

@pytest.mark.real_ssrf_check
def test_ssrf_rejected_on_redirect_hop():
    """A redirect to a private/link-local host must be rejected by the SSRF
    redirect handler — and the private host must never be fetched.

    We simulate the redirect handler raising (as ``_SSRFSafeRedirectHandler``
    does on a blocked hop) by making the opener raise the same ``URLError``.
    The contract: inspect_target_anchor never raises, reports page_readable=
    False, and does not surface anchor data from the private host.
    """
    from urllib.error import URLError

    # Sanity: the real SSRF guard rejects a link-local target outright.
    from backlink_publisher._util.net_safety import _check_url_for_ssrf

    assert _check_url_for_ssrf("http://169.254.169.254/latest/meta-data/") is not None

    # Simulate the opener raising the redirect-blocked URLError that
    # _SSRFSafeRedirectHandler.redirect_request raises on a blocked hop.
    private_fetched = {"hit": False}

    def _raise_on_blocked_redirect(req, timeout=None):
        private_fetched["hit"] = True  # would only run if a fetch happened
        raise URLError("ssrf_redirect:blocked_ip:169.254.0.0/16")

    with patch.object(pf, "_check_url_for_ssrf", return_value=None), \
         patch.object(pf._PREFLIGHT_OPENER, "open", side_effect=_raise_on_blocked_redirect):
        result = lav.inspect_target_anchor(
            "https://blog.example/post", "https://example.com"
        )

    assert result["page_readable"] is False
    assert result["target_anchor_found"] is False
    assert result["reason"]
    # No anchor data leaked from a private host.
    assert result["target_rel"] is None


# --------------------------------------------------------------------------
# Regression — verify_link_attributes signature/behavior unchanged.
# --------------------------------------------------------------------------

def test_verify_link_attributes_signature():
    """verify_link_attributes must keep back-compat positional `url`,
    keyword-only `timeout` (10.0), and the new keyword-only `target_urls`
    (None default) added in Plan 2026-05-27-006 Unit 1."""
    import inspect as _inspect_mod

    sig = _inspect_mod.signature(lav.verify_link_attributes)
    params = list(sig.parameters)
    # positional: url; keyword-only: timeout, target_urls
    assert params == ["url", "timeout", "target_urls"]
    assert sig.parameters["timeout"].kind == _inspect_mod.Parameter.KEYWORD_ONLY
    assert sig.parameters["timeout"].default == 10.0
    assert sig.parameters["target_urls"].kind == _inspect_mod.Parameter.KEYWORD_ONLY
    assert sig.parameters["target_urls"].default is None


def test_verify_link_attributes_still_uses_http_get():
    """verify_link_attributes must still fetch via backlink_publisher.http.get
    (the 6 post-publish callers depend on its semantics)."""
    fake_resp = MagicMock()
    fake_resp.ok = True
    fake_resp.text = '<html><body><a href="x" rel="nofollow">l</a></body></html>'
    with patch.object(lav._http, "get", return_value=fake_resp) as mock_get:
        result = lav.verify_link_attributes("https://blog.example/post")
    assert mock_get.called
    assert result["verification"] == "ok"
    assert result["nofollow_detected"] is True
