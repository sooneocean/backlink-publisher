"""Unit 9 — Write.as adapter (Plan 2026-05-19-006).

Tests cover:
  - WriteAsConfig + load_writeas_token / save_writeas_token contract
  - WriteAsAPIAdapter.publish() happy path on collection (201 → AdapterResult)
  - Default-feed publish (no collection_alias)
  - Draft mode (no POST, returns drafted sentinel)
  - 401 → ExternalServiceError with re-login hint
  - non-200 / missing-slug defense
  - Authorization header carries "Token <token>" (NOT Bearer / NOT bare)
  - Endpoint resolution: with vs without collection_alias
  - Published URL derivation from api_base + slug
  - Offline verify (config missing / token file missing → DependencyError)
  - Live verify (GET /me mapping for 200 / 401 / 404 / timeout / connection)
  - Live verify read-only invariant (token file untouched)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from backlink_publisher.config import (
    Config,
    WriteAsConfig,
    load_config,
    load_writeas_token,
    save_writeas_token,
)
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing.adapters import (
    WriteAsAPIAdapter,
    WriteAsCdpAdapter,
    verify_adapter_setup,
)
from backlink_publisher.publishing.adapters import instant_web as instant_web_mod
from backlink_publisher.publishing.adapters.writeas import (
    DEFAULT_API_BASE,
    _build_post_body,
    _publish_endpoint,
    _published_url,
    _required_headers,
)


# ───────────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────────

def _seed_token(config_dir: Path, token: str = "wa_fake_tok") -> Path:
    path = config_dir / "writeas-token.json"
    path.write_text(json.dumps({"token": token}))
    os.chmod(path, 0o600)
    return path


def _config_with_writeas(
    tmp_path: Path,
    collection_alias: str = "myblog",
    api_base: str = "https://write.as/api",
) -> Config:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[writeas]\ncollection_alias = "{collection_alias}"\napi_base = "{api_base}"\n'
    )
    return load_config(cfg_file)


def _ok_publish_response(slug: str = "post-x", url: str = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 201
    resp.headers = {}
    body = {"data": {"id": "p1", "slug": slug}}
    if url is not None:
        body["data"]["url"] = url
    resp.json.return_value = body
    return resp


def _ok_me_response(username: str = "opname") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = {"data": {"username": username, "email": "e@x"}}
    return resp


def _http_status_response(status: int, body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {}
    resp.text = json.dumps(body or {})
    resp.json.return_value = body or {}
    return resp


# ───────────────────────────────────────────────────────────────────────────────
# Token file I/O
# ───────────────────────────────────────────────────────────────────────────────

class TestWriteAsTokenIO:
    def test_load_returns_none_when_missing(self, tmp_path):
        assert load_writeas_token(tmp_path / "missing.json") is None

    def test_save_and_load_round_trip(self, tmp_path):
        path = tmp_path / "writeas-token.json"
        save_writeas_token({"token": "wa_xyz"}, path)
        assert load_writeas_token(path) == {"token": "wa_xyz", "token_rev": 1}

    def test_save_sets_0600_permissions(self, tmp_path):
        path = tmp_path / "writeas-token.json"
        save_writeas_token({"token": "abc"}, path)
        assert os.stat(path).st_mode & 0o777 == 0o600


# ───────────────────────────────────────────────────────────────────────────────
# Config wiring
# ───────────────────────────────────────────────────────────────────────────────

class TestWriteAsConfig:
    def test_defaults(self):
        cfg = WriteAsConfig()
        assert cfg.collection_alias == ""
        assert cfg.api_base == "https://write.as/api"

    def test_loader_parses_section(self, tmp_path):
        cfg = _config_with_writeas(tmp_path, collection_alias="mysite")
        assert cfg.writeas is not None
        assert cfg.writeas.collection_alias == "mysite"
        assert cfg.writeas.api_base == "https://write.as/api"

    def test_loader_custom_api_base(self, tmp_path):
        cfg = _config_with_writeas(
            tmp_path,
            collection_alias="x",
            api_base="https://my.writefreely.example/api",
        )
        assert cfg.writeas.api_base == "https://my.writefreely.example/api"

    def test_no_section_yields_none(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text("")
        cfg = load_config(cfg_file)
        assert cfg.writeas is None


# ───────────────────────────────────────────────────────────────────────────────
# Required headers — "Token <tok>" not "Bearer <tok>" not bare
# ───────────────────────────────────────────────────────────────────────────────

class TestRequiredHeaders:
    def test_authorization_uses_token_scheme(self):
        headers = _required_headers("abc")
        assert headers["Authorization"] == "Token abc"

    def test_authorization_not_bearer(self):
        assert not _required_headers("x")["Authorization"].startswith("Bearer ")

    def test_authorization_not_bare(self):
        # Hashnode uses bare PAT; Write.as must NOT — regression guard.
        h = _required_headers("xyz")
        assert h["Authorization"] != "xyz"

    def test_content_type_is_json(self):
        assert _required_headers("x")["Content-Type"] == "application/json"


# ───────────────────────────────────────────────────────────────────────────────
# Helpers — endpoint + URL derivation + body shape
# ───────────────────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_publish_endpoint_with_collection(self):
        assert _publish_endpoint("https://write.as/api", "mysite") == (
            "https://write.as/api/collections/mysite/posts"
        )

    def test_publish_endpoint_without_collection(self):
        assert _publish_endpoint("https://write.as/api", "") == (
            "https://write.as/api/posts"
        )

    def test_publish_endpoint_strips_trailing_slash(self):
        assert _publish_endpoint("https://write.as/api/", "x") == (
            "https://write.as/api/collections/x/posts"
        )

    def test_published_url_strips_api_suffix(self):
        assert _published_url("https://write.as/api", "mysite", "hello") == (
            "https://write.as/mysite/hello"
        )

    def test_published_url_without_collection(self):
        assert _published_url("https://write.as/api", "", "post123") == (
            "https://write.as/post123"
        )

    def test_post_body_uses_content_markdown(self):
        body = _build_post_body({"title": "T", "content_markdown": "# Hi", "language": "es"})
        assert body["body"] == "# Hi"
        assert body["title"] == "T"
        assert body["lang"] == "es"
        assert body["font"] == "norm"

    def test_post_body_omits_title_when_empty(self):
        body = _build_post_body({"content_markdown": "body"})
        assert "title" not in body

    def test_post_body_defaults_language_to_en(self):
        body = _build_post_body({"content_markdown": "x"})
        assert body["lang"] == "en"


# ───────────────────────────────────────────────────────────────────────────────
# Adapter — publish path
# ───────────────────────────────────────────────────────────────────────────────

class TestPublish:
    def test_published_happy_path_with_collection(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        _seed_token(config_dir)
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = _config_with_writeas(tmp_path, collection_alias="mysite")

        adapter = WriteAsAPIAdapter()
        with patch("backlink_publisher.publishing.adapters.writeas.http_post", return_value=_ok_publish_response(slug="hi-world")) as mock:
            result = adapter.publish(
                {"title": "Hi", "content_markdown": "body"},
                mode="publish",
                config=cfg,
            )
        assert result.status == "published"
        assert result.platform == "writeas"
        # URL is derived from api_base when not in response
        assert result.published_url == "https://write.as/mysite/hi-world"

        call_kwargs = mock.call_args.kwargs
        # Token scheme, NOT Bearer
        assert call_kwargs["headers"]["Authorization"] == "Token wa_fake_tok"
        # Correct collection endpoint
        assert mock.call_args.args[0] == "https://write.as/api/collections/mysite/posts"

    def test_published_uses_response_url_when_present(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        _seed_token(config_dir)
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = _config_with_writeas(tmp_path, collection_alias="mysite")

        resp = _ok_publish_response(slug="x", url="https://write.as/mysite/custom-x")
        adapter = WriteAsAPIAdapter()
        with patch("backlink_publisher.publishing.adapters.writeas.http_post", return_value=resp):
            result = adapter.publish(
                {"title": "X", "content_markdown": "b"},
                mode="publish", config=cfg,
            )
        assert result.published_url == "https://write.as/mysite/custom-x"

    def test_published_without_collection_uses_default_feed(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        _seed_token(config_dir)
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = _config_with_writeas(tmp_path, collection_alias="")

        adapter = WriteAsAPIAdapter()
        with patch("backlink_publisher.publishing.adapters.writeas.http_post", return_value=_ok_publish_response(slug="anon-z")) as mock:
            result = adapter.publish(
                {"content_markdown": "body"}, mode="publish", config=cfg,
            )
        assert mock.call_args.args[0] == "https://write.as/api/posts"
        assert result.published_url == "https://write.as/anon-z"

    def test_draft_mode_skips_http(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        _seed_token(config_dir)
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = _config_with_writeas(tmp_path, collection_alias="mysite")

        adapter = WriteAsAPIAdapter()
        with patch("backlink_publisher.publishing.adapters.writeas.http_post") as mock:
            result = adapter.publish(
                {"title": "Hi", "content_markdown": "body"},
                mode="draft", config=cfg,
            )
        mock.assert_not_called()
        assert result.status == "drafted"
        assert "mysite" in (result.draft_url or "")

    def test_no_token_raises_dependency_error(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = _config_with_writeas(tmp_path, collection_alias="x")

        adapter = WriteAsAPIAdapter()
        with pytest.raises(DependencyError, match="Write.as token not configured"):
            adapter.publish({"content_markdown": "y"}, "publish", cfg)

    def test_no_writeas_config_raises_dependency_error(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        _seed_token(config_dir)
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = Config()  # no writeas section

        adapter = WriteAsAPIAdapter()
        with pytest.raises(DependencyError, match="Write.as config missing"):
            adapter.publish({"content_markdown": "y"}, "publish", cfg)

    def test_401_raises_external_service_error(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        _seed_token(config_dir)
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = _config_with_writeas(tmp_path)

        adapter = WriteAsAPIAdapter()
        with patch("backlink_publisher.publishing.adapters.writeas.http_post", return_value=_http_status_response(401)):
            with pytest.raises(ExternalServiceError, match="401"):
                adapter.publish({"content_markdown": "y"}, "publish", cfg)

    def test_missing_slug_raises_external_service_error(self, tmp_path, monkeypatch):
        """Defensive: 201 without slug shouldn't silently mint a URL."""
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        _seed_token(config_dir)
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = _config_with_writeas(tmp_path)

        no_slug = _http_status_response(201, {"data": {"id": "p"}})
        adapter = WriteAsAPIAdapter()
        with patch("backlink_publisher.publishing.adapters.writeas.http_post", return_value=no_slug):
            with pytest.raises(ExternalServiceError, match="no slug"):
                adapter.publish({"content_markdown": "y"}, "publish", cfg)

    def test_blocked_content_raises_site_policy_error(self, tmp_path, monkeypatch):
        """Write.as anti-spam returns 201 + data.id=contentisblocked. The
        adapter must surface that as a site-policy rejection, not the generic
        'no slug — check collection_alias' misdiagnosis."""
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        _seed_token(config_dir)
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = _config_with_writeas(tmp_path)

        blocked = _http_status_response(
            201,
            {
                "code": 201,
                "data": {
                    "id": "contentisblocked",
                    "slug": None,
                    "title": "",
                    "body": "",
                    "full_post_url": "",
                },
            },
        )
        adapter = WriteAsAPIAdapter()
        with patch("backlink_publisher.publishing.adapters.writeas.http_post", return_value=blocked):
            with pytest.raises(ExternalServiceError) as excinfo:
                adapter.publish({"content_markdown": "y"}, "publish", cfg)
        msg = str(excinfo.value)
        assert "contentisblocked" in msg
        assert "site policy" in msg
        # Negative assertion: the misdiagnosis must be gone.
        assert "no slug" not in msg
        assert "collection_alias" not in msg

    def test_unknown_id_sentinel_falls_back_to_no_slug(self, tmp_path, monkeypatch):
        """If Write.as introduces a different sentinel (e.g. contentisflagged),
        the new detection branch must NOT swallow it — the generic no-slug
        branch should still catch it so existing operators see no surprise."""
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        _seed_token(config_dir)
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = _config_with_writeas(tmp_path)

        other = _http_status_response(
            201,
            {"code": 201, "data": {"id": "contentisflagged", "slug": None}},
        )
        adapter = WriteAsAPIAdapter()
        with patch("backlink_publisher.publishing.adapters.writeas.http_post", return_value=other):
            with pytest.raises(ExternalServiceError, match="no slug"):
                adapter.publish({"content_markdown": "y"}, "publish", cfg)


# ───────────────────────────────────────────────────────────────────────────────
# WriteAsCdpAdapter blocked-content detection
# ───────────────────────────────────────────────────────────────────────────────

class _FakeCdpPage:
    """Stand-in for the CDP page object handed to WriteAsCdpAdapter.publish."""

    def __init__(self, evaluate_result):
        self._evaluate_result = evaluate_result

    def wait_for_function(self, *_args, **_kwargs):  # noqa: D401 -- mock surface
        return None

    def evaluate(self, *_args, **_kwargs):
        return self._evaluate_result


class _FakeChromeSession:
    """Mocked _ChromeSession that returns a fixed evaluate result."""

    def __init__(self, evaluate_result):
        self._evaluate_result = evaluate_result
        self.closed = False

    def open(self, _url):
        return _FakeCdpPage(self._evaluate_result)

    def close(self):
        self.closed = True


class TestWriteAsCdpBlockedContent:
    def _patch_session(self, monkeypatch, evaluate_result):
        sentinel = _FakeChromeSession(evaluate_result)
        monkeypatch.setattr(
            instant_web_mod, "_ChromeSession", lambda: sentinel
        )
        return sentinel

    def test_blocked_content_raises_site_policy_error(self, monkeypatch):
        evaluate_result = {
            "status": 201,
            "parsed": {
                "code": 201,
                "data": {
                    "id": "contentisblocked",
                    "slug": None,
                    "full_post_url": "",
                },
            },
        }
        session = self._patch_session(monkeypatch, evaluate_result)

        adapter = WriteAsCdpAdapter()
        with pytest.raises(ExternalServiceError) as excinfo:
            adapter.publish(
                {"title": "T", "content_markdown": "body"},
                "publish",
                Config(),
            )
        msg = str(excinfo.value)
        assert "contentisblocked" in msg
        assert "site policy" in msg
        # Negative assertions: the previous misdiagnosis must be gone.
        assert "returned no URL" not in msg
        assert "CDP publish failed" not in msg
        assert session.closed is True

    def test_draft_mode_unaffected(self):
        """Draft mode never hits the network — blocked detection must not
        regress that fast path."""
        adapter = WriteAsCdpAdapter()
        result = adapter.publish({"title": "x"}, "draft", Config())
        assert result.status == "drafted"
        assert result.platform == "writeas"


# ───────────────────────────────────────────────────────────────────────────────
# verify_adapter_setup (offline mode)
# ───────────────────────────────────────────────────────────────────────────────

class TestOfflineVerify:
    def test_missing_config_raises(self):
        with pytest.raises(DependencyError, match="Write.as config missing"):
            verify_adapter_setup("writeas", Config())

    def test_missing_token_file_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cfg = Config(writeas=WriteAsConfig(collection_alias="x"))
        with pytest.raises(DependencyError, match="Write.as token not stored"):
            verify_adapter_setup("writeas", cfg)

    def test_present_config_and_token_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_token(tmp_path)
        cfg = Config(writeas=WriteAsConfig(collection_alias="x"))
        assert verify_adapter_setup("writeas", cfg) is None

    def test_empty_collection_alias_is_valid(self, tmp_path, monkeypatch):
        """Anonymous-style publish is supported; only token+section required."""
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_token(tmp_path)
        cfg = Config(writeas=WriteAsConfig(collection_alias=""))
        assert verify_adapter_setup("writeas", cfg) is None

    def test_cdp_availability_does_not_short_circuit_api_contract(
        self, monkeypatch
    ):
        """Regression: a previous WIP attempted to skip the API verify gate
        when ``WriteAsCdpAdapter.available()`` returned True (Chrome binary
        present). That let machines with Chrome but no writeas-token pass
        verify and crash at publish-time when the dispatch chain fell
        through to the API adapter. Force-flag CDP availability and assert
        verify still requires the API prerequisites.
        """
        from backlink_publisher.publishing.adapters import instant_web as iw

        monkeypatch.setattr(iw.WriteAsCdpAdapter, "available", classmethod(lambda cls, cfg: True))
        with pytest.raises(DependencyError, match="Write.as config missing"):
            verify_adapter_setup("writeas", Config())


# ───────────────────────────────────────────────────────────────────────────────
# Live verify (mode='live')
# ───────────────────────────────────────────────────────────────────────────────

class TestLiveVerify:
    def _setup_bound(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_token(tmp_path)
        return Config(writeas=WriteAsConfig(collection_alias="x"))

    def test_unbound_returns_never_without_http(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cfg = Config(writeas=WriteAsConfig(collection_alias="x"))
        with patch("backlink_publisher.http.get") as mock:
            result = verify_adapter_setup("writeas", cfg, mode="live")
        mock.assert_not_called()
        assert result.last_verify_result == "never"

    def test_happy_path_returns_ok_with_username(self, tmp_path, monkeypatch):
        cfg = self._setup_bound(tmp_path, monkeypatch)
        with patch("backlink_publisher.http.get", return_value=_ok_me_response("opn")) as mock:
            result = verify_adapter_setup("writeas", cfg, mode="live")
        assert result.ok is True
        assert result.identity == "opn"
        assert result.last_verify_result == "ok"
        assert result.dofollow is True
        # GET /api/me, not POST
        call_url = mock.call_args.args[0]
        assert call_url.endswith("/me")
        assert call_url.startswith("https://write.as/api")

    def test_401_returns_token_expired(self, tmp_path, monkeypatch):
        cfg = self._setup_bound(tmp_path, monkeypatch)
        with patch("backlink_publisher.http.get", return_value=_http_status_response(401)):
            result = verify_adapter_setup("writeas", cfg, mode="live")
        assert result.last_verify_result == "token_expired"

    @pytest.mark.parametrize("status", [403, 404, 500, 502, 503])
    def test_non_200_returns_never_not_token_expired(self, tmp_path, monkeypatch, status):
        cfg = self._setup_bound(tmp_path, monkeypatch)
        with patch("backlink_publisher.http.get", return_value=_http_status_response(status)):
            result = verify_adapter_setup("writeas", cfg, mode="live")
        assert result.last_verify_result == "never"

    def test_timeout_returns_timeout(self, tmp_path, monkeypatch):
        cfg = self._setup_bound(tmp_path, monkeypatch)
        with patch("backlink_publisher.http.get", side_effect=requests.Timeout("slow")):
            result = verify_adapter_setup("writeas", cfg, mode="live")
        assert result.last_verify_result == "timeout"

    def test_connection_error_returns_never(self, tmp_path, monkeypatch):
        cfg = self._setup_bound(tmp_path, monkeypatch)
        with patch("backlink_publisher.http.get", side_effect=requests.ConnectionError("dead")):
            result = verify_adapter_setup("writeas", cfg, mode="live")
        assert result.last_verify_result == "never"

    def test_malformed_json_returns_never(self, tmp_path, monkeypatch):
        cfg = self._setup_bound(tmp_path, monkeypatch)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("nope")
        with patch("backlink_publisher.http.get", return_value=resp):
            result = verify_adapter_setup("writeas", cfg, mode="live")
        assert result.last_verify_result == "never"

    def test_empty_data_returns_never(self, tmp_path, monkeypatch):
        """Defensive: 200 + missing data shouldn't be reported as ok."""
        cfg = self._setup_bound(tmp_path, monkeypatch)
        with patch("backlink_publisher.http.get", return_value=_http_status_response(200, {"data": None})):
            result = verify_adapter_setup("writeas", cfg, mode="live")
        assert result.last_verify_result == "never"


# ───────────────────────────────────────────────────────────────────────────────
# Read-only invariant — live verify must never touch the token file
# ───────────────────────────────────────────────────────────────────────────────

class TestReadOnlyInvariant:
    def _snapshot_token(self, tmp_path):
        path = _seed_token(tmp_path)
        return path, path.stat().st_mtime_ns, path.read_text()

    def test_happy_verify_does_not_mutate_token_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        path, mtime_before, content_before = self._snapshot_token(tmp_path)
        cfg = Config(writeas=WriteAsConfig(collection_alias="x"))
        with patch("backlink_publisher.http.get", return_value=_ok_me_response()):
            verify_adapter_setup("writeas", cfg, mode="live")
        assert path.stat().st_mtime_ns == mtime_before
        assert path.read_text() == content_before

    def test_401_verify_does_not_mutate_token_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        path, mtime_before, content_before = self._snapshot_token(tmp_path)
        cfg = Config(writeas=WriteAsConfig(collection_alias="x"))
        with patch("backlink_publisher.http.get", return_value=_http_status_response(401)):
            verify_adapter_setup("writeas", cfg, mode="live")
        assert path.stat().st_mtime_ns == mtime_before
        assert path.read_text() == content_before


# ───────────────────────────────────────────────────────────────────────────────
# Registry integration
# ───────────────────────────────────────────────────────────────────────────────

class TestRegistryIntegration:
    def test_writeas_in_registered_platforms(self):
        from backlink_publisher.publishing.registry import registered_platforms
        assert "writeas" in registered_platforms()

    def test_default_api_base_constant(self):
        assert DEFAULT_API_BASE == "https://write.as/api"
