"""Tests for MediumAPIAdapter."""

from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.adapters.medium_api import MediumAPIAdapter
from backlink_publisher.config import Config
from backlink_publisher.errors import DependencyError, ExternalServiceError

PAYLOAD = {
    "id": "abc123",
    "title": "Test Post",
    "content_markdown": "# Hello\n\nWorld with [link](https://example.com).",
    "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6"],
    "seo": {"canonical_url": "https://example.com/article"},
    "publish_mode": "draft",
}

CONFIG_WITH_TOKEN = Config(medium_integration_token="my-token-xyz")
CONFIG_NO_TOKEN = Config(medium_integration_token=None)

ME_RESP = {"data": {"id": "user123", "username": "testuser"}}
POST_RESP_DRAFT = {"data": {"id": "post456", "url": "https://medium.com/@testuser/test-post-abc123"}}


def make_mock_get(status=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status
    resp.ok = status < 400
    resp.json.return_value = json_data or ME_RESP
    return resp


def make_mock_post(status=201, json_data=None):
    resp = MagicMock()
    resp.status_code = status
    resp.ok = status < 400
    resp.json.return_value = json_data or POST_RESP_DRAFT
    resp.text = ""
    return resp


def test_no_token_raises_dependency_error():
    adapter = MediumAPIAdapter()
    with pytest.raises(DependencyError, match="integration token not configured"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG_NO_TOKEN)


@patch("backlink_publisher.adapters.medium_api.requests.get")
@patch("backlink_publisher.adapters.medium_api.requests.post")
def test_draft_mode_returns_draft_url(mock_post, mock_get):
    mock_get.return_value = make_mock_get()
    mock_post.return_value = make_mock_post()

    adapter = MediumAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)

    assert result.status == "drafted"
    assert result.draft_url == "https://medium.com/@testuser/test-post-abc123"
    assert result.published_url == ""
    assert result.adapter == "medium-api"


@patch("backlink_publisher.adapters.medium_api.requests.get")
@patch("backlink_publisher.adapters.medium_api.requests.post")
def test_publish_mode_sends_public_status(mock_post, mock_get):
    mock_get.return_value = make_mock_get()
    pub_resp = {"data": {"id": "post789", "url": "https://medium.com/@testuser/live-post"}}
    mock_post.return_value = make_mock_post(json_data=pub_resp)

    adapter = MediumAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="publish", config=CONFIG_WITH_TOKEN)

    assert result.status == "published"
    post_body = mock_post.call_args[1]["json"]
    assert post_body["publishStatus"] == "public"


@patch("backlink_publisher.adapters.medium_api.requests.get")
def test_401_on_me_raises_external_service_error(mock_get):
    mock_get.return_value = make_mock_get(status=401)

    adapter = MediumAPIAdapter()
    with pytest.raises(ExternalServiceError, match="invalid"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)


@patch("backlink_publisher.adapters.retry.time.sleep")
@patch("backlink_publisher.adapters.medium_api.requests.get")
@patch("backlink_publisher.adapters.medium_api.requests.post")
def test_429_raises_rate_limited(mock_post, mock_get, mock_sleep):
    """429 on all retry attempts → ExternalServiceError after retries exhausted."""
    mock_get.return_value = make_mock_get()
    mock_post.return_value = make_mock_post(status=429)

    adapter = MediumAPIAdapter()
    with pytest.raises(ExternalServiceError, match="429"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)


@patch("backlink_publisher.adapters.retry.time.sleep")
@patch("backlink_publisher.adapters.medium_api.requests.get")
@patch("backlink_publisher.adapters.medium_api.requests.post")
def test_posts_429_retried_and_recovers(mock_post, mock_get, mock_sleep):
    """/posts 429 on first call triggers retry; second call succeeds."""
    mock_get.return_value = make_mock_get()
    mock_post.side_effect = [make_mock_post(status=429), make_mock_post()]

    adapter = MediumAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)
    assert result.status == "drafted"
    mock_sleep.assert_called_once()


@patch("backlink_publisher.adapters.retry.time.sleep")
@patch("backlink_publisher.adapters.medium_api.requests.get")
@patch("backlink_publisher.adapters.medium_api.requests.post")
def test_posts_503_retried_and_recovers(mock_post, mock_get, mock_sleep):
    """/posts 503 on first call triggers retry; second call succeeds."""
    mock_get.return_value = make_mock_get()
    mock_post.side_effect = [make_mock_post(status=503), make_mock_post()]

    adapter = MediumAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)
    assert result.status == "drafted"
    mock_sleep.assert_called_once()


@patch("backlink_publisher.adapters.retry.time.sleep")
@patch("backlink_publisher.adapters.medium_api.requests.get")
@patch("backlink_publisher.adapters.medium_api.requests.post")
def test_me_429_retried_and_recovers(mock_post, mock_get, mock_sleep):
    """/me 429 on first call triggers retry; second call succeeds."""
    mock_get.side_effect = [make_mock_get(status=429), make_mock_get()]
    mock_post.return_value = make_mock_post()

    adapter = MediumAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)
    assert result.status == "drafted"
    mock_sleep.assert_called_once()


@patch("backlink_publisher.adapters.retry.time.sleep")
@patch("backlink_publisher.adapters.medium_api.requests.get")
@patch("backlink_publisher.adapters.medium_api.requests.post")
def test_posts_connection_error_retried(mock_post, mock_get, mock_sleep):
    """/posts ConnectionError triggers retry; second call succeeds."""
    import requests as req
    mock_get.return_value = make_mock_get()
    mock_post.side_effect = [req.ConnectionError("network"), make_mock_post()]

    adapter = MediumAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)
    assert result.status == "drafted"
    mock_sleep.assert_called_once()


@patch("backlink_publisher.adapters.retry.time.sleep")
@patch("backlink_publisher.adapters.medium_api.requests.get")
@patch("backlink_publisher.adapters.medium_api.requests.post")
def test_posts_401_not_retried(mock_post, mock_get, mock_sleep):
    """/posts 401 is non-retryable — no sleep, ExternalServiceError immediately."""
    mock_get.return_value = make_mock_get()
    mock_post.return_value = make_mock_post(status=401)

    adapter = MediumAPIAdapter()
    with pytest.raises(ExternalServiceError, match="401"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)
    mock_sleep.assert_not_called()


@patch("backlink_publisher.adapters.retry.time.sleep")
@patch("backlink_publisher.adapters.medium_api.requests.get")
@patch("backlink_publisher.adapters.medium_api.requests.post")
def test_user_id_not_refetched_on_posts_retry(mock_post, mock_get, mock_sleep):
    """user_id from /me is cached — /me called once even on /posts retry."""
    import requests as req
    mock_get.return_value = make_mock_get()
    mock_post.side_effect = [req.Timeout("slow"), make_mock_post()]

    adapter = MediumAPIAdapter()
    adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)
    assert mock_get.call_count == 1  # /me called exactly once


@patch("backlink_publisher.adapters.medium_api.requests.get")
@patch("backlink_publisher.adapters.medium_api.requests.post")
def test_tags_truncated_to_5(mock_post, mock_get):
    mock_get.return_value = make_mock_get()
    mock_post.return_value = make_mock_post()

    adapter = MediumAPIAdapter()
    adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)  # payload has 6 tags

    post_body = mock_post.call_args[1]["json"]
    assert len(post_body["tags"]) == 5


@patch("backlink_publisher.adapters.medium_api.requests.get")
@patch("backlink_publisher.adapters.medium_api.requests.post")
def test_canonical_url_omitted_if_empty(mock_post, mock_get):
    mock_get.return_value = make_mock_get()
    mock_post.return_value = make_mock_post()

    payload = {**PAYLOAD, "seo": {"canonical_url": ""}}
    adapter = MediumAPIAdapter()
    adapter.publish(payload, mode="draft", config=CONFIG_WITH_TOKEN)

    post_body = mock_post.call_args[1]["json"]
    assert "canonicalUrl" not in post_body


@patch("backlink_publisher.adapters.medium_api.requests.get")
@patch("backlink_publisher.adapters.medium_api.requests.post")
def test_html_body_is_rendered_markdown(mock_post, mock_get):
    """The POST body must contain rendered HTML, not raw markdown."""
    mock_get.return_value = make_mock_get()
    mock_post.return_value = make_mock_post()

    adapter = MediumAPIAdapter()
    adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)

    post_body = mock_post.call_args[1]["json"]
    assert post_body["contentFormat"] == "html"
    assert "<h1>" in post_body["content"]
    assert "https://example.com" in post_body["content"]
