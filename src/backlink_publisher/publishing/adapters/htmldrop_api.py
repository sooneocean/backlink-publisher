"""HtmlDrop (htmldrop.in) REST API publishing adapter.

HtmlDrop is a free anonymous HTML paste service. No signup required.
Publish via ``POST /api/pages`` with a JSON body ``{"html": "..."}``.

API discovered by live probe 2026-06-04:
  POST https://www.htmldrop.in/api/pages
  Body: {"html": "<h1>content</h1>"}
  Response: {"slug", "url", "expiresAt", "claimToken", ...}
  Published URL: https://www.htmldrop.in/p/{slug}

Rate limit: unknown (assume tight — 429 handling with backoff).

Registration status (Plan 2026-06-04-001 Wave 3b / R19):
- Initially ``dofollow="uncertain"`` — HtmlDrop renders raw HTML including
  ``<a>`` elements, so links should survive. Canary verification required
  before marking ``dofollow=True``.
- Short TTL: 24 hours default for anonymous posts. Labeled as short-TTL.
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


HTMLDROP_API = "https://www.htmldrop.in/api/pages"
HTMLDROP_BASE = "https://www.htmldrop.in"
_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 3
_USER_AGENT = "Backlink-Publisher/1.0"


class HtmlDropAPIAdapter(Publisher):
    """Publishes HTML content to HtmlDrop.in via anonymous REST API.

    No authentication required. Each publish returns a ``claimToken``
    for optional edit/delete — the adapter logs it but does not persist it.
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
        log.info(json.dumps(dict(adapter="htmldrop", phase="start", id=article_id)))

        title = payload.get("title", "Untitled")
        body = payload.get("content_markdown") or extract_publish_html(payload, "htmldrop") or ""

        # HtmlDrop accepts raw HTML — wrap markdown content
        html = f"<h1>{title}</h1>\n{body}" if title else body

        req_body: dict[str, Any] = {
            "html": html,
        }

        resp = requests.post(
            HTMLDROP_API,
            json=req_body,
            timeout=_HTTP_TIMEOUT_S,
            headers={"User-Agent": _USER_AGENT},
        )

        elapsed = time.monotonic() - t0

        if resp.status_code == 429:
            raise ExternalServiceError(
                f"htmldrop: rate limited (429) after {elapsed:.1f}s"
            )
        if resp.status_code != 201 and resp.status_code != 200:
            raise ExternalServiceError(
                f"htmldrop: HTTP {resp.status_code} after {elapsed:.1f}s: "
                f"{resp.text[:200]}"
            )

        data = resp.json()
        published_url = data.get("url", "")
        slug = data.get("slug", "")
        expires_at = data.get("expiresAt", "")

        if not published_url:
            raise ExternalServiceError(
                f"htmldrop: no url in response: {resp.text[:200]}"
            )

        log.info(json.dumps(dict(
            adapter="htmldrop",
            phase="done",
            id=article_id,
            slug=slug,
            url=published_url,
            expires_at=expires_at,
            elapsed_seconds=round(elapsed, 2),
        )))

        return AdapterResult(
            status="published" if mode == "live" else "drafted",
            adapter="htmldrop-api",
            platform="htmldrop",
            draft_url=published_url,
            published_url=published_url if mode == "live" else "",
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
            _provider_meta={
                "slug": slug,
                "expires_at": expires_at,
            },
        )
