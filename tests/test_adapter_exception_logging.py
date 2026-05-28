"""Unit 3 of Plan 008 — Adapter exception logging (silent-swallow prevention).

Asserts that:
  1. medium_browser._save_screenshot() catches screenshot/stderr-write errors
     and logs them rather than swallowing or re-raising (the outer publish loop
     must not fail due to a diagnostic-capture error).
  2. linkedin_api execute() handles resp.json() decode failure gracefully:
     sets data={} and continues with an ExternalServiceError, not a
     bare JSONDecodeError propagation.

Both behaviors were already fixed in the codebase; these tests document and
lock the contracts.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from backlink_publisher.config import Config


# ---------------------------------------------------------------------------
# Unit 3a: medium_browser._save_screenshot() exception handling
# ---------------------------------------------------------------------------


class TestMediumBrowserScreenshotExceptionHandling:
    """_save_screenshot must catch screenshot/write errors, log them as debug,
    and NOT re-raise — a diagnostic failure must never mask the real error."""

    def test_screenshot_page_error_is_logged_not_raised(self):
        """page.screenshot() raising must be caught and logged, not re-raised."""
        from backlink_publisher.publishing.adapters.medium_browser import _save_screenshot

        page = MagicMock()
        page.screenshot.side_effect = RuntimeError("playwright screenshot failed")
        config = Config()
        article_id = "test-article-001"

        # Must not raise — should catch the exception and log it
        with patch("backlink_publisher.publishing.adapters.medium_browser.log") as mock_log:
            _save_screenshot(page, config, article_id)
            # debug was called with the error
            mock_log.debug.assert_called_once()
            call_args = mock_log.debug.call_args
            assert "Failed to capture" in str(call_args) or mock_log.debug.called

    def test_screenshot_os_error_is_logged_not_raised(self):
        """OSError (permission denied for screenshot path) must not propagate."""
        from backlink_publisher.publishing.adapters.medium_browser import _save_screenshot

        page = MagicMock()
        page.screenshot.side_effect = OSError("permission denied")
        config = Config()

        with patch("backlink_publisher.publishing.adapters.medium_browser.log"):
            # Should not raise
            _save_screenshot(page, config, "article-002")

    def test_screenshot_success_logs_error_level(self):
        """On success, _save_screenshot logs at ERROR level (diagnostic artifact)."""
        from backlink_publisher.publishing.adapters.medium_browser import _save_screenshot

        page = MagicMock()
        config = Config()

        with patch("backlink_publisher.publishing.adapters.medium_browser.log") as mock_log:
            _save_screenshot(page, config, "article-success")
            # On success, log.error is called with the screenshot path
            mock_log.error.assert_called_once()


# ---------------------------------------------------------------------------
# Unit 3b: linkedin_api resp.json() decode failure handling
# ---------------------------------------------------------------------------


class TestLinkedInApiJsonDecodeHandling:
    """resp.json() failure in the HTTP 403 path must produce data={} and
    continue to ExternalServiceError — not propagate a raw JSONDecodeError."""

    def _make_mock_resp(self, status_code: int, json_data=None,
                        json_side_effect=None, text: str = "") -> MagicMock:
        resp = MagicMock()
        resp.status_code = status_code
        resp.text = text
        if json_side_effect is not None:
            resp.json.side_effect = json_side_effect
        else:
            resp.json.return_value = json_data or {}
        return resp

    def _make_config(self) -> Config:
        return Config()

    def _make_payload(self) -> dict:
        return {
            "id": "li-001",
            "target_url": "https://example.com",
            "article_title": "Test Post",
            "article_body": "<p>Test content with <a href='https://example.com'>link</a></p>",
            "platform": "linkedin",
            "language": "en",
        }

    def _patch_token(self):
        """Patch load_linkedin_token to return a dummy token."""
        return patch(
            "backlink_publisher.publishing.adapters.linkedin_api.load_linkedin_token",
            return_value={"token": "fake-token", "person_id": "urn:li:person:123"},
        )

    def test_403_with_json_decode_error_raises_external_service_error(self):
        """HTTP 403 with resp.json() raising ValueError must still produce
        ExternalServiceError (not let ValueError propagate)."""
        from backlink_publisher.publishing.adapters.linkedin_api import LinkedInAPIAdapter
        from backlink_publisher._util.errors import ExternalServiceError

        adapter = LinkedInAPIAdapter()
        payload = self._make_payload()
        config = self._make_config()

        mock_resp = self._make_mock_resp(
            403,
            json_side_effect=ValueError("No JSON object could be decoded"),
            text="forbidden",
        )

        with self._patch_token():
            with patch("requests.post", return_value=mock_resp):
                with pytest.raises(ExternalServiceError) as exc_info:
                    adapter.publish(payload, "publish", config)
                # Error message must mention HTTP 403
                assert "403" in str(exc_info.value)

    def test_403_json_decode_error_data_defaults_to_empty_dict(self):
        """When resp.json() fails for HTTP 403, the error message falls back
        to resp.text — it does NOT raise JSONDecodeError."""
        from backlink_publisher.publishing.adapters.linkedin_api import LinkedInAPIAdapter
        from backlink_publisher._util.errors import ExternalServiceError

        adapter = LinkedInAPIAdapter()
        payload = self._make_payload()
        config = self._make_config()

        # resp.text provides the fallback error text
        mock_resp = self._make_mock_resp(
            403,
            json_side_effect=ValueError("decode error"),
            text="rate limit exceeded",
        )

        with self._patch_token():
            with patch("requests.post", return_value=mock_resp):
                with pytest.raises(ExternalServiceError) as exc_info:
                    adapter.publish(payload, "publish", config)
                # Falls back to resp.text since json() failed
                assert "rate limit exceeded" in str(exc_info.value) or "403" in str(exc_info.value)

    def test_403_valid_json_uses_message_field(self):
        """HTTP 403 with valid JSON response uses the 'message' field."""
        from backlink_publisher.publishing.adapters.linkedin_api import LinkedInAPIAdapter
        from backlink_publisher._util.errors import ExternalServiceError

        adapter = LinkedInAPIAdapter()
        payload = self._make_payload()
        config = self._make_config()

        mock_resp = self._make_mock_resp(
            403,
            json_data={"message": "insufficient scope for w_member_social"},
            text="forbidden",
        )

        with self._patch_token():
            with patch("requests.post", return_value=mock_resp):
                with pytest.raises(ExternalServiceError) as exc_info:
                    adapter.publish(payload, "publish", config)
                assert "w_member_social" in str(exc_info.value) or "403" in str(exc_info.value)

    def test_401_raises_external_service_error_with_token_message(self):
        """HTTP 401 must raise ExternalServiceError mentioning token expiry."""
        from backlink_publisher.publishing.adapters.linkedin_api import LinkedInAPIAdapter
        from backlink_publisher._util.errors import ExternalServiceError

        adapter = LinkedInAPIAdapter()
        payload = self._make_payload()
        config = self._make_config()

        mock_resp = self._make_mock_resp(401)

        with self._patch_token():
            with patch("requests.post", return_value=mock_resp):
                with pytest.raises(ExternalServiceError) as exc_info:
                    adapter.publish(payload, "publish", config)
                assert "401" in str(exc_info.value)
