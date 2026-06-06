"""Nonograph (nonogra.ph) form-POST publishing adapter.

Nonograph is an anonymous publishing platform. No accounts, no tracking.
Publish via ``POST /create`` with form-encoded CSRF token + content.

API reference: https://nonogra.ph (open-source, github.com/du82/nonograph)
Form discovered by live probe 2026-06-04:
  POST https://nonogra.ph/create
  Fields: title (optional), content (markdown, max 256000), alias (optional),
          csrf_token (from hidden input on the form page)

This adapter follows the same pattern as the txt.fyi adapter:
1. GET the form page to extract hidden CSRF fields
2. Wait briefly for anti-spam gate
3. POST with content + CSRF token
4. Extract published URL from redirect target

Registration status (Plan 2026-06-04-001 Wave 3a / R19):
- Initially ``dofollow="uncertain"`` — Nonograph renders markdown content
  to static HTML. URLs in markdown become ``<a>`` elements. Canary
  verification must confirm no ``rel="nofollow"`` before marking
  ``dofollow=True``.
- Permanent storage (no TTL, no deletion).
"""

from __future__ import annotations

import json
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

import requests

from backlink_publisher._util.errors import ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.config import Config
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult


NONOGRAPH_FORM = "https://nonogra.ph"
NONOGRAPH_SUBMIT = "https://nonogra.ph/create"
NONOGRAPH_BASE = "https://nonogra.ph"
_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 5
# Anti-spam dwell-time: wait between GET and POST.
_SUBMIT_DELAY_S = 2
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 backlink-publisher"
)


class _CSRFExtractor(HTMLParser):
    """Minimal HTML parser to extract the ``csrf_token`` hidden input value."""

    def __init__(self) -> None:
        super().__init__()
        self.csrf_token: str | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag != "input":
            return
        attr_dict = dict(attrs)
        if attr_dict.get("name") == "csrf_token":
            value = attr_dict.get("value")
            if value is not None:
                self.csrf_token = value


class NonographAPIAdapter(Publisher):
    """Publishes Markdown content to Nonograph via anonymous form POST.

    No authentication required. The adapter fetches the homepage to
    extract a CSRF token, then POSTs the content to ``/create``.
    The published URL is determined from the redirect target.
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        return True

    def _fetch_csrf(self) -> str:
        """GET the form page and extract the csrf_token.

        Raises ExternalServiceError on transport/HTTP failure or if the
        CSRF token cannot be found.
        """
        resp = requests.get(
            NONOGRAPH_FORM,
            timeout=_HTTP_TIMEOUT_S,
            headers={"User-Agent": _USER_AGENT},
        )
        if resp.status_code != 200:
            raise ExternalServiceError(
                f"nonograph: homepage returned HTTP {resp.status_code}"
            )

        parser = _CSRFExtractor()
        parser.feed(resp.text)
        if parser.csrf_token is None:
            raise ExternalServiceError(
                "nonograph: could not find csrf_token in form page"
            )
        return parser.csrf_token

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="nonograph", phase="start", id=article_id)))

        title = payload.get("title", "")
        body = payload.get("content_markdown") or extract_publish_html(payload, "nonograph") or ""

        # Step 1: fetch CSRF token from the form page
        csrf_token = self._fetch_csrf()

        # Step 2: dwell for anti-spam gate
        time.sleep(_SUBMIT_DELAY_S)

        # Step 3: POST content
        form_data: dict[str, str] = {
            "csrf_token": csrf_token,
            "content": body,
        }
        if title:
            form_data["title"] = title

        log.info(json.dumps(dict(adapter="nonograph", phase="post", id=article_id)))

        resp = requests.post(
            NONOGRAPH_SUBMIT,
            data=form_data,
            timeout=_HTTP_TIMEOUT_S,
            headers={"User-Agent": _USER_AGENT},
            allow_redirects=False,  # We want the redirect target
        )

        elapsed = time.monotonic() - t0

        # Nonograph redirects to the published page on success (302/303)
        published_url: str | None = None
        if resp.status_code in (302, 303, 301):
            location = resp.headers.get("Location", "")
            if location:
                published_url = urljoin(NONOGRAPH_BASE, location)
        elif resp.status_code == 200:
            # Could be the published page rendered directly, or an error.
            # Check for error indicators.
            if "error" in resp.text[:500].lower():
                raise ExternalServiceError(
                    f"nonograph: publish returned 200 with error: {resp.text[:300]}"
                )
            # If no Location header, try to extract from response URL
            if resp.url and resp.url.startswith(NONOGRAPH_BASE + "/"):
                published_url = resp.url
        else:
            raise ExternalServiceError(
                f"nonograph: HTTP {resp.status_code} after {elapsed:.1f}s: "
                f"{resp.text[:200]}"
            )

        if not published_url:
            raise ExternalServiceError(
                f"nonograph: could not determine published URL "
                f"(HTTP {resp.status_code}, Location: {resp.headers.get('Location', 'none')})"
            )

        log.info(json.dumps(dict(
            adapter="nonograph",
            phase="done",
            id=article_id,
            url=published_url,
            elapsed_seconds=round(elapsed, 2),
        )))

        return AdapterResult(
            status="published" if mode == "live" else "drafted",
            adapter="nonograph-form-post",
            platform="nonograph",
            draft_url=published_url,
            published_url=published_url if mode == "live" else "",
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
        )
