"""HackMD adapter — publishes Markdown notes via the HackMD REST API.

Plan 2026-06-01-007 Unit 1 (Wave 1 dofollow channels). Implements the R9
extension recipe on the devto token-REST archetype.

DOFOLLOW NOTICE:

    A 2026-06-01 third-party live probe (``link_attr_verifier`` on a real
    public note) sampled 188 outbound anchors with 0 nofollow, and the page
    carries ``<meta robots="index,follow">`` (DA ~71). HackMD is therefore a
    plausible *dofollow* channel, but the adapter ships ``dofollow="uncertain"``
    pending an OUR-pipeline canary that confirms our own placed link renders
    dofollow on our own published note (the hashnode/substack/hatena
    discipline). Operator flips to ``True`` via ``verify_link_attributes``.

Design choices (mirrors ``devto_api.py``):

  - **Bearer auth** — HackMD uses ``Authorization: Bearer <token>`` (NOT the
    devto ``api-key`` header). Centralised in ``_required_headers``.
  - **Title via leading H1** — the v1 ``POST /notes`` body has no ``title``
    field; HackMD derives the title from the first ``# `` heading in
    ``content``. We prepend ``# {title}`` when a title is present.
  - **readPermission=guest** — makes the note publicly readable (the published
    surface our backlink lives on). writePermission stays ``owner``.
  - **No 5xx body retries beyond the shared transient policy** — only the
    ``RETRYABLE_HTTP_STATUSES`` set is retried (429/5xx), same as devto.
  - **R9** — never log the token or the Authorization header; only response
    bodies (``resp.text[:200]``), which do not carry the credential.
"""

from __future__ import annotations

import json
import os
import stat
import time
from typing import Any

from backlink_publisher.http import post as http_post

from backlink_publisher.config import Config, load_hackmd_token
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call


HACKMD_NOTES_API = "https://api.hackmd.io/v1/notes"
_HTTP_TIMEOUT_S = 30


def _required_headers(token: str) -> dict[str, str]:
    """HackMD's required headers — Bearer token (NOT the devto api-key header)."""
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
                f"HackMD token file {path} has mode {oct(mode)}; must be 0o600. "
                f"Run: chmod 600 {path}"
            )


def _load_token(config: Config) -> str:
    """Return the API token, raising DependencyError when not configured."""
    _require_secure_mode(config.hackmd_token_path)
    data = load_hackmd_token(config.hackmd_token_path)
    token = (data or {}).get("token", "").strip()
    if not token:
        raise DependencyError(
            "HackMD API token not configured. "
            f"Write {{\"token\": \"<token>\"}} to {config.hackmd_token_path} "
            "(chmod 600). Generate at HackMD → Settings → API → Create token."
        )
    return token


def _build_note_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Build the HackMD ``POST /notes`` request body.

    The v1 API derives the note title from the first H1 in ``content``, so we
    prepend ``# {title}`` when a title is present. ``readPermission=guest``
    makes the note publicly readable (the indexable backlink surface).
    """
    title = payload.get("title", "").strip()
    body = (
        payload.get("content_markdown")
        or extract_publish_html(payload, "hackmd")
        or ""
    )
    content = f"# {title}\n\n{body}" if title else body
    return {
        "content": content,
        "readPermission": "guest",
        "writePermission": "owner",
        "commentPermission": "disabled",
    }


def _published_url(body: dict[str, Any]) -> str:
    """Public URL for the created note.

    Prefer the API's ``publishLink``; fall back to composing from the note id.
    """
    link = (body.get("publishLink") or "").strip()
    if link:
        return link
    note_id = (body.get("id") or "").strip()
    if note_id:
        return f"https://hackmd.io/{note_id}"
    return ""


class HackmdAPIAdapter(Publisher):
    """Publishes Markdown notes to HackMD via the v1 REST API.

    dofollow="uncertain" pending an OUR-pipeline canary — see module docstring.
    """

    @classmethod
    def available(cls, config: Config) -> bool:
        """Return True when hackmd-token.json exists with a non-empty token."""
        data = load_hackmd_token(config.hackmd_token_path)
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
        log.info(json.dumps(dict(adapter="hackmd", phase="start", id=article_id)))

        token = _load_token(config)
        note_payload = _build_note_payload(payload)

        if mode == "draft":
            log.info(json.dumps(dict(
                adapter="hackmd", phase="draft-skip", id=article_id,
            )))
            return AdapterResult(
                status="drafted",
                adapter="hackmd",
                platform="hackmd",
                draft_url="https://hackmd.io/",
            )

        def execute():
            resp = http_post(
                HACKMD_NOTES_API,
                headers=_required_headers(token),
                json=note_payload,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code == 401:
                raise ExternalServiceError(
                    "HackMD API token rejected (HTTP 401) — regenerate at "
                    "HackMD → Settings → API and re-save to hackmd-token.json"
                )
            if resp.status_code == 403:
                raise ExternalServiceError(
                    "HackMD API forbidden (HTTP 403) — token missing notes scope "
                    "or team/permission restriction"
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"HackMD API returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                body = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"HackMD returned non-JSON response: {exc}"
                )
            published_url = _published_url(body)
            if not published_url:
                raise ExternalServiceError(
                    "HackMD createNote returned no publishLink/id — check API response shape"
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
                adapter="hackmd",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"HackMD publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="hackmd", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            status="published",
            adapter="hackmd",
            platform="hackmd",
            published_url=published_url,
        )
