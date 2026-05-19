"""Unit 4 — generic /api/<channel>/{status,verify,dry-run} routes.

Three contracts:
  - ``GET  /api/<channel>/status``   → JSON status (offline check, cheap)
  - ``POST /api/<channel>/verify``   → JSON VerifyResult (live API ping)
  - ``POST /api/<channel>/dry-run``  → JSON VerifyResult (payload build, ZERO HTTP)

The dispatcher routes by channel name and 404s on unregistered platforms.
Drift between registry and dashboard is enforced by ``test_dashboard_drift.py``.

Plan: docs/plans/2026-05-19-006-feat-channel-binding-dashboard-and-platform-expansion-plan.md
Companion: ``test_verify_adapter_setup_modes.py`` (Unit 2 — the verify contract).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from webui_app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


# ── GET /api/<channel>/status ─────────────────────────────────────────────────


class TestStatusEndpoint:
    """Cheap offline status — no API calls, just config inspection."""

    def test_status_known_channel_returns_200(self, client):
        resp = client.get("/api/blogger/status")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["channel"] == "blogger"
        assert "bound" in body
        assert "last_verify_result" in body
        assert "dofollow" in body

    def test_status_unbound_channel_reports_bound_false(self, client):
        """No credentials → bound=False, blockers populated, no exception."""
        resp = client.get("/api/blogger/status")
        body = resp.get_json()
        assert body["bound"] is False
        assert isinstance(body["blockers"], list)
        assert any("Blogger OAuth" in b for b in body["blockers"])

    def test_status_telegraph_bound_true(self, client):
        """Telegraph has no required prereqs → bound=True even with empty config."""
        resp = client.get("/api/telegraph/status")
        body = resp.get_json()
        assert body["bound"] is True
        assert body["channel"] == "telegraph"

    def test_status_unknown_channel_returns_404(self, client):
        resp = client.get("/api/nonexistent/status")
        assert resp.status_code == 404


# ── POST /api/<channel>/verify ────────────────────────────────────────────────


class TestVerifyEndpoint:
    """Live verify — calls platform's lightweight API endpoint."""

    def test_verify_requires_csrf(self, client):
        """POST without CSRF token → 403."""
        resp = client.post("/api/blogger/verify")
        assert resp.status_code == 403

    def test_verify_with_csrf_header_succeeds(self, client):
        """X-CSRFToken header is accepted (in addition to form field) for JSON fetch."""
        with client.session_transaction() as sess:
            sess["csrf_token"] = "test-token-abc"
        resp = client.post(
            "/api/blogger/verify",
            headers={"X-CSRFToken": "test-token-abc"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        # Unbound blogger → ok=False but well-formed VerifyResult
        assert body["ok"] is False
        assert body["last_verify_result"] in ("never", "token_expired", "timeout")

    def test_verify_unknown_channel_returns_404(self, client):
        with client.session_transaction() as sess:
            sess["csrf_token"] = "t"
        resp = client.post(
            "/api/notachannel/verify",
            headers={"X-CSRFToken": "t"},
        )
        assert resp.status_code == 404


# ── POST /api/<channel>/dry-run ───────────────────────────────────────────────


class TestDryRunEndpoint:
    """Dry-run — builds payload but emits ZERO real HTTP."""

    def test_dry_run_requires_csrf(self, client):
        resp = client.post("/api/telegraph/dry-run")
        assert resp.status_code == 403

    def test_dry_run_emits_zero_real_http(self, client):
        """Defense-in-depth: even if adapter forgets, Session.send is patched.

        We assert no real Session.send happens (autouse socket fixture already
        blocks real network — this is the explicit assertion).
        """
        with client.session_transaction() as sess:
            sess["csrf_token"] = "t"

        with patch.object(
            requests.Session, "send", side_effect=AssertionError("real HTTP escaped")
        ) as mock_send:
            resp = client.post(
                "/api/telegraph/dry-run",
                headers={"X-CSRFToken": "t"},
                json={"id": "x", "title": "T", "content_markdown": "body"},
            )
            assert resp.status_code == 200
            assert mock_send.call_count == 0

    def test_dry_run_returns_verify_result_shape(self, client):
        with client.session_transaction() as sess:
            sess["csrf_token"] = "t"
        resp = client.post(
            "/api/telegraph/dry-run",
            headers={"X-CSRFToken": "t"},
            json={"id": "x", "title": "T", "content_markdown": "b"},
        )
        body = resp.get_json()
        assert "ok" in body
        assert "last_verify_result" in body
        assert "blockers" in body


# ── CSRF header extension (Unit 4 sub-deliverable) ────────────────────────────


class TestCSRFHeaderSupport:
    """``_check_csrf_or_abort`` must accept ``X-CSRFToken`` header in addition
    to ``request.form['csrf_token']`` so JS fetch() with JSON body works."""

    def test_csrf_form_field_still_works(self, client):
        """Backward compat: existing form-field path unchanged."""
        with client.session_transaction() as sess:
            sess["csrf_token"] = "t"
        resp = client.post(
            "/api/blogger/verify",
            data={"csrf_token": "t"},
        )
        assert resp.status_code == 200

    def test_csrf_wrong_token_in_header_rejected(self, client):
        with client.session_transaction() as sess:
            sess["csrf_token"] = "real-token"
        resp = client.post(
            "/api/blogger/verify",
            headers={"X-CSRFToken": "wrong-token"},
        )
        assert resp.status_code == 403
