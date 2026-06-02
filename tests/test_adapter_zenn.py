"""Zenn GitHub adapter tests (mocked HTTP — no live API).

Covers: publish happy path (create + update/idempotent), draft mode, missing
config/token DependencyError, no token leak on 401/403, and verify_adapter_setup.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.publishing.adapters.zenn_github import (
    ZennGitHubAdapter,
    _build_zenn_markdown,
    _slugify,
)

_PUT = "backlink_publisher.publishing.adapters.zenn_github.http_put"
_GET = "backlink_publisher.publishing.adapters.zenn_github.http_get"
_TOKEN = "github-zenn-test-token-secret"
_REPO = "testuser/zenn-docs"
_USERNAME = "testuser"


@pytest.fixture
def config(tmp_path):
    cfg = MagicMock()
    cfg.zenn_token_path = tmp_path / "zenn-token.json"
    tok = cfg.zenn_token_path
    tok.write_text(json.dumps({"token": _TOKEN}))
    os.chmod(tok, 0o600)
    cfg.zenn = MagicMock()
    cfg.zenn.github_repo = _REPO
    cfg.zenn.username = _USERNAME
    cfg.zenn.branch = "main"
    return cfg


@pytest.fixture
def config_no_token(tmp_path):
    cfg = MagicMock()
    cfg.zenn_token_path = tmp_path / "zenn-token.json"
    cfg.zenn = MagicMock()
    cfg.zenn.github_repo = _REPO
    cfg.zenn.username = _USERNAME
    cfg.zenn.branch = "main"
    return cfg


@pytest.fixture
def config_no_section(tmp_path):
    cfg = MagicMock()
    cfg.zenn_token_path = tmp_path / "zenn-token.json"
    cfg.zenn = None
    return cfg


def _payload(**kwargs):
    base = {
        "id": "z1",
        "title": "Testing Zenn Adapter",
        "content_markdown": "body with [link](https://example.com)",
        "tags": ["python", "seo"],
    }
    base.update(kwargs)
    return base


def _created_resp():
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"content": {"sha": "abc123new"}}
    return resp


def _no_file_resp():
    resp = MagicMock()
    resp.status_code = 404
    return resp


def _existing_file_resp():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"sha": "existingsha"}
    return resp


# ── happy path ───────────────────────────────────────────────────────────────


def test_publish_creates_article(config):
    with patch(_GET, return_value=_no_file_resp()), \
         patch(_PUT, return_value=_created_resp()) as mock_put:
        result = ZennGitHubAdapter().publish(_payload(), "publish", config)
    assert isinstance(result, AdapterResult)
    assert result.status == "published"
    assert result.platform == "zenn"
    assert f"https://zenn.dev/{_USERNAME}/articles/" in result.published_url


def test_publish_uses_bearer_auth(config):
    with patch(_GET, return_value=_no_file_resp()), \
         patch(_PUT, return_value=_created_resp()) as mock_put:
        ZennGitHubAdapter().publish(_payload(), "publish", config)
    headers = mock_put.call_args.kwargs.get("headers") or mock_put.call_args[1]["headers"]
    assert headers["Authorization"] == f"Bearer {_TOKEN}"


def test_publish_idempotent_update_uses_sha(config):
    with patch(_GET, return_value=_existing_file_resp()), \
         patch(_PUT, return_value=_created_resp()) as mock_put:
        ZennGitHubAdapter().publish(_payload(), "publish", config)
    body = mock_put.call_args.kwargs.get("json") or mock_put.call_args[1]["json"]
    assert body.get("sha") == "existingsha"


def test_draft_mode_skips_api(config):
    with patch(_GET) as mock_get, patch(_PUT) as mock_put:
        result = ZennGitHubAdapter().publish(_payload(), "draft", config)
    assert result.status == "drafted"
    mock_get.assert_not_called()
    mock_put.assert_not_called()


# ── missing config / token ────────────────────────────────────────────────────


def test_missing_zenn_section_raises(config_no_section):
    with pytest.raises(DependencyError, match="\\[zenn\\]"):
        ZennGitHubAdapter().publish(_payload(), "publish", config_no_section)


def test_missing_token_raises(config_no_token):
    with pytest.raises(DependencyError, match="GitHub PAT not configured"):
        ZennGitHubAdapter().publish(_payload(), "publish", config_no_token)


# ── HTTP error handling ───────────────────────────────────────────────────────


def test_401_raises_without_token_leak(config):
    resp = MagicMock()
    resp.status_code = 401
    with patch(_GET, return_value=_no_file_resp()), \
         patch(_PUT, return_value=resp):
        with pytest.raises(ExternalServiceError) as exc_info:
            ZennGitHubAdapter().publish(_payload(), "publish", config)
    assert _TOKEN not in str(exc_info.value)
    assert "401" in str(exc_info.value)


def test_403_raises_with_permission_hint(config):
    resp = MagicMock()
    resp.status_code = 403
    with patch(_GET, return_value=_no_file_resp()), \
         patch(_PUT, return_value=resp):
        with pytest.raises(ExternalServiceError, match="lacks permission"):
            ZennGitHubAdapter().publish(_payload(), "publish", config)


# ── content helpers ───────────────────────────────────────────────────────────


def test_slugify_basic():
    assert _slugify("Hello World") == "hello-world"


def test_slugify_special_chars():
    result = _slugify("Test! Article #1")
    assert " " not in result
    assert "#" not in result


def test_build_zenn_markdown_has_frontmatter():
    slug, content = _build_zenn_markdown({"title": "My Article", "tags": ["python"]})
    assert slug == "my-article"
    assert "---" in content
    assert "published: true" in content
    assert "python" in content


def test_build_zenn_markdown_public():
    _, content = _build_zenn_markdown({"title": "T"})
    assert "published: true" in content
    assert "published: false" not in content


# ── available() ──────────────────────────────────────────────────────────────


def test_available_true_when_configured(config):
    assert ZennGitHubAdapter.available(config) is True


def test_available_false_no_token(config_no_token):
    assert ZennGitHubAdapter.available(config_no_token) is False


def test_available_false_no_section(config_no_section):
    assert ZennGitHubAdapter.available(config_no_section) is False
