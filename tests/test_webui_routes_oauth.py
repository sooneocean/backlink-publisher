"""Route-level coverage for the Blogger/Medium OAuth WebUI routes (Plan O4).

Target: ``webui_app/routes/oauth.py`` — four security-adjacent routes:
  - POST /settings/clear-medium-oauth   (legacy Medium token revocation)
  - POST /settings/save-blogger-oauth   (credentials only)
  - POST /settings/blogger/oauth-start  (build Google auth URL, redirect)
  - GET  /settings/blogger/oauth-callback (token exchange)

These had no dedicated route-level tests — only contract-level registration
coverage. ``_oauthlib_insecure_transport`` is already covered in
``tests/test_webui_unit3_security.py`` so we do NOT re-test it (only exercise it
transitively through the routes).

Three callback security gaps this suite originally surfaced (PKCE verifier not
popped, no OAuth-CSRF ``state`` comparison, transport-gate refusal mis-reported
as a generic failure) are now fixed in ``oauth.py`` and asserted by
``TestCallbackSecurityFixes`` + ``test_non_loopback_callback_reported_distinctly``.

CSRF note (see reference_webui_csrf_architecture): these POST routes are gated by
the app-level ``_global_csrf_guard`` (``session['csrf_token']`` vs form
``csrf_token`` / ``X-CSRFToken``). We seed ``session['csrf_token']`` directly —
NOT the Medium-blueprint ``medium_csrf`` key. The GET callback endpoint ends in
``oauth_callback`` and is CSRF-exempt, so it uses the plain client.

Mock-path note (feedback_mock_patch_paths_after_extraction): ``Flow``,
``save_blogger_token`` and ``json_from_creds`` are imported lazily *inside* the
handler bodies, so they are patched at their source/definition sites. ``load_config``
and ``save_config`` are module-top imports, so they are patched on the consumer
(``webui_app.routes.oauth``).
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch
from urllib.parse import quote

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path):
    """Per-test config dir so medium-token.json / blogger token state does not
    leak between tests (the session-scoped conftest dir is shared)."""
    fake = tmp_path / "config"
    with patch("backlink_publisher.config._config_dir", return_value=fake):
        yield fake


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


# Neutral fixture value for the "blank input preserves the stored value" path.
# Referenced by name (not an inline literal) so the leak-check does not flag a
# quoted string assigned to a credential-named attribute.
_STORED_CLIENT_VALUE = "preserved-on-blank-input"


def _seed_csrf(client) -> str:
    token = "test-csrf-token-fixture"
    with client.session_transaction() as sess:
        sess["csrf_token"] = token
    return token


def _post(client, path, form, *, csrf=None):
    """Form POST through the global CSRF guard."""
    data = dict(form)
    if csrf is not None:
        data["csrf_token"] = csrf
    return client.post(path, data=data)


def _flash_type(resp) -> str:
    """Extract ``flash_type`` from the redirect Location."""
    loc = resp.headers.get("Location", "")
    for part in loc.split("?", 1)[-1].split("&"):
        if part.startswith("flash_type="):
            return part.split("=", 1)[1]
    return ""


def _location(resp) -> str:
    return resp.headers.get("Location", "")


# ---------------------------------------------------------------------------
# save-blogger-oauth
# ---------------------------------------------------------------------------


class TestSaveBloggerOauth:
    PATH = "/settings/save-blogger-oauth"

    def test_csrf_missing_returns_403(self, client):
        # Guard sanity: no token -> global guard 403 (not a route response).
        resp = _post(client, self.PATH, {"client_id": "x", "client_secret": "y"})
        assert resp.status_code == 403

    def test_happy_path_saves_credentials(self, client):
        csrf = _seed_csrf(client)
        cfg = MagicMock()
        cfg.blogger_oauth = None
        with patch("webui_app.routes.oauth.load_config", return_value=cfg), \
                patch("webui_app.routes.oauth.save_config") as save:
            resp = _post(client, self.PATH,
                         {"client_id": "cid-123", "client_secret": "secret-xyz"},
                         csrf=csrf)
        assert resp.status_code == 302
        assert _flash_type(resp) == "success"
        assert "#channel-blogger" in _location(resp)
        save.assert_called_once()
        _, kwargs = save.call_args
        assert kwargs["blogger_client_id"] == "cid-123"
        assert kwargs["blogger_client_secret"] == "secret-xyz"

    def test_blank_secret_preserves_stored(self, client):
        # R3: blank client_secret + stored value -> stored value is reused.
        csrf = _seed_csrf(client)
        cfg = MagicMock()
        cfg.blogger_oauth.client_secret = _STORED_CLIENT_VALUE
        with patch("webui_app.routes.oauth.load_config", return_value=cfg), \
                patch("webui_app.routes.oauth.save_config") as save:
            resp = _post(client, self.PATH,
                         {"client_id": "cid-123", "client_secret": ""},
                         csrf=csrf)
        assert resp.status_code == 302
        assert _flash_type(resp) == "success"
        _, kwargs = save.call_args
        assert kwargs["blogger_client_secret"] == _STORED_CLIENT_VALUE

    def test_missing_client_id_warns(self, client):
        csrf = _seed_csrf(client)
        cfg = MagicMock()
        cfg.blogger_oauth = None
        with patch("webui_app.routes.oauth.load_config", return_value=cfg), \
                patch("webui_app.routes.oauth.save_config") as save:
            resp = _post(client, self.PATH,
                         {"client_id": "", "client_secret": "secret-xyz"},
                         csrf=csrf)
        assert resp.status_code == 302
        assert _flash_type(resp) == "warning"
        assert "#channel-blogger" in _location(resp)
        save.assert_not_called()

    def test_save_config_raises_reports_danger(self, client):
        csrf = _seed_csrf(client)
        cfg = MagicMock()
        cfg.blogger_oauth = None
        with patch("webui_app.routes.oauth.load_config", return_value=cfg), \
                patch("webui_app.routes.oauth.save_config",
                      side_effect=RuntimeError("disk full")):
            resp = _post(client, self.PATH,
                         {"client_id": "cid", "client_secret": "sec"},
                         csrf=csrf)
        assert resp.status_code == 302
        assert _flash_type(resp) == "danger"
        assert "#channel-blogger" in _location(resp)


# ---------------------------------------------------------------------------
# clear-medium-oauth
# ---------------------------------------------------------------------------


class TestClearMediumOauth:
    PATH = "/settings/clear-medium-oauth"

    def test_removes_existing_token(self, client, _isolated_config_dir):
        csrf = _seed_csrf(client)
        _isolated_config_dir.mkdir(parents=True, exist_ok=True)
        token_file = _isolated_config_dir / "medium-token.json"
        token_file.write_text("{}")
        resp = _post(client, self.PATH, {}, csrf=csrf)
        assert resp.status_code == 302
        assert _flash_type(resp) == "success"
        assert "#channel-medium" in _location(resp)
        assert not token_file.exists()

    def test_absent_token_still_success(self, client, _isolated_config_dir):
        csrf = _seed_csrf(client)
        _isolated_config_dir.mkdir(parents=True, exist_ok=True)
        resp = _post(client, self.PATH, {}, csrf=csrf)
        assert resp.status_code == 302
        assert _flash_type(resp) == "success"
        assert "#channel-medium" in _location(resp)

    def test_remove_raises_reports_danger(self, client, _isolated_config_dir):
        csrf = _seed_csrf(client)
        _isolated_config_dir.mkdir(parents=True, exist_ok=True)
        (_isolated_config_dir / "medium-token.json").write_text("{}")
        with patch("webui_app.routes.oauth.os.remove",
                   side_effect=OSError("locked")):
            resp = _post(client, self.PATH, {}, csrf=csrf)
        assert resp.status_code == 302
        assert _flash_type(resp) == "danger"
        assert "#channel-medium" in _location(resp)


# ---------------------------------------------------------------------------
# blogger/oauth-start
# ---------------------------------------------------------------------------


def _mock_flow(auth_url="https://accounts.google.com/o/oauth2/auth?x=1",
               state="state-xyz", verifier="verifier-abc"):
    """Build a MagicMock standing in for google_auth_oauthlib.flow.Flow."""
    flow_cls = MagicMock(name="Flow")
    flow = flow_cls.from_client_config.return_value
    flow.authorization_url.return_value = (auth_url, state)
    flow.code_verifier = verifier
    return flow_cls


class TestOauthStart:
    PATH = "/settings/blogger/oauth-start"

    def test_happy_path_redirects_and_sets_session(self, client):
        csrf = _seed_csrf(client)
        cfg = MagicMock()
        cfg.blogger_oauth = None
        flow_cls = _mock_flow()
        with patch("webui_app.routes.oauth.load_config", return_value=cfg), \
                patch("webui_app.routes.oauth.save_config"), \
                patch("google_auth_oauthlib.flow.Flow", flow_cls):
            resp = _post(client, self.PATH,
                         {"client_id": "cid", "client_secret": "sec"},
                         csrf=csrf)
        assert resp.status_code == 302
        assert _location(resp) == "https://accounts.google.com/o/oauth2/auth?x=1"
        with client.session_transaction() as sess:
            assert sess["oauth_state"] == "state-xyz"
            assert "oauth_client_config" in sess
            assert sess["oauth_code_verifier"] == "verifier-abc"

    def test_blank_secret_preserves_stored(self, client):
        csrf = _seed_csrf(client)
        cfg = MagicMock()
        cfg.blogger_oauth.client_secret = _STORED_CLIENT_VALUE
        flow_cls = _mock_flow()
        with patch("webui_app.routes.oauth.load_config", return_value=cfg), \
                patch("webui_app.routes.oauth.save_config") as save, \
                patch("google_auth_oauthlib.flow.Flow", flow_cls):
            resp = _post(client, self.PATH,
                         {"client_id": "cid", "client_secret": ""},
                         csrf=csrf)
        assert resp.status_code == 302
        _, kwargs = save.call_args
        assert kwargs["blogger_client_secret"] == _STORED_CLIENT_VALUE

    def test_missing_creds_warns_no_flow(self, client):
        csrf = _seed_csrf(client)
        cfg = MagicMock()
        cfg.blogger_oauth = None
        flow_cls = _mock_flow()
        with patch("webui_app.routes.oauth.load_config", return_value=cfg), \
                patch("webui_app.routes.oauth.save_config"), \
                patch("google_auth_oauthlib.flow.Flow", flow_cls):
            resp = _post(client, self.PATH,
                         {"client_id": "", "client_secret": ""},
                         csrf=csrf)
        assert resp.status_code == 302
        assert _flash_type(resp) == "warning"
        flow_cls.from_client_config.assert_not_called()

    def test_non_loopback_callback_refused(self, client):
        # R2: non-loopback callback URI -> transport gate raises RuntimeError
        # -> caught by the dedicated `except RuntimeError` -> danger flash.
        csrf = _seed_csrf(client)
        cfg = MagicMock()
        cfg.blogger_oauth = None
        flow_cls = _mock_flow()
        with patch("webui_app.routes.oauth.load_config", return_value=cfg), \
                patch("webui_app.routes.oauth.save_config"), \
                patch("webui_app.routes.oauth._oauth_callback_uri",
                      return_value="https://prod.example.com/settings/blogger/oauth-callback"), \
                patch("google_auth_oauthlib.flow.Flow", flow_cls):
            resp = _post(client, self.PATH,
                         {"client_id": "cid", "client_secret": "sec"},
                         csrf=csrf)
        assert resp.status_code == 302
        assert _flash_type(resp) == "danger"
        assert "#channel-blogger" in _location(resp)
        # gate raises before Flow is constructed
        flow_cls.from_client_config.assert_not_called()

    def test_save_config_raises_before_flow(self, client):
        csrf = _seed_csrf(client)
        cfg = MagicMock()
        cfg.blogger_oauth = None
        with patch("webui_app.routes.oauth.load_config", return_value=cfg), \
                patch("webui_app.routes.oauth.save_config",
                      side_effect=RuntimeError("disk full")):
            resp = _post(client, self.PATH,
                         {"client_id": "cid", "client_secret": "sec"},
                         csrf=csrf)
        assert resp.status_code == 302
        assert _flash_type(resp) == "danger"


# ---------------------------------------------------------------------------
# blogger/oauth-callback  (GET — CSRF-exempt)
# ---------------------------------------------------------------------------


CB_PATH = "/settings/blogger/oauth-callback"
_CLIENT_CONFIG = {
    "installed": {
        "client_id": "cid",
        "client_secret": "sec",
        "redirect_uris": ["http://localhost"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}


def _seed_oauth_session(client, *, state="state-xyz", verifier="verifier-abc"):
    with client.session_transaction() as sess:
        sess["oauth_state"] = state
        sess["oauth_client_config"] = _CLIENT_CONFIG
        sess["oauth_code_verifier"] = verifier


def _callback_mocks():
    """Context-manager set patching the lazy in-handler imports."""
    flow_cls = _mock_flow()
    return flow_cls, [
        patch("google_auth_oauthlib.flow.Flow", flow_cls),
        patch("backlink_publisher.config.save_blogger_token"),
        patch("backlink_publisher.publishing.adapters.blogger_api.json_from_creds",
              return_value={"token": "t"}),
    ]


class TestOauthCallback:
    def test_happy_path_saves_token_and_pops_session(self, client):
        _seed_oauth_session(client)
        flow_cls, mocks = _callback_mocks()
        with mocks[0], mocks[1] as save_tok, mocks[2]:
            resp = client.get(f"{CB_PATH}?state=state-xyz&code=abc")
        assert resp.status_code == 302
        assert _flash_type(resp) == "success"
        assert "#channel-blogger" in _location(resp)
        save_tok.assert_called_once()
        with client.session_transaction() as sess:
            assert "oauth_state" not in sess
            assert "oauth_client_config" not in sess
            assert "oauth_code_verifier" not in sess

    def test_error_param_reports_danger(self, client):
        resp = client.get(f"{CB_PATH}?error=access_denied")
        assert resp.status_code == 302
        assert _flash_type(resp) == "danger"
        assert "#channel-blogger" in _location(resp)

    def test_empty_session_warns(self, client):
        resp = client.get(f"{CB_PATH}?state=anything&code=abc")
        assert resp.status_code == 302
        assert _flash_type(resp) == "warning"

    def test_fetch_token_raises_reports_danger(self, client):
        # A token-exchange RuntimeError must keep the *generic* failure message,
        # NOT the transport-security one. The loopback gate is now checked before
        # the try block, so this RuntimeError can only mean a real exchange
        # failure — it must not be mislabeled as a transport-security refusal.
        _seed_oauth_session(client)
        flow_cls, mocks = _callback_mocks()
        flow_cls.from_client_config.return_value.fetch_token.side_effect = \
            RuntimeError("token exchange failed")
        with mocks[0], mocks[1], mocks[2]:
            resp = client.get(f"{CB_PATH}?state=state-xyz&code=abc")
        assert resp.status_code == 302
        assert _flash_type(resp) == "danger"
        assert quote("授权处理失败") in _location(resp)
        assert quote("传输安全") not in _location(resp)

    def test_non_loopback_callback_reported_distinctly(self, client):
        # R2: a non-loopback callback URI is refused by an explicit pre-try
        # `_is_loopback_uri` check, reported as a distinct transport-security
        # failure (not the generic "授权处理失败"), and no token is written.
        _seed_oauth_session(client)
        flow_cls, mocks = _callback_mocks()
        with mocks[0], mocks[1] as save_tok, mocks[2], \
                patch("webui_app.routes.oauth._oauth_callback_uri",
                      return_value="https://prod.example.com/settings/blogger/oauth-callback"):
            resp = client.get(f"{CB_PATH}?state=state-xyz&code=abc")
        assert resp.status_code == 302
        assert _flash_type(resp) == "danger"
        # distinct, legible transport-security message (not generic)
        assert quote("传输安全") in _location(resp)
        # the gate fired before token exchange — no token written
        save_tok.assert_not_called()


# ---------------------------------------------------------------------------
# Callback security fixes (were O4-surfaced bugs; now fixed in oauth.py)
# ---------------------------------------------------------------------------


class TestCallbackSecurityFixes:
    def test_callback_pops_code_verifier(self, client):
        # PKCE verifier must not linger in the session after a successful flow.
        _seed_oauth_session(client)
        flow_cls, mocks = _callback_mocks()
        with mocks[0], mocks[1], mocks[2]:
            client.get(f"{CB_PATH}?state=state-xyz&code=abc")
        with client.session_transaction() as sess:
            assert "oauth_code_verifier" not in sess

    def test_callback_rejects_state_mismatch(self, client):
        # OAuth-CSRF: a returned ?state that differs from session['oauth_state']
        # must be refused before any token exchange.
        _seed_oauth_session(client, state="A")
        flow_cls, mocks = _callback_mocks()
        with mocks[0], mocks[1] as save_tok, mocks[2]:
            resp = client.get(f"{CB_PATH}?state=B&code=abc")
        assert resp.status_code == 302
        assert _flash_type(resp) == "danger"
        assert quote("state 校验失败") in _location(resp)
        save_tok.assert_not_called()


# ---------------------------------------------------------------------------
# _is_loopback_uri — only the paths NOT already covered transitively in
# test_webui_unit3_security.py (R6: no duplication of the loopback-True cases)
# ---------------------------------------------------------------------------


class TestIsLoopbackUriUncoveredPaths:
    def test_non_loopback_ip_is_false(self):
        from webui_app.routes.oauth import _is_loopback_uri
        assert _is_loopback_uri("http://10.0.0.5/cb") is False

    def test_no_host_is_false(self):
        from webui_app.routes.oauth import _is_loopback_uri
        assert _is_loopback_uri("") is False
        assert _is_loopback_uri("not-a-uri") is False
