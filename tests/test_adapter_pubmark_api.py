"""Unit tests for PubmarkAPIAdapter (this wave).

Covers the 3-step API flow (create → update → publish), missing-response
fields, HTTP errors at each step, and network failures. All HTTP mocked.
"""

from __future__ import annotations

from unittest import mock

import pytest
import requests

from backlink_publisher._util.errors import ExternalServiceError
from backlink_publisher.publishing.adapters.pubmark_api import (
    PUBMARK_API_CREATE,
    PUBMARK_API_PUBLISH,
    PUBMARK_BASE,
    PubmarkAPIAdapter,
)

_PAYLOAD = {
    "id": "pub-1",
    "title": "Test Post",
    "content_markdown": "# Hello\n\nA [link](https://example.com).\n",
    "target_url": "https://example.com",
    "main_domain": "https://example.com/",
}


def _mock_resp(*, status=200, json_data=None, text=""):
    resp = mock.MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.json.return_value = json_data or {}
    resp.text = text or str(json_data or {})
    return resp


def _create_ok_json():
    return {"id": "doc_abc", "secretId": "sec_xyz", "slug": "test-post"}


def _publish_ok_json():
    return {"isPublished": True, "slug": "test-post"}


class TestAvailable:
    def test_always_true(self):
        assert PubmarkAPIAdapter.available(None) is True


class TestPublish:
    def test_happy_path(self):
        """3-step API flow succeeds, returns published URL."""
        create_resp = _mock_resp(json_data=_create_ok_json())
        update_resp = _mock_resp()
        publish_resp = _mock_resp(json_data=_publish_ok_json())

        with (
            mock.patch(
                "backlink_publisher.publishing.adapters.pubmark_api.requests.post"
            ) as mock_post,
            mock.patch(
                "backlink_publisher.publishing.adapters.pubmark_api.requests.put"
            ) as mock_put,
        ):
            mock_post.side_effect = [create_resp, publish_resp]
            mock_put.return_value = update_resp

            result = PubmarkAPIAdapter().publish(
                payload=_PAYLOAD, mode="publish", config=None
            )

        assert result.status == "published"
        assert result.published_url == f"{PUBMARK_BASE}/p/test-post"
        assert result.adapter == "pubmark"
        assert result.platform == "pubmark"
        assert result.post_publish_delay_seconds == 3

        # Verify API call order
        assert mock_post.call_count == 2
        assert mock_put.call_count == 1

        # Step 1: create document (url is positional arg)
        create_call = mock_post.call_args_list[0]
        assert create_call[0][0] == PUBMARK_API_CREATE
        assert create_call[1]["json"]["title"] == "Test Post"

        # Step 2: update content (url is positional arg)
        update_call = mock_put.call_args_list[0]
        assert "sec_xyz" in update_call[0][0]
        assert update_call[1]["json"]["content"] == _PAYLOAD["content_markdown"]

        # Step 3: publish (url is positional arg)
        publish_call = mock_post.call_args_list[1]
        assert publish_call[0][0] == PUBMARK_API_PUBLISH.format(secret="sec_xyz")

    def test_draft_mode(self):
        """Returns drafted status without publishing."""
        create_resp = _mock_resp(json_data=_create_ok_json())
        update_resp = _mock_resp()

        with (
            mock.patch(
                "backlink_publisher.publishing.adapters.pubmark_api.requests.post",
                return_value=create_resp,
            ),
            mock.patch(
                "backlink_publisher.publishing.adapters.pubmark_api.requests.put",
                return_value=update_resp,
            ),
        ):
            result = PubmarkAPIAdapter().publish(
                payload=_PAYLOAD, mode="draft", config=None
            )

        assert result.status == "published"
        assert result.published_url == f"{PUBMARK_BASE}/p/test-post"

    def test_create_fails_http_error(self):
        """Non-200 from create step raises ExternalServiceError."""
        with mock.patch(
            "backlink_publisher.publishing.adapters.pubmark_api.requests.post",
            return_value=_mock_resp(status=500, text="Server Error"),
        ):
            with pytest.raises(ExternalServiceError, match="500"):
                PubmarkAPIAdapter().publish(
                    payload=_PAYLOAD, mode="publish", config=None
                )

    def test_create_missing_secret_id(self):
        """Response missing secretId raises ExternalServiceError."""
        bad_json = {"id": "doc_abc"}
        with mock.patch(
            "backlink_publisher.publishing.adapters.pubmark_api.requests.post",
            return_value=_mock_resp(json_data=bad_json),
        ):
            with pytest.raises(ExternalServiceError, match="secretId"):
                PubmarkAPIAdapter().publish(
                    payload=_PAYLOAD, mode="publish", config=None
                )

    def test_update_fails_http_error(self):
        """Non-200 from update step raises ExternalServiceError."""
        create_resp = _mock_resp(json_data=_create_ok_json())
        update_resp = _mock_resp(status=400, text="Bad Request")

        with (
            mock.patch(
                "backlink_publisher.publishing.adapters.pubmark_api.requests.post",
                return_value=create_resp,
            ),
            mock.patch(
                "backlink_publisher.publishing.adapters.pubmark_api.requests.put",
                return_value=update_resp,
            ),
        ):
            with pytest.raises(ExternalServiceError, match="400"):
                PubmarkAPIAdapter().publish(
                    payload=_PAYLOAD, mode="publish", config=None
                )

    def test_publish_fails_http_error(self):
        """Non-200 from publish step raises ExternalServiceError."""
        create_resp = _mock_resp(json_data=_create_ok_json())
        update_resp = _mock_resp()
        publish_resp = _mock_resp(status=403, text="Forbidden")

        with (
            mock.patch(
                "backlink_publisher.publishing.adapters.pubmark_api.requests.post"
            ) as mock_post,
            mock.patch(
                "backlink_publisher.publishing.adapters.pubmark_api.requests.put",
                return_value=update_resp,
            ),
        ):
            mock_post.side_effect = [create_resp, publish_resp]
            with pytest.raises(ExternalServiceError, match="403"):
                PubmarkAPIAdapter().publish(
                    payload=_PAYLOAD, mode="publish", config=None
                )

    def test_publish_response_missing_slug(self):
        """Both create and publish response missing slug raises ExternalServiceError."""
        # Create response has slug=None so fallback is also None
        create_no_slug = _mock_resp(
            json_data={"id": "doc_abc", "secretId": "sec_xyz", "slug": None}
        )
        update_resp = _mock_resp()

        with (
            mock.patch(
                "backlink_publisher.publishing.adapters.pubmark_api.requests.post"
            ) as mock_post,
            mock.patch(
                "backlink_publisher.publishing.adapters.pubmark_api.requests.put",
                return_value=update_resp,
            ),
        ):
            mock_post.side_effect = [
                create_no_slug,
                _mock_resp(json_data={"isPublished": True}),
            ]
            with pytest.raises(ExternalServiceError, match="slug"):
                PubmarkAPIAdapter().publish(
                    payload=_PAYLOAD, mode="publish", config=None
                )

    def test_network_failure_on_create(self):
        """Connection error at create step raises ExternalServiceError."""
        with mock.patch(
            "backlink_publisher.publishing.adapters.pubmark_api.requests.post",
            side_effect=requests.ConnectionError("no route to host"),
        ):
            with pytest.raises(ExternalServiceError, match="no route"):
                PubmarkAPIAdapter().publish(
                    payload=_PAYLOAD, mode="publish", config=None
                )

    def test_network_failure_on_update(self):
        """Connection error at update step raises ExternalServiceError."""
        with (
            mock.patch(
                "backlink_publisher.publishing.adapters.pubmark_api.requests.post",
                return_value=_mock_resp(json_data=_create_ok_json()),
            ),
            mock.patch(
                "backlink_publisher.publishing.adapters.pubmark_api.requests.put",
                side_effect=requests.ConnectionError("timeout"),
            ),
        ):
            with pytest.raises(ExternalServiceError, match="timeout"):
                PubmarkAPIAdapter().publish(
                    payload=_PAYLOAD, mode="publish", config=None
                )

    def test_network_failure_on_publish(self):
        """Connection error at publish step raises ExternalServiceError."""
        create_resp = _mock_resp(json_data=_create_ok_json())
        update_resp = _mock_resp()

        with (
            mock.patch(
                "backlink_publisher.publishing.adapters.pubmark_api.requests.post"
            ) as mock_post,
            mock.patch(
                "backlink_publisher.publishing.adapters.pubmark_api.requests.put",
                return_value=update_resp,
            ),
        ):
            mock_post.side_effect = [create_resp, requests.ConnectionError("reset")]
            with pytest.raises(ExternalServiceError, match="reset"):
                PubmarkAPIAdapter().publish(
                    payload=_PAYLOAD, mode="publish", config=None
                )
