"""Unmarkdown — markdown publishing platform REST API adapter.

Unmarkdown (https://unmarkdown.com) is a markdown-to-HTML publishing platform
with a clean REST API. It supports two publishing paths:

**Demo mode** (no auth, zero-setup):
  ``POST /v1/demo/publish`` — creates a publicly accessible page with a 3-day
  TTL. Free tier capped at 3 published pages. No API key needed.

**Authenticated mode** (Pro-tier, API key):
  ``POST /v1/documents/publish`` — creates and publishes a document in one call.
  Requires a Pro subscription and an API key stored in ``unmarkdown-token.json``.
  Unlimited published pages, custom slugs, hide branding.

Both paths render external Markdown links (``[text](url)``) as clean ``<a>``
tags with no ``rel`` attribute — confirmed by live browser probe 2026-06-06.
Registered as ``dofollow=True``.

API docs: https://docs.unmarkdown.com/api/overview
OpenAPI:  https://unmarkdown.com/openapi.json
Rate limit: 10 req/sec (free), 30 req/sec (Pro)
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.config import Config
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .http_form_post import attach_link_verification
from .link_attr_verifier import required_link_urls

UNMARKDOWN_API = "https://api.unmarkdown.com/v1"
UNMARKDOWN_BASE = "https://unmarkdown.com"
_DEMO_PUBLISH_PATH = "/demo/publish"
_AUTH_PUBLISH_PATH = "/documents/publish"
_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 3
_USER_AGENT = "Backlink-Publisher/1.0"
_DEFAULT_TEMPLATE = "github"
_DEFAULT_THEME = "light"


def _load_api_key(config: Config) -> str | None:
    """Read the Unmarkdown API key from the token file, or return None."""
    token_path = config.unmarkdown_token_path
    if not token_path.exists():
        return None
    try:
        data = json.loads(token_path.read_text())
        key = (data.get("api_key") or "").strip()
        return key if key else None
    except (json.JSONDecodeError, OSError):
        return None


class UnmarkdownAdapter(Publisher):
    """Publishes Markdown content to Unmarkdown via REST API.

    When an API key is configured, uses the authenticated endpoint
    (``POST /documents/publish``) for unlimited publishing. Without an
    API key, falls back to the demo endpoint (``POST /demo/publish``)
    which is capped at 3 published pages on the free tier.

    External links in published pages render with empty ``rel`` (dofollow).
    Confirmed by live browser probe 2026-06-06.
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        """Always available — demo mode requires no auth."""
        return True

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(
            json.dumps(dict(adapter="unmarkdown", phase="start", id=article_id))
        )

        title = payload.get("title", "Untitled")
        body = (
            payload.get("content_markdown")
            or extract_publish_html(payload, "unmarkdown")
            or ""
        )
        content = f"# {title}\n\n{body}"

        api_key = _load_api_key(config)
        if api_key:
            data = self._publish_authenticated(content, title, api_key)
            published_url = data["published_url"]
        else:
            data = self._publish_demo(content, title)
            published_url = data["url"]

        elapsed = time.monotonic() - t0

        log.info(
            json.dumps(
                dict(
                    adapter="unmarkdown",
                    phase="done",
                    id=article_id,
                    url=published_url,
                    authenticated=bool(api_key),
                    seconds=round(elapsed, 2),
                )
            )
        )

        meta = attach_link_verification(
            published_url,
            {
                "unmarkdown_id": data.get("id"),
                "unmarkdown_template": data.get("template_id"),
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

    def _publish_demo(self, content: str, title: str) -> dict[str, Any]:
        """Publish via the no-auth demo endpoint."""
        resp = requests.post(
            f"{UNMARKDOWN_API}{_DEMO_PUBLISH_PATH}",
            json={
                "title": title,
                "content": content,
                "template_id": _DEFAULT_TEMPLATE,
                "theme_mode": _DEFAULT_THEME,
            },
            headers={
                "User-Agent": _USER_AGENT,
                "Content-Type": "application/json",
            },
            timeout=_HTTP_TIMEOUT_S,
        )

        if resp.status_code == 429:
            raise ExternalServiceError(
                "Unmarkdown rate limited (429): retry later"
            )
        if resp.status_code not in (200, 201):
            raise ExternalServiceError(
                f"Unmarkdown demo API returned HTTP {resp.status_code}: "
                f"{resp.text[:200]}"
            )
        data = resp.json()
        if "url" not in data:
            raise ExternalServiceError(
                f"Unmarkdown demo response missing 'url': {json.dumps(data)[:200]}"
            )
        return data

    def _publish_authenticated(
        self, content: str, title: str, api_key: str
    ) -> dict[str, Any]:
        """Publish via the authenticated endpoint using an API key."""
        resp = requests.post(
            f"{UNMARKDOWN_API}{_AUTH_PUBLISH_PATH}",
            json={
                "title": title,
                "content": content,
                "template_id": _DEFAULT_TEMPLATE,
                "theme_mode": _DEFAULT_THEME,
            },
            headers={
                "User-Agent": _USER_AGENT,
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            timeout=_HTTP_TIMEOUT_S,
        )

        if resp.status_code == 401:
            raise DependencyError(
                "Unmarkdown API key rejected (401). "
                f"Check {_load_api_key.__globals__['__name__']} — "
                "regenerate at https://unmarkdown.com/settings/api"
            )
        if resp.status_code == 429:
            raise ExternalServiceError(
                "Unmarkdown rate limited (429): retry later"
            )
        if resp.status_code not in (200, 201):
            raise ExternalServiceError(
                f"Unmarkdown API returned HTTP {resp.status_code}: "
                f"{resp.text[:200]}"
            )
        data = resp.json()
        if "published_url" not in data:
            raise ExternalServiceError(
                f"Unmarkdown response missing 'published_url': "
                f"{json.dumps(data)[:200]}"
            )
        return data


_ADAPTER = "unmarkdown-api"
_PLATFORM = "unmarkdown"
