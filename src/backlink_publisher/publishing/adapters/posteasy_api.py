"""PostEasy (post-easy.org) REST API publishing adapter.

PostEasy is an anonymous microblogging platform. No account required to post.
Publish via ``POST /api/posts`` with a JSON body.

API note: PostEasy is a Next.js app. The ``/api/posts`` endpoint accepts
``{title, content, category?, author?}`` and returns ``{post: {id, ...}}``.
The published URL is ``https://post-easy.org/posts/{uuid}``.

Rate limit: tight — approximately 1 request per 5 minutes per IP on the
create endpoint. The adapter retries on 429 with backoff.

Registration status (Plan 2026-06-04-001 Wave 1b):
- Initially ``dofollow="uncertain"`` — PostEasy renders Markdown content
  client-side via Next.js hydration; the markdown renderer likely auto-links
  URLs as ``<a>`` elements, but rel="nofollow" status must be confirmed via
  canary / RenderedLinkVerifier before marking ``dofollow=True``.
- 90-day TTL. Posts can be "boosted" to permanent for $1.
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
from .http_form_post import attach_link_verification
from .link_attr_verifier import required_link_urls



POSTEASY_API = "https://post-easy.org/api/posts"
POSTEASY_BASE = "https://post-easy.org"
_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 10
_USER_AGENT = "Backlink-Publisher/1.0"


class PostEasyAPIAdapter(Publisher):
    """Publishes Markdown content to PostEasy via anonymous REST API.

    No authentication required. Posts are publicly accessible at
    ``https://post-easy.org/posts/{uuid}``.
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
        log.info(json.dumps(dict(adapter="posteasy", phase="start", id=article_id)))

        title = payload.get("title", "Untitled")
        body = payload.get("content_markdown") or extract_publish_html(payload, "posteasy") or ""
        content = body

        req_body: dict[str, Any] = {
            "title": title,
            "content": content,
        }

        category = payload.get("category", "").strip()
        if category:
            req_body["category"] = category

        author = payload.get("author", "").strip()
        if author:
            req_body["author"] = author

        def _do_publish() -> dict[str, Any]:
            resp = requests.post(
                POSTEASY_API,
                json=req_body,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Content-Type": "application/json",
                    "Origin": POSTEASY_BASE,
                    "Referer": f"{POSTEASY_BASE}/post",
                },
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code == 429:
                retry_msg = "Too many requests"
                try:
                    body = resp.json()
                    retry_msg = body.get("error", retry_msg)
                except Exception:
                    pass
                raise ExternalServiceError(
                    f"PostEasy rate limited (429): {retry_msg}"
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"PostEasy API returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            data = resp.json()
            if "post" not in data or "id" not in data["post"]:
                raise ExternalServiceError(
                    f"PostEasy response missing 'post.id': {json.dumps(data)[:200]}"
                )
            return data

        data = _do_publish()

        post_id = data["post"]["id"]
        published_url = f"{POSTEASY_BASE}/posts/{post_id}"
        elapsed = time.monotonic() - t0

        log.info(json.dumps(dict(
            adapter="posteasy",
            phase="done",
            id=article_id,
            url=published_url,
            seconds=round(elapsed, 2),
        )))

        meta = attach_link_verification(
            published_url,
            {
                "posteasy_id": post_id,
                "posteasy_title": data["post"].get("title"),
                "posteasy_created_at": data["post"].get("createdAt"),
                "posteasy_expires_at": data["post"].get("expiresAt"),
            },
            target_urls=required_link_urls(payload),
        )

        return AdapterResult(
            status="published",
            adapter=_ADAPTER,
            platform=_PLATFORM,
            draft_url="",
            published_url=published_url,
            error=None,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
            _provider_meta=meta,
        )


_ADAPTER = "posteasy-api"
_PLATFORM = "posteasy"
