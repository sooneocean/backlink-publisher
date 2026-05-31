"""Unit tests for helpers extracted from fetch_target.

Covers _classify_url_error and _build_facts_from_response.
All tests are pure-unit: no network, no I/O.
"""

from __future__ import annotations

import email.message
import socket
import ssl
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from backlink_publisher.content._preflight_fetch import (
    _build_facts_from_response,
    _classify_url_error,
)
from backlink_publisher.content import _preflight_fetch as pf


# ── helpers ───────────────────────────────────────────────────────────────────


def _headers(pairs: dict | None = None) -> email.message.Message:
    msg = email.message.Message()
    for k, v in (pairs or {}).items():
        msg[k] = v
    return msg


def _mock_resp(
    *,
    status: int = 200,
    final_url: str = "https://example.com/page",
    body: bytes = b"<html><head><title>Hi</title></head><body><h1>Heading</h1></body></html>",
    headers: dict | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.getcode.return_value = status
    resp.geturl.return_value = final_url
    resp.info.return_value = _headers(headers)
    chunks = [body, b""]
    resp.read.side_effect = lambda n=-1: chunks.pop(0) if chunks else b""
    resp.close.return_value = None
    return resp


def _url_error(reason) -> URLError:
    exc = URLError(reason)
    exc.reason = reason
    return exc


# ── _classify_url_error ───────────────────────────────────────────────────────


class TestClassifyUrlError:
    def test_ssrf_string_reason(self):
        facts = _classify_url_error(_url_error("ssrf_blocked_ip"))
        assert facts.reason == "ssrf_blocked"

    def test_ssrf_prefix_required(self):
        facts = _classify_url_error(_url_error("not_ssrf"))
        assert facts.reason == "network_error"

    def test_ssl_error_reason(self):
        facts = _classify_url_error(_url_error(ssl.SSLError("cert verify failed")))
        assert facts.reason == "tls_unverified"
        assert facts.tls_unverified is True

    def test_socket_timeout_reason(self):
        facts = _classify_url_error(_url_error(socket.timeout("timed out")))
        assert facts.reason == "timeout"

    def test_other_reason_is_network_error(self):
        facts = _classify_url_error(_url_error(ConnectionRefusedError()))
        assert facts.reason == "network_error"

    def test_none_reason_is_network_error(self):
        exc = URLError("fallback")
        exc.reason = None
        facts = _classify_url_error(exc)
        assert facts.reason == "network_error"


# ── _build_facts_from_response ────────────────────────────────────────────────


class TestBuildFactsFromResponse:
    def test_200_happy_path_full_facts(self):
        resp = _mock_resp(status=200, final_url="https://example.com/page")
        with patch.object(pf, "_check_url_for_ssrf", return_value=None):
            facts = _build_facts_from_response(resp, "https://example.com/page")
        assert facts.status == 200
        assert facts.reason is None
        assert facts.has_title is True
        assert facts.has_h1 is True
        assert facts.noindex is False
        assert facts.redirected is False
        assert facts.host_diff is False

    def test_200_with_redirect(self):
        resp = _mock_resp(status=200, final_url="https://www.example.com/page")
        with patch.object(pf, "_check_url_for_ssrf", return_value=None):
            facts = _build_facts_from_response(resp, "https://example.com/page")
        assert facts.redirected is True
        assert facts.host_diff is True

    def test_200_same_host_redirect_no_host_diff(self):
        resp = _mock_resp(status=200, final_url="https://example.com/page2")
        with patch.object(pf, "_check_url_for_ssrf", return_value=None):
            facts = _build_facts_from_response(resp, "https://example.com/page")
        assert facts.redirected is True
        assert facts.host_diff is False

    def test_404_returns_reason(self):
        resp = _mock_resp(status=404, body=b"<html>not found</html>")
        with patch.object(pf, "_check_url_for_ssrf", return_value=None):
            facts = _build_facts_from_response(resp, "https://example.com/page")
        assert facts.status == 404
        assert facts.reason == "http_404"

    def test_503_returns_http_5xx(self):
        resp = _mock_resp(status=503, body=b"<html>down</html>")
        with patch.object(pf, "_check_url_for_ssrf", return_value=None):
            facts = _build_facts_from_response(resp, "https://example.com/page")
        assert facts.status == 503
        assert facts.reason == "http_5xx"

    def test_resp_getcode_raises_returns_network_error(self):
        resp = MagicMock()
        resp.getcode.side_effect = OSError("read error")
        facts = _build_facts_from_response(resp, "https://example.com/page")
        assert facts.reason == "network_error"

    def test_noindex_via_meta_robots(self):
        body = b'<html><head><meta name="robots" content="noindex"></head><body><h1>H</h1></body></html>'
        resp = _mock_resp(status=200, body=body)
        with patch.object(pf, "_check_url_for_ssrf", return_value=None):
            facts = _build_facts_from_response(resp, "https://example.com/")
        assert facts.noindex is True

    def test_noindex_via_x_robots_header(self):
        resp = _mock_resp(
            status=200,
            body=b"<html><head><title>T</title></head><body><h1>H</h1></body></html>",
            headers={"X-Robots-Tag": "noindex, nofollow"},
        )
        with patch.object(pf, "_check_url_for_ssrf", return_value=None):
            facts = _build_facts_from_response(resp, "https://example.com/")
        assert facts.noindex is True

    def test_post_redirect_ssrf_blocked_ip(self):
        resp = _mock_resp(status=200, final_url="https://internal.corp/page")
        with patch.object(pf, "_check_url_for_ssrf", return_value="blocked_ip_private"):
            facts = _build_facts_from_response(resp, "https://example.com/page")
        assert facts.reason == "ssrf_blocked"

    def test_soft_404_detected(self):
        body = b"<html><head><title>Page Not Found</title></head><body></body></html>"
        resp = _mock_resp(status=200, body=body)
        with patch.object(pf, "_check_url_for_ssrf", return_value=None):
            facts = _build_facts_from_response(resp, "https://example.com/")
        assert facts.soft404 is True

    def test_resp_close_exception_does_not_propagate(self):
        resp = _mock_resp(status=200)
        resp.close.side_effect = OSError("already closed")
        with patch.object(pf, "_check_url_for_ssrf", return_value=None):
            facts = _build_facts_from_response(resp, "https://example.com/page")
        assert facts.status == 200
