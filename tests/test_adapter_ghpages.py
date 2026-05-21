"""Unit 7 — GitHub Pages adapter (Plan 2026-05-19-006).

Tests cover:
  - GhpagesConfig + load_ghpages_token / save_ghpages_token contract
  - GitHubPagesAPIAdapter.publish() happy path (201 → AdapterResult)
  - 422 sha-required retry path
  - Draft mode (no PUT, returns drafted with predicted URL)
  - 401 → ExternalServiceError with re-bind hint
  - 403 → distinguished from 401 (rate-limit not auth)
  - Offline verify (config missing / token file missing → DependencyError)
  - Live verify (GET /user mapping for 200 / 401 / 403 / timeout)
  - Live verify read-only invariant (token file untouched)
  - Markdown helper: slug + path + front-matter shape
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from backlink_publisher.config import (
    Config,
    GhpagesConfig,
    load_config,
    load_ghpages_token,
    save_ghpages_token,
)
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing._verify import VerifyResult


# ───────────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────────

def _seed_token(config_dir: Path, token: str = "ghp_fake_pat") -> Path:
    path = config_dir / "ghpages-token.json"
    path.write_text(json.dumps({"token": token}))
    os.chmod(path, 0o600)
    return path


def _config_with_ghpages(tmp_path: Path, repo: str = "owner/repo") -> Config:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f"[ghpages]\nrepo = \"{repo}\"\nbranch = \"gh-pages\"\n"
        "path_template = \"_posts/{date}-{slug}.md\"\n"
    )
    return load_config(cfg_file)


def _ok_put_response(status: int = 201) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {}
    resp.json.return_value = {
        "content": {"sha": "newsha123", "html_url": "https://github.com/o/r/blob/.../x.md"},
        "commit": {"sha": "commit-sha-456"},
    }
    return resp


def _ok_get_user_response(login: str = "operator-handle") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = {"login": login, "id": 12345, "name": "Op Erator"}
    return resp


def _http_status_response(status: int, headers: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.text = "{}"
    resp.json.return_value = {}
    return resp


# ───────────────────────────────────────────────────────────────────────────────
# Token file I/O
# ───────────────────────────────────────────────────────────────────────────────

class TestGhpagesTokenIO:
    def test_load_returns_none_when_missing(self, tmp_path):
        assert load_ghpages_token(tmp_path / "missing.json") is None

    def test_save_and_load_round_trip(self, tmp_path):
        path = tmp_path / "ghpages-token.json"
        save_ghpages_token({"token": "ghp_xyz"}, path)
        assert load_ghpages_token(path) == {"token": "ghp_xyz", "token_rev": 1}

    def test_save_sets_0600_permissions(self, tmp_path):
        path = tmp_path / "ghpages-token.json"
        save_ghpages_token({"token": "abc"}, path)
        assert os.stat(path).st_mode & 0o777 == 0o600


# ───────────────────────────────────────────────────────────────────────────────
# Config wiring
# ───────────────────────────────────────────────────────────────────────────────

class TestGhpagesConfig:
    def test_defaults(self):
        cfg = GhpagesConfig()
        assert cfg.repo == ""
        assert cfg.branch == "gh-pages"
        assert cfg.path_template == "_posts/{date}-{slug}.md"

    def test_loader_parses_section(self, tmp_path):
        cfg = _config_with_ghpages(tmp_path, repo="me/blog")
        assert cfg.ghpages is not None
        assert cfg.ghpages.repo == "me/blog"
        assert cfg.ghpages.branch == "gh-pages"

    def test_loader_returns_none_when_section_absent(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text("")
        cfg = load_config(cfg_file)
        assert cfg.ghpages is None

    def test_config_ghpages_token_path_is_in_config_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cfg = Config()
        assert cfg.ghpages_token_path == tmp_path / "ghpages-token.json"


# ───────────────────────────────────────────────────────────────────────────────
# Helper functions (slug, path, body)
# ───────────────────────────────────────────────────────────────────────────────

class TestGhpagesHelpers:
    def test_slugify_replaces_spaces_and_punctuation(self):
        from backlink_publisher.publishing.adapters.ghpages import _slugify
        assert _slugify("Hello, World!") == "hello-world"

    def test_slugify_handles_consecutive_separators(self):
        from backlink_publisher.publishing.adapters.ghpages import _slugify
        assert _slugify("a---b   c") == "a-b-c"

    def test_slugify_strips_leading_dot(self):
        """Leading dots would make Jekyll skip the file."""
        from backlink_publisher.publishing.adapters.ghpages import _slugify
        assert not _slugify(".hidden").startswith(".")

    def test_slugify_fallback_on_empty(self):
        from backlink_publisher.publishing.adapters.ghpages import _slugify
        assert _slugify("!!!") == "post"

    def test_render_target_path_substitutes(self):
        from backlink_publisher.publishing.adapters.ghpages import _render_target_path
        result = _render_target_path("_posts/{date}-{slug}.md", slug="hello",
                                     date_iso="2026-05-19")
        assert result == "_posts/2026-05-19-hello.md"

    def test_build_markdown_body_includes_front_matter(self):
        from backlink_publisher.publishing.adapters.ghpages import _build_markdown_body
        body = _build_markdown_body({
            "title": "Why backlinks matter",
            "content_markdown": "# Backlinks\n\nThey help.",
            "tags": ["seo", "backlinks"],
        })
        assert body.startswith("---\n")
        assert "layout: post" in body
        assert "\"Why backlinks matter\"" in body
        assert "\"seo\", \"backlinks\"" in body
        assert "# Backlinks" in body

    def test_published_url_jekyll_post_md(self):
        """_posts/YYYY-MM-DD-slug.md → Jekyll serves /YYYY/MM/DD/slug.html."""
        from backlink_publisher.publishing.adapters.ghpages import _published_url
        url = _published_url("owner/repo", "_posts/2026-05-21-hello-world.md")
        assert url == "https://owner.github.io/repo/2026/05/21/hello-world.html"

    def test_published_url_jekyll_post_markdown_ext(self):
        from backlink_publisher.publishing.adapters.ghpages import _published_url
        url = _published_url("owner/repo", "_posts/2026-01-03-my-post.markdown")
        assert url == "https://owner.github.io/repo/2026/01/03/my-post.html"

    def test_published_url_non_posts_path_unchanged(self):
        """Custom layouts (pages/, docs/, etc.) are served at the raw path."""
        from backlink_publisher.publishing.adapters.ghpages import _published_url
        url = _published_url("owner/repo", "pages/about.md")
        assert url == "https://owner.github.io/repo/pages/about.md"

    def test_published_url_no_underscore_posts_prefix(self):
        """Path without leading _posts/ is not rewritten even if date-slug named."""
        from backlink_publisher.publishing.adapters.ghpages import _published_url
        url = _published_url("owner/blog", "posts/2026-05-21-hello.md")
        assert url == "https://owner.github.io/blog/posts/2026-05-21-hello.md"


# ───────────────────────────────────────────────────────────────────────────────
# publish() happy path
# ───────────────────────────────────────────────────────────────────────────────

class TestGhpagesPublishHappy:
    def test_publishes_new_file_with_base64_content(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_token(tmp_path, token="PAT_X")
        cfg = _config_with_ghpages(tmp_path, repo="me/blog")
        from backlink_publisher.publishing.adapters.ghpages import GitHubPagesAPIAdapter

        with patch("requests.put", return_value=_ok_put_response(201)) as mock_put:
            result = GitHubPagesAPIAdapter().publish(
                {"title": "Hello", "content_markdown": "# Hi\n\nbody",
                 "tags": ["a"], "slug": "hello"},
                mode="published",
                config=cfg,
            )

        assert result.status == "published"
        assert result.platform == "ghpages"
        # Jekyll rewrites _posts/YYYY-MM-DD-slug.md → /YYYY/MM/DD/slug.html
        assert "github.io/blog/" in result.published_url
        assert "_posts/" not in result.published_url
        assert "hello" in result.published_url
        assert result.published_url.endswith(".html")

        call = mock_put.call_args
        url = call.args[0]
        assert "api.github.com/repos/me/blog/contents/_posts/" in url
        body = call.kwargs["json"]
        decoded = base64.b64decode(body["content"]).decode("utf-8")
        assert "# Hi" in decoded
        assert body["branch"] == "gh-pages"
        # No sha on first attempt (creating new file)
        assert "sha" not in body
        headers = call.kwargs["headers"]
        assert headers["Authorization"] == "Bearer PAT_X"
        assert headers["X-GitHub-Api-Version"] == "2022-11-28"

    def test_draft_mode_skips_put_and_predicts_url(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_token(tmp_path)
        cfg = _config_with_ghpages(tmp_path, repo="me/blog")
        from backlink_publisher.publishing.adapters.ghpages import GitHubPagesAPIAdapter

        with patch("requests.put") as mock_put:
            result = GitHubPagesAPIAdapter().publish(
                {"title": "Draft thing", "slug": "drafty"},
                mode="draft",
                config=cfg,
            )

        assert mock_put.call_count == 0
        assert result.status == "drafted"
        assert result.draft_url is not None
        assert "drafty" in result.draft_url

    def test_422_triggers_get_sha_retry(self, tmp_path, monkeypatch):
        """File exists → 422 → GET sha → PUT with sha → 200."""
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_token(tmp_path)
        cfg = _config_with_ghpages(tmp_path, repo="me/blog")
        from backlink_publisher.publishing.adapters.ghpages import GitHubPagesAPIAdapter

        put_responses = [
            _http_status_response(422),
            _ok_put_response(200),
        ]
        get_resp = MagicMock(status_code=200)
        get_resp.json.return_value = {"sha": "existing-sha-789"}

        with patch("requests.put", side_effect=put_responses) as mock_put, \
             patch("requests.get", return_value=get_resp) as mock_get:
            result = GitHubPagesAPIAdapter().publish(
                {"title": "Update", "content_markdown": "v2", "slug": "updated"},
                mode="published",
                config=cfg,
            )

        assert result.status == "published"
        assert mock_put.call_count == 2
        assert mock_get.call_count == 1
        second_put_body = mock_put.call_args_list[1].kwargs["json"]
        assert second_put_body["sha"] == "existing-sha-789"


# ───────────────────────────────────────────────────────────────────────────────
# publish() error paths
# ───────────────────────────────────────────────────────────────────────────────

class TestGhpagesPublishErrors:
    def test_missing_token_raises_dependency_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        # No token file written
        cfg = _config_with_ghpages(tmp_path)
        from backlink_publisher.publishing.adapters.ghpages import GitHubPagesAPIAdapter

        with pytest.raises(DependencyError, match="PAT not configured"):
            GitHubPagesAPIAdapter().publish(
                {"title": "x", "slug": "x"}, mode="published", config=cfg,
            )

    def test_missing_config_raises_dependency_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_token(tmp_path)
        cfg = Config()  # no ghpages config
        from backlink_publisher.publishing.adapters.ghpages import GitHubPagesAPIAdapter

        with pytest.raises(DependencyError, match="ghpages"):
            GitHubPagesAPIAdapter().publish(
                {"title": "x", "slug": "x"}, mode="published", config=cfg,
            )

    def test_401_raises_external_service_error_with_rebind_hint(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_token(tmp_path)
        cfg = _config_with_ghpages(tmp_path)
        from backlink_publisher.publishing.adapters.ghpages import GitHubPagesAPIAdapter

        with patch("requests.put", return_value=_http_status_response(401)):
            with pytest.raises(ExternalServiceError, match="re-bind"):
                GitHubPagesAPIAdapter().publish(
                    {"title": "x", "slug": "x"}, mode="published", config=cfg,
                )

    def test_403_distinguished_from_401(self, tmp_path, monkeypatch):
        """403 must NOT be reported as 'rejected' — could be rate limit."""
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_token(tmp_path)
        cfg = _config_with_ghpages(tmp_path)
        from backlink_publisher.publishing.adapters.ghpages import GitHubPagesAPIAdapter

        resp = _http_status_response(403, headers={"retry-after": "60"})
        with patch("requests.put", return_value=resp):
            with pytest.raises(ExternalServiceError) as exc:
                GitHubPagesAPIAdapter().publish(
                    {"title": "x", "slug": "x"}, mode="published", config=cfg,
                )

        msg = str(exc.value)
        assert "403" in msg
        assert "retry-after=60" in msg
        # The 401 hint about "regenerate" should NOT appear for 403
        assert "rejected" not in msg.lower()


# ───────────────────────────────────────────────────────────────────────────────
# Offline verify
# ───────────────────────────────────────────────────────────────────────────────

class TestGhpagesOfflineVerify:
    def test_offline_passes_when_config_and_token_present(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_token(tmp_path)
        cfg = _config_with_ghpages(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        # Returns None on success (mode='offline' contract)
        assert verify_adapter_setup("ghpages", cfg, mode="offline") is None

    def test_offline_raises_when_token_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cfg = _config_with_ghpages(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with pytest.raises(DependencyError, match="PAT"):
            verify_adapter_setup("ghpages", cfg, mode="offline")

    def test_offline_raises_when_config_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_token(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with pytest.raises(DependencyError, match="ghpages"):
            verify_adapter_setup("ghpages", Config(), mode="offline")


# ───────────────────────────────────────────────────────────────────────────────
# Live verify
# ───────────────────────────────────────────────────────────────────────────────

class TestGhpagesLiveVerify:
    def test_returns_ok_with_login_as_identity(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_token(tmp_path, token="PAT_LIVE")
        cfg = _config_with_ghpages(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("requests.get", return_value=_ok_get_user_response("dex")):
            result = verify_adapter_setup("ghpages", cfg, mode="live")

        assert isinstance(result, VerifyResult)
        assert result.ok is True
        assert result.identity == "dex"
        assert result.dofollow is True
        assert result.last_verify_result == "ok"
        assert result.last_verified_at is not None
        assert result.last_verified_at.endswith("Z")

    def test_calls_user_endpoint_with_bearer_and_api_version(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_token(tmp_path, token="PAT_HEADER_CHECK")
        cfg = _config_with_ghpages(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("requests.get", return_value=_ok_get_user_response()) as mock_get:
            verify_adapter_setup("ghpages", cfg, mode="live")

        assert mock_get.call_count == 1
        url = mock_get.call_args.args[0]
        assert url == "https://api.github.com/user"
        headers = mock_get.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer PAT_HEADER_CHECK"
        assert headers["X-GitHub-Api-Version"] == "2022-11-28"

    def test_401_yields_token_expired(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_token(tmp_path)
        cfg = _config_with_ghpages(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("requests.get", return_value=_http_status_response(401)):
            result = verify_adapter_setup("ghpages", cfg, mode="live")

        assert result.ok is False
        assert result.last_verify_result == "token_expired"
        assert any("regenerate" in b.lower() for b in result.blockers)

    def test_403_yields_never_not_token_expired(self, tmp_path, monkeypatch):
        """Secondary rate limit / missing scope must NOT be reported as token expired."""
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_token(tmp_path)
        cfg = _config_with_ghpages(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        resp = _http_status_response(403, headers={"retry-after": "120"})
        with patch("requests.get", return_value=resp):
            result = verify_adapter_setup("ghpages", cfg, mode="live")

        assert result.ok is False
        assert result.last_verify_result == "never"  # NOT token_expired
        assert any("retry-after=120s" in b for b in result.blockers)

    def test_timeout_yields_timeout_result(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_token(tmp_path)
        cfg = _config_with_ghpages(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("requests.get", side_effect=requests.Timeout("slow")):
            result = verify_adapter_setup("ghpages", cfg, mode="live")

        assert result.ok is False
        assert result.last_verify_result == "timeout"

    def test_connection_error_yields_never(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_token(tmp_path)
        cfg = _config_with_ghpages(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("requests.get", side_effect=requests.ConnectionError("dns")):
            result = verify_adapter_setup("ghpages", cfg, mode="live")

        assert result.ok is False
        assert result.last_verify_result == "never"

    def test_no_token_short_circuits_to_never(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cfg = _config_with_ghpages(tmp_path)  # no token seeded
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("requests.get") as mock_get:
            result = verify_adapter_setup("ghpages", cfg, mode="live")

        assert result.ok is False
        assert result.last_verify_result == "never"
        assert mock_get.call_count == 0


class TestGhpagesLiveVerifyReadOnly:
    """Strict read-only — verify must NOT touch ghpages-token.json."""

    def test_verify_does_not_modify_token_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        token_file = _seed_token(tmp_path, token="ORIGINAL_PAT")
        mtime_before = token_file.stat().st_mtime
        contents_before = token_file.read_text()
        cfg = _config_with_ghpages(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("requests.get", return_value=_ok_get_user_response()):
            verify_adapter_setup("ghpages", cfg, mode="live")

        assert token_file.stat().st_mtime == mtime_before
        assert token_file.read_text() == contents_before

    def test_verify_does_not_modify_token_on_401(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        token_file = _seed_token(tmp_path)
        contents_before = token_file.read_text()
        cfg = _config_with_ghpages(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("requests.get", return_value=_http_status_response(401)):
            result = verify_adapter_setup("ghpages", cfg, mode="live")

        assert result.last_verify_result == "token_expired"
        assert token_file.read_text() == contents_before


# ───────────────────────────────────────────────────────────────────────────────
# Registry wiring
# ───────────────────────────────────────────────────────────────────────────────

class TestGhpagesRegistration:
    def test_ghpages_appears_in_registered_platforms(self):
        from backlink_publisher.publishing.registry import registered_platforms
        assert "ghpages" in registered_platforms()
