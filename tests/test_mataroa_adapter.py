"""Wave 1 Unit 2: Mataroa adapter tests (Plan 2026-06-01-007)."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing.adapters.mataroa_api import (
    MataroaAPIAdapter,
    _build_post_payload,
    _load_token,
    _required_headers,
)

_PATCH_TARGET = "backlink_publisher.publishing.adapters.mataroa_api.http_post"


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    cfg = MagicMock()
    cfg.mataroa_token_path = tmp_path / "mataroa-token.json"
    return cfg


@pytest.fixture
def config_with_token(config):
    config.mataroa_token_path.write_text(json.dumps({"token": "mat_secret_xyz", "token_rev": 1}))
    os.chmod(config.mataroa_token_path, 0o600)  # R10: real bind writes 0o600
    return config


def _mock_success(url="https://me.mataroa.blog/blog/test/", slug="test"):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"ok": True, "slug": slug, "url": url}
    resp.text = ""
    return resp


class TestRequiredHeaders:
    def test_uses_bearer(self):
        assert _required_headers("t")["Authorization"] == "Bearer t"


class TestLoadToken:
    def test_raises_when_no_file(self, config):
        with pytest.raises(DependencyError, match="token"):
            _load_token(config)

    def test_returns_token(self, config_with_token):
        assert _load_token(config_with_token) == "mat_secret_xyz"

    def test_rejects_world_readable_token_file(self, config):
        """R10: a 0o644 token file must be refused."""
        config.mataroa_token_path.write_text(json.dumps({"token": "x"}))
        os.chmod(config.mataroa_token_path, 0o644)
        with pytest.raises(DependencyError, match="0o600"):
            _load_token(config)


class TestBuildPostPayload:
    def test_title_and_body(self):
        body = _build_post_payload({"title": "T", "content_markdown": "B"})
        assert body == {"title": "T", "body": "B"}


class TestAvailable:
    def test_false_when_no_file(self, config):
        assert MataroaAPIAdapter.available(config) is False

    def test_true_when_token(self, config_with_token):
        assert MataroaAPIAdapter.available(config_with_token) is True


class TestPublish:
    def test_happy_path(self, config_with_token):
        with patch(_PATCH_TARGET, return_value=_mock_success()):
            result = MataroaAPIAdapter().publish(
                {"title": "Test", "content_markdown": "Body"}, mode="live", config=config_with_token,
            )
        assert result.status == "published"
        assert result.platform == "mataroa"
        assert result.published_url == "https://me.mataroa.blog/blog/test/"

    def test_draft_mode_no_api_call(self, config_with_token):
        with patch(_PATCH_TARGET) as mock_post:
            result = MataroaAPIAdapter().publish({"title": "D"}, mode="draft", config=config_with_token)
        assert result.status == "drafted"
        assert mock_post.call_count == 0

    def test_401_raises(self, config_with_token):
        resp = MagicMock(status_code=401, text="Unauthorized")
        with patch(_PATCH_TARGET, return_value=resp):
            with pytest.raises(ExternalServiceError, match="401"):
                MataroaAPIAdapter().publish({"title": "T"}, mode="live", config=config_with_token)

    def test_ok_false_raises(self, config_with_token):
        resp = MagicMock(status_code=200, text="")
        resp.json.return_value = {"ok": False, "error": "bad"}
        with patch(_PATCH_TARGET, return_value=resp):
            with pytest.raises(ExternalServiceError, match="rejected"):
                MataroaAPIAdapter().publish({"title": "T"}, mode="live", config=config_with_token)

    def test_no_url_raises(self, config_with_token):
        resp = MagicMock(status_code=200, text="")
        resp.json.return_value = {"ok": True, "slug": "s", "url": ""}
        with patch(_PATCH_TARGET, return_value=resp):
            with pytest.raises(ExternalServiceError, match="no url"):
                MataroaAPIAdapter().publish({"title": "T"}, mode="live", config=config_with_token)

    def test_non_json_raises(self, config_with_token):
        resp = MagicMock(status_code=200, text="<html>")
        resp.json.side_effect = ValueError("no json")
        with patch(_PATCH_TARGET, return_value=resp):
            with pytest.raises(ExternalServiceError, match="non-JSON"):
                MataroaAPIAdapter().publish({"title": "T"}, mode="live", config=config_with_token)

    def test_missing_token_raises(self, config):
        with pytest.raises(DependencyError):
            MataroaAPIAdapter().publish({"title": "T"}, mode="live", config=config)

    def test_token_not_leaked_on_401(self, config_with_token):
        resp = MagicMock(status_code=401, text="Unauthorized")
        with patch(_PATCH_TARGET, return_value=resp):
            with pytest.raises(ExternalServiceError) as exc:
                MataroaAPIAdapter().publish({"title": "T"}, mode="live", config=config_with_token)
        assert "mat_secret_xyz" not in str(exc.value)


class TestRegistration:
    def test_registered(self):
        from backlink_publisher.publishing.registry import registered_platforms
        assert "mataroa" in registered_platforms()

    def test_dofollow_uncertain(self):
        from backlink_publisher.publishing.registry import dofollow_status
        assert dofollow_status("mataroa") == "uncertain"

    def test_referral_value_high(self):
        from backlink_publisher.publishing.registry import referral_value
        assert referral_value("mataroa") == "high"

    def test_rationale_min_length(self):
        from backlink_publisher.publishing.registry import dofollow_rationale
        assert len(dofollow_rationale("mataroa").strip()) >= 80
