"""Qiita v2 REST API adapter tests (mocked HTTP — no live API).

Covers: publish happy path, draft mode, DependencyError on missing token,
no token leak on 401, 422 error surfacing, tag normalization, R9 extension
gate compatibility (no cli/*.py edits), and verify_adapter_setup offline check.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.publishing.adapters.qiita_api import (
    QiitaAPIAdapter,
    _build_item_payload,
    _load_token,
    _required_headers,
)

_POST = "backlink_publisher.publishing.adapters.qiita_api.http_post"
_TOKEN = "qiita-test-token-secret-abc123"


@pytest.fixture
def config(tmp_path):
    cfg = MagicMock()
    cfg.qiita_token_path = tmp_path / "qiita-token.json"
    tok = cfg.qiita_token_path
    tok.write_text(json.dumps({"token": _TOKEN}))
    os.chmod(tok, 0o600)
    return cfg


@pytest.fixture
def config_no_token(tmp_path):
    cfg = MagicMock()
    cfg.qiita_token_path = tmp_path / "qiita-token.json"
    # File absent → not configured
    return cfg


def _payload(**kwargs):
    base = {
        "id": "q1",
        "title": "Testing Qiita Adapter",
        "content_markdown": "body with [link](https://example.com)",
        "tags": ["python", "seo"],
    }
    base.update(kwargs)
    return base


def _created_resp(url="https://qiita.com/user/items/abc123"):
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"id": "abc123", "url": url}
    return resp


# ── happy path ───────────────────────────────────────────────────────────────


def test_publish_returns_published_url(config):
    with patch(_POST, return_value=_created_resp()) as mock_post:
        result = QiitaAPIAdapter().publish(_payload(), "publish", config)
    assert isinstance(result, AdapterResult)
    assert result.status == "published"
    assert result.published_url == "https://qiita.com/user/items/abc123"
    assert result.platform == "qiita"
    mock_post.assert_called_once()


def test_publish_uses_bearer_auth(config):
    with patch(_POST, return_value=_created_resp()) as mock_post:
        QiitaAPIAdapter().publish(_payload(), "publish", config)
    headers = mock_post.call_args.kwargs.get("headers") or mock_post.call_args[1]["headers"]
    assert headers["Authorization"] == f"Bearer {_TOKEN}"
    assert "Content-Type" in headers


def test_draft_mode_skips_api(config):
    with patch(_POST) as mock_post:
        result = QiitaAPIAdapter().publish(_payload(), "draft", config)
    assert result.status == "drafted"
    assert result.draft_url == "https://qiita.com/drafts"
    mock_post.assert_not_called()


# ── credential errors ─────────────────────────────────────────────────────────


def test_missing_token_raises_dependency_error(config_no_token):
    with pytest.raises(DependencyError, match="Qiita personal access token not configured"):
        QiitaAPIAdapter().publish(_payload(), "publish", config_no_token)


def test_token_not_leaked_in_401_error(config):
    resp = MagicMock()
    resp.status_code = 401
    with patch(_POST, return_value=resp):
        with pytest.raises(ExternalServiceError) as exc_info:
            QiitaAPIAdapter().publish(_payload(), "publish", config)
    assert _TOKEN not in str(exc_info.value)


# ── HTTP error handling ───────────────────────────────────────────────────────


def test_422_raises_with_message(config):
    resp = MagicMock()
    resp.status_code = 422
    resp.json.return_value = {"message": "Tags is invalid"}
    with patch(_POST, return_value=resp):
        with pytest.raises(ExternalServiceError, match="Tags is invalid"):
            QiitaAPIAdapter().publish(_payload(), "publish", config)


def test_unexpected_status_raises(config):
    resp = MagicMock()
    resp.status_code = 500
    resp.text = "Internal server error"
    with patch(_POST, return_value=resp):
        with pytest.raises(ExternalServiceError, match="500"):
            QiitaAPIAdapter().publish(_payload(), "publish", config)


def test_missing_url_in_response_raises(config):
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"id": "abc"}  # no 'url' field
    with patch(_POST, return_value=resp):
        with pytest.raises(ExternalServiceError, match="no 'url'"):
            QiitaAPIAdapter().publish(_payload(), "publish", config)


# ── tag normalization ─────────────────────────────────────────────────────────


def test_tag_normalization():
    p = _build_item_payload({"title": "T", "tags": ["Python 3", "SEO tips!", "go"]})
    tag_names = [t["name"] for t in p["tags"]]
    assert "python3" in tag_names  # space stripped, lowercased
    assert "seotips" in tag_names  # exclamation stripped
    assert "go" in tag_names


def test_max_five_tags():
    p = _build_item_payload({"title": "T", "tags": ["a", "b", "c", "d", "e", "f"]})
    assert len(p["tags"]) == 5


def test_empty_tags_fallback():
    p = _build_item_payload({"title": "T"})
    assert p["tags"] == [{"name": "programming"}]


def test_payload_is_public():
    p = _build_item_payload({"title": "T"})
    assert p["private"] is False


# ── available() / verify_adapter_setup ───────────────────────────────────────


def test_available_true_when_token_present(config):
    assert QiitaAPIAdapter.available(config) is True


def test_available_false_when_token_absent(config_no_token):
    assert QiitaAPIAdapter.available(config_no_token) is False


def test_verify_adapter_setup_no_token(config_no_token):
    from backlink_publisher._util.errors import DependencyError
    from backlink_publisher.publishing.adapters._setup_checks import _verify_offline_setup
    with pytest.raises(DependencyError) as exc_info:
        _verify_offline_setup("qiita", config_no_token)
    assert "qiita-token.json" in str(exc_info.value)
    assert _TOKEN not in str(exc_info.value)  # token not leaked


def test_verify_adapter_setup_with_token(config):
    from backlink_publisher.publishing.adapters._setup_checks import _verify_offline_setup
    result = _verify_offline_setup("qiita", config)
    assert result is None  # configured → no error
