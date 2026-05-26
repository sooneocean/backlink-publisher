"""WebUI /url-verify blueprint contract — Plan v1.0 Unit 3.

Covers the security perimeter for the homepage URL auto-derive feature:
  - 4-guard stack (loopback / ALLOW_NETWORK / Origin / CSRF)
  - URL parsing and normalization (length cap, scheme allow-list, userinfo
    strip, IDN→ASCII)
  - Throttle integration (rate_limited / host_busy / upstream_overloaded)
  - Uniform response shape with closed-enum ``reason`` field
  - No IP leak in response body when SSRF gate fires
  - Title truncation to 24 chars

Mirrors the fixture skeleton of ``tests/test_webui_bind_routes.py``.
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path):
    fake_config_dir = tmp_path / "config"
    with patch(
        "backlink_publisher.config._config_dir", return_value=fake_config_dir,
    ):
        yield fake_config_dir


@pytest.fixture(autouse=True)
def _reset_throttle_state():
    """Reset module-level throttle state between tests so per-session windows
    and host locks do not leak (would otherwise produce ``host_busy`` /
    ``rate_limited`` rejections from prior test fallout)."""
    from webui_app.services import url_verify_throttle as throttle
    throttle.reset_state()
    yield
    throttle.reset_state()


@pytest.fixture
def app():
    from webui_app import create_app
    a = create_app(start_scheduler=False)
    a.config["TESTING"] = True
    a.config["SESSION_COOKIE_SECURE"] = False
    return a


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_csrf(client) -> str:
    with client.session_transaction() as sess:
        sess["csrf_token"] = "test-csrf-token-fixture"
    return "test-csrf-token-fixture"


def _origin_headers() -> dict[str, str]:
    from webui_app.helpers.security import _FLASK_PORT
    return {"Origin": f"http://127.0.0.1:{_FLASK_PORT}"}


def _hdrs(csrf: str) -> dict[str, str]:
    h = _origin_headers()
    h["X-CSRFToken"] = csrf
    h["Content-Type"] = "application/json"
    return h


def _post(client, payload, *, csrf=None, headers=None, environ_overrides=None):
    h = headers if headers is not None else _hdrs(csrf or "")
    return client.post(
        "/url-verify",
        data=json.dumps(payload) if isinstance(payload, dict) else payload,
        headers=h,
        environ_overrides=environ_overrides or {},
    )


# ---------------------------------------------------------------------------
# Closed-enum reason vocabulary (R8e contract)
# ---------------------------------------------------------------------------

_ALLOWED_REASONS = frozenset({
    "ok",
    "invalid_url",
    "network_error",
    "ssrf_blocked",
    "timeout",
    "http_404",
    "http_5xx",
    "http_200_no_title",
    "soft_404_title",
    "body_too_small",
    "blocked_scheme",
    "rate_limited",
    "host_busy",
    "upstream_overloaded",
})


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def _patch_ok(monkeypatch, title="mock title"):
    """Re-patch verify_url_has_content with a signature that matches the
    route's call kwargs (max_age_seconds / timeout_seconds / max_redirects).
    The autouse conftest mock only accepts a positional URL and would raise
    TypeError under the route's keyword call."""
    def _ok(url, **kw):
        return (True, None, title)
    monkeypatch.setattr(
        "backlink_publisher.content.fetch.verify_url_has_content",
        _ok,
        raising=True,
    )


class TestHappyPath:
    def test_happy_path_valid_url(self, client, monkeypatch):
        _patch_ok(monkeypatch)
        token = _seed_csrf(client)
        resp = _post(client, {"url": "https://example.com"}, csrf=token)
        assert resp.status_code == 200, resp.data[:200]
        body = resp.get_json()
        assert body["ok"] is True
        assert body["status"] == 200
        assert body["title"] == "mock title"
        assert body["reason"] == "ok"

    def test_response_shape_uniform(self, client, monkeypatch):
        _patch_ok(monkeypatch)
        token = _seed_csrf(client)
        resp = _post(client, {"url": "https://example.com/x"}, csrf=token)
        body = resp.get_json()
        assert set(body.keys()) == {"ok", "status", "title", "reason"}
        assert isinstance(body["ok"], bool)
        assert isinstance(body["status"], int)
        assert isinstance(body["title"], str)
        assert isinstance(body["reason"], str)


# ---------------------------------------------------------------------------
# NO_FETCH_VERIFY short-circuit
# ---------------------------------------------------------------------------


class TestFetchVerifyDisabled:
    def test_no_fetch_verify_returns_204(self, client, monkeypatch):
        monkeypatch.setenv("BACKLINK_NO_FETCH_VERIFY", "1")
        token = _seed_csrf(client)
        resp = _post(client, {"url": "https://example.com"}, csrf=token)
        assert resp.status_code == 204
        assert resp.data == b""

    def test_no_fetch_verify_does_not_consume_throttle(
        self, client, monkeypatch
    ):
        """Short-circuit must occur BEFORE throttle.try_acquire, otherwise
        the operator's ``BACKLINK_NO_FETCH_VERIFY=1`` escape hatch is silently
        rate-limited."""
        monkeypatch.setenv("BACKLINK_NO_FETCH_VERIFY", "1")
        calls = {"n": 0}

        from webui_app.services import url_verify_throttle as throttle

        def _spy(**kw):
            calls["n"] += 1
            return None

        monkeypatch.setattr(throttle, "try_acquire", _spy)
        token = _seed_csrf(client)
        resp = _post(client, {"url": "https://example.com"}, csrf=token)
        assert resp.status_code == 204
        assert calls["n"] == 0


# ---------------------------------------------------------------------------
# URL parsing + normalization
# ---------------------------------------------------------------------------


class TestUrlParsing:
    def test_url_too_long_invalid_url(self, client):
        token = _seed_csrf(client)
        long_url = "https://example.com/" + ("a" * 2049)
        resp = _post(client, {"url": long_url}, csrf=token)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is False
        assert body["reason"] == "invalid_url"

    def test_empty_url_invalid_url(self, client):
        token = _seed_csrf(client)
        resp = _post(client, {"url": ""}, csrf=token)
        body = resp.get_json()
        assert body["reason"] == "invalid_url"

    def test_non_string_url_invalid_url(self, client):
        token = _seed_csrf(client)
        resp = _post(client, {"url": 12345}, csrf=token)
        body = resp.get_json()
        assert body["reason"] == "invalid_url"

    def test_missing_url_key_invalid_url(self, client):
        token = _seed_csrf(client)
        resp = _post(client, {}, csrf=token)
        body = resp.get_json()
        assert body["reason"] == "invalid_url"

    def test_non_https_scheme_blocked(self, client):
        token = _seed_csrf(client)
        resp = _post(client, {"url": "ftp://example.com"}, csrf=token)
        body = resp.get_json()
        assert body["ok"] is False
        assert body["reason"] == "blocked_scheme"

    def test_javascript_scheme_blocked(self, client):
        token = _seed_csrf(client)
        resp = _post(client, {"url": "javascript:alert(1)"}, csrf=token)
        body = resp.get_json()
        assert body["reason"] == "blocked_scheme"

    def test_file_scheme_blocked(self, client):
        token = _seed_csrf(client)
        resp = _post(client, {"url": "file:///etc/passwd"}, csrf=token)
        body = resp.get_json()
        assert body["reason"] == "blocked_scheme"

    def test_no_host_invalid_url(self, client):
        token = _seed_csrf(client)
        resp = _post(client, {"url": "https://"}, csrf=token)
        body = resp.get_json()
        assert body["reason"] == "invalid_url"

    def test_userinfo_stripped(self, client, monkeypatch):
        token = _seed_csrf(client)
        captured = {}

        def _spy(url, **kw):
            captured["url"] = url
            return (True, None, "stripped")

        monkeypatch.setattr(
            "backlink_publisher.content.fetch.verify_url_has_content",
            _spy,
            raising=True,
        )
        resp = _post(
            client, {"url": "http://user:pass@example.com/x"}, csrf=token,
        )
        assert resp.status_code == 200
        assert "user" not in captured["url"]
        assert "pass" not in captured["url"]
        assert "@" not in captured["url"]
        assert "example.com" in captured["url"]

    def test_idn_host_encoded(self, client, monkeypatch):
        """IDN host must be punycode-encoded (xn--...) before fetch."""
        token = _seed_csrf(client)
        captured = {}

        def _spy(url, **kw):
            captured["url"] = url
            return (True, None, "ja")

        monkeypatch.setattr(
            "backlink_publisher.content.fetch.verify_url_has_content",
            _spy,
            raising=True,
        )
        resp = _post(client, {"url": "http://例え.jp/"}, csrf=token)
        assert resp.status_code == 200
        # IDNA-encoded host must be ASCII (xn--...) — never the raw unicode.
        assert "xn--" in captured["url"]
        assert "例え" not in captured["url"]


# ---------------------------------------------------------------------------
# 4-guard stack
# ---------------------------------------------------------------------------


class TestGuards:
    def test_csrf_missing_403(self, client):
        _seed_csrf(client)
        h = _origin_headers()
        h["Content-Type"] = "application/json"
        resp = client.post(
            "/url-verify",
            data=json.dumps({"url": "https://example.com"}),
            headers=h,
        )
        assert resp.status_code == 403

    def test_csrf_mismatch_403(self, client):
        _seed_csrf(client)
        resp = _post(client, {"url": "https://example.com"}, csrf="wrong")
        assert resp.status_code == 403

    def test_origin_mismatch_403(self, client):
        token = _seed_csrf(client)
        h = {
            "Origin": "https://attacker.example",
            "X-CSRFToken": token,
            "Content-Type": "application/json",
        }
        resp = client.post(
            "/url-verify",
            data=json.dumps({"url": "https://example.com"}),
            headers=h,
        )
        assert resp.status_code == 403

    def test_loopback_refused(self, client):
        token = _seed_csrf(client)
        resp = _post(
            client,
            {"url": "https://example.com"},
            csrf=token,
            environ_overrides={"REMOTE_ADDR": "1.2.3.4"},
        )
        assert resp.status_code == 403

    def test_allow_network_refused(self, client, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_ALLOW_NETWORK", "1")
        token = _seed_csrf(client)
        resp = _post(client, {"url": "https://example.com"}, csrf=token)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Throttle integration
# ---------------------------------------------------------------------------


class TestThrottle:
    def _patch_throttle(self, monkeypatch, reason):
        from webui_app.services import url_verify_throttle as throttle
        released = {"n": 0}

        def _rejector(**kw):
            return reason

        def _spy_release(host):
            released["n"] += 1

        monkeypatch.setattr(throttle, "try_acquire", _rejector)
        monkeypatch.setattr(throttle, "release", _spy_release)
        return released

    def test_throttle_rate_limited(self, client, monkeypatch):
        released = self._patch_throttle(monkeypatch, "rate_limited")
        token = _seed_csrf(client)
        resp = _post(client, {"url": "https://example.com"}, csrf=token)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is False
        assert body["reason"] == "rate_limited"
        assert body["status"] == 0
        # release must NOT be called on rejected acquire
        assert released["n"] == 0

    def test_throttle_host_busy(self, client, monkeypatch):
        self._patch_throttle(monkeypatch, "host_busy")
        token = _seed_csrf(client)
        resp = _post(client, {"url": "https://example.com"}, csrf=token)
        body = resp.get_json()
        assert body["reason"] == "host_busy"

    def test_throttle_upstream_overloaded(self, client, monkeypatch):
        self._patch_throttle(monkeypatch, "upstream_overloaded")
        token = _seed_csrf(client)
        resp = _post(client, {"url": "https://example.com"}, csrf=token)
        body = resp.get_json()
        assert body["reason"] == "upstream_overloaded"

    def test_throttle_released_on_fetch_exception(self, client, monkeypatch):
        """Even if verify_url_has_content raises, throttle.release MUST run
        (try/finally), otherwise the host lock leaks permanently."""
        from webui_app.services import url_verify_throttle as throttle
        released = {"n": 0}

        def _orig_release(host):
            released["n"] += 1

        monkeypatch.setattr(throttle, "release", _orig_release)

        def _explode(url, **kw):
            raise RuntimeError("simulated network failure")

        monkeypatch.setattr(
            "backlink_publisher.content.fetch.verify_url_has_content",
            _explode,
            raising=True,
        )
        token = _seed_csrf(client)
        resp = _post(client, {"url": "https://example.com"}, csrf=token)
        # Whatever happens on exception, release must have fired.
        assert released["n"] == 1
        # And the response should be a uniform-shape failure, not a 500.
        assert resp.status_code in {200, 500}


# ---------------------------------------------------------------------------
# Malformed bodies + contract
# ---------------------------------------------------------------------------


class TestMalformedBody:
    def test_invalid_json_body(self, client):
        token = _seed_csrf(client)
        h = _hdrs(token)
        resp = client.post(
            "/url-verify",
            data="not json at all",
            headers=h,
        )
        # Uniform contract: non-JSON → invalid_url, NOT 400
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is False
        assert body["reason"] == "invalid_url"

    def test_empty_body(self, client):
        token = _seed_csrf(client)
        h = _hdrs(token)
        resp = client.post("/url-verify", data="", headers=h)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["reason"] == "invalid_url"


# ---------------------------------------------------------------------------
# Closed-enum reason vocabulary
# ---------------------------------------------------------------------------


class TestReasonEnum:
    def test_reason_field_closed_enum(self, client, monkeypatch):
        """Drive several reason paths, assert every emitted reason is in
        the closed allow-list (R8e — UI relies on this for i18n keying)."""
        token = _seed_csrf(client)
        reasons_seen = []

        def _record(client, payload):
            resp = _post(client, payload, csrf=token)
            if resp.status_code == 200 and resp.data:
                body = resp.get_json()
                reasons_seen.append(body["reason"])

        # Happy (must locally patch — autouse mock signature is incompatible)
        def _ok(url, **kw):
            return (True, None, "ok-title")
        monkeypatch.setattr(
            "backlink_publisher.content.fetch.verify_url_has_content",
            _ok,
            raising=True,
        )
        _record(client, {"url": "https://example.com"})
        # Blocked schemes
        _record(client, {"url": "ftp://example.com"})
        _record(client, {"url": "javascript:alert(1)"})
        # Invalid url
        _record(client, {"url": ""})
        _record(client, {"url": "https://"})
        _record(client, {"url": "x" * 3000})

        # Drive content-fetch reason values
        for fetch_reason in (
            "ssrf_blocked", "timeout", "http_404", "http_5xx",
            "http_200_no_title", "soft_404_title", "body_too_small",
            "network_error",
        ):
            r = fetch_reason  # capture

            def _stub(url, **kw):
                return (False, r, None)

            monkeypatch.setattr(
                "backlink_publisher.content.fetch.verify_url_has_content",
                _stub,
                raising=True,
            )
            _record(client, {"url": "https://example.com/" + r})

        assert reasons_seen, "test must observe at least one reason"
        for r in reasons_seen:
            assert r in _ALLOWED_REASONS, f"reason {r!r} not in allow-list"


# ---------------------------------------------------------------------------
# SSRF response shape — no IP leak
# ---------------------------------------------------------------------------


class TestNoIpLeak:
    def test_response_no_ip_leak(self, client, monkeypatch):
        """When verify_url_has_content reports ssrf_blocked, the response
        body MUST NOT include the resolved IP family. The reason field is
        the only signal the UI gets."""

        def _ssrf(url, **kw):
            return (False, "ssrf_blocked", None)

        monkeypatch.setattr(
            "backlink_publisher.content.fetch.verify_url_has_content",
            _ssrf,
            raising=True,
        )
        token = _seed_csrf(client)
        resp = _post(client, {"url": "https://example.com"}, csrf=token)
        assert resp.status_code == 200
        raw = resp.data.decode("utf-8", errors="replace")
        for needle in ("127.", "10.", "169.254", "192.168", "::1"):
            assert needle not in raw, (
                f"IP fragment {needle!r} leaked into response body: {raw!r}"
            )
        body = resp.get_json()
        assert body["reason"] == "ssrf_blocked"
        assert body["ok"] is False


# ---------------------------------------------------------------------------
# Title truncation
# ---------------------------------------------------------------------------


class TestTitleTruncation:
    def test_title_truncated_to_24_chars(self, client, monkeypatch):
        long_title = "x" * 100

        def _ok_long(url, **kw):
            return (True, None, long_title)

        monkeypatch.setattr(
            "backlink_publisher.content.fetch.verify_url_has_content",
            _ok_long,
            raising=True,
        )
        token = _seed_csrf(client)
        resp = _post(client, {"url": "https://example.com"}, csrf=token)
        body = resp.get_json()
        assert body["ok"] is True
        assert len(body["title"]) == 24
        assert body["title"] == "x" * 24

    def test_title_none_returns_empty_string(self, client, monkeypatch):
        def _ok_none(url, **kw):
            return (True, None, None)

        monkeypatch.setattr(
            "backlink_publisher.content.fetch.verify_url_has_content",
            _ok_none,
            raising=True,
        )
        token = _seed_csrf(client)
        resp = _post(client, {"url": "https://example.com"}, csrf=token)
        body = resp.get_json()
        # Type stays str (uniform contract); empty string when title missing.
        assert body["title"] == ""


# ---------------------------------------------------------------------------
# Fetch-exception diagnostics (Plan 2026-05-25-009 Unit 3)
# ---------------------------------------------------------------------------


def _patch_raises(monkeypatch, exc):
    def _boom(url, **kw):
        raise exc
    monkeypatch.setattr(
        "backlink_publisher.content.fetch.verify_url_has_content",
        _boom, raising=True,
    )


class TestFetchExceptionDiagnostics:
    """A raised fetch exception must keep the uniform shape, carry the right
    reason, and leave a diagnosable RECON line with the exception class — while
    preserving the host-hash privacy invariant and the per-session throttle."""

    def test_generic_exception_is_network_error_with_exc_class(self, client, monkeypatch):
        _patch_raises(monkeypatch, ValueError("boom"))
        token = _seed_csrf(client)
        captured = {}
        import webui_app.routes.url_verify as uv
        monkeypatch.setattr(uv._logger, "recon",
                            lambda ev, **kw: captured.update(event=ev, **kw))

        resp = _post(client, {"url": "https://example.com"}, csrf=token)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is False
        assert body["reason"] == "network_error"
        # diagnosable: the exception class is logged ...
        assert captured["exc_class"] == "ValueError"
        # ... and the host is hashed, never raw.
        assert "example.com" not in str(captured.get("host_hash", ""))
        assert captured.get("host_hash")  # non-empty hash present

    def test_timeout_maps_to_timeout_reason(self, client, monkeypatch):
        _patch_raises(monkeypatch, TimeoutError("slow"))
        token = _seed_csrf(client)
        resp = _post(client, {"url": "https://example.com"}, csrf=token)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is False
        assert body["reason"] == "timeout"
        assert body["reason"] in _ALLOWED_REASONS  # closed-enum contract intact

    def test_socket_timeout_maps_to_timeout_reason(self, client, monkeypatch):
        import socket
        _patch_raises(monkeypatch, socket.timeout("timed out"))
        token = _seed_csrf(client)
        resp = _post(client, {"url": "https://example.com"}, csrf=token)
        assert resp.get_json()["reason"] == "timeout"

    def test_no_raw_host_in_recon_fields(self, client, monkeypatch):
        """Privacy guard: the enriched log must not regress to a raw host."""
        _patch_raises(monkeypatch, RuntimeError("x"))
        token = _seed_csrf(client)
        captured = {}
        import webui_app.routes.url_verify as uv
        monkeypatch.setattr(uv._logger, "recon",
                            lambda ev, **kw: captured.update(kw))
        _post(client, {"url": "https://secret-internal-host.example/"}, csrf=token)
        assert "secret-internal-host" not in json.dumps(captured)


# ---------------------------------------------------------------------------
# Blueprint registration
# ---------------------------------------------------------------------------


class TestBlueprintRegistered:
    def test_blueprint_is_registered(self, app):
        assert "url_verify" in app.blueprints

    def test_route_is_addressable(self, client):
        # No-CSRF POST hits the guard stack, proving the route is wired.
        resp = client.post("/url-verify", data="{}", headers=_origin_headers())
        # Either 403 (CSRF) or 200 (uniform contract) — never 404.
        assert resp.status_code != 404
