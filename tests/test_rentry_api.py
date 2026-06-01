"""Rentry adapter tests (P1#12).

Rentry is anonymous (no credentials), so it has no DependencyError path —
``available()`` is always True. The contract here is: availability,
a happy-path paste creation (CSRF fetch → POST → "created"), and an
ExternalServiceError when the upstream returns an error.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher._util.errors import ExternalServiceError
from backlink_publisher.publishing.adapters.rentry_api import RentryAPIAdapter
from backlink_publisher.publishing.adapters.base import AdapterResult

_GET = "backlink_publisher.publishing.adapters.rentry_api.requests.get"
_POST = "backlink_publisher.publishing.adapters.rentry_api.requests.post"


def _payload():
    return {"id": "a1", "title": "T", "content_html": "<p>hi</p>"}


def _home_ok():
    home = MagicMock()
    home.status_code = 200
    home.text = '<input name="csrfmiddlewaretoken" value="csrf123">'
    home.cookies.get_dict.return_value = {"csrftoken": "csrf123"}
    return home


def test_available_is_true_without_credentials():
    assert RentryAPIAdapter.available(MagicMock()) is True


def test_homepage_failure_raises_external_service_error():
    home = MagicMock()
    home.status_code = 503
    with patch(_GET, return_value=home):
        with pytest.raises(ExternalServiceError):
            RentryAPIAdapter().publish(_payload(), "publish", MagicMock())


def test_api_error_status_raises_external_service_error():
    post = MagicMock()
    post.status_code = 200
    post.json.return_value = {"status": "error", "message": "rate limited"}
    post.text = ""
    with patch(_GET, return_value=_home_ok()), patch(_POST, return_value=post):
        with pytest.raises(ExternalServiceError):
            RentryAPIAdapter().publish(_payload(), "publish", MagicMock())


def test_post_429_not_retried_no_duplicate_paste():
    """P2: the non-idempotent create POST must run exactly once even on a
    429 — retrying it (the old behavior, when the whole GET+POST was inside
    retry_transient_call) would create a duplicate paste."""
    post = MagicMock()
    post.status_code = 429
    post.text = "rate limited"
    post_mock = MagicMock(return_value=post)
    with patch(_GET, return_value=_home_ok()), patch(_POST, post_mock):
        with pytest.raises(ExternalServiceError):
            RentryAPIAdapter().publish(_payload(), "publish", MagicMock())
    assert post_mock.call_count == 1


def test_happy_path_returns_adapter_result():
    """Legacy schema: status=created + url_id (kept for backward compat)."""
    post = MagicMock()
    post.status_code = 200
    post.json.return_value = {"status": "created", "url_id": "abc", "edit_code": "e"}
    post.text = ""
    with patch(_GET, return_value=_home_ok()), patch(_POST, return_value=post):
        result = RentryAPIAdapter().publish(_payload(), "publish", MagicMock())
    assert isinstance(result, AdapterResult)
    assert "abc" in result.published_url


def test_happy_path_current_api_schema():
    """Current Rentry API: status="200", full URL in ``url``, no ``url_id``."""
    post = MagicMock()
    post.status_code = 200
    post.json.return_value = {
        "status": "200",
        "content": "OK",
        "url": "https://rentry.co/noom4zt8",
        "url_short": "noom4zt8",
        "edit_code": "75eymTRz",
    }
    post.text = ""
    with patch(_GET, return_value=_home_ok()), patch(_POST, return_value=post):
        result = RentryAPIAdapter().publish(_payload(), "publish", MagicMock())
    assert isinstance(result, AdapterResult)
    assert result.published_url == "https://rentry.co/noom4zt8"
