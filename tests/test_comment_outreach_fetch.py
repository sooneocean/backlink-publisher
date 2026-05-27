"""Never-raises + reason-taxonomy + SSRF/cap-hardening tests for the comment fetcher.

Test seam: patch this module's own ``_COMMENT_OPENER.open`` and ``_check_url_for_ssrf``
(both imported into the module namespace), so no real network is touched. Mirrors
``tests/test_preflight_fetch.py``.
"""

from __future__ import annotations

import socket
import ssl
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from backlink_publisher.comment_outreach import fetch as cf


def _mock_resp(*, status: int = 200, final_url: str = "https://example.com/page",
               body: bytes = b"<html><body><h1>Hi</h1></body></html>") -> MagicMock:
    resp = MagicMock()
    resp.getcode.return_value = status
    resp.geturl.return_value = final_url
    chunks = [body, b""]
    resp.read.side_effect = lambda n=-1: chunks.pop(0) if chunks else b""
    resp.close.return_value = None
    return resp


def _ssrf_ok():
    return patch.object(cf, "_check_url_for_ssrf", return_value=None)


# --- Happy path ------------------------------------------------------------
def test_healthy_200_returns_body_and_ok():
    body = b"<html><body><div id='disqus_thread'></div></body></html>"
    with _ssrf_ok(), patch.object(cf._COMMENT_OPENER, "open", return_value=_mock_resp(body=body)):
        result = cf.fetch_comment_page("https://example.com/page")
    assert result.reason == "ok"
    assert result.html == body


# --- Scheme gate / malformed URL (never raises) ----------------------------
@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/passwd", "not-a-url", ""])
def test_non_http_scheme_is_invalid_url(url: str):
    # No SSRF/opener patch needed: rejected before any network attempt.
    result = cf.fetch_comment_page(url)
    assert result == cf.FetchResult(html=None, reason="invalid_url")


def test_malformed_ipv6_does_not_crash_and_is_invalid_url():
    result = cf.fetch_comment_page("http://[invalid")  # urlparse would raise
    assert result.reason == "invalid_url"
    assert result.html is None


# --- SSRF blocking ---------------------------------------------------------
def test_blocked_ip_is_ssrf_blocked_without_fetching():
    with patch.object(cf, "_check_url_for_ssrf", return_value="blocked_ip:10.0.0.0/8"), \
         patch.object(cf._COMMENT_OPENER, "open") as mock_open:
        result = cf.fetch_comment_page("http://10.0.0.1/internal")
    assert result == cf.FetchResult(html=None, reason="ssrf_blocked")
    mock_open.assert_not_called()


def test_ssrf_redirect_urlerror_is_ssrf_blocked():
    err = URLError("ssrf_redirect:blocked_ip:10.0.0.0/8")
    with _ssrf_ok(), patch.object(cf._COMMENT_OPENER, "open", side_effect=err):
        result = cf.fetch_comment_page("https://example.com/")
    assert result.reason == "ssrf_blocked"


def test_post_redirect_final_url_blocked_ip_is_ssrf_blocked():
    resp = _mock_resp(final_url="http://10.0.0.5/landing")
    # First (initial) SSRF check passes; the final-URL re-check returns blocked.
    with patch.object(cf, "_check_url_for_ssrf", side_effect=[None, "blocked_ip:10.0.0.0/8"]), \
         patch.object(cf._COMMENT_OPENER, "open", return_value=resp):
        result = cf.fetch_comment_page("https://example.com/start")
    assert result.reason == "ssrf_blocked"
    assert result.html is None


# --- Non-200 / timeout / network errors ------------------------------------
@pytest.mark.parametrize("code", [403, 404, 410, 500, 503])
def test_httperror_status_is_non_200(code: int):
    err = HTTPError("https://example.com/", code, "x", None, None)
    with _ssrf_ok(), patch.object(cf._COMMENT_OPENER, "open", side_effect=err):
        assert cf.fetch_comment_page("https://example.com/").reason == "non_200"


def test_200_with_non200_getcode_is_non_200():
    with _ssrf_ok(), patch.object(cf._COMMENT_OPENER, "open", return_value=_mock_resp(status=204)):
        assert cf.fetch_comment_page("https://example.com/").reason == "non_200"


def test_socket_timeout_is_timeout():
    with _ssrf_ok(), patch.object(cf._COMMENT_OPENER, "open", side_effect=socket.timeout()):
        assert cf.fetch_comment_page("https://example.com/").reason == "timeout"


def test_urlerror_wrapping_timeout_is_timeout():
    with _ssrf_ok(), patch.object(cf._COMMENT_OPENER, "open", side_effect=URLError(socket.timeout())):
        assert cf.fetch_comment_page("https://example.com/").reason == "timeout"


def test_ssl_error_is_network_error():
    err = URLError(ssl.SSLCertVerificationError("bad cert"))
    with _ssrf_ok(), patch.object(cf._COMMENT_OPENER, "open", side_effect=err):
        assert cf.fetch_comment_page("https://example.com/").reason == "network_error"


def test_generic_urlerror_is_network_error():
    with _ssrf_ok(), patch.object(cf._COMMENT_OPENER, "open", side_effect=URLError("dns boom")):
        assert cf.fetch_comment_page("https://example.com/").reason == "network_error"


def test_unexpected_exception_never_propagates():
    with _ssrf_ok(), patch.object(cf._COMMENT_OPENER, "open", side_effect=RuntimeError("boom")):
        assert cf.fetch_comment_page("https://example.com/").reason == "network_error"


# --- Body cap / compression-bomb hardening ---------------------------------
def test_oversized_body_is_oversized_by_wire_bytes(monkeypatch):
    monkeypatch.setattr(cf, "MAX_BODY_BYTES", 100)
    resp = _mock_resp(body=b"x" * 500)  # wire bytes far exceed the cap
    with _ssrf_ok(), patch.object(cf._COMMENT_OPENER, "open", return_value=resp):
        result = cf.fetch_comment_page("https://example.com/")
    assert result == cf.FetchResult(html=None, reason="oversized")


def test_response_closed_even_if_getcode_raises():
    # Regression: getcode()/geturl() raising must not leak the socket; `with resp` closes it.
    resp = MagicMock()  # MagicMock auto-supports the context-manager protocol
    resp.getcode.side_effect = RuntimeError("boom")
    with _ssrf_ok(), patch.object(cf._COMMENT_OPENER, "open", return_value=resp):
        result = cf.fetch_comment_page("https://example.com/")
    assert result.reason == "network_error"
    resp.__exit__.assert_called()  # the context manager ran teardown despite the raise


def test_body_exactly_at_cap_is_ok(monkeypatch):
    monkeypatch.setattr(cf, "MAX_BODY_BYTES", 100)
    resp = _mock_resp(body=b"y" * 100)
    with _ssrf_ok(), patch.object(cf._COMMENT_OPENER, "open", return_value=resp):
        result = cf.fetch_comment_page("https://example.com/")
    assert result.reason == "ok"
    assert result.html == b"y" * 100


def test_request_sends_no_gzip_and_a_distinct_user_agent():
    captured = []

    def _open(req, timeout=None):
        captured.append(req)
        return _mock_resp()

    with _ssrf_ok(), patch.object(cf._COMMENT_OPENER, "open", side_effect=_open):
        cf.fetch_comment_page("https://example.com/")

    req = captured[0]
    assert req.get_header("Accept-encoding") is None  # never opt into gzip
    assert "comment-outreach" in req.get_header("User-agent", "")


# --- Non-ASCII normalization -----------------------------------------------
def test_non_ascii_url_normalized_before_request():
    captured = []

    def _open(req, timeout=None):
        captured.append(req)
        return _mock_resp()

    with _ssrf_ok(), patch.object(cf._COMMENT_OPENER, "open", side_effect=_open):
        result = cf.fetch_comment_page("https://例え.example/パス")

    assert result.reason == "ok"
    # The URL handed to urllib must be ASCII-safe (IDNA host + percent-encoded path).
    captured[0].full_url.encode("ascii")  # must not raise
