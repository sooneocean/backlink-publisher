"""txt.fyi adapter — anonymous form-POST publishing (Plan 2026-05-25-001 Unit 7).

txt.fyi is a minimalist anonymous pastebin/publishing platform by Rob Beschizza.
No accounts, no JavaScript, no cookies — just a single form POST at
``https://txt.fyi/`` with hidden CSRF fields (``nonce``, ``form_time``).
This adapter composes the Unit 4 ``http_form_post`` helpers (``fetch_form`` →
``extract_hidden_fields`` → ``submit_form``) to publish content and captures
the permalink URL from the final redirect target.

Anti-spam dwell-time gate: ``edit.php`` rejects POSTs that arrive too soon after
the form was served (keyed off the hidden ``form_time``). A sub-second GET→POST
is treated as a bot and silently tarpitted (200 "Thank you" page, no permalink),
so the adapter waits ``_SUBMIT_DELAY_SECONDS`` before submitting. See
:data:`_SUBMIT_DELAY_ENV`.

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

import os
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

# txt.fyi anti-spam dwell-time gate. The form embeds a server-issued
# ``form_time`` timestamp; ``edit.php`` rejects POSTs that arrive too soon after
# it (a "no human fills a form this fast" check). A naive sub-second GET→POST is
# treated as a bot: edit.php returns a **200 "Thank you for your submission!"**
# tarpit page with NO redirect and NO permalink — the post is silently dropped,
# never published — instead of the 302→permalink a real browser receives.
# Empirically (probed 2026-05-29) the gate clears by ~3s; we wait a margin above
# that. Overridable via env for tuning, and set to 0 in tests for zero wait.
_SUBMIT_DELAY_ENV = "BACKLINK_TXTFYI_SUBMIT_DELAY_SECONDS"
_DEFAULT_SUBMIT_DELAY_SECONDS = 4.0
# Lowercased body marker of the tarpit page (see above) — distinguishes an
# anti-spam rejection from a generic no-redirect failure.
_TARPIT_MARKER = "thank you for your submission"


def _submit_delay_seconds() -> float:
    """Resolve the pre-submit dwell time, honoring ``_SUBMIT_DELAY_ENV``.

    Falls back to :data:`_DEFAULT_SUBMIT_DELAY_SECONDS` when the env var is
    unset or unparseable; clamps negatives to 0.
    """
    raw = os.environ.get(_SUBMIT_DELAY_ENV)
    if raw is None:
        return _DEFAULT_SUBMIT_DELAY_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_SUBMIT_DELAY_SECONDS


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
        # Clear txt.fyi's dwell-time gate before submitting (see
        # _SUBMIT_DELAY_ENV). Without this wait the POST is flagged as a bot and
        # silently dropped to the tarpit page, never publishing.
        delay = _submit_delay_seconds()
        if delay > 0:
            time.sleep(delay)
        submit_resp = submit_form(_TXTFYI_SUBMIT, post_data)

        # 4. Capture the published URL from the final redirect target.
        published_url = (submit_resp.url or "").strip()
        if not published_url or published_url == _TXTFYI_SUBMIT:
            body_text = (getattr(submit_resp, "text", "") or "").lower()
            if _TARPIT_MARKER in body_text:
                # Anti-spam rejection, not a transport hiccup: edit.php served
                # the "Thank you" tarpit. Surface the cause + the knob to turn.
                raise ExternalServiceError(
                    "txt.fyi rejected the submission as automated (anti-spam "
                    "dwell-time gate); the post was NOT published. Raise "
                    f"{_SUBMIT_DELAY_ENV} above the current {delay:g}s and retry."
                )
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
