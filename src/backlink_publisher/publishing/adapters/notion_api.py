"""Notion adapter — creates a Page in a database via the Notion Integration API.

Plan 2026-05-21-003 Phase 2 Unit 6. Implements the R9 extension recipe:
one ``register("notion", NotionAPIAdapter)`` call in ``adapters/__init__.py``,
no CLI or schema changes.

Design choices:

  - **Notion Integrations API v1** — POST ``https://api.notion.com/v1/pages``
    with a parent database reference. Requires an integration token scoped to
    the target database.
  - **Authorization: Bearer <integration_token>** — Notion uses standard Bearer.
    Note the contrast with Hashnode (bare PAT, no Bearer prefix).
  - **Notion-Version: 2022-06-28** — pinned version header per Notion API docs.
    Increment only when Notion deprecates the endpoint.
  - **Database-scoped** — every page lives in a parent database. Operators must
    create a Notion Integration, share the database with it, and supply the
    ``database_id`` in ``notion-token.json``.
  - **Canonical URL injection** — when ``payload.seo.canonical_url`` is present,
    appended as a rich-text paragraph child block with a link. Notion Page API
    has no ``<head>`` meta equivalent; the block is the best-effort signal for
    syndicators reading the page content.
  - **Markdown → Notion blocks** — Paragraph-per-line strategy for v1. Full
    Markdown→Notion rich-text conversion (bold/italic/code/lists) is deferred.
  - **dofollow status** — Notion applies ``rel=nofollow`` to outbound hyperlinks
    on public pages. The adapter is registered with ``dofollow=False`` to
    surface this accurately in the dashboard. Value: entity signal / content
    syndication. Not a primary PageRank conduit.
  - **No draft mode** — Notion pages are either created (published) or not.
    ``mode='draft'`` returns a sentinel drafted result without API call.
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests
from backlink_publisher.http import post as http_post

from backlink_publisher.config import Config, load_notion_token
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call


NOTION_PAGES_API = "https://api.notion.com/v1/pages"
_NOTION_VERSION = "2022-06-28"
_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 30


def _required_headers(integration_token: str) -> dict[str, str]:
    """Notion's mandatory headers.

    Authorization uses ``Bearer <token>`` (unlike Hashnode's bare PAT).
    Notion-Version must be pinned — omitting it falls back to an
    undocumented version that may change without notice.
    """
    return {
        "Authorization": f"Bearer {integration_token}",
        "Content-Type": "application/json",
        "Notion-Version": _NOTION_VERSION,
    }


def _load_credentials(config: Config) -> tuple[str, str]:
    """Return (integration_token, database_id), raising DependencyError when absent."""
    token_path = config.notion_token_path
    data = load_notion_token(token_path)
    integration_token = (data or {}).get("integration_token", "").strip()
    database_id = (data or {}).get("database_id", "").strip()
    if not integration_token:
        raise DependencyError(
            "Notion integration token not configured. "
            f"Write {{\"integration_token\": \"secret_...\", \"database_id\": \"...\"}} "
            f"to {token_path} (chmod 600). "
            "Create an Integration at https://www.notion.so/my-integrations "
            "and share the target database with it."
        )
    if not database_id:
        raise DependencyError(
            "Notion database_id missing. "
            f"Add 'database_id' to {token_path}. "
            "Find the database ID in the Notion page URL: "
            "notion.so/<database_id>?v=..."
        )
    return integration_token, database_id


def _text_to_paragraph_blocks(text: str) -> list[dict[str, Any]]:
    """Convert plain text to Notion paragraph blocks (one per non-empty line)."""
    blocks = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"type": "text", "text": {"content": line[:2000]}}
                ]
            },
        })
    return blocks


def _build_page_payload(
    payload: dict[str, Any],
    database_id: str,
) -> dict[str, Any]:
    """Build the Notion pages API request body.

    Children blocks:
    1. Content paragraphs (one per line from body text).
    2. Canonical URL paragraph — appended when ``seo.canonical_url`` present.
       Uses a rich_text link so Notion renders it as a clickable hyperlink.
    """
    title = payload.get("title", "Untitled")
    body = (
        payload.get("content_markdown")
        or extract_publish_html(payload, "notion")
        or ""
    )

    children = _text_to_paragraph_blocks(body)

    # Mixed canonical (Plan 003 R5): append a paragraph child with a link.
    # Notion has no <head> equivalent; this is the best-effort syndication marker.
    # Empty string or missing → omit (pure backlink mode).
    canonical = payload.get("seo", {}).get("canonical_url") or None
    if canonical:
        children.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": "Original: ",
                        },
                    },
                    {
                        "type": "text",
                        "text": {
                            "content": canonical,
                            "link": {"url": canonical},
                        },
                    },
                ]
            },
        })

    return {
        "parent": {"database_id": database_id},
        "properties": {
            "Name": {
                "title": [
                    {"type": "text", "text": {"content": title[:2000]}}
                ]
            }
        },
        "children": children[:100],  # Notion API cap: 100 children per create call
    }


class NotionAPIAdapter(Publisher):
    """Publishes content to a Notion database as a new Page.

    Notion applies ``rel=nofollow`` to outbound links on public pages —
    this adapter's value is entity signal and content syndication speed,
    not PageRank transfer. Registered with ``dofollow=False`` in the
    adapter table.

    Setup requirements for operators:
    1. Create a Notion Integration at https://www.notion.so/my-integrations.
    2. Copy the integration's ``secret_...`` token.
    3. Open the target Notion database and share it with the Integration
       (Share → Invite → select integration name).
    4. Copy the database ID from the Notion page URL.
    5. Write ``{"integration_token": "secret_...", "database_id": "..."}``
       to ``~/.config/backlink-publisher/notion-token.json`` (chmod 600).
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        """Return True when the notion-token.json file exists with both keys."""
        token_path = config.notion_token_path
        data = load_notion_token(token_path)
        if not data:
            return False
        return bool(
            (data.get("integration_token") or "").strip()
            and (data.get("database_id") or "").strip()
        )

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="notion", phase="start", id=article_id)))

        integration_token, database_id = _load_credentials(config)
        page_payload = _build_page_payload(payload, database_id)

        if mode == "draft":
            log.info(json.dumps(dict(
                adapter="notion", phase="draft-skip", id=article_id,
            )))
            return AdapterResult(
                status="drafted",
                adapter="notion",
                platform="notion",
                draft_url=f"notion://database/{database_id}",
            )

        def execute():
            resp = http_post(
                NOTION_PAGES_API,
                headers=_required_headers(integration_token),
                json=page_payload,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code == 401:
                raise ExternalServiceError(
                    "Notion integration token rejected (HTTP 401) — check that the "
                    "token is valid and the database is shared with the integration."
                )
            if resp.status_code == 400:
                try:
                    err_body = resp.json()
                    msg = err_body.get("message", resp.text[:200])
                except Exception:
                    msg = resp.text[:200]
                raise ExternalServiceError(
                    f"Notion API rejected request (HTTP 400): {msg}"
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"Notion API returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                body = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"Notion returned non-JSON response: {exc}"
                )
            page_url = body.get("url", "")
            if not page_url:
                # Notion sometimes returns an internal page ID; construct URL.
                page_id = body.get("id", "")
                if page_id:
                    page_url = f"https://www.notion.so/{page_id.replace('-', '')}"
            if not page_url:
                raise ExternalServiceError(
                    "Notion createPage returned no URL — check API response shape"
                )
            return page_url

        try:
            published_url = retry_transient_call(
                execute,
                is_retryable=lambda exc: (
                    isinstance(exc, ExternalServiceError)
                    and any(
                        f"HTTP {code}" in str(exc)
                        for code in RETRYABLE_HTTP_STATUSES
                    )
                ),
                adapter="notion",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"Notion publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="notion", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            status="published",
            adapter="notion",
            platform="notion",
            published_url=published_url,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
        )
