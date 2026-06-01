"""Wave 1 Unit 3: GitLab Pages adapter tests (Plan 2026-06-01-007)."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.config.types import GitlabPagesConfig
from backlink_publisher.publishing.adapters.gitlabpages import (
    GitLabPagesAPIAdapter,
    _build_html_body,
    _load_token,
    _published_url,
    _required_headers,
)

_POST = "backlink_publisher.publishing.adapters.gitlabpages.http_post"
_PUT = "backlink_publisher.publishing.adapters.gitlabpages.http_put"


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    cfg = MagicMock()
    cfg.gitlabpages_token_path = tmp_path / "gitlabpages-token.json"
    cfg.gitlabpages = GitlabPagesConfig(
        project="me/myrepo", branch="main",
        path_template="public/{slug}/index.html", pages_base_url="",
    )
    return cfg


@pytest.fixture
def config_with_token(config):
    config.gitlabpages_token_path.write_text(json.dumps({"token": "glpat_secret_99", "token_rev": 1}))
    os.chmod(config.gitlabpages_token_path, 0o600)  # R10: real bind writes 0o600
    return config


def _resp(status, text="", json_body=None):
    r = MagicMock(status_code=status, text=text)
    r.headers = {}
    if json_body is not None:
        r.json.return_value = json_body
    return r


class TestRequiredHeaders:
    def test_uses_private_token_not_bearer(self):
        headers = _required_headers("pat")
        assert headers["PRIVATE-TOKEN"] == "pat"
        assert "Authorization" not in headers


class TestLoadToken:
    def test_raises_with_pages_precondition(self, config):
        with pytest.raises(DependencyError, match="pages"):
            _load_token(config)

    def test_returns_token(self, config_with_token):
        assert _load_token(config_with_token) == "glpat_secret_99"

    def test_rejects_world_readable_token_file(self, config):
        """R10: a 0o644 PAT file must be refused."""
        config.gitlabpages_token_path.write_text(json.dumps({"token": "x"}))
        os.chmod(config.gitlabpages_token_path, 0o644)
        with pytest.raises(DependencyError, match="0o600"):
            _load_token(config)


class TestPublishedUrl:
    def test_default_namespace_project_path(self):
        cfg = GitlabPagesConfig(project="me/myrepo", pages_base_url="")
        assert _published_url(cfg, "public/test/index.html") == "https://me.gitlab.io/myrepo/test/"

    def test_namespace_root_project(self):
        cfg = GitlabPagesConfig(project="me/me.gitlab.io", pages_base_url="")
        assert _published_url(cfg, "public/test/index.html") == "https://me.gitlab.io/test/"

    def test_pages_base_url_override(self):
        cfg = GitlabPagesConfig(project="me/myrepo", pages_base_url="https://myrepo-a1b2c3.gitlab.io")
        assert _published_url(cfg, "public/test/index.html") == "https://myrepo-a1b2c3.gitlab.io/test/"

    def test_subgroup_path(self):
        cfg = GitlabPagesConfig(project="acme/docs/manual", pages_base_url="")
        assert _published_url(cfg, "public/x/index.html") == "https://acme.gitlab.io/docs/manual/x/"


class TestAvailable:
    def test_false_when_no_config(self, config):
        config.gitlabpages = None
        assert GitLabPagesAPIAdapter.available(config) is False

    def test_false_when_no_token(self, config):
        assert GitLabPagesAPIAdapter.available(config) is False

    def test_true_when_config_and_token(self, config_with_token):
        assert GitLabPagesAPIAdapter.available(config_with_token) is True


class TestPublish:
    def test_happy_path_post_create(self, config_with_token):
        with patch(_POST, return_value=_resp(201, json_body={})):
            result = GitLabPagesAPIAdapter().publish(
                {"title": "Test", "slug": "test", "content_markdown": "Body"},
                mode="live", config=config_with_token,
            )
        assert result.status == "published"
        assert result.platform == "gitlabpages"
        assert result.published_url == "https://me.gitlab.io/myrepo/test/"

    def test_existing_file_falls_through_to_put(self, config_with_token):
        post = _resp(400, text="A file with this name already exists")
        put = _resp(200, json_body={})
        with patch(_POST, return_value=post), patch(_PUT, return_value=put) as mput:
            result = GitLabPagesAPIAdapter().publish(
                {"title": "T", "slug": "test"}, mode="live", config=config_with_token,
            )
        assert result.status == "published"
        assert mput.call_count == 1

    def test_noop_byte_identical_put_400_is_success(self, config_with_token):
        post = _resp(400, text="A file with this name already exists")
        put = _resp(400, text="A file with these contents already exists")
        with patch(_POST, return_value=post), patch(_PUT, return_value=put):
            result = GitLabPagesAPIAdapter().publish(
                {"title": "T", "slug": "test"}, mode="live", config=config_with_token,
            )
        assert result.status == "published"

    def test_put_400_without_marker_raises_not_silent_success(self, config_with_token):
        """A PUT 400 that is NOT the no-op/already-exists marker must raise, not
        silently report success (would mask a permission/path failure)."""
        post = _resp(400, text="A file with this name already exists")
        put = _resp(400, text="403 Forbidden: insufficient permission to push")
        with patch(_POST, return_value=post), patch(_PUT, return_value=put):
            with pytest.raises(ExternalServiceError, match="400"):
                GitLabPagesAPIAdapter().publish(
                    {"title": "T", "slug": "test"}, mode="live", config=config_with_token,
                )

    def test_draft_mode_no_commit(self, config_with_token):
        with patch(_POST) as mpost, patch(_PUT) as mput:
            result = GitLabPagesAPIAdapter().publish(
                {"title": "Draft", "slug": "d"}, mode="draft", config=config_with_token,
            )
        assert result.status == "drafted"
        assert mpost.call_count == 0 and mput.call_count == 0

    def test_private_token_header_sent(self, config_with_token):
        with patch(_POST, return_value=_resp(201, json_body={})) as mpost:
            GitLabPagesAPIAdapter().publish(
                {"title": "T", "slug": "t"}, mode="live", config=config_with_token,
            )
        headers = mpost.call_args.kwargs["headers"]
        assert headers["PRIVATE-TOKEN"] == "glpat_secret_99"
        assert "Authorization" not in headers

    def test_401_raises(self, config_with_token):
        with patch(_POST, return_value=_resp(401, text="unauthorized")):
            with pytest.raises(ExternalServiceError, match="401"):
                GitLabPagesAPIAdapter().publish(
                    {"title": "T", "slug": "t"}, mode="live", config=config_with_token,
                )

    def test_403_forbidden_not_auth(self, config_with_token):
        with patch(_POST, return_value=_resp(403, text="forbidden")):
            with pytest.raises(ExternalServiceError, match="403"):
                GitLabPagesAPIAdapter().publish(
                    {"title": "T", "slug": "t"}, mode="live", config=config_with_token,
                )

    def test_missing_config_raises(self, config_with_token):
        config_with_token.gitlabpages = None
        with pytest.raises(DependencyError):
            GitLabPagesAPIAdapter().publish(
                {"title": "T", "slug": "t"}, mode="live", config=config_with_token,
            )

    def test_token_not_leaked_on_401(self, config_with_token):
        with patch(_POST, return_value=_resp(401, text="unauthorized")):
            with pytest.raises(ExternalServiceError) as exc:
                GitLabPagesAPIAdapter().publish(
                    {"title": "T", "slug": "t"}, mode="live", config=config_with_token,
                )
        assert "glpat_secret_99" not in str(exc.value)


class TestHtmlBody:
    def test_title_is_html_escaped(self):
        """A title with HTML metacharacters must be escaped in the <title> tag —
        no broken page / injection via the title text node."""
        body = _build_html_body({"title": "Evil </title><script>alert(1)</script>", "content_html": "<p>x</p>"})
        assert "</title><script>" not in body
        assert "&lt;/title&gt;&lt;script&gt;" in body

    def test_body_html_not_escaped(self):
        """The negotiated HTML island is intentionally not escaped."""
        body = _build_html_body({"title": "Plain", "content_html": "<p>hi <a href=\"https://x.com\">l</a></p>"})
        assert "<title>Plain</title>" in body


class TestRegistration:
    def test_registered(self):
        from backlink_publisher.publishing.registry import registered_platforms
        assert "gitlabpages" in registered_platforms()

    def test_dofollow_uncertain_not_in_cohort(self):
        from backlink_publisher.publishing.registry import dofollow_status
        assert dofollow_status("gitlabpages") == "uncertain"

    def test_referral_value_high(self):
        from backlink_publisher.publishing.registry import referral_value
        assert referral_value("gitlabpages") == "high"

    def test_rationale_min_length(self):
        from backlink_publisher.publishing.registry import dofollow_rationale
        assert len(dofollow_rationale("gitlabpages").strip()) >= 80
