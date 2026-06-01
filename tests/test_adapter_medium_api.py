"""Tests for MediumAPIAdapter."""

from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.publishing.adapters.medium_api import MediumAPIAdapter
from backlink_publisher.config import Config
from backlink_publisher._util.errors import AuthExpiredError, DependencyError, ExternalServiceError

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


@patch("backlink_publisher.publishing.adapters.medium_api.http_get")
@patch("backlink_publisher.publishing.adapters.medium_api.http_post")
def test_draft_mode_returns_draft_url(mock_post, mock_get):
    mock_get.return_value = make_mock_get()
    mock_post.return_value = make_mock_post()

    adapter = MediumAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)

    assert result.status == "drafted"
    assert result.draft_url == "https://medium.com/@testuser/test-post-abc123"
    assert result.published_url == ""
    assert result.adapter == "medium-api"


@patch(
    "backlink_publisher.publishing.adapters.medium_api.verify_link_attributes",
    return_value={"verification": "skipped", "reason": "test-mock"},
)
@patch("backlink_publisher.publishing.adapters.medium_api.http_get")
@patch("backlink_publisher.publishing.adapters.medium_api.http_post")
def test_publish_mode_sends_public_status(mock_post, mock_get, _mock_verify):
    mock_get.return_value = make_mock_get()
    pub_resp = {"data": {"id": "post789", "url": "https://medium.com/@testuser/live-post"}}
    mock_post.return_value = make_mock_post(json_data=pub_resp)

    adapter = MediumAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="publish", config=CONFIG_WITH_TOKEN)

    assert result.status == "published"
    post_body = mock_post.call_args[1]["json"]
    assert post_body["publishStatus"] == "public"


@patch("backlink_publisher.publishing.adapters.medium_api.http_get")
def test_401_on_me_raises_auth_expired_error(mock_get):
    """Plan 2026-05-19-001 Unit 6: /me 401 → AuthExpiredError (not
    ExternalServiceError). Existing ``except DependencyError`` callers
    still catch this because AuthExpiredError inherits from it."""
    mock_get.return_value = make_mock_get(status=401)

    adapter = MediumAPIAdapter()
    with pytest.raises(AuthExpiredError) as exc_info:
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)
    assert exc_info.value.channel == "medium"
    assert "Medium /me HTTP 401" in (exc_info.value.reason or "")
    assert isinstance(exc_info.value, DependencyError)


@patch("backlink_publisher.publishing.adapters.retry.time.sleep")
@patch("backlink_publisher.publishing.adapters.medium_api.http_get")
@patch("backlink_publisher.publishing.adapters.medium_api.http_post")
def test_429_raises_rate_limited(mock_post, mock_get, mock_sleep):
    """429 on all retry attempts → ExternalServiceError after retries exhausted."""
    mock_get.return_value = make_mock_get()
    mock_post.return_value = make_mock_post(status=429)

    adapter = MediumAPIAdapter()
    with pytest.raises(ExternalServiceError, match="429"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)


@patch("backlink_publisher.publishing.adapters.retry.time.sleep")
@patch("backlink_publisher.publishing.adapters.medium_api.http_get")
@patch("backlink_publisher.publishing.adapters.medium_api.http_post")
def test_posts_429_retried_and_recovers(mock_post, mock_get, mock_sleep):
    """/posts 429 on first call triggers retry; second call succeeds."""
    mock_get.return_value = make_mock_get()
    mock_post.side_effect = [make_mock_post(status=429), make_mock_post()]

    adapter = MediumAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)
    assert result.status == "drafted"
    mock_sleep.assert_called_once()


@patch("backlink_publisher.publishing.adapters.retry.time.sleep")
@patch("backlink_publisher.publishing.adapters.medium_api.http_post")
@patch("backlink_publisher.publishing.adapters.medium_api.http_get")
def test_posts_503_not_retried(mock_get, mock_post, mock_sleep):
    """/posts 503 is NOT retried (no idempotency guarantee from Medium API)."""
    mock_get.return_value = make_mock_get()
    mock_post.return_value = make_mock_post(status=503)

    adapter = MediumAPIAdapter()
    with pytest.raises(ExternalServiceError, match="503"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)
    mock_sleep.assert_not_called()


@patch("backlink_publisher.publishing.adapters.retry.time.sleep")
@patch("backlink_publisher.publishing.adapters.medium_api.http_get")
@patch("backlink_publisher.publishing.adapters.medium_api.http_post")
def test_me_429_retried_and_recovers(mock_post, mock_get, mock_sleep):
    """/me 429 on first call triggers retry; second call succeeds."""
    mock_get.side_effect = [make_mock_get(status=429), make_mock_get()]
    mock_post.return_value = make_mock_post()

    adapter = MediumAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)
    assert result.status == "drafted"
    mock_sleep.assert_called_once()


@pytest.mark.parametrize("exc_name", ["Timeout", "ConnectionError"])
@patch("backlink_publisher.publishing.adapters.retry.time.sleep")
@patch("backlink_publisher.publishing.adapters.medium_api.http_get")
@patch("backlink_publisher.publishing.adapters.medium_api.http_post")
def test_posts_network_error_not_retried(mock_post, mock_get, mock_sleep, exc_name):
    """/posts is a non-idempotent create — a Timeout/ConnectionError may mean the
    post was already created server-side, so it is NEVER retried (would duplicate).
    The create-POST is attempted exactly once; the error surfaces as
    ExternalServiceError for the resume/dedup layer to adjudicate. Mirrors
    test_adapter_http_form_post.py::test_submit_form_does_not_retry_nonidempotent_post."""
    import requests as req
    mock_get.return_value = make_mock_get()
    # The 2nd response is the duplicate that MUST NOT be sent.
    mock_post.side_effect = [getattr(req, exc_name)("net"), make_mock_post()]

    adapter = MediumAPIAdapter()
    with pytest.raises(ExternalServiceError, match="unreachable"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)
    assert mock_post.call_count == 1  # create POST sent exactly once
    mock_sleep.assert_not_called()


@patch("backlink_publisher.publishing.adapters.retry.time.sleep")
@patch("backlink_publisher.publishing.adapters.medium_api.http_get")
@patch("backlink_publisher.publishing.adapters.medium_api.http_post")
def test_posts_401_not_retried(mock_post, mock_get, mock_sleep):
    """Plan 2026-05-19-001 Unit 6: /posts 401 → AuthExpiredError (not
    ExternalServiceError). Still non-retryable — no sleep."""
    mock_get.return_value = make_mock_get()
    mock_post.return_value = make_mock_post(status=401)

    adapter = MediumAPIAdapter()
    with pytest.raises(AuthExpiredError) as exc_info:
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)
    assert exc_info.value.channel == "medium"
    assert "Medium /posts HTTP 401" in (exc_info.value.reason or "")
    mock_sleep.assert_not_called()


@patch("backlink_publisher.publishing.adapters.retry.time.sleep")
@patch("backlink_publisher.publishing.adapters.medium_api.http_get")
@patch("backlink_publisher.publishing.adapters.medium_api.http_post")
def test_user_id_not_refetched_on_posts_retry(mock_post, mock_get, mock_sleep):
    """user_id from /me is cached — /me called once even on a /posts retry.
    Drives the retry via 429 (a pre-create rate-limit rejection, the only
    retryable create-POST case) rather than a network error."""
    mock_get.return_value = make_mock_get()
    mock_post.side_effect = [make_mock_post(status=429), make_mock_post()]

    adapter = MediumAPIAdapter()
    adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)
    assert mock_get.call_count == 1  # /me called exactly once


@patch("backlink_publisher.publishing.adapters.medium_api.http_get")
@patch("backlink_publisher.publishing.adapters.medium_api.http_post")
def test_tags_truncated_to_5(mock_post, mock_get):
    mock_get.return_value = make_mock_get()
    mock_post.return_value = make_mock_post()

    adapter = MediumAPIAdapter()
    adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)  # payload has 6 tags

    post_body = mock_post.call_args[1]["json"]
    assert len(post_body["tags"]) == 5


@patch("backlink_publisher.publishing.adapters.medium_api.http_get")
@patch("backlink_publisher.publishing.adapters.medium_api.http_post")
def test_canonical_url_omitted_if_empty(mock_post, mock_get):
    mock_get.return_value = make_mock_get()
    mock_post.return_value = make_mock_post()

    payload = {**PAYLOAD, "seo": {"canonical_url": ""}}
    adapter = MediumAPIAdapter()
    adapter.publish(payload, mode="draft", config=CONFIG_WITH_TOKEN)

    post_body = mock_post.call_args[1]["json"]
    assert "canonicalUrl" not in post_body


@patch("backlink_publisher.publishing.adapters.medium_api.http_get")
@patch("backlink_publisher.publishing.adapters.medium_api.http_post")
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


# ---------------------------------------------------------------------------
# Medium pre-flight expiry check (R5, R6)
# All time-sensitive tests mock time.time to a fixed value for determinism.
# ---------------------------------------------------------------------------

_FIXED_NOW = 1_000_000.0  # arbitrary fixed Unix timestamp


def _token_data_with_expiry(offset: float) -> dict:
    return {"access_token": "oauth-tok", "expires_at": _FIXED_NOW + offset}


@patch("backlink_publisher.publishing.adapters.medium_api.time.time", return_value=_FIXED_NOW)
@patch("backlink_publisher.config.load_medium_token")
@patch("backlink_publisher.publishing.adapters.medium_api.http_get")
@patch("backlink_publisher.publishing.adapters.medium_api.http_post")
def test_preflight_no_error_when_expiry_600s_future(mock_post, mock_get, mock_load, _mock_time):
    mock_load.return_value = _token_data_with_expiry(600)
    mock_get.return_value = make_mock_get()
    mock_post.return_value = make_mock_post()

    adapter = MediumAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG_NO_TOKEN)
    assert result.status == "drafted"
    mock_get.assert_called_once()  # publish proceeded to API call


@patch("backlink_publisher.publishing.adapters.medium_api.time.time", return_value=_FIXED_NOW)
@patch("backlink_publisher.config.load_medium_token")
def test_preflight_raises_when_expiry_200s_future(mock_load, _mock_time):
    mock_load.return_value = _token_data_with_expiry(200)

    adapter = MediumAPIAdapter()
    with pytest.raises(ExternalServiceError, match="re-authorize"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG_NO_TOKEN)


@patch("backlink_publisher.publishing.adapters.medium_api.time.time", return_value=_FIXED_NOW)
@patch("backlink_publisher.config.load_medium_token")
def test_preflight_raises_at_exactly_300s_boundary(mock_load, _mock_time):
    # now >= (now + 300) - 300 → True (inclusive boundary)
    mock_load.return_value = _token_data_with_expiry(300)

    adapter = MediumAPIAdapter()
    with pytest.raises(ExternalServiceError, match="re-authorize"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG_NO_TOKEN)


@patch("backlink_publisher.publishing.adapters.medium_api.time.time", return_value=_FIXED_NOW)
@patch("backlink_publisher.config.load_medium_token")
@patch("backlink_publisher.publishing.adapters.medium_api.http_get")
@patch("backlink_publisher.publishing.adapters.medium_api.http_post")
def test_preflight_no_error_at_301s_boundary(mock_post, mock_get, mock_load, _mock_time):
    # now < (now + 301) - 300 → False (just outside window)
    mock_load.return_value = _token_data_with_expiry(301)
    mock_get.return_value = make_mock_get()
    mock_post.return_value = make_mock_post()

    adapter = MediumAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG_NO_TOKEN)
    assert result.status == "drafted"


@patch("backlink_publisher.publishing.adapters.medium_api.time.time", return_value=_FIXED_NOW)
@patch("backlink_publisher.config.load_medium_token")
def test_preflight_raises_when_already_expired(mock_load, _mock_time):
    mock_load.return_value = _token_data_with_expiry(-30)

    adapter = MediumAPIAdapter()
    with pytest.raises(ExternalServiceError, match="re-authorize"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG_NO_TOKEN)


@patch("backlink_publisher.publishing.adapters.medium_api.time.time", return_value=_FIXED_NOW)
@patch("backlink_publisher.config.load_medium_token")
@patch("backlink_publisher.publishing.adapters.medium_api.http_get")
@patch("backlink_publisher.publishing.adapters.medium_api.http_post")
def test_preflight_skipped_for_zero_expires_at_sentinel(mock_post, mock_get, mock_load, _mock_time):
    # expires_at = 0 treated as absent (fail-open)
    mock_load.return_value = {"access_token": "oauth-tok", "expires_at": 0}
    mock_get.return_value = make_mock_get()
    mock_post.return_value = make_mock_post()

    adapter = MediumAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG_NO_TOKEN)
    assert result.status == "drafted"


@patch("backlink_publisher.config.load_medium_token")
@patch("backlink_publisher.publishing.adapters.medium_api.http_get")
@patch("backlink_publisher.publishing.adapters.medium_api.http_post")
def test_preflight_skipped_when_expires_at_absent(mock_post, mock_get, mock_load):
    # Token without expires_at → pre-flight skipped entirely
    mock_load.return_value = {"access_token": "oauth-tok"}
    mock_get.return_value = make_mock_get()
    mock_post.return_value = make_mock_post()

    adapter = MediumAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG_NO_TOKEN)
    assert result.status == "drafted"


@patch("backlink_publisher.config.load_medium_token", return_value=None)
@patch("backlink_publisher.publishing.adapters.medium_api.http_get")
@patch("backlink_publisher.publishing.adapters.medium_api.http_post")
def test_preflight_skipped_for_integration_token(mock_post, mock_get, _mock_load):
    # medium_token_data is None → integration token path, no pre-flight check
    mock_get.return_value = make_mock_get()
    mock_post.return_value = make_mock_post()

    adapter = MediumAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG_WITH_TOKEN)
    assert result.status == "drafted"


@patch("backlink_publisher.publishing.adapters.medium_api.time.time", return_value=_FIXED_NOW)
@patch("backlink_publisher.config.load_medium_token")
def test_preflight_raises_external_service_error_not_dependency_error(mock_load, _mock_time):
    # Near-expiry must raise ExternalServiceError (not DependencyError) so the
    # dispatcher does NOT silently fall through to browser adapters.
    mock_load.return_value = _token_data_with_expiry(100)

    adapter = MediumAPIAdapter()
    try:
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG_NO_TOKEN)
        pytest.fail("Expected ExternalServiceError")
    except ExternalServiceError:
        pass  # correct
    except Exception as exc:
        from backlink_publisher._util.errors import DependencyError as DE
        assert not isinstance(exc, DE), f"Near-expiry raised DependencyError instead of ExternalServiceError: {exc}"
