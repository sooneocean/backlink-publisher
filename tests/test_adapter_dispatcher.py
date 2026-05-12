"""Tests for the adapter dispatcher in adapters/__init__.py."""

from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.adapters import publish, verify_adapter_setup
from backlink_publisher.adapters.base import AdapterResult
from backlink_publisher.config import Config, BloggerOAuthConfig
from backlink_publisher.errors import DependencyError, ExternalServiceError

BLOGGER_PAYLOAD = {
    "id": "id1",
    "platform": "blogger",
    "title": "Test",
    "content_markdown": "Hello.",
    "tags": [],
    "main_domain": "https://myblog.com/",
}
MEDIUM_PAYLOAD = {
    "id": "id2",
    "platform": "medium",
    "title": "Test",
    "content_markdown": "Hello.",
    "tags": [],
    "seo": {"canonical_url": ""},
}

CONFIG_BLOGGER = Config(
    blogger_blog_ids={"https://myblog.com": "999"},
    blogger_oauth=BloggerOAuthConfig("cid", "csecret"),
)
CONFIG_MEDIUM_TOKEN = Config(medium_integration_token="tok123")
CONFIG_NO_TOKEN = Config(medium_integration_token=None)

BLOGGER_RESULT = AdapterResult(
    status="drafted", adapter="blogger-api", platform="blogger",
    draft_url="https://blog.example.com/p/123"
)
MEDIUM_API_RESULT = AdapterResult(
    status="drafted", adapter="medium-api", platform="medium",
    draft_url="https://medium.com/@u/post"
)
MEDIUM_BROWSER_RESULT = AdapterResult(
    status="drafted", adapter="medium-browser", platform="medium",
    draft_url="https://medium.com/new-story?id=abc"
)


@patch("backlink_publisher.adapters.BloggerAPIAdapter.publish", return_value=BLOGGER_RESULT)
def test_blogger_routes_to_blogger_adapter(mock_pub):
    result = publish(BLOGGER_PAYLOAD, mode="draft", config=CONFIG_BLOGGER)
    assert result.adapter == "blogger-api"
    mock_pub.assert_called_once()


@patch("backlink_publisher.adapters.MediumAPIAdapter.publish", return_value=MEDIUM_API_RESULT)
def test_medium_with_token_uses_api_adapter(mock_pub):
    result = publish(MEDIUM_PAYLOAD, mode="draft", config=CONFIG_MEDIUM_TOKEN)
    assert result.adapter == "medium-api"
    mock_pub.assert_called_once()


@patch("backlink_publisher.adapters.MediumBrowserAdapter.publish", return_value=MEDIUM_BROWSER_RESULT)
@patch("backlink_publisher.adapters.MediumAPIAdapter.publish", side_effect=DependencyError("no token"))
def test_medium_fallthrough_to_browser_on_dependency_error(mock_api, mock_browser):
    result = publish(MEDIUM_PAYLOAD, mode="draft", config=CONFIG_NO_TOKEN)
    assert result.adapter == "medium-browser"
    mock_api.assert_called_once()
    mock_browser.assert_called_once()


@patch("backlink_publisher.adapters.MediumBrowserAdapter.publish")
@patch("backlink_publisher.adapters.MediumAPIAdapter.publish", side_effect=ExternalServiceError("401"))
def test_medium_no_fallthrough_on_external_service_error(mock_api, mock_browser):
    with pytest.raises(ExternalServiceError):
        publish(MEDIUM_PAYLOAD, mode="draft", config=CONFIG_MEDIUM_TOKEN)
    mock_browser.assert_not_called()


def test_dry_run_returns_sentinel():
    result = publish(BLOGGER_PAYLOAD, mode="draft", config=CONFIG_BLOGGER, dry_run=True)
    assert result._dry_run is True
    assert result.adapter == "blogger-api"


def test_unsupported_platform_raises_external_service_error():
    payload = {**BLOGGER_PAYLOAD, "platform": "myspace"}
    with pytest.raises(ExternalServiceError, match="unsupported"):
        publish(payload, mode="draft", config=Config())


def test_verify_blogger_requires_oauth():
    cfg = Config(blogger_blog_ids={}, blogger_oauth=None)
    with pytest.raises(DependencyError, match="OAuth"):
        verify_adapter_setup("blogger", cfg)


def test_verify_blogger_ok_with_oauth():
    cfg = Config(blogger_oauth=BloggerOAuthConfig("id", "secret"))
    verify_adapter_setup("blogger", cfg)  # should not raise


def test_verify_medium_requires_token_or_playwright():
    cfg = Config(medium_integration_token=None)
    # Playwright may or may not be installed in test env — mock it absent
    import backlink_publisher.adapters.medium_browser as mb
    original = mb.sync_playwright
    mb.sync_playwright = None
    try:
        with pytest.raises(DependencyError, match="integration_token"):
            verify_adapter_setup("medium", cfg)
    finally:
        mb.sync_playwright = original


def test_verify_medium_ok_with_token():
    cfg = Config(medium_integration_token="tok")
    verify_adapter_setup("medium", cfg)  # should not raise
