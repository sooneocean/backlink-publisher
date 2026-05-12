"""Tests for MediumBrowserAdapter (Playwright mocked)."""

from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from backlink_publisher.adapters.medium_browser import MediumBrowserAdapter
from backlink_publisher.config import Config
from backlink_publisher.errors import DependencyError, ExternalServiceError

PAYLOAD = {
    "id": "abc123",
    "title": "Test Post",
    "content_markdown": "# Hello\n\nWorld.",
    "tags": ["tag1", "tag2"],
    "seo": {"canonical_url": "https://example.com/article"},
}

CONFIG = Config(medium_user_data_dir=Path("/tmp/test-chrome-profile"))


def make_mock_pw(page_url="https://medium.com/@user/test-draft-abc123"):
    """Build a minimal Playwright mock that looks like a successful run."""
    mock_page = MagicMock()
    mock_page.url = page_url
    mock_page.locator.return_value = MagicMock()
    mock_page.locator.return_value.count.return_value = 0
    mock_page.evaluate = MagicMock()
    mock_page.keyboard = MagicMock()

    mock_context = MagicMock()
    mock_context.new_page.return_value = mock_page
    mock_context.__enter__ = MagicMock(return_value=mock_context)
    mock_context.__exit__ = MagicMock(return_value=False)

    mock_pw = MagicMock()
    mock_pw.chromium.launch_persistent_context.return_value = mock_context
    mock_pw.__enter__ = MagicMock(return_value=mock_pw)
    mock_pw.__exit__ = MagicMock(return_value=False)

    return mock_pw, mock_context, mock_page


@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_draft_mode_returns_draft_url(mock_sync_pw):
    mock_pw, mock_ctx, mock_page = make_mock_pw()
    mock_sync_pw.return_value = mock_pw

    adapter = MediumBrowserAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG)

    assert result.status == "drafted"
    assert result.draft_url == "https://medium.com/@user/test-draft-abc123"
    assert result.adapter == "medium-browser"


@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_publish_mode_clicks_publish_button(mock_sync_pw):
    mock_pw, mock_ctx, mock_page = make_mock_pw(
        "https://medium.com/@user/live-post-abc123"
    )
    mock_sync_pw.return_value = mock_pw

    adapter = MediumBrowserAdapter()
    result = adapter.publish(PAYLOAD, mode="publish", config=CONFIG)

    assert result.status == "published"
    assert result.published_url == "https://medium.com/@user/live-post-abc123"


@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_login_redirect_raises_external_service_error(mock_sync_pw):
    mock_pw, mock_ctx, mock_page = make_mock_pw()
    mock_page.url = "https://medium.com/m/signin?redirect=..."
    mock_sync_pw.return_value = mock_pw

    adapter = MediumBrowserAdapter()
    with pytest.raises(ExternalServiceError, match="login expired"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)


@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_captcha_raises_external_service_error(mock_sync_pw):
    mock_pw, mock_ctx, mock_page = make_mock_pw()
    mock_page.url = "https://medium.com/new-story"
    # CAPTCHA iframe present
    captcha_locator = MagicMock()
    captcha_locator.count.return_value = 1

    def locator_side_effect(sel_str):
        if "captcha" in sel_str:
            return captcha_locator
        return MagicMock()

    mock_page.locator.side_effect = locator_side_effect
    mock_sync_pw.return_value = mock_pw

    adapter = MediumBrowserAdapter()
    with pytest.raises(ExternalServiceError, match="CAPTCHA"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)


def test_playwright_not_installed_raises_dependency_error():
    """When sync_playwright is None (import failed at module load), raise DependencyError."""
    import backlink_publisher.adapters.medium_browser as mod
    original = mod.sync_playwright
    try:
        mod.sync_playwright = None
        adapter = MediumBrowserAdapter()
        with pytest.raises(DependencyError, match="Playwright is not installed"):
            adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    finally:
        mod.sync_playwright = original


@patch("backlink_publisher.adapters.retry.time.sleep")
@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_playwright_timeout_retried_and_recovers(mock_sync_pw, mock_sleep):
    """PlaywrightTimeoutError on first attempt triggers retry; second succeeds."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    success_pw, success_ctx, success_page = make_mock_pw()

    call_count = [0]

    def sync_pw_factory():
        call_count[0] += 1
        if call_count[0] == 1:
            # First attempt: page.goto raises TimeoutError, no CAPTCHA
            fail_page = MagicMock()
            fail_page.url = "https://medium.com/new-story"
            fail_page.locator.return_value.count.return_value = 0
            fail_page.goto.side_effect = PlaywrightTimeout("timeout")

            fail_ctx = MagicMock()
            fail_ctx.new_page.return_value = fail_page
            fail_ctx.__enter__ = MagicMock(return_value=fail_ctx)
            fail_ctx.__exit__ = MagicMock(return_value=False)

            fail_pw = MagicMock()
            fail_pw.chromium.launch_persistent_context.return_value = fail_ctx
            fail_pw.__enter__ = MagicMock(return_value=fail_pw)
            fail_pw.__exit__ = MagicMock(return_value=False)
            return fail_pw
        return success_pw

    mock_sync_pw.side_effect = sync_pw_factory

    adapter = MediumBrowserAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    assert result.status == "drafted"
    mock_sleep.assert_called_once()
    assert call_count[0] == 2  # two browser contexts opened


@patch("backlink_publisher.adapters.retry.time.sleep")
@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_captcha_after_timeout_not_retried(mock_sync_pw, mock_sleep):
    """TimeoutError with CAPTCHA in DOM → non-retryable ExternalServiceError, no sleep."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    mock_pw = MagicMock()
    mock_page = MagicMock()
    mock_page.url = "https://medium.com/new-story"

    # CAPTCHA present
    captcha_locator = MagicMock()
    captcha_locator.count.return_value = 1
    mock_page.locator.return_value = captcha_locator
    mock_page.goto.side_effect = PlaywrightTimeout("slow load")

    mock_ctx = MagicMock()
    mock_ctx.new_page.return_value = mock_page
    mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
    mock_ctx.__exit__ = MagicMock(return_value=False)
    mock_pw.chromium.launch_persistent_context.return_value = mock_ctx
    mock_pw.__enter__ = MagicMock(return_value=mock_pw)
    mock_pw.__exit__ = MagicMock(return_value=False)

    mock_sync_pw.return_value = mock_pw

    adapter = MediumBrowserAdapter()
    with pytest.raises(ExternalServiceError, match="CAPTCHA"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    mock_sleep.assert_not_called()


@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_html_clipboard_content_matches_render(mock_sync_pw):
    """Clipboard write must contain rendered HTML, not raw markdown."""
    from backlink_publisher.markdown_utils import render_to_html
    expected_html = render_to_html(PAYLOAD["content_markdown"])

    mock_pw, mock_ctx, mock_page = make_mock_pw()
    mock_sync_pw.return_value = mock_pw

    adapter = MediumBrowserAdapter()
    adapter.publish(PAYLOAD, mode="draft", config=CONFIG)

    # page.evaluate was called with the rendered HTML as the second argument
    evaluate_calls = mock_page.evaluate.call_args_list
    html_args = [c.args[1] for c in evaluate_calls if len(c.args) > 1]
    assert any(expected_html in arg for arg in html_args), (
        f"Expected rendered HTML in clipboard evaluate args. Got: {html_args}"
    )
