"""Unit 8 — Hashnode adapter (Plan 2026-05-19-006).

Tests cover:
  - HashnodeConfig + load_hashnode_token / save_hashnode_token contract
  - HashnodeAPIAdapter.publish() happy path (200 → AdapterResult)
  - Draft mode (no GraphQL POST, returns drafted sentinel)
  - 401 → ExternalServiceError with re-bind hint
  - non-200 / GraphQL errors → ExternalServiceError
  - publishPost with empty url → ExternalServiceError (defensive)
  - Authorization header is the bare PAT (no "Bearer " prefix)
  - Offline verify (config missing / token file missing → DependencyError)
  - Live verify (me query mapping for 200 / 401 / auth-error / timeout)
  - Live verify read-only invariant (token file untouched)
  - Tag truncation to 5 + slug generation contract
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
    HashnodeConfig,
    load_config,
    load_hashnode_token,
    save_hashnode_token,
)
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing.adapters import (
    HashnodeAPIAdapter,
    verify_adapter_setup,
)
from backlink_publisher.publishing.adapters.hashnode import (
    HASHNODE_API,
    ME_QUERY,
    PUBLISH_POST_MUTATION,
    _build_publish_input,
    _required_headers,
    _tag_slug,
)


# ───────────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────────

def _seed_token(config_dir: Path, token: str = "hn_fake_pat") -> Path:
    path = config_dir / "hashnode-token.json"
    path.write_text(json.dumps({"token": token}))
    os.chmod(path, 0o600)
    return path


def _config_with_hashnode(
    tmp_path: Path, publication_id: str = "pub_xyz", host: str = ""
) -> Config:
    cfg_file = tmp_path / "config.toml"
    host_line = f'\nhost = "{host}"' if host else ""
    cfg_file.write_text(
        f'[hashnode]\npublication_id = "{publication_id}"{host_line}\n'
    )
    return load_config(cfg_file)


def _ok_publish_response(url: str = "https://op.hashnode.dev/my-post") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = {
        "data": {
            "publishPost": {
                "post": {"id": "p1", "slug": "my-post", "url": url}
            }
        }
    }
    return resp


def _ok_me_response(username: str = "operator") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = {
        "data": {"me": {"id": "u1", "username": username, "name": "Op"}}
    }
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

class TestHashnodeTokenIO:
    def test_load_returns_none_when_missing(self, tmp_path):
        assert load_hashnode_token(tmp_path / "missing.json") is None

    def test_save_and_load_round_trip(self, tmp_path):
        path = tmp_path / "hashnode-token.json"
        save_hashnode_token({"token": "hn_xyz"}, path)
        assert load_hashnode_token(path) == {"token": "hn_xyz"}

    def test_save_sets_0600_permissions(self, tmp_path):
        path = tmp_path / "hashnode-token.json"
        save_hashnode_token({"token": "abc"}, path)
        assert os.stat(path).st_mode & 0o777 == 0o600


# ───────────────────────────────────────────────────────────────────────────────
# Config wiring
# ───────────────────────────────────────────────────────────────────────────────

class TestHashnodeConfig:
    def test_defaults(self):
        cfg = HashnodeConfig()
        assert cfg.publication_id == ""
        assert cfg.host == ""

    def test_loader_parses_section(self, tmp_path):
        cfg = _config_with_hashnode(tmp_path, publication_id="pub_123", host="me.hashnode.dev")
        assert cfg.hashnode is not None
        assert cfg.hashnode.publication_id == "pub_123"
        assert cfg.hashnode.host == "me.hashnode.dev"

    def test_no_section_yields_none(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text("")
        cfg = load_config(cfg_file)
        assert cfg.hashnode is None


# ───────────────────────────────────────────────────────────────────────────────
# Required headers contract — the bare-PAT rule that's easy to break
# ───────────────────────────────────────────────────────────────────────────────

class TestRequiredHeaders:
    def test_authorization_is_bare_pat_no_bearer_prefix(self):
        headers = _required_headers("pat_abc")
        assert headers["Authorization"] == "pat_abc"
        assert not headers["Authorization"].startswith("Bearer ")

    def test_content_type_is_json(self):
        assert _required_headers("x")["Content-Type"] == "application/json"


# ───────────────────────────────────────────────────────────────────────────────
# Helpers — tag slugging + publish input shape
# ───────────────────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_tag_slug_lowercases_and_dashes(self):
        assert _tag_slug("Web Dev") == "web-dev"

    def test_tag_slug_collapses_repeated_separators(self):
        assert _tag_slug("a //   b") == "a-b"

    def test_tag_slug_falls_back_for_empty(self):
        assert _tag_slug("") == "tag"
        assert _tag_slug("!!!") == "tag"

    def test_publish_input_caps_tags_at_five(self):
        payload = {"title": "T", "content_markdown": "body", "tags": ["a", "b", "c", "d", "e", "f", "g"]}
        result = _build_publish_input(payload, "pub1")
        assert len(result["tags"]) == 5
        assert [t["name"] for t in result["tags"]] == ["a", "b", "c", "d", "e"]

    def test_publish_input_prefers_content_markdown_over_html(self):
        payload = {
            "title": "T",
            "content_markdown": "# heading",
            "content_html": "<h1>heading</h1>",
            "tags": [],
        }
        result = _build_publish_input(payload, "pub1")
        assert result["contentMarkdown"] == "# heading"

    def test_publish_input_includes_publication_id(self):
        payload = {"title": "T", "content_markdown": "body", "tags": []}
        result = _build_publish_input(payload, "pub_abc")
        assert result["publicationId"] == "pub_abc"


# ───────────────────────────────────────────────────────────────────────────────
# Adapter — publish path
# ───────────────────────────────────────────────────────────────────────────────

class TestPublish:
    def _adapter_and_config(self, tmp_path):
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        _seed_token(config_dir)
        cfg = _config_with_hashnode(tmp_path, publication_id="pub_z")
        # Re-point token path at our temp dir
        cfg = Config(**{**cfg.__dict__, "hashnode": cfg.hashnode})
        return HashnodeAPIAdapter(), cfg, config_dir

    def test_published_happy_path(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        _seed_token(config_dir)
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = _config_with_hashnode(tmp_path, publication_id="pub_z")

        adapter = HashnodeAPIAdapter()
        with patch.object(requests, "post", return_value=_ok_publish_response("https://op.hashnode.dev/x")) as mock:
            result = adapter.publish(
                {"title": "Hi", "content_markdown": "body", "tags": ["seo"]},
                mode="publish",
                config=cfg,
            )
        assert result.status == "published"
        assert result.platform == "hashnode"
        assert result.published_url == "https://op.hashnode.dev/x"
        # Verify the call carried bare-PAT auth (regression: easy to add Bearer)
        call_kwargs = mock.call_args.kwargs
        assert call_kwargs["headers"]["Authorization"] == "hn_fake_pat"
        assert call_kwargs["json"]["query"].strip().startswith("mutation PublishPost")
        assert call_kwargs["json"]["variables"]["input"]["publicationId"] == "pub_z"

    def test_draft_mode_skips_http(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        _seed_token(config_dir)
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = _config_with_hashnode(tmp_path, publication_id="pub_z")

        adapter = HashnodeAPIAdapter()
        with patch.object(requests, "post") as mock:
            result = adapter.publish(
                {"title": "Hi", "content_markdown": "body"},
                mode="draft",
                config=cfg,
            )
        mock.assert_not_called()
        assert result.status == "drafted"
        assert result.platform == "hashnode"
        assert "pub_z" in (result.draft_url or "")

    def test_no_token_raises_dependency_error(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = _config_with_hashnode(tmp_path, publication_id="pub_z")

        adapter = HashnodeAPIAdapter()
        with pytest.raises(DependencyError, match="Hashnode PAT not configured"):
            adapter.publish({"title": "x", "content_markdown": "y"}, "publish", cfg)

    def test_no_publication_id_raises_dependency_error(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        _seed_token(config_dir)
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = Config(hashnode=HashnodeConfig(publication_id=""))

        adapter = HashnodeAPIAdapter()
        with pytest.raises(DependencyError, match="Hashnode config missing"):
            adapter.publish({"title": "x", "content_markdown": "y"}, "publish", cfg)

    def test_401_raises_external_service_error(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        _seed_token(config_dir)
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = _config_with_hashnode(tmp_path, publication_id="pub_z")

        adapter = HashnodeAPIAdapter()
        with patch.object(requests, "post", return_value=_http_status_response(401)):
            with pytest.raises(ExternalServiceError, match="HTTP 401"):
                adapter.publish({"title": "x", "content_markdown": "y"}, "publish", cfg)

    def test_graphql_errors_only_raises_external_service_error(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        _seed_token(config_dir)
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = _config_with_hashnode(tmp_path, publication_id="pub_z")

        err_resp = _http_status_response(200, {"errors": [{"message": "tag slug invalid"}]})
        adapter = HashnodeAPIAdapter()
        with patch.object(requests, "post", return_value=err_resp):
            with pytest.raises(ExternalServiceError, match="tag slug invalid"):
                adapter.publish({"title": "x", "content_markdown": "y"}, "publish", cfg)

    def test_missing_post_url_raises_external_service_error(self, tmp_path, monkeypatch):
        """Defensive: 200 + empty url shouldn't silently produce a fake URL."""
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        _seed_token(config_dir)
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = _config_with_hashnode(tmp_path, publication_id="pub_z")

        missing_url = _http_status_response(200, {"data": {"publishPost": {"post": {"slug": "x"}}}})
        adapter = HashnodeAPIAdapter()
        with patch.object(requests, "post", return_value=missing_url):
            with pytest.raises(ExternalServiceError, match="no URL"):
                adapter.publish({"title": "x", "content_markdown": "y"}, "publish", cfg)


# ───────────────────────────────────────────────────────────────────────────────
# verify_adapter_setup (offline mode)
# ───────────────────────────────────────────────────────────────────────────────

class TestOfflineVerify:
    def test_missing_config_raises(self):
        with pytest.raises(DependencyError, match="Hashnode config missing"):
            verify_adapter_setup("hashnode", Config())

    def test_missing_token_file_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cfg = Config(hashnode=HashnodeConfig(publication_id="pub_x"))
        with pytest.raises(DependencyError, match="Hashnode PAT not stored"):
            verify_adapter_setup("hashnode", cfg)

    def test_present_config_and_token_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_token(tmp_path)
        cfg = Config(hashnode=HashnodeConfig(publication_id="pub_x"))
        assert verify_adapter_setup("hashnode", cfg) is None


# ───────────────────────────────────────────────────────────────────────────────
# Live verify (mode='live')
# ───────────────────────────────────────────────────────────────────────────────

class TestLiveVerify:
    def _setup_bound(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_token(tmp_path)
        return Config(hashnode=HashnodeConfig(publication_id="pub_x"))

    def test_unbound_returns_never(self, tmp_path, monkeypatch):
        # No token file → live verify must not call HTTP
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cfg = Config(hashnode=HashnodeConfig(publication_id="pub_x"))
        with patch.object(requests, "post") as mock:
            result = verify_adapter_setup("hashnode", cfg, mode="live")
        mock.assert_not_called()
        assert result.ok is False
        assert result.last_verify_result == "never"

    def test_happy_path_returns_ok_with_username(self, tmp_path, monkeypatch):
        cfg = self._setup_bound(tmp_path, monkeypatch)
        with patch.object(requests, "post", return_value=_ok_me_response("opx")) as mock:
            result = verify_adapter_setup("hashnode", cfg, mode="live")
        assert result.ok is True
        assert result.identity == "opx"
        assert result.last_verify_result == "ok"
        assert result.dofollow is True
        # Verify the query shape sent
        call = mock.call_args.kwargs
        assert call["json"]["query"] == ME_QUERY
        # And the URL
        assert mock.call_args.args[0] == HASHNODE_API

    def test_401_returns_token_expired(self, tmp_path, monkeypatch):
        cfg = self._setup_bound(tmp_path, monkeypatch)
        with patch.object(requests, "post", return_value=_http_status_response(401)):
            result = verify_adapter_setup("hashnode", cfg, mode="live")
        assert result.ok is False
        assert result.last_verify_result == "token_expired"

    def test_graphql_unauthorized_returns_token_expired(self, tmp_path, monkeypatch):
        """Hashnode sometimes returns 200 + errors: [{message: 'Unauthorized'}]."""
        cfg = self._setup_bound(tmp_path, monkeypatch)
        body = {"errors": [{"message": "Unauthorized — invalid token"}]}
        with patch.object(requests, "post", return_value=_http_status_response(200, body)):
            result = verify_adapter_setup("hashnode", cfg, mode="live")
        assert result.last_verify_result == "token_expired"

    def test_graphql_generic_error_returns_never(self, tmp_path, monkeypatch):
        cfg = self._setup_bound(tmp_path, monkeypatch)
        body = {"errors": [{"message": "rate limited"}]}
        with patch.object(requests, "post", return_value=_http_status_response(200, body)):
            result = verify_adapter_setup("hashnode", cfg, mode="live")
        assert result.last_verify_result == "never"

    @pytest.mark.parametrize("status", [403, 500, 502, 503])
    def test_non_200_returns_never_not_token_expired(self, tmp_path, monkeypatch, status):
        cfg = self._setup_bound(tmp_path, monkeypatch)
        with patch.object(requests, "post", return_value=_http_status_response(status)):
            result = verify_adapter_setup("hashnode", cfg, mode="live")
        assert result.last_verify_result == "never"

    def test_timeout_returns_timeout(self, tmp_path, monkeypatch):
        cfg = self._setup_bound(tmp_path, monkeypatch)
        with patch.object(requests, "post", side_effect=requests.Timeout("slow")):
            result = verify_adapter_setup("hashnode", cfg, mode="live")
        assert result.last_verify_result == "timeout"

    def test_connection_error_returns_never(self, tmp_path, monkeypatch):
        cfg = self._setup_bound(tmp_path, monkeypatch)
        with patch.object(requests, "post", side_effect=requests.ConnectionError("dead")):
            result = verify_adapter_setup("hashnode", cfg, mode="live")
        assert result.last_verify_result == "never"

    def test_malformed_json_returns_never(self, tmp_path, monkeypatch):
        cfg = self._setup_bound(tmp_path, monkeypatch)
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        resp.json.side_effect = ValueError("nope")
        with patch.object(requests, "post", return_value=resp):
            result = verify_adapter_setup("hashnode", cfg, mode="live")
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
        cfg = Config(hashnode=HashnodeConfig(publication_id="pub_x"))
        with patch.object(requests, "post", return_value=_ok_me_response()):
            verify_adapter_setup("hashnode", cfg, mode="live")
        assert path.stat().st_mtime_ns == mtime_before
        assert path.read_text() == content_before

    def test_401_verify_does_not_mutate_token_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        path, mtime_before, content_before = self._snapshot_token(tmp_path)
        cfg = Config(hashnode=HashnodeConfig(publication_id="pub_x"))
        with patch.object(requests, "post", return_value=_http_status_response(401)):
            verify_adapter_setup("hashnode", cfg, mode="live")
        assert path.stat().st_mtime_ns == mtime_before
        assert path.read_text() == content_before


# ───────────────────────────────────────────────────────────────────────────────
# Dispatcher integration — adapter is reachable via registry
# ───────────────────────────────────────────────────────────────────────────────

class TestRegistryIntegration:
    def test_hashnode_in_registered_platforms(self):
        from backlink_publisher.publishing.registry import registered_platforms
        assert "hashnode" in registered_platforms()

    def test_publish_mutation_query_shape(self):
        """The mutation string must reference publishPost + input variable."""
        assert "mutation PublishPost" in PUBLISH_POST_MUTATION
        assert "publishPost(input: $input)" in PUBLISH_POST_MUTATION
        assert "post { id slug url }" in PUBLISH_POST_MUTATION
