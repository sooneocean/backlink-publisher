"""Unit 2: notes.io form-POST adapter (Plan 2026-06-02-002).

Covers publish happy/draft paths, missing content, response parse failure,
and network error. All HTTP is mocked — no real network.
"""

from __future__ import annotations

from unittest import mock

import pytest

from backlink_publisher._util.errors import ExternalServiceError
from backlink_publisher.config import Config
from backlink_publisher.publishing.adapters.notesio_api import (
    NotesioFormPostAdapter,
)

_ADAPTER = "backlink_publisher.publishing.adapters.notesio_api"

_PAYLOAD = {
    "id": "notes-1",
    "title": "Hello notes.io",
    "content_markdown": "# Hi\n\nA [link](https://example.com) here.\n",
    "target_url": "https://example.com",
    "main_domain": "https://example.com/",
}

# Response HTML returned by short.php on success: a div.shortURL containing
# an anchor with the published note URL.
_SUCCESS_HTML = (
    '<div class="shortURL">'
    '<a href="https://notes.io/abcd1234" target="_blank">'
    "https://notes.io/abcd1234</a>"
    "</div>"
)

# Response HTML that is missing the expected URL structure (e.g. empty).
_BROKEN_HTML = "<html><body>Oops</body></html>"


def _mock_response(*, status=200, text="", url="https://notes.io/"):
    """Build a requests.Response-like mock."""
    resp = mock.MagicMock()
    resp.status_code = status
    resp.text = text
    resp.url = url
    resp.headers = {"server": "cloudflare"}
    resp.__iter__ = mock.MagicMock(return_value=iter([]))
    return resp


# ── happy paths ────────────────────────────────────────────────────────────


def test_publish_happy_returns_published_url():
    form_resp = _mock_response(text="<html><body><textarea></textarea></body></html>")
    submit_resp = _mock_response(text=_SUCCESS_HTML)

    with mock.patch(
        f"{_ADAPTER}.fetch_form", return_value=form_resp
    ) as mock_fetch, mock.patch(
        f"{_ADAPTER}.submit_form", return_value=submit_resp
    ) as mock_submit:
        adapter = NotesioFormPostAdapter()
        result = adapter.publish(_PAYLOAD, mode="publish", config=Config())

    assert result.status == "published"
    assert result.published_url == "https://notes.io/abcd1234"
    assert result.adapter == "notesio-form-post"
    assert result.platform == "notesio"
    mock_fetch.assert_called_once_with("https://notes.io/")
    mock_submit.assert_called_once()
    # Verify the txt field was sent with the content.
    posted_data = mock_submit.call_args[0][1]
    assert "txt" in posted_data
    assert _PAYLOAD["content_markdown"] in posted_data["txt"]


def test_publish_draft_returns_draft_url():
    form_resp = _mock_response(text="<html><body><textarea></textarea></body></html>")
    submit_resp = _mock_response(text=_SUCCESS_HTML)

    with mock.patch(
        f"{_ADAPTER}.fetch_form", return_value=form_resp
    ), mock.patch(
        f"{_ADAPTER}.submit_form", return_value=submit_resp
    ):
        adapter = NotesioFormPostAdapter()
        result = adapter.publish(_PAYLOAD, mode="draft", config=Config())

    assert result.status == "drafted"
    assert result.draft_url == "https://notes.io/abcd1234"
    assert result.adapter == "notesio-form-post"


# ── error paths ────────────────────────────────────────────────────────────


def test_publish_missing_content_raises_error():
    empty_payload = {**_PAYLOAD, "content_markdown": ""}

    adapter = NotesioFormPostAdapter()
    with pytest.raises(ExternalServiceError, match="no content_markdown"):
        adapter.publish(empty_payload, mode="publish", config=Config())


def test_publish_response_missing_url_raises_error():
    form_resp = _mock_response(text="<html><body><textarea></textarea></body></html>")
    submit_resp = _mock_response(text=_BROKEN_HTML)

    with mock.patch(
        f"{_ADAPTER}.fetch_form", return_value=form_resp
    ), mock.patch(
        f"{_ADAPTER}.submit_form", return_value=submit_resp
    ):
        adapter = NotesioFormPostAdapter()
        with pytest.raises(
            ExternalServiceError, match="did not contain a published note URL"
        ):
            adapter.publish(_PAYLOAD, mode="publish", config=Config())


def test_publish_network_error_from_submit():
    form_resp = _mock_response(text="<html><body><textarea></textarea></body></html>")

    with mock.patch(
        f"{_ADAPTER}.fetch_form", return_value=form_resp
    ), mock.patch(
        f"{_ADAPTER}.submit_form",
        side_effect=ExternalServiceError("notes.io submit failed"),
    ):
        adapter = NotesioFormPostAdapter()
        with pytest.raises(ExternalServiceError, match="submit failed"):
            adapter.publish(_PAYLOAD, mode="publish", config=Config())


# ── setup verification ─────────────────────────────────────────────────────


def test_verify_adapter_setup():
    """Offline setup check — no network required; notes.io has no credential
    dependency so this should pass with any config."""
    adapter = NotesioFormPostAdapter()
    # ``available()`` always returns True for credential-less form-post
    # adapters (same as txtfyi).
    assert adapter.available(Config()) is True
