"""BrewPage (brewpage.app) REST API publishing adapter.

BrewPage is a free instant HTML/Markdown hosting service. No signup required.
Publish via ``POST /api/html?format=markdown`` with a JSON body.

API docs: https://brewpage.app/api
OpenAPI:  https://brewpage.app/api/openapi.yaml

Rate limit: 60 uploads / hour / IP (429 → Retry-After).

Registration status (Plan 2026-06-04-001 Wave 1c):
- Initially ``dofollow="uncertain"`` — BrewPage's markdown renderer converts
  URLs to ``<a href="...">`` elements with no ``rel="nofollow"`` decoration
  (confirmed by live probe 2026-06-04), but canary evidence must confirm
  stability before marking ``dofollow=True``.
- Short TTL: 15 days default, max 30 days. Labeled as short-TTL in the
  zero-auth classification.
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



BREWPAGE_API = "https://brewpage.app/api/html"
BREWPAGE_BASE = "https://brewpage.app"
_DEFAULT_TTL_DAYS = 15
_MAX_TTL_DAYS = 30
_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 5
_USER_AGENT = "Backlink-Publisher/1.0"


class BrewPageAPIAdapter(Publisher):
    """Publishes Markdown content to BrewPage.app via anonymous REST API.

    No authentication required. Each publish returns an ``ownerToken``
    for optional edit/delete — the adapter logs it but does not persist
    it (edit is a future enhancement).
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
        log.info(json.dumps(dict(adapter="brewpage", phase="start", id=article_id)))

        title = payload.get("title", "Untitled")
        body = payload.get("content_markdown") or extract_publish_html(payload, "brewpage") or ""
        content = f"# {title}\n\n{body}"

        # Determine TTL: use payload TTL if set and within bounds, else default
        ttl = _DEFAULT_TTL_DAYS
        payload_ttl = payload.get("ttl_days")
        if payload_ttl is not None:
            try:
                ttl = min(int(payload_ttl), _MAX_TTL_DAYS)
            except (ValueError, TypeError):
                pass

        def _do_publish() -> dict[str, Any]:
            resp = requests.post(
                f"{BREWPAGE_API}?format=markdown&ttl={ttl}",
                json={"content": content},
                headers={
                    "User-Agent": _USER_AGENT,
                    "Content-Type": "application/json",
                },
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "60")
                raise ExternalServiceError(
                    f"BrewPage rate limited (429): retry after {retry_after}s"
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"BrewPage API returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            data = resp.json()
            if "link" not in data:
                raise ExternalServiceError(
                    f"BrewPage response missing 'link': {json.dumps(data)[:200]}"
                )
            return data

        data = _do_publish()

        published_url = data["link"]
        elapsed = time.monotonic() - t0

        log.info(json.dumps(dict(
            adapter="brewpage",
            phase="done",
            id=article_id,
            url=published_url,
            ttl_days=ttl,
            seconds=round(elapsed, 2),
        )))

        return AdapterResult(
            status="published",
            adapter=_ADAPTER,
            platform=_PLATFORM,
            draft_url="",
            published_url=published_url,
            error=None,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
            _provider_meta={
                "brewpage_id": data.get("id"),
                "brewpage_owner_token": data.get("ownerToken"),
                "brewpage_expires_at": data.get("expiresAt"),
                "brewpage_ttl_days": ttl,
            },
        )


_ADAPTER = "brewpage-api"
_PLATFORM = "brewpage"
