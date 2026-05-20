"""Unit tests for velog recipe host-filter primitives.

Pure-function tests — no Playwright, no I/O.
"""

from __future__ import annotations

import pytest

from backlink_publisher.cli._bind.recipes.velog import _velog_cookie_host_filter
from backlink_publisher.cli._bind.driver import _apply_host_filter


# ── _velog_cookie_host_filter ─────────────────────────────────────────────────

class TestVelogCookieHostFilter:
    def test_velog_io_bare(self):
        assert _velog_cookie_host_filter("velog.io") is True

    def test_velog_io_with_dot_prefix(self):
        assert _velog_cookie_host_filter(".velog.io") is True

    def test_velog_io_uppercase(self):
        assert _velog_cookie_host_filter("VELOG.IO") is True

    def test_v2_subdomain_rejected(self):
        # Subdomains are now explicitly rejected per recipe comment
        assert _velog_cookie_host_filter("v2.velog.io") is False

    def test_v3_subdomain_rejected(self):
        assert _velog_cookie_host_filter("v3.velog.io") is False

    def test_prefix_confusion_evilvelog(self):
        assert _velog_cookie_host_filter("evilvelog.io") is False

    def test_suffix_confusion_attacker(self):
        assert _velog_cookie_host_filter("velog.io.attacker.com") is False

    def test_google_idp(self):
        assert _velog_cookie_host_filter("accounts.google.com") is False

    def test_github_idp(self):
        assert _velog_cookie_host_filter("github.com") is False

    def test_empty_string(self):
        assert _velog_cookie_host_filter("") is False

    def test_none(self):
        assert _velog_cookie_host_filter(None) is False  # type: ignore[arg-type]


# ── _apply_host_filter for Velog ──────────────────────────────────────────────

class TestApplyHostFilterVelog:
    def _cookie(self, name: str, domain: str) -> dict:
        return {"name": name, "domain": domain, "value": "x"}

    def test_happy_path_keeps_velog_drops_idp_and_subdomain(self):
        raw = {
            "cookies": [
                self._cookie("access_token", "velog.io"),
                self._cookie("refresh_token", ".velog.io"),
                self._cookie("CONSENT", "accounts.google.com"),
                self._cookie("sub", "v2.velog.io"),
            ]
        }
        result = _apply_host_filter(raw, _velog_cookie_host_filter)
        cookies = result.get("cookies", [])
        assert len(cookies) == 2
        names = {c["name"] for c in cookies}
        assert names == {"access_token", "refresh_token"}

    def test_missing_domain_key_dropped(self):
        raw = {
            "cookies": [{"name": "mystery", "value": "y"}]  # no 'domain' key
        }
        result = _apply_host_filter(raw, _velog_cookie_host_filter)
        assert result.get("cookies") == []

    def test_empty_input(self):
        result = _apply_host_filter({}, _velog_cookie_host_filter)
        assert result == {"cookies": [], "origins": []}


# ── Origin Filtering for Velog ────────────────────────────────────────────────

class TestApplyHostFilterOrigins:
    def _origin(self, origin_url: str, ls_name: str = "tok") -> dict:
        return {
            "origin": origin_url,
            "localStorage": [{"name": ls_name, "value": "eyJ..."}],
        }

    def test_keeps_velog_drops_google(self):
        raw = {
            "cookies": [
                {"name": "access_token", "domain": "velog.io", "value": "at"},
                {"name": "CONSENT", "domain": "accounts.google.com", "value": "YES"},
            ],
            "origins": [
                self._origin("https://velog.io", "app_state"),
                self._origin("https://accounts.google.com", "id_token"),
            ],
        }
        result = _apply_host_filter(raw, _velog_cookie_host_filter)

        # cookies
        assert len(result["cookies"]) == 1
        assert result["cookies"][0]["name"] == "access_token"

        # origins
        assert len(result["origins"]) == 1
        assert result["origins"][0]["origin"] == "https://velog.io"

    def test_uppercase_velog_origin_kept(self):
        raw = {
            "cookies": [],
            "origins": [self._origin("https://Velog.IO")],
        }
        result = _apply_host_filter(raw, _velog_cookie_host_filter)
        assert len(result["origins"]) == 1

    def test_suffix_confusion_origin_dropped(self):
        raw = {
            "cookies": [],
            "origins": [self._origin("https://velog.io.attacker.com")],
        }
        result = _apply_host_filter(raw, _velog_cookie_host_filter)
        assert result["origins"] == []

    def test_malformed_origin_url_dropped(self):
        raw = {
            "cookies": [],
            "origins": [{"origin": "not-a-url", "localStorage": []}],
        }
        result = _apply_host_filter(raw, _velog_cookie_host_filter)
        assert result["origins"] == []
