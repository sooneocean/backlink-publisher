"""txt.fyi adapter — anonymous form-POST publishing (Plan 2026-05-25-001 Unit 7).

txt.fyi is a minimalist anonymous pastebin/publishing platform by Rob Beschizza.
No accounts, no JavaScript, no cookies — just a single form POST at
``https://txt.fyi/`` with hidden CSRF fields (``nonce``, ``form_time``).
This adapter composes the Unit 4 ``http_form_post`` helpers (``fetch_form`` →
``extract_hidden_fields`` → ``submit_form``) to publish content and captures
the permalink URL from the final redirect target.

SEO note (Phase 0 find): txt.fyi serves raw static HTML pages with no dynamic
link processing, so outbound ``<a>`` elements carry no ``rel="nofollow"``
decoration server-side.  The ``dofollow="uncertain"`` registration below is
the R4 canary convention — the R4 two-phase loop will read
``verify_link_attributes`` on the live page and amend this entry to
``dofollow=True`` once confirmed.

txt.fyi supports basic Markdown: headers, bold, italic, inline code,
blockquotes, and hyperlinks of the form ``link``.
"""

from __future__ import annotations

import time
from typing import Any

from backlink_publisher._util.errors import ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.config import Config
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .http_form_post import (
    attach_link_verification,
    extract_hidden_fields,
    fetch_form,
    submit_form,
)
from .link_attr_verifier import required_link_urls

_TXTFYI_FORM = "https://txt.fyi/"
_TXTFYI_SUBMIT = "https://txt.fyi/edit.php"
_ADAPTER = "txtfyi-form-post"
_PLATFORM = "txtfyi"

# Required hidden tokens on the form page (CSRF protection).
_HIDDEN_FIELDS = ("nonce", "form_time")


class TxtfyiFormPostAdapter(Publisher):
    """Anonymous form-POST publisher for txt.fyi.

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
        title = (payload.get("title") or "").strip()
        log.info("txtfyi_publish_start", id=article_id, title=title)

        # 1. Compose the body from markdown content.
        content_md = payload.get("content_markdown") or payload.get("content_md") or ""
        if not content_md.strip():
            raise ExternalServiceError("txt.fyi payload has no content_markdown")
        # txt.fyi has no dedicated title field — prepend as a heading.
        body = f"# {title}\n\n{content_md}" if title else content_md

        # 2. Fetch the form page and extract CSRF tokens.
        form_resp = fetch_form(_TXTFYI_FORM)
        hidden = extract_hidden_fields(form_resp.text, _HIDDEN_FIELDS)
        missing = [f for f in _HIDDEN_FIELDS if f not in hidden]
        if missing:
            raise ExternalServiceError(
                f"txt.fyi form missing hidden fields: {', '.join(missing)}"
            )

        # 3. Submit the form.
        post_data: dict[str, str] = {
            "txt": body,
            "url": "",  # anti-spam / unused; content carries the backlink
            "go": "PUBLISH",
            **hidden,
        }
        submit_resp = submit_form(_TXTFYI_SUBMIT, post_data)

        # 4. Capture the published URL from the final redirect target.
        published_url = (submit_resp.url or "").strip()
        if not published_url or published_url == _TXTFYI_SUBMIT:
            raise ExternalServiceError(
                "txt.fyi did not redirect to a published URL after submit"
            )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "txtfyi_publish_done",
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
        meta = attach_link_verification(published_url, target_urls=required_link_urls(payload))
        return AdapterResult(
            status="published",
            adapter=_ADAPTER,
            platform=_PLATFORM,
            published_url=published_url,
            _provider_meta=meta,
        )
