"""Unit tests for module-level helpers extracted from MediumAPIAdapter.

Covers _resolve_medium_token_data, _check_medium_token_expiry,
_fetch_medium_user_id, and _create_medium_post.
All tests run without I/O — HTTP calls are mocked via patch.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch, call

import pytest
import requests

from backlink_publisher.config import Config
from backlink_publisher._util.errors import (
    AuthExpiredError,
    DependencyError,
    ExternalServiceError,
)
from backlink_publisher.publishing.adapters.medium_api import (
    _check_medium_token_expiry,
    _create_medium_post,
    _fetch_medium_user_id,
    _resolve_medium_token_data,
)

_CONFIG_NO_TOKEN = Config(medium_integration_token=None)
_CONFIG_TOML_TOKEN = Config(medium_integration_token="toml-token")
_HEADERS = {"Authorization": "Bearer tok", "Content-Type": "application/json"}
_BODY = {"title": "T", "contentFormat": "html", "content": "<p>hi</p>", "tags": [], "publishStatus": "draft"}


# ── _resolve_medium_token_data ────────────────────────────────────────────────


class TestResolveMediumTokenData:
    def _call(self, config=_CONFIG_NO_TOKEN, *, oauth=None, integration=None):
        with patch("backlink_publisher.config.load_medium_token", return_value=oauth), \
             patch("backlink_publisher.config.tokens.load_medium_integration_token", return_value=integration):
            return _resolve_medium_token_data(config)

    def test_oauth_token_returned_first(self):
        token, data = self._call(oauth={"access_token": "oauth-tok"})
        assert token == "oauth-tok"
        assert data == {"access_token": "oauth-tok"}

    def test_integration_token_fallback(self):
        token, data = self._call(
            oauth=None,
            integration={"integration_token": " it-tok "},
        )
        assert token == "it-tok"
        assert data is None

    def test_toml_token_fallback(self):
        token, data = self._call(config=_CONFIG_TOML_TOKEN, oauth=None, integration=None)
        assert token == "toml-token"
        assert data is None

    def test_no_token_raises_dependency_error(self):
        with pytest.raises(DependencyError, match="integration token not configured"):
            self._call(oauth=None, integration=None)

    def test_empty_integration_token_falls_through_to_toml(self):
        token, _ = self._call(
            config=_CONFIG_TOML_TOKEN,
            oauth=None,
            integration={"integration_token": "   "},
        )
        assert token == "toml-token"

    def test_oauth_missing_access_token_falls_through(self):
        token, _ = self._call(
            config=_CONFIG_TOML_TOKEN,
            oauth={"other_field": "x"},
        )
        assert token == "toml-token"


# ── _check_medium_token_expiry ────────────────────────────────────────────────


_FIXED_NOW = 1_000_000.0


class TestCheckMediumTokenExpiry:
    def test_none_data_is_noop(self):
        _check_medium_token_expiry(None)  # no raise

    def test_missing_expires_at_is_noop(self):
        _check_medium_token_expiry({"access_token": "tok"})  # no raise

    def test_zero_expires_at_sentinel_is_noop(self):
        with patch("backlink_publisher.publishing.adapters.medium_api.time.time", return_value=_FIXED_NOW):
            _check_medium_token_expiry({"expires_at": 0})  # no raise

    def test_expiry_600s_future_is_noop(self):
        with patch("backlink_publisher.publishing.adapters.medium_api.time.time", return_value=_FIXED_NOW):
            _check_medium_token_expiry({"expires_at": int(_FIXED_NOW) + 600})  # no raise

    def test_expiry_200s_future_raises(self):
        with patch("backlink_publisher.publishing.adapters.medium_api.time.time", return_value=_FIXED_NOW):
            with pytest.raises(ExternalServiceError, match="expires in < 5 minutes"):
                _check_medium_token_expiry({"expires_at": int(_FIXED_NOW) + 200})

    def test_already_expired_raises(self):
        with patch("backlink_publisher.publishing.adapters.medium_api.time.time", return_value=_FIXED_NOW):
            with pytest.raises(ExternalServiceError):
                _check_medium_token_expiry({"expires_at": int(_FIXED_NOW) - 60})

    def test_exactly_300s_boundary_raises(self):
        with patch("backlink_publisher.publishing.adapters.medium_api.time.time", return_value=_FIXED_NOW):
            with pytest.raises(ExternalServiceError):
                _check_medium_token_expiry({"expires_at": int(_FIXED_NOW) + 300})

    def test_exactly_301s_boundary_is_noop(self):
        with patch("backlink_publisher.publishing.adapters.medium_api.time.time", return_value=_FIXED_NOW):
            _check_medium_token_expiry({"expires_at": int(_FIXED_NOW) + 301})  # no raise


# ── _fetch_medium_user_id ─────────────────────────────────────────────────────


def _make_resp(status: int, json_data=None, text="") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.ok = status < 400
    r.json.return_value = json_data or {}
    r.text = text
    return r


class TestFetchMediumUserId:
    def test_happy_path_returns_user_id(self):
        resp = _make_resp(200, {"data": {"id": "user123"}})
        with patch("backlink_publisher.publishing.adapters.medium_api.http_get", return_value=resp):
            uid = _fetch_medium_user_id(_HEADERS)
        assert uid == "user123"

    def test_401_raises_auth_expired(self):
        resp = _make_resp(401)
        with patch("backlink_publisher.publishing.adapters.medium_api.http_get", return_value=resp):
            with pytest.raises(AuthExpiredError, match="medium"):
                _fetch_medium_user_id(_HEADERS)

    def test_500_raises_external_service_error(self):
        resp = _make_resp(500)
        with patch("backlink_publisher.publishing.adapters.medium_api.http_get", return_value=resp):
            with pytest.raises(ExternalServiceError, match="/me returned HTTP 500"):
                _fetch_medium_user_id(_HEADERS)

    def test_connection_error_raises_external_service_error(self):
        with patch(
            "backlink_publisher.publishing.adapters.medium_api.http_get",
            side_effect=requests.ConnectionError("no route"),
        ):
            with pytest.raises(ExternalServiceError, match="unreachable"):
                _fetch_medium_user_id(_HEADERS)

    def test_429_retried_then_succeeds(self):
        rate_resp = _make_resp(429)
        ok_resp = _make_resp(200, {"data": {"id": "u99"}})
        with patch("backlink_publisher.publishing.adapters.medium_api.http_get", side_effect=[rate_resp, ok_resp]), \
             patch("backlink_publisher.publishing.adapters.retry.time.sleep"):
            uid = _fetch_medium_user_id(_HEADERS)
        assert uid == "u99"


# ── _create_medium_post ───────────────────────────────────────────────────────


class TestCreateMediumPost:
    def test_happy_201_returns_response(self):
        resp = _make_resp(201, {"data": {"url": "https://medium.com/@u/p"}})
        with patch("backlink_publisher.publishing.adapters.medium_api.http_post", return_value=resp):
            result = _create_medium_post("u1", _HEADERS, _BODY)
        assert result.status_code == 201

    def test_401_raises_auth_expired(self):
        resp = _make_resp(401)
        with patch("backlink_publisher.publishing.adapters.medium_api.http_post", return_value=resp):
            with pytest.raises(AuthExpiredError, match="medium"):
                _create_medium_post("u1", _HEADERS, _BODY)

    def test_429_exhausts_retries_and_raises(self):
        resp = _make_resp(429)
        with patch("backlink_publisher.publishing.adapters.medium_api.http_post", return_value=resp), \
             patch("backlink_publisher.publishing.adapters.retry.time.sleep"):
            with pytest.raises(ExternalServiceError, match="429"):
                _create_medium_post("u1", _HEADERS, _BODY)

    def test_503_raises_not_retried(self):
        resp = _make_resp(503, text="down")
        with patch("backlink_publisher.publishing.adapters.medium_api.http_post", return_value=resp), \
             patch("backlink_publisher.publishing.adapters.retry.time.sleep") as mock_sleep:
            with pytest.raises(ExternalServiceError, match="/posts returned HTTP 503"):
                _create_medium_post("u1", _HEADERS, _BODY)
        mock_sleep.assert_not_called()

    def test_network_error_not_retried(self):
        with patch(
            "backlink_publisher.publishing.adapters.medium_api.http_post",
            side_effect=requests.ConnectionError("reset"),
        ), patch("backlink_publisher.publishing.adapters.retry.time.sleep") as mock_sleep:
            with pytest.raises(ExternalServiceError, match="unreachable"):
                _create_medium_post("u1", _HEADERS, _BODY)
        mock_sleep.assert_not_called()

    def test_429_retried_then_succeeds(self):
        rate_resp = _make_resp(429)
        ok_resp = _make_resp(201, {"data": {"url": "https://medium.com/@u/post"}})
        with patch("backlink_publisher.publishing.adapters.medium_api.http_post", side_effect=[rate_resp, ok_resp]), \
             patch("backlink_publisher.publishing.adapters.retry.time.sleep"):
            result = _create_medium_post("u1", _HEADERS, _BODY)
        assert result.status_code == 201
