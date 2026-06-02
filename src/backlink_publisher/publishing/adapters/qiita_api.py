"""Qiita adapter — publishes articles via the Qiita v2 REST API.

Qiita (qiita.com) is Japan's leading developer-content platform (DA ~90+).
All outbound links in article bodies carry ``rel="nofollow noopener"``:
confirmed on 12 real articles, 12/86 non-nofollow (all internal) in the
2026-06-01 discovery run. This adapter is registered as ``dofollow=False``.

Value rationale:
  - **Entity signal**: a high-DA Japanese-language tech domain is crawled
    frequently and boosts brand entity recognition in JP search.
  - **Referral traffic**: Qiita is the canonical JP dev reading channel;
    a well-indexed article drives real developer click-through.
  - **Topical authority**: co-citation with JP dev topics strengthens the
    operator's niche authority for Japanese audiences.

API reference: https://qiita.com/api/v2/docs

Design choices:
  - **Authorization: Bearer <token>** (standard; NOT Dev.to's ``api-key``).
  - **Tags format**: each tag is ``{"name": "string"}`` (not a flat list).
  - **private: false** — posts go public immediately.
  - **No retries for 5xx** — Qiita has no idempotency tokens; a timeout
    on POST might mean the item was created. Only 429 is retried.
  - **Language constraint**: Qiita is a Japanese-language platform.
    The adapter does not enforce this; the operator's language_whitelist
    and plan-backlinks controls govern language selection.
"""

from __future__ import annotations

import json
import time
from typing import Any

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.config import Config, load_qiita_token
from backlink_publisher.http import post as http_post
from backlink_publisher.publishing.registry import Publisher

from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call

_QIITA_ITEMS_API = "https://qiita.com/api/v2/items"
_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 5
_MAX_TAGS = 5


def _required_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _load_token(config: Config) -> str:
    token_path = config.qiita_token_path
    data = load_qiita_token(token_path)
    token = (data or {}).get("token", "").strip()
    if not token:
        raise DependencyError(
            "Qiita personal access token not configured. "
            f'Write {{"token": "<token>"}} to {token_path} (chmod 600). '
            "Generate at qiita.com → Settings → Applications → New token "
            "(read_qiita + write_qiita scopes)."
        )
    return token


def _build_item_payload(payload: dict[str, Any]) -> dict[str, Any]:
    title = payload.get("title") or "Untitled"
    body_markdown = (
        payload.get("content_markdown") or payload.get("content_md") or ""
    )

    raw_tags = payload.get("tags", []) or ["programming"]
    # Qiita tag format: {"name": "<slug>"}, max 5, no spaces/symbols.
    tags = []
    for t in raw_tags[:_MAX_TAGS]:
        cleaned = "".join(
            ch if (ch.isalnum() or ch in "-_") else "" for ch in str(t).lower()
        ).strip("-_")
        if cleaned:
            tags.append({"name": cleaned})
    if not tags:
        tags = [{"name": "programming"}]

    return {
        "title": title,
        "body": body_markdown,
        "private": False,
        "tags": tags,
    }


class QiitaAPIAdapter(Publisher):
    """Publishes Markdown articles to Qiita via the v2 REST API.

    NOFOLLOW NOTICE: Qiita applies rel="nofollow noopener" to all outbound
    links server-side (confirmed 2026-06-01 discovery run, 12/86 ratio).
    This adapter's value is entity signal + JP referral traffic, not PageRank.
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        token_path = config.qiita_token_path
        data = load_qiita_token(token_path)
        if not data:
            return False
        return bool((data.get("token") or "").strip())

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="qiita", phase="start", id=article_id)))

        token = _load_token(config)
        item_payload = _build_item_payload(payload)

        if mode == "draft":
            log.info(json.dumps(dict(adapter="qiita", phase="draft-skip", id=article_id)))
            return AdapterResult(
                status="drafted",
                adapter="qiita-api",
                platform="qiita",
                draft_url="https://qiita.com/drafts",
            )

        def execute():
            resp = http_post(
                _QIITA_ITEMS_API,
                headers=_required_headers(token),
                json=item_payload,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code == 401:
                raise ExternalServiceError(
                    "Qiita token rejected (HTTP 401) — regenerate at "
                    "qiita.com → Settings → Applications and re-save to qiita-token.json. "
                    "Ensure write_qiita scope is enabled."
                )
            if resp.status_code == 422:
                try:
                    err_body = resp.json()
                    msg = err_body.get("message") or resp.text[:200]
                except ValueError:
                    msg = resp.text[:200]
                raise ExternalServiceError(
                    f"Qiita rejected the item payload (HTTP 422): {msg}"
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"Qiita API returned unexpected status {resp.status_code}: "
                    f"{resp.text[:200]}"
                )
            data = resp.json()
            url = data.get("url", "")
            if not url:
                raise ExternalServiceError(
                    "Qiita API returned 201 but no 'url' in response body"
                )
            return url

        published_url = retry_transient_call(
            execute,
            is_retryable=lambda exc: (
                isinstance(exc, ExternalServiceError)
                and any(f"HTTP {code}" in str(exc) for code in RETRYABLE_HTTP_STATUSES)
            ),
            adapter="qiita",
        )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            json.dumps(
                dict(
                    adapter="qiita",
                    phase="done",
                    id=article_id,
                    url=published_url,
                    elapsed_ms=elapsed_ms,
                )
            )
        )

        return AdapterResult(
            status="published",
            adapter="qiita-api",
            platform="qiita",
            published_url=published_url,
        )
