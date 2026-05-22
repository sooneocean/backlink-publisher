"""Tests for VelogGraphQLAdapter (Unit 4).

Covers:
- _slugify
- _load_cookies: happy path, missing file, wrong perms, empty cookies
- _effective_cap: phase 1 / phase 2 date gate
- _read_count / _write_count: happy, UTC rollover, corrupt file
- publish(): happy path, silent-drop retry, daily cap, cookie expired
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from backlink_publisher.config import Config
from backlink_publisher.config.types import VelogConfig
from backlink_publisher._util.errors import (
    AuthExpiredError,
    ContentRejectedError,
    DependencyError,
    ExternalServiceError,
)
from backlink_publisher.publishing.adapters.velog_graphql import (
    UNLOCK_DATE_UTC,
    VelogGraphQLAdapter,
    _effective_cap,
    _load_cookies,
    _mask_cookies,
    _probe_session_alive,
    _read_count,
    _save_null_artifact,
    _slugify,
    _write_count,
)


# ── _slugify ──────────────────────────────────────────────────────────────────

class TestSlugify:
    def test_basic(self):
        assert _slugify("Hello World") == "hello-world"

    def test_punctuation_stripped(self):
        assert _slugify("Test Post #1!") == "test-post-1"

    def test_multiple_spaces(self):
        assert _slugify("  foo   bar  ") == "foo-bar"

    def test_empty_returns_post(self):
        assert _slugify("") == "post"

    def test_hyphens_collapsed(self):
        assert _slugify("a - b") == "a-b"


# ── _load_cookies ─────────────────────────────────────────────────────────────

class TestLoadCookies:
    def _write_cookie_file(self, path: Path, mode: int, data: dict) -> None:
        path.write_text(json.dumps(data))
        os.chmod(path, mode)

    def test_happy_path(self, tmp_path):
        p = tmp_path / "velog-cookies.json"
        self._write_cookie_file(p, 0o600, {
            "cookies": [
                {"name": "access_token", "value": "at123"},
                {"name": "refresh_token", "value": "rt456"},
            ]
        })
        result = _load_cookies(p)
        assert result == {"access_token": "at123", "refresh_token": "rt456"}

    def test_storage_state_with_account_localstorage_is_accepted(self, tmp_path):
        legacy = tmp_path / "velog-cookies.json"
        legacy.write_text(json.dumps({
            "cookies": [],
            "origins": [{
                "origin": "https://velog.io",
                "localStorage": [{
                    "name": "account",
                    "value": json.dumps({"access_token": "at123", "refresh_token": "rt456"}),
                }],
            }],
        }))
        os.chmod(legacy, 0o600)
        result = _load_cookies(legacy)
        assert result == {"access_token": "at123", "refresh_token": "rt456"}

    def test_missing_file(self, tmp_path):
        with pytest.raises(DependencyError, match="velog-login"):
            _load_cookies(tmp_path / "no-file.json")

    def test_wrong_permissions(self, tmp_path):
        p = tmp_path / "velog-cookies.json"
        self._write_cookie_file(p, 0o644, {"cookies": [{"name": "a", "value": "b"}]})
        with pytest.raises(DependencyError, match="0600"):
            _load_cookies(p)

    def test_empty_cookies_list(self, tmp_path):
        p = tmp_path / "velog-cookies.json"
        self._write_cookie_file(p, 0o600, {"cookies": []})
        with pytest.raises(DependencyError, match="velog-login"):
            _load_cookies(p)

    def test_tracking_only_cookies_are_rejected(self, tmp_path):
        p = tmp_path / "velog-cookies.json"
        self._write_cookie_file(p, 0o600, {
            "cookies": [
                {"name": "_ga", "value": "tracking"},
                {"name": "theme", "value": "light"},
            ]
        })
        with pytest.raises(AuthExpiredError, match="no access_token or refresh_token"):
            _load_cookies(p)

    def test_corrupt_json(self, tmp_path):
        p = tmp_path / "velog-cookies.json"
        p.write_text("not-json{{{")
        os.chmod(p, 0o600)
        with pytest.raises(DependencyError, match="velog-login"):
            _load_cookies(p)


# ── _effective_cap ────────────────────────────────────────────────────────────

class TestEffectiveCap:
    def test_before_unlock_returns_initial(self):
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        with patch(
            "backlink_publisher.publishing.adapters.velog_graphql.UNLOCK_DATE_UTC",
            datetime(2099, 1, 1, tzinfo=timezone.utc),
        ):
            cap = _effective_cap()
        assert cap == 5  # _VELOG_DAILY_CAP_INITIAL

    def test_after_unlock_returns_prod(self):
        with patch(
            "backlink_publisher.publishing.adapters.velog_graphql.UNLOCK_DATE_UTC",
            datetime(2020, 1, 1, tzinfo=timezone.utc),
        ):
            cap = _effective_cap()
        assert cap == 30  # _VELOG_DAILY_CAP_PROD


# ── _read_count / _write_count ────────────────────────────────────────────────

class TestCountFile:
    def test_missing_file_returns_zero(self, tmp_path):
        count, last = _read_count(tmp_path / "no-file.json")
        assert count == 0
        assert last == 0.0

    def test_today_returns_count(self, tmp_path):
        p = tmp_path / "count.json"
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date().isoformat()
        p.write_text(json.dumps({"date_utc": today, "count": 3, "last_publish_at": 9999.0}))
        count, last = _read_count(p)
        assert count == 3
        assert last == 9999.0

    def test_read_and_write_use_utc_date_not_local(self, tmp_path, monkeypatch):
        """Regression: ``_read_count`` and ``_write_count`` must derive the
        ``date_utc`` field from UTC, not the host's local timezone.

        Prior code used ``date.today()`` which returns the local-time date.
        On a machine in UTC+9 (KST), at 2026-05-19T22:00 UTC the local
        date is already 2026-05-20; the cap would reset 9 hours early
        relative to the documented UTC boundary. Conversely, in UTC-8,
        the cap would still report 2026-05-19 hours after UTC midnight.

        Simulates a UTC+9 host at 2026-05-19T23:30Z (local = 2026-05-20):
        - UTC-derived implementation writes ``date_utc=2026-05-19``.
        - ``date.today()``-based implementation writes ``date_utc=2026-05-20``.
        """
        from datetime import datetime, timezone

        from backlink_publisher.publishing.adapters import velog_graphql

        UTC_DATE = "2026-05-19"
        LOCAL_DATE_IN_FAKE_KST = "2026-05-20"

        class _FakeDate:
            """Stand-in for ``datetime.date`` matching the bug's local-time semantics."""

            @classmethod
            def today(cls):
                from datetime import date as _real_date
                return _real_date.fromisoformat(LOCAL_DATE_IN_FAKE_KST)

        class _FakeDatetime(datetime):
            """Stand-in for ``datetime.datetime`` returning the correct UTC instant."""

            @classmethod
            def now(cls, tz=None):
                base = datetime(2026, 5, 19, 23, 30, 0, tzinfo=timezone.utc)
                if tz is None:
                    return _FakeDate.today()  # local naive
                return base.astimezone(tz)

        # Patch both names that either implementation might use. The
        # current fix only touches ``datetime``; the bug version reads
        # ``date.today()``. raising=False so the test passes against
        # either source state.
        monkeypatch.setattr(velog_graphql, "datetime", _FakeDatetime)
        monkeypatch.setattr(velog_graphql, "date", _FakeDate, raising=False)

        p = tmp_path / "count.json"
        _write_count(p, 5, 1.0)
        body = json.loads(p.read_text())
        assert body["date_utc"] == UTC_DATE, (
            f"date_utc={body['date_utc']!r} — derived from local time "
            "instead of UTC. Cap reset boundary must be UTC midnight."
        )

        # And the corresponding read on the same UTC day must NOT reset.
        count, _ = _read_count(p)
        assert count == 5

    def test_stale_date_resets(self, tmp_path):
        p = tmp_path / "count.json"
        p.write_text(json.dumps({"date_utc": "2020-01-01", "count": 30, "last_publish_at": 1.0}))
        count, last = _read_count(p)
        assert count == 0
        assert last == 0.0

    def test_corrupt_json_resets(self, tmp_path):
        p = tmp_path / "count.json"
        p.write_text("garbage{")
        count, last = _read_count(p)
        assert count == 0

    def test_write_then_read_roundtrip(self, tmp_path):
        p = tmp_path / "count.json"
        _write_count(p, 7, 1234567890.0)
        count, last = _read_count(p)
        assert count == 7
        assert last == 1234567890.0
        # File must be 0600
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


# ── VelogGraphQLAdapter.publish() ─────────────────────────────────────────────

def _make_config(tmp_path: Path) -> Config:
    cookies_file = tmp_path / "velog-cookies.json"
    cookies_file.write_text(json.dumps({
        "cookies": [
            {"name": "access_token", "value": "AT_TEST"},
            {"name": "refresh_token", "value": "RT_TEST"},
        ]
    }))
    os.chmod(cookies_file, 0o600)
    return Config(velog=VelogConfig(cookies_path=cookies_file))


PAYLOAD = {
    "id": "test-001",
    "title": "Test Velog Post",
    "content_markdown": "# Hello\n\nCheck out [this link](https://example.com).",
    "tags": ["test", "spike"],
    "target_url": "https://example.com",
}


def _mock_success_response(url_slug="test-velog-post", username="redredchen01"):
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.json.return_value = {
        "data": {
            "writePost": {
                "id": "post-uuid-123",
                "user": {"id": "user-uuid", "username": username, "__typename": "User"},
                "url_slug": url_slug,
                "__typename": "Post",
            }
        }
    }
    return resp


def _mock_null_response():
    """Simulates silent-drop (access_token expired, refresh happening)."""
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.json.return_value = {"data": {"writePost": None}}
    return resp


class TestVelogGraphQLAdapterPublish:
    def _patch_lock_and_count(self, tmp_path):
        """Context manager patches that bypass fcntl locking for tests."""
        import contextlib

        @contextlib.contextmanager
        def _patches():
            with patch(
                "backlink_publisher.publishing.adapters.velog_graphql._acquire_lock"
            ) as mock_lock, patch(
                "backlink_publisher.publishing.adapters.velog_graphql._release_lock"
            ), patch(
                "backlink_publisher.publishing.adapters.velog_graphql._read_count",
                return_value=(0, 0.0),
            ), patch(
                "backlink_publisher.publishing.adapters.velog_graphql._write_count"
            ), patch(
                "backlink_publisher.publishing.adapters.velog_graphql.random.uniform",
                return_value=0,  # skip jitter
            ):
                mock_lock.return_value = 99  # fake fd
                yield
        return _patches()

    def test_happy_path_publishes(self, tmp_path):
        config = _make_config(tmp_path)
        adapter = VelogGraphQLAdapter()

        with self._patch_lock_and_count(tmp_path):
            with patch("requests.Session") as MockSession:
                sess = MagicMock()
                MockSession.return_value = sess
                sess.post.return_value = _mock_success_response()
                with patch(
                    "backlink_publisher.publishing.adapters.velog_graphql.verify_link_attributes",
                    return_value={"verification": "ok"},
                ):
                    result = adapter.publish(PAYLOAD, mode="publish", config=config)

        assert result.status == "published"
        assert result.platform == "velog"
        assert result.adapter == "velog-graphql"
        assert "redredchen01" in result.published_url
        assert result._provider_meta["post_id"] == "post-uuid-123"

    def test_silent_drop_retry_succeeds(self, tmp_path):
        """First call returns null, second (with new AT from Set-Cookie) succeeds."""
        config = _make_config(tmp_path)
        adapter = VelogGraphQLAdapter()

        with self._patch_lock_and_count(tmp_path):
            with patch("requests.Session") as MockSession:
                sess = MagicMock()
                MockSession.return_value = sess
                sess.post.side_effect = [
                    _mock_null_response(),        # first: silent-drop
                    _mock_success_response(),     # retry: success
                ]
                with patch(
                    "backlink_publisher.publishing.adapters.velog_graphql.verify_link_attributes",
                    return_value={"verification": "ok"},
                ):
                    result = adapter.publish(PAYLOAD, mode="publish", config=config)

        assert result.status == "published"
        assert sess.post.call_count == 2

    def test_null_after_retry_probe_dead_raises_auth_expired(self, tmp_path):
        """Both writePost calls return null; probe says cookie dead → AuthExpiredError."""
        config = _make_config(tmp_path)
        adapter = VelogGraphQLAdapter()

        probe_resp = MagicMock()
        probe_resp.ok = True
        probe_resp.status_code = 200
        probe_resp.json.return_value = {"data": {"currentUser": None}}

        with self._patch_lock_and_count(tmp_path):
            with patch("requests.Session") as MockSession:
                sess = MagicMock()
                MockSession.return_value = sess
                sess.post.side_effect = [
                    _mock_null_response(),  # first writePost
                    _mock_null_response(),  # retry writePost
                    probe_resp,             # liveness probe
                ]

                with pytest.raises(AuthExpiredError, match="cookie dead"):
                    adapter.publish(PAYLOAD, mode="publish", config=config)

        assert sess.post.call_count == 3

    def test_null_after_retry_probe_alive_raises_content_rejected(self, tmp_path):
        """Both writePost calls return null; probe says cookie alive → ContentRejectedError."""
        config = _make_config(tmp_path)
        adapter = VelogGraphQLAdapter()

        probe_resp = MagicMock()
        probe_resp.ok = True
        probe_resp.status_code = 200
        probe_resp.json.return_value = {
            "data": {"currentUser": {"id": "user-123", "username": "testuser"}}
        }

        with self._patch_lock_and_count(tmp_path):
            with patch("requests.Session") as MockSession:
                sess = MagicMock()
                MockSession.return_value = sess
                sess.post.side_effect = [
                    _mock_null_response(),  # first writePost
                    _mock_null_response(),  # retry writePost
                    probe_resp,             # liveness probe
                ]

                with pytest.raises(ContentRejectedError) as exc_info:
                    adapter.publish(PAYLOAD, mode="publish", config=config)

        assert "cookie alive" in str(exc_info.value)
        assert exc_info.value.channel == "velog"

    def test_null_after_retry_probe_unreachable_fails_safe_to_auth_expired(self, tmp_path):
        """Probe network error → fail-safe: AuthExpiredError (not ContentRejectedError)."""
        config = _make_config(tmp_path)
        adapter = VelogGraphQLAdapter()

        with self._patch_lock_and_count(tmp_path):
            with patch("requests.Session") as MockSession:
                sess = MagicMock()
                MockSession.return_value = sess
                sess.post.side_effect = [
                    _mock_null_response(),               # first writePost
                    _mock_null_response(),               # retry writePost
                    requests.ConnectionError("timeout"), # probe fails
                ]

                with pytest.raises(AuthExpiredError, match="cookie dead"):
                    adapter.publish(PAYLOAD, mode="publish", config=config)

    def test_null_after_retry_saves_artifact(self, tmp_path):
        """null-after-retry writes a debug artifact JSON with full response body."""
        config = _make_config(tmp_path)
        adapter = VelogGraphQLAdapter()

        probe_resp = MagicMock()
        probe_resp.ok = True
        probe_resp.status_code = 200
        probe_resp.json.return_value = {"data": {"currentUser": None}}

        with self._patch_lock_and_count(tmp_path):
            with patch("requests.Session") as MockSession:
                sess = MagicMock()
                MockSession.return_value = sess
                sess.post.side_effect = [
                    _mock_null_response(),
                    _mock_null_response(),
                    probe_resp,
                ]
                with pytest.raises(AuthExpiredError):
                    adapter.publish(PAYLOAD, mode="publish", config=config)

        artifacts = list((config.config_dir / "debug").glob("velog-null-*.json"))
        assert len(artifacts) == 1
        data = json.loads(artifacts[0].read_text())
        assert data["adapter"] == "velog-graphql"
        assert data["article_id"] == PAYLOAD["id"]
        assert "response_body" in data

    def test_daily_cap_raises_dependency_error(self, tmp_path):
        """When count >= cap, DependencyError before any HTTP call."""
        config = _make_config(tmp_path)
        adapter = VelogGraphQLAdapter()

        with patch(
            "backlink_publisher.publishing.adapters.velog_graphql._acquire_lock",
            return_value=99,
        ), patch(
            "backlink_publisher.publishing.adapters.velog_graphql._release_lock"
        ), patch(
            "backlink_publisher.publishing.adapters.velog_graphql._read_count",
            return_value=(5, time.time()),  # count == initial cap (5)
        ), patch(
            "backlink_publisher.publishing.adapters.velog_graphql._effective_cap",
            return_value=5,
        ):
            with pytest.raises(DependencyError, match="daily cap"):
                adapter.publish(PAYLOAD, mode="publish", config=config)

    def test_missing_cookies_raises_dependency_error(self, tmp_path):
        """Cookie file absent → DependencyError before HTTP."""
        config = Config(velog=VelogConfig(cookies_path=tmp_path / "no-file.json"))
        adapter = VelogGraphQLAdapter()

        with pytest.raises(DependencyError, match="velog-login"):
            adapter.publish(PAYLOAD, mode="publish", config=config)

    def test_url_slug_generated_from_title(self, tmp_path):
        """Published URL slug is derived from title, not null."""
        config = _make_config(tmp_path)
        adapter = VelogGraphQLAdapter()

        captured_payload = {}

        def _capture_and_respond(*args, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            return _mock_success_response(url_slug="test-velog-post")

        with self._patch_lock_and_count(tmp_path):
            with patch("requests.Session") as MockSession:
                sess = MagicMock()
                MockSession.return_value = sess
                sess.post.side_effect = _capture_and_respond
                with patch(
                    "backlink_publisher.publishing.adapters.velog_graphql.verify_link_attributes",
                    return_value={"verification": "ok"},
                ):
                    adapter.publish(PAYLOAD, mode="publish", config=config)

        slug_sent = captured_payload["variables"]["url_slug"]
        assert slug_sent is not None
        assert slug_sent != ""
        assert "test" in slug_sent  # derived from "Test Velog Post"


# ── _probe_session_alive ───────────────────────────────────────────────────────

class TestProbeSessionAlive:
    def _make_probe_session(self, json_body=None, status_code=200, raise_exc=None):
        sess = MagicMock()
        if raise_exc:
            sess.post.side_effect = raise_exc
        else:
            resp = MagicMock()
            resp.ok = (status_code < 400)
            resp.status_code = status_code
            resp.json.return_value = json_body
            sess.post.return_value = resp
        return sess

    def test_alive_returns_true_with_username(self):
        sess = self._make_probe_session(
            json_body={"data": {"currentUser": {"id": "uid123", "username": "alice"}}}
        )
        alive, reason = _probe_session_alive(sess)
        assert alive is True
        assert reason == "alice"

    def test_null_current_user_returns_false(self):
        sess = self._make_probe_session(json_body={"data": {"currentUser": None}})
        alive, reason = _probe_session_alive(sess)
        assert alive is False
        assert "no_current_user" in reason

    def test_missing_id_returns_false(self):
        sess = self._make_probe_session(
            json_body={"data": {"currentUser": {"username": "bob"}}}  # no id
        )
        alive, reason = _probe_session_alive(sess)
        assert alive is False

    def test_http_401_returns_false(self):
        sess = self._make_probe_session(json_body={}, status_code=401)
        alive, reason = _probe_session_alive(sess)
        assert alive is False
        assert "401" in reason

    def test_connection_error_returns_false_probe_unreachable(self):
        sess = self._make_probe_session(raise_exc=requests.ConnectionError("refused"))
        alive, reason = _probe_session_alive(sess)
        assert alive is False
        assert reason == "probe_unreachable"

    def test_timeout_returns_false_probe_unreachable(self):
        sess = self._make_probe_session(raise_exc=requests.Timeout())
        alive, reason = _probe_session_alive(sess)
        assert alive is False
        assert reason == "probe_unreachable"

    def test_invalid_json_returns_false(self):
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.json.side_effect = ValueError("bad json")
        sess = MagicMock()
        sess.post.return_value = resp
        alive, reason = _probe_session_alive(sess)
        assert alive is False
        assert "invalid_json" in reason


# ── _save_null_artifact ────────────────────────────────────────────────────────

class TestSaveNullArtifact:
    def test_writes_artifact_0600(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        config = _make_config(tmp_path)
        artifact = _save_null_artifact(
            resp_json={"data": {"writePost": None}},
            resp_headers={"content-type": "application/json"},
            article_id="abc123",
            config=config,
        )
        assert artifact is not None
        p = Path(artifact)
        assert p.exists()
        assert oct(p.stat().st_mode & 0o777) == "0o600"
        data = json.loads(p.read_text())
        assert data["article_id"] == "abc123"
        assert data["adapter"] == "velog-graphql"

    def test_captures_gql_errors_array(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        config = _make_config(tmp_path)
        resp_body = {
            "data": {"writePost": None},
            "errors": [{"message": "forbidden"}],
        }
        artifact = _save_null_artifact(resp_body, {}, "x1", config)
        data = json.loads(Path(artifact).read_text())
        assert data["gql_errors"] == [{"message": "forbidden"}]

    def test_returns_none_on_io_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        config = _make_config(tmp_path)
        with patch(
            "backlink_publisher.publishing.adapters.velog_graphql.os.replace",
            side_effect=OSError("disk full"),
        ):
            result = _save_null_artifact({}, {}, "z1", config)
        # Should not raise — returns None on failure
        assert result is None


# ── _mask_cookies ──────────────────────────────────────────────────────────────

class TestMaskCookies:
    def test_masks_token_fields(self):
        cookies = {
            "access_token": "secret_at",
            "refresh_token": "secret_rt",
            "token": "secret_t",
            "other_cookie": "visible",
        }
        masked = _mask_cookies(cookies)
        assert masked["access_token"] == "<masked>"
        assert masked["refresh_token"] == "<masked>"
        assert masked["token"] == "<masked>"
        assert masked["other_cookie"] == "visible"

    def test_does_not_mutate_original(self):
        cookies = {"access_token": "secret"}
        masked = _mask_cookies(cookies)
        assert cookies["access_token"] == "secret"
        assert masked["access_token"] == "<masked>"


# ── ContentRejectedError taxonomy ─────────────────────────────────────────────

class TestContentRejectedErrorTaxonomy:
    def test_is_dependency_error_subclass(self):
        from backlink_publisher._util.errors import DependencyError
        assert issubclass(ContentRejectedError, DependencyError)

    def test_is_not_auth_expired_subclass(self):
        assert not issubclass(ContentRejectedError, AuthExpiredError)

    def test_exit_code_is_3(self):
        exc = ContentRejectedError(channel="velog", reason="x")
        assert exc.exit_code == 3

    def test_message_contains_channel_and_reason(self):
        exc = ContentRejectedError(channel="velog", reason="slug collision")
        assert "velog" in str(exc)
        assert "slug collision" in str(exc)
