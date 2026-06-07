"""Notes.io adapter — anonymous form-POST publishing (Plan 2026-06-02-002 Unit 2).

Notes.io is a minimalist anonymous pastebin — no accounts, no JavaScript
required for reading, just a textarea + JS AJAX POST to ``short.php``.
This adapter composes the ``http_form_post`` helpers (``fetch_form`` →
``submit_form``) to publish content and captures the published note URL
from the response HTML.

Unlike txt.fyi there is no CSRF nonce to extract and no dwell-time gate
— the POST endpoint accepts raw ``txt=<content>`` without hidden fields.

Content rendering: notes.io serves user content as literal plain text in
a ``<div class="notesTextArea">``. Markdown syntax (``[text](url)``) and
bare URLs are displayed verbatim — no server-side Markdown rendering, no
HTML anchor tags in user content. Backlinks in the note body appear only
as raw text strings (no PageRank transfer). dofollow="uncertain" per plan
2026-06-02-002; the 12/0 dofollow ratio in the discovery run counted all
page-level links (navigation, footer), not user-content links.
"""

from __future__ import annotations

import time
from typing import Any
from html.parser import HTMLParser

from backlink_publisher._util.errors import ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.config import Config
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .http_form_post import (
    fetch_form,
    submit_form,
)

_NOTESIO_FORM = "https://notes.io/"
_NOTESIO_SUBMIT = "https://notes.io/short.php"
_ADAPTER = "notesio-form-post"
_PLATFORM = "notesio"


class _UrlExtractor(HTMLParser):
    """Minimal HTML parser to extract the first ``href`` from a
    ``div.shortURL a`` element — notes.io's POST response format.
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_short_url_div = False
        self._in_anchor = False
        self.found_url: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "div" and attrs_dict.get("class") == "shortURL":
            self._in_short_url_div = True
        if self._in_short_url_div and tag == "a":
            self._in_anchor = True
            href = attrs_dict.get("href", "")
            if href:
                self.found_url = href

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._in_short_url_div:
            self._in_short_url_div = False
        if tag == "a":
            self._in_anchor = False


def _extract_note_url(html_text: str) -> str:
    """Parse the ``div.shortURL a[href]`` from notes.io's POST response.

    Returns the first matching URL, or raises ``ExternalServiceError``.
    """
    parser = _UrlExtractor()
    parser.feed(html_text)
    url = parser.found_url
    if not url:
        raise ExternalServiceError(
            "notes.io response did not contain a published note URL"
        )
    return url.strip()


class NotesioFormPostAdapter(Publisher):
    """Anonymous form-POST publisher for notes.io.

    No config, credentials, or browser needed — pure HTTP form submission.
    """

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info("notesio_publish_start", id=article_id)

        # 1. Compose the body from markdown content.
        content_md = payload.get("content_markdown") or payload.get("content_md") or ""
        if not content_md.strip():
            raise ExternalServiceError("notes.io payload has no content_markdown")

        # 2. Fetch the form page to establish a session cookie (PHP session).
        fetch_form(_NOTESIO_FORM)

        # 3. Submit the note content. Notes.io's short.php accepts
        #    a single ``txt`` field — no CSRF tokens, no hidden fields.
        post_data: dict[str, str] = {
            "txt": content_md,
        }
        submit_resp = submit_form(_NOTESIO_SUBMIT, post_data)

        # 4. Parse the published URL from the response HTML.
        published_url = _extract_note_url(submit_resp.text)

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "notesio_publish_done",
            id=article_id,
            url=published_url,
            elapsed_ms=elapsed_ms,
        )

        if mode == "draft":
            return AdapterResult(
                status="drafted",
                adapter=_ADAPTER,
                platform=_PLATFORM,
                draft_url=published_url,
            )
        return AdapterResult(
            status="published",
            adapter=_ADAPTER,
            platform=_PLATFORM,
            published_url=published_url,
        )
