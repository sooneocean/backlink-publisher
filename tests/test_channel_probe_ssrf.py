"""Tests for SSRF hardening in scripts/channel_probe.py (plan 2026-06-02-002 R14).

Verifies that:
- RFC1918 / loopback / cloud-metadata URLs are blocked before any network fetch
- Redirect hops to blocked addresses are blocked before following the hop
- Normal public URLs still produce Hit objects with real HTTP data
- The SSRF guard is in the right place (before requests.get)
"""

from __future__ import annotations

import sys
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# scripts/ is not a package; add the scripts directory to sys.path so we can
# import channel_probe directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import channel_probe  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_response(
    status: int = 200,
    url: str = "https://example.com/",
    headers: dict | None = None,
    text: str = "",
    content: bytes = b"",
    is_redirect: bool = False,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.url = url
    resp.headers = headers or {"Server": "nginx"}
    resp.text = text
    resp.content = content
    resp.is_redirect = is_redirect
    return resp


# ── Unit 1: initial URL SSRF guard ───────────────────────────────────────────


class TestSsrfInitialUrl:
    """_probe() must reject blocked hosts before any requests.get() call."""

    def test_loopback_blocked(self):
        with patch.object(
            channel_probe, "_ssrf_check", return_value="loopback"
        ), patch("requests.get") as mock_get:
            hit = channel_probe._probe("http://127.0.0.1/", "browser", "UA")
        assert hit.status is None
        assert "ssrf-blocked" in hit.error
        assert "loopback" in hit.error
        mock_get.assert_not_called()

    def test_rfc1918_blocked(self):
        with patch.object(
            channel_probe, "_ssrf_check", return_value="rfc1918"
        ), patch("requests.get") as mock_get:
            hit = channel_probe._probe("http://192.168.1.1/", "browser", "UA")
        assert hit.status is None
        assert "ssrf-blocked" in hit.error
        mock_get.assert_not_called()

    def test_cloud_metadata_blocked(self):
        with patch.object(
            channel_probe, "_ssrf_check", return_value="cloud_metadata"
        ), patch("requests.get") as mock_get:
            hit = channel_probe._probe("http://169.254.169.254/latest/meta-data/", "browser", "UA")
        assert hit.status is None
        assert "ssrf-blocked" in hit.error
        mock_get.assert_not_called()

    def test_public_url_not_blocked(self):
        with patch.object(
            channel_probe, "_ssrf_check", return_value=None
        ), patch("requests.get", return_value=_make_response(200, "https://example.com/")) as mock_get:
            hit = channel_probe._probe("https://example.com/", "browser", "UA")
        assert hit.status == 200
        assert hit.error == ""
        mock_get.assert_called_once()


# ── Unit 2: redirect-hop SSRF guard ──────────────────────────────────────────


class TestSsrfRedirectHop:
    """Redirect to a blocked address must be blocked before following the hop."""

    def test_redirect_to_rfc1918_blocked(self):
        # First GET returns a redirect to 10.0.0.1
        redirect_resp = _make_response(
            status=302,
            url="https://example.com/",
            headers={"Location": "http://10.0.0.1/"},
            is_redirect=True,
        )

        def _ssrf_side_effect(url: str):
            # Initial URL passes; redirect target fails.
            if "10.0.0.1" in url:
                return "rfc1918"
            return None

        with patch.object(
            channel_probe, "_ssrf_check", side_effect=_ssrf_side_effect
        ), patch("requests.get", return_value=redirect_resp) as mock_get:
            hit = channel_probe._probe("https://example.com/", "browser", "UA")

        assert hit.status is None
        assert "ssrf-redirect-blocked" in hit.error
        assert "rfc1918" in hit.error
        # Should have fetched the initial URL but not followed the redirect.
        assert mock_get.call_count == 1

    def test_redirect_to_cloud_metadata_blocked(self):
        redirect_resp = _make_response(
            status=301,
            url="https://legit.com/",
            headers={"Location": "http://169.254.169.254/iam/"},
            is_redirect=True,
        )

        def _ssrf_side_effect(url: str):
            if "169.254" in url:
                return "cloud_metadata"
            return None

        with patch.object(
            channel_probe, "_ssrf_check", side_effect=_ssrf_side_effect
        ), patch("requests.get", return_value=redirect_resp):
            hit = channel_probe._probe("https://legit.com/", "browser", "UA")

        assert hit.status is None
        assert "ssrf-redirect-blocked" in hit.error

    def test_redirect_to_public_url_allowed(self):
        redirect_resp = _make_response(
            status=301,
            url="https://example.com/",
            headers={"Location": "https://www.example.com/"},
            is_redirect=True,
        )
        final_resp = _make_response(200, "https://www.example.com/")

        with patch.object(
            channel_probe, "_ssrf_check", return_value=None
        ), patch("requests.get", side_effect=[redirect_resp, final_resp]):
            hit = channel_probe._probe("https://example.com/", "browser", "UA")

        assert hit.status == 200
        assert hit.error == ""


# ── Unit 3: guard absent (package not installed) ──────────────────────────────


class TestSsrfGuardAbsent:
    """When _ssrf_check is None (package not installed), probe still works."""

    def test_probe_works_without_guard(self):
        with patch.object(channel_probe, "_ssrf_check", None), patch(
            "requests.get", return_value=_make_response(200, "https://example.com/")
        ):
            hit = channel_probe._probe("https://example.com/", "browser", "UA")
        assert hit.status == 200
        assert hit.error == ""


# ── Unit 4: relative redirects resolved correctly ────────────────────────────


class TestRelativeRedirect:
    """Relative Location headers are resolved before SSRF check."""

    def test_relative_redirect_resolved_and_checked(self):
        redirect_resp = _make_response(
            status=302,
            url="https://example.com/page",
            headers={"Location": "/safe-path"},
            is_redirect=True,
        )
        final_resp = _make_response(200, "https://example.com/safe-path")

        with patch.object(
            channel_probe, "_ssrf_check", return_value=None
        ), patch("requests.get", side_effect=[redirect_resp, final_resp]):
            hit = channel_probe._probe("https://example.com/page", "browser", "UA")

        assert hit.status == 200
