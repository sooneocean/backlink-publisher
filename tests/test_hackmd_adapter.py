"""Wave 1 Unit 1: HackMD adapter tests (Plan 2026-06-01-007)."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing.adapters.hackmd_api import (
    HackmdAPIAdapter,
    _build_note_payload,
    _load_token,
    _published_url,
    _required_headers,
)

_PATCH_TARGET = "backlink_publisher.publishing.adapters.hackmd_api.http_post"


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    cfg = MagicMock()
    cfg.hackmd_token_path = tmp_path / "hackmd-token.json"
    return cfg


@pytest.fixture
def config_with_token(config):
    config.hackmd_token_path.write_text(json.dumps({"token": "hmd_secret_abc", "token_rev": 1}))
    os.chmod(config.hackmd_token_path, 0o600)  # R10: real bind writes 0o600
    return config


def _mock_success(note_id="abcd1234", publish_link="https://hackmd.io/@me/abcd1234"):
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"id": note_id, "publishLink": publish_link}
    resp.text = ""
    return resp


class TestRequiredHeaders:
    def test_uses_bearer_authorization(self):
        headers = _required_headers("tok123")
        assert headers["Authorization"] == "Bearer tok123"
        assert "api-key" not in headers

    def test_includes_content_type(self):
        assert _required_headers("k")["Content-Type"] == "application/json"


class TestLoadToken:
    def test_raises_when_no_file(self, config):
        with pytest.raises(DependencyError, match="token"):
            _load_token(config)

    def test_raises_when_token_empty(self, config):
        config.hackmd_token_path.write_text(json.dumps({"token": ""}))
        with pytest.raises(DependencyError, match="token"):
            _load_token(config)

    def test_returns_token_when_present(self, config_with_token):
        assert _load_token(config_with_token) == "hmd_secret_abc"

    def test_rejects_world_readable_token_file(self, config):
        """R10: a 0o644 token file must be refused, not loaded silently."""
        config.hackmd_token_path.write_text(json.dumps({"token": "x"}))
        os.chmod(config.hackmd_token_path, 0o644)
        with pytest.raises(DependencyError, match="0o600"):
            _load_token(config)


class TestBuildNotePayload:
    def test_prepends_title_as_h1(self):
        body = _build_note_payload({"title": "My Note", "content_markdown": "Hello"})
        assert body["content"].startswith("# My Note")
        assert "Hello" in body["content"]

    def test_read_permission_is_guest(self):
        assert _build_note_payload({"title": "T"})["readPermission"] == "guest"

    def test_no_title_no_h1_prefix(self):
        body = _build_note_payload({"content_markdown": "Just body"})
        assert not body["content"].startswith("# ")


class TestPublishedUrl:
    def test_prefers_publish_link(self):
        assert _published_url({"publishLink": "https://hackmd.io/x", "id": "y"}) == "https://hackmd.io/x"

    def test_falls_back_to_id(self):
        assert _published_url({"id": "abc"}) == "https://hackmd.io/abc"

    def test_empty_when_neither(self):
        assert _published_url({}) == ""


class TestAvailable:
    def test_false_when_no_file(self, config):
        assert HackmdAPIAdapter.available(config) is False

    def test_false_when_token_empty(self, config):
        config.hackmd_token_path.write_text(json.dumps({"token": ""}))
        assert HackmdAPIAdapter.available(config) is False

    def test_true_when_token_present(self, config_with_token):
        assert HackmdAPIAdapter.available(config_with_token) is True


class TestPublish:
    def test_happy_path(self, config_with_token):
        with patch(_PATCH_TARGET, return_value=_mock_success()):
            result = HackmdAPIAdapter().publish(
                {"title": "Test", "content_markdown": "Body"}, mode="live", config=config_with_token,
            )
        assert result.status == "published"
        assert result.platform == "hackmd"
        assert result.published_url == "https://hackmd.io/@me/abcd1234"

    def test_published_url_fallback_from_id(self, config_with_token):
        resp = MagicMock()
        resp.status_code = 201
        resp.json.return_value = {"id": "noteXYZ", "publishLink": ""}
        resp.text = ""
        with patch(_PATCH_TARGET, return_value=resp):
            result = HackmdAPIAdapter().publish({"title": "T"}, mode="live", config=config_with_token)
        assert result.published_url == "https://hackmd.io/noteXYZ"

    def test_bearer_header_sent(self, config_with_token):
        with patch(_PATCH_TARGET, return_value=_mock_success()) as mock_post:
            HackmdAPIAdapter().publish({"title": "T"}, mode="live", config=config_with_token)
        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer hmd_secret_abc"

    def test_draft_mode_no_api_call(self, config_with_token):
        with patch(_PATCH_TARGET) as mock_post:
            result = HackmdAPIAdapter().publish({"title": "Draft"}, mode="draft", config=config_with_token)
        assert result.status == "drafted"
        assert mock_post.call_count == 0

    def test_401_raises(self, config_with_token):
        resp = MagicMock(status_code=401, text="Unauthorized")
        with patch(_PATCH_TARGET, return_value=resp):
            with pytest.raises(ExternalServiceError, match="401"):
                HackmdAPIAdapter().publish({"title": "T"}, mode="live", config=config_with_token)

    def test_403_raises(self, config_with_token):
        resp = MagicMock(status_code=403, text="Forbidden")
        with patch(_PATCH_TARGET, return_value=resp):
            with pytest.raises(ExternalServiceError, match="403"):
                HackmdAPIAdapter().publish({"title": "T"}, mode="live", config=config_with_token)

    def test_non_json_raises(self, config_with_token):
        resp = MagicMock(status_code=201, text="<html>")
        resp.json.side_effect = ValueError("no json")
        with patch(_PATCH_TARGET, return_value=resp):
            with pytest.raises(ExternalServiceError, match="non-JSON"):
                HackmdAPIAdapter().publish({"title": "T"}, mode="live", config=config_with_token)

    def test_missing_token_raises_dependency_error(self, config):
        with pytest.raises(DependencyError):
            HackmdAPIAdapter().publish({"title": "T"}, mode="live", config=config)

    def test_token_not_leaked_in_401_error(self, config_with_token):
        """R9: the raw token must never appear in the raised error text."""
        resp = MagicMock(status_code=401, text="Unauthorized")
        with patch(_PATCH_TARGET, return_value=resp):
            with pytest.raises(ExternalServiceError) as exc:
                HackmdAPIAdapter().publish({"title": "T"}, mode="live", config=config_with_token)
        assert "hmd_secret_abc" not in str(exc.value)


class TestDocstring:
    def test_docstring_mentions_dofollow(self):
        assert "dofollow" in HackmdAPIAdapter.__doc__.lower()


class TestRegistration:
    def test_registered(self):
        from backlink_publisher.publishing.registry import registered_platforms
        assert "hackmd" in registered_platforms()

    def test_dofollow_uncertain(self):
        from backlink_publisher.publishing.registry import dofollow_status
        assert dofollow_status("hackmd") == "uncertain"

    def test_referral_value_high(self):
        from backlink_publisher.publishing.registry import referral_value
        assert referral_value("hackmd") == "high"

    def test_rationale_min_length(self):
        from backlink_publisher.publishing.registry import dofollow_rationale
        assert len(dofollow_rationale("hackmd").strip()) >= 80
