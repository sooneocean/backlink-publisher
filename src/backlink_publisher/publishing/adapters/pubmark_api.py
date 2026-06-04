"""Pubmark (pubmark.site) REST API publishing adapter.

Pubmark is a free instant Markdown publishing platform. No account required
to publish (zero-auth). Write markdown, publish instantly, get a shareable URL.

API flow (Next.js server actions, discovered 2026-06-04):
  1. POST  /api/documents           -> {id, secretId, slug}  (create draft)
  2. PUT   /api/documents/{secret}  -> {id, slug, ...}       (set content)
  3. POST  /api/documents/{secret}/publish  -> {isPublished: true, slug}

Published URL: https://pubmark.site/p/{slug}

Dofollow status (confirmed 2026-06-04 browser probe):
  - Outbound <a> elements carry no rel="nofollow" — rel=null confirmed via
    Playwright browser evaluation on a live published post.
  - Markdown links like [text](url) render as clean <a href="..."> with
    no rel decoration — dofollow by construction.

Free plan limit: 5 published documents (per IP/session). The adapter does not
track consumption — if the publish action returns a 403/402 or the "document"
endpoint errors, the failure is surfaced as a transient ExternalServiceError.

Registration status (this wave):
  - Initially ``dofollow="uncertain"`` — the 2026-06-04 browser probe confirmed
    dofollow for a single post; a pipeline canary must confirm stability across
    multiple publishes before marking ``dofollow=True``.
  - Referral value: low (modest DA, anonymous publishing platform).
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from backlink_publisher._util.errors import ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.config import Config
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult

PUBMARK_BASE = "https://pubmark.site"
PUBMARK_API_CREATE = f"{PUBMARK_BASE}/api/documents"
PUBMARK_API_PUBLISH = f"{PUBMARK_BASE}/api/documents/{{secret}}/publish"
_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 3
_USER_AGENT = "Backlink-Publisher/1.0"


class PubmarkAPIAdapter(Publisher):
    """Publishes Markdown content to Pubmark via anonymous REST API.

    No authentication required. The adapter uses a 3-step API flow:
    create draft -> set content -> publish. The published URL follows
    the pattern ``https://pubmark.site/p/{slug}``.
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        return True

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="pubmark", phase="start", id=article_id)))

        title = payload.get("title", "Untitled")
        body = (
            payload.get("content_markdown")
            or extract_publish_html(payload, "pubmark")
            or ""
        )

        headers = {
            "User-Agent": _USER_AGENT,
            "Content-Type": "application/json",
            "Origin": PUBMARK_BASE,
            "Referer": f"{PUBMARK_BASE}/editor",
        }

        # Step 1: Create a draft document
        try:
            resp = requests.post(
                PUBMARK_API_CREATE,
                json={"title": title},
                headers=headers,
                timeout=_HTTP_TIMEOUT_S,
            )
        except requests.RequestException as exc:
            raise ExternalServiceError(
                f"Pubmark create draft failed: {exc}"
            ) from exc

        if resp.status_code != 200:
            raise ExternalServiceError(
                f"Pubmark create draft returned {resp.status_code}: "
                f"{resp.text[:200]}"
            )

        data = resp.json()
        doc_id: str | None = data.get("id")
        secret_id: str | None = data.get("secretId")
        slug: str | None = data.get("slug")
        if not doc_id or not secret_id:
            raise ExternalServiceError(
                f"Pubmark create draft response missing id/secretId: {data}"
            )

        log.info(
            json.dumps(
                dict(
                    adapter="pubmark",
                    phase="draft_created",
                    id=article_id,
                    doc_id=doc_id,
                    slug=slug,
                )
            )
        )

        # Step 2: Set document content
        update_headers = {
            **headers,
            "Referer": f"{PUBMARK_BASE}/edit/{secret_id}",
        }
        try:
            resp2 = requests.put(
                f"{PUBMARK_BASE}/api/documents/{secret_id}",
                json={
                    "title": title,
                    "content": body,
                    "theme": "paper",
                    "colorPreset": "copper",
                },
                headers=update_headers,
                timeout=_HTTP_TIMEOUT_S,
            )
        except requests.RequestException as exc:
            raise ExternalServiceError(
                f"Pubmark update document failed: {exc}"
            ) from exc

        if resp2.status_code != 200:
            raise ExternalServiceError(
                f"Pubmark update document returned {resp2.status_code}: "
                f"{resp2.text[:200]}"
            )

        log.info(
            json.dumps(
                dict(
                    adapter="pubmark",
                    phase="content_set",
                    id=article_id,
                    doc_id=doc_id,
                )
            )
        )

        # Step 3: Publish the document
        try:
            resp3 = requests.post(
                PUBMARK_API_PUBLISH.format(secret=secret_id),
                json={},
                headers=update_headers,
                timeout=_HTTP_TIMEOUT_S,
            )
        except requests.RequestException as exc:
            raise ExternalServiceError(
                f"Pubmark publish failed: {exc}"
            ) from exc

        if resp3.status_code != 200:
            raise ExternalServiceError(
                f"Pubmark publish returned {resp3.status_code}: "
                f"{resp3.text[:200]}"
            )

        pub_data = resp3.json()
        final_slug: str | None = pub_data.get("slug") or slug
        published_url = f"{PUBMARK_BASE}/p/{final_slug}" if final_slug else None
        if not published_url:
            raise ExternalServiceError(
                f"Pubmark publish response missing slug: {pub_data}"
            )

        elapsed = time.monotonic() - t0
        log.info(
            json.dumps(
                dict(
                    adapter="pubmark",
                    phase="published",
                    id=article_id,
                    url=published_url,
                    elapsed_s=round(elapsed, 2),
                )
            )
        )

        return AdapterResult(
            status="published",
            published_url=published_url,
            adapter="pubmark",
            platform="pubmark",
            post_publish_delay_seconds=self.post_publish_delay_seconds,
        )
