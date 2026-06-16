"""Mataroa adapter — publishes Markdown posts via the Mataroa REST API.

Plan 2026-06-01-007 Unit 2 (Wave 1 dofollow channels). Implements the R9
extension recipe on the devto token-REST archetype.

DOFOLLOW NOTICE:

    A 2026-06-01 third-party live probe (``verify_link_attributes`` on real
    public posts) found outbound external links carry no rel (= dofollow) and
    ``site:mataroa.blog`` returns fresh indexed content. The adapter ships
    ``dofollow="uncertain"`` pending an OUR-pipeline canary confirming our own
    placed link renders dofollow on our own published post (the
    hashnode/substack/hatena discipline). The platform currently tolerates
    marketing posts, so it could tighten — confirm before amending to ``True``.

Design choices (mirrors ``hackmd_api.py`` / ``devto_api.py``):

  - **Bearer auth** — Mataroa uses ``Authorization: Bearer <token>``.
    Centralised in ``_required_headers``.
  - **title + body** — the API body is ``{title, body}`` (Markdown); Mataroa
    derives the slug from the title server-side.
  - **published_url** — taken from the API response ``url``; the per-user
    subdomain shape (``<user>.mataroa.blog/blog/<slug>/``) cannot be composed
    offline without the username, so a missing ``url`` is a hard error.
  - **R9** — never log the token or the Authorization header.
"""

from __future__ import annotations

import json
import os
import stat
import time
from typing import Any

from backlink_publisher.http import post as http_post

from backlink_publisher.config import Config, load_mataroa_token
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call


MATAROA_POSTS_API = "https://mataroa.blog/api/posts/"
_HTTP_TIMEOUT_S = 30


def _required_headers(token: str) -> dict[str, str]:
    """Mataroa's required headers — Bearer token."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _require_secure_mode(path) -> None:
    """R10: refuse a group/world-readable token file (mirrors telegraph/livejournal)."""
    if path.exists():
        mode = os.stat(path).st_mode & 0o777
        if mode != 0o600:
            raise DependencyError(
                f"Mataroa token file {path} has mode {oct(mode)}; must be 0o600. "
                f"Run: chmod 600 {path}"
            )


def _load_token(config: Config) -> str:
    """Return the API token, raising DependencyError when not configured."""
    _require_secure_mode(config.mataroa_token_path)
    data = load_mataroa_token(config.mataroa_token_path)
    token = (data or {}).get("token", "").strip()
    if not token:
        raise DependencyError(
            "Mataroa API token not configured. "
            f"Write {{\"token\": \"<token>\"}} to {config.mataroa_token_path} "
            "(chmod 600). Enable at mataroa.blog → account settings → API."
        )
    return token


def _build_post_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Build the Mataroa ``POST /api/posts/`` request body ({title, body})."""
    title = payload.get("title", "Untitled")
    body = (
        payload.get("content_markdown")
        or extract_publish_html(payload, "mataroa")
        or ""
    )
    return {"title": title, "body": body}


class MataroaAPIAdapter(Publisher):
    """Publishes Markdown posts to Mataroa via the REST API.

    dofollow="uncertain" pending an OUR-pipeline canary — see module docstring.
    """

    @classmethod
    def available(cls, config: Config) -> bool:
        """Return True when mataroa-token.json exists with a non-empty token."""
        data = load_mataroa_token(config.mataroa_token_path)
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
        log.info(json.dumps(dict(adapter="mataroa", phase="start", id=article_id)))

        token = _load_token(config)
        post_payload = _build_post_payload(payload)

        if mode == "draft":
            log.info(json.dumps(dict(
                adapter="mataroa", phase="draft-skip", id=article_id,
            )))
            return AdapterResult(
                status="drafted",
                adapter="mataroa",
                platform="mataroa",
                draft_url="https://mataroa.blog/",
            )

        def execute():
            resp = http_post(
                MATAROA_POSTS_API,
                headers=_required_headers(token),
                json=post_payload,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code == 401:
                raise ExternalServiceError(
                    "Mataroa API token rejected (HTTP 401) — re-enable at "
                    "mataroa.blog → account settings → API and re-save to "
                    "mataroa-token.json"
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"Mataroa API returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                body = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"Mataroa returned non-JSON response: {exc}"
                )
            if not body.get("ok", False):
                raise ExternalServiceError(
                    f"Mataroa rejected post: {str(body)[:200]}"
                )
            published_url = (body.get("url") or "").strip()
            if not published_url:
                raise ExternalServiceError(
                    "Mataroa createPost returned no url — check API response shape"
                )
            return published_url

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
                adapter="mataroa",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"Mataroa publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="mataroa", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            status="published",
            adapter="mataroa",
            platform="mataroa",
            published_url=published_url,
        )
