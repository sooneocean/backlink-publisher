"""Unit tests for helpers extracted from VelogGraphQLAdapter.publish.

Covers _apply_publish_jitter, _execute_write_post, and _handle_null_write_post.
All tests are pure-unit: no filesystem, no real HTTP.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest
import requests

from backlink_publisher._util.errors import (
    AuthExpiredError,
    ContentRejectedError,
    ExternalServiceError,
)
from backlink_publisher.publishing.adapters.velog_graphql import (
    _apply_publish_jitter,
    _execute_write_post,
    _handle_null_write_post,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _resp(status: int = 200, json_data=None, ok: bool | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.ok = ok if ok is not None else (status < 400)
    r.json.return_value = json_data or {}
    r.headers = {}
    return r


def _session(post_return=None, post_side_effect=None) -> MagicMock:
    s = MagicMock(spec=requests.Session)
    if post_side_effect is not None:
        s.post.side_effect = post_side_effect
    else:
        s.post.return_value = post_return or _resp(200, {"data": {"writePost": {"id": "p1", "url_slug": "slug", "user": {"username": "u"}}}})
    return s


_GQL = {"operationName": "WritePost", "query": "...", "variables": {}}


# ── _apply_publish_jitter ─────────────────────────────────────────────────────


class TestApplyPublishJitter:
    def test_zero_last_publish_is_noop(self):
        with patch("backlink_publisher.publishing.adapters.velog_graphql.time.sleep") as mock_sleep:
            _apply_publish_jitter("art1", 0.0)
        mock_sleep.assert_not_called()

    def test_negative_last_publish_is_noop(self):
        with patch("backlink_publisher.publishing.adapters.velog_graphql.time.sleep") as mock_sleep:
            _apply_publish_jitter("art1", -1.0)
        mock_sleep.assert_not_called()

    def test_recent_publish_sleeps(self):
        fixed_now = 1_000_000.0
        last = fixed_now - 10.0  # only 10s ago, well under 60s min jitter
        with patch("backlink_publisher.publishing.adapters.velog_graphql.time.time", return_value=fixed_now), \
             patch("backlink_publisher.publishing.adapters.velog_graphql.random.uniform", return_value=90.0), \
             patch("backlink_publisher.publishing.adapters.velog_graphql.time.sleep") as mock_sleep:
            _apply_publish_jitter("art1", last)
        mock_sleep.assert_called_once()
        wait = mock_sleep.call_args[0][0]
        assert abs(wait - 80.0) < 0.01  # 90 - 10 = 80s

    def test_old_enough_publish_does_not_sleep(self):
        fixed_now = 1_000_000.0
        last = fixed_now - 200.0  # 200s ago, more than max jitter 180s
        with patch("backlink_publisher.publishing.adapters.velog_graphql.time.time", return_value=fixed_now), \
             patch("backlink_publisher.publishing.adapters.velog_graphql.random.uniform", return_value=120.0), \
             patch("backlink_publisher.publishing.adapters.velog_graphql.time.sleep") as mock_sleep:
            _apply_publish_jitter("art1", last)
        mock_sleep.assert_not_called()


# ── _execute_write_post ───────────────────────────────────────────────────────


class TestExecuteWritePost:
    def test_happy_path_returns_response(self):
        ok_resp = _resp(200)
        s = _session(post_return=ok_resp)
        result = _execute_write_post(s, _GQL)
        assert result is ok_resp

    def test_connection_error_raises_external_service(self):
        s = _session(post_side_effect=requests.ConnectionError("down"))
        with pytest.raises(ExternalServiceError, match="unreachable"):
            _execute_write_post(s, _GQL)

    def test_429_retried_then_succeeds(self):
        rate_resp = _resp(429)
        ok_resp = _resp(200)
        s = _session(post_side_effect=[rate_resp, ok_resp])
        with patch("backlink_publisher.publishing.adapters.retry.time.sleep"):
            result = _execute_write_post(s, _GQL)
        assert result is ok_resp

    def test_429_exhausted_raises_with_status(self):
        s = _session(post_return=_resp(429))
        with patch("backlink_publisher.publishing.adapters.retry.time.sleep"):
            with pytest.raises(ExternalServiceError, match="429"):
                _execute_write_post(s, _GQL)

    def test_label_appended_to_error_message(self):
        s = _session(post_side_effect=requests.ConnectionError("down"))
        with pytest.raises(ExternalServiceError, match="on retry"):
            _execute_write_post(s, _GQL, label=" on retry")


# ── _handle_null_write_post ───────────────────────────────────────────────────


def _make_config(tmp_path):
    cfg = MagicMock()
    cfg.config_dir = tmp_path
    return cfg


class TestHandleNullWritePost:
    def test_returns_write_post_on_success(self, tmp_path):
        wp = {"id": "p1", "url_slug": "slug", "user": {"username": "u"}}
        resp2 = _resp(200, {"data": {"writePost": wp}})
        s = _session(post_return=resp2)
        result = _handle_null_write_post(s, _GQL, "art1", _make_config(tmp_path))
        assert result == wp

    def test_resp2_not_ok_raises_external_service(self, tmp_path):
        resp2 = _resp(500, ok=False)
        s = _session(post_return=resp2)
        with pytest.raises(ExternalServiceError, match="HTTP 500 on retry"):
            _handle_null_write_post(s, _GQL, "art1", _make_config(tmp_path))

    def test_resp2_non_json_raises_external_service(self, tmp_path):
        resp2 = _resp(200)
        resp2.json.side_effect = ValueError("not json")
        s = _session(post_return=resp2)
        with pytest.raises(ExternalServiceError, match="not valid JSON"):
            _handle_null_write_post(s, _GQL, "art1", _make_config(tmp_path))

    def test_null_after_retry_alive_raises_content_rejected(self, tmp_path):
        resp2 = _resp(200, {"data": {"writePost": None}})
        s = _session(post_return=resp2)
        with patch("backlink_publisher.publishing.adapters.velog_graphql._probe_session_alive", return_value=(True, "user:alive")), \
             patch("backlink_publisher.publishing.adapters.velog_graphql._save_null_artifact", return_value=None):
            with pytest.raises(ContentRejectedError, match="cookie alive"):
                _handle_null_write_post(s, _GQL, "art1", _make_config(tmp_path))

    def test_null_after_retry_dead_raises_auth_expired(self, tmp_path):
        resp2 = _resp(200, {"data": {"writePost": None}})
        s = _session(post_return=resp2)
        with patch("backlink_publisher.publishing.adapters.velog_graphql._probe_session_alive", return_value=(False, "no-token")), \
             patch("backlink_publisher.publishing.adapters.velog_graphql._save_null_artifact", return_value=None):
            with pytest.raises(AuthExpiredError, match="cookie dead"):
                _handle_null_write_post(s, _GQL, "art1", _make_config(tmp_path))

    def test_gql_errors_included_in_content_rejected_reason(self, tmp_path):
        resp2 = _resp(200, {"data": {"writePost": None}, "errors": [{"message": "bad content"}]})
        s = _session(post_return=resp2)
        with patch("backlink_publisher.publishing.adapters.velog_graphql._probe_session_alive", return_value=(True, "ok")), \
             patch("backlink_publisher.publishing.adapters.velog_graphql._save_null_artifact", return_value=None):
            with pytest.raises(ContentRejectedError, match="bad content"):
                _handle_null_write_post(s, _GQL, "art1", _make_config(tmp_path))
