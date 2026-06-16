"""Hatena Blog publisher adapter — AtomPub + WSSE.

Publishes a real (non-draft) entry to a Hatena Blog via the AtomPub
collection endpoint:

    POST https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry

Authentication is WSSE (``X-WSSE`` header): Username = Hatena ID,
Password = the per-blog API key from *Settings → Advanced → AtomPub*.
The digest is ``base64(sha1(nonce + created + api_key))`` with the raw
nonce echoed base64-encoded in the ``Nonce`` field (standard WSSE
UsernameToken).

Credentials live in a single 0600 JSON file
``<config_dir>/hatena-credentials.json``::

    {"hatena_id": "...", "blog_id": "...", "api_key": "..."}

``hatena_id`` and ``blog_id`` are not secret (they appear in the post
URL); ``api_key`` is password-equivalent.

Content is sent as ``text/x-markdown`` (one of Hatena's documented AtomPub
content types) using the row's ``content_markdown`` — the blog must be in
Markdown editing mode for it to render as intended. The canary step
confirms rendering + the placed link's ``rel`` on a real post.

Registered ``dofollow="uncertain"``: a 2026-05-29 3rd-party probe
(``scripts/channel_probe.py``) found Hatena post bodies render outbound
links dofollow (11/12 sampled, no redirect interstitial), but an
OUR-pipeline canary has not yet confirmed our own placed link. Operator
flips to ``True`` by running a fresh canary and reading
``verify_link_attributes`` (the livejournal/txtfyi workflow).

SCAFFOLD NOTE: the publish path is complete and unit-tested against mocked
HTTP, but has NOT been run against the live Hatena AtomPub API. Finishing
requires a real Hatena account + API key (operator-supplied; an agent
must not create accounts or enter credentials) and one live canary.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from xml.sax.saxutils import escape

import requests

from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call

_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 30
_ATOM_NS = "http://www.w3.org/2005/Atom"
_CRED_FILENAME = "hatena-credentials.json"


def _load_credentials(config: Config) -> tuple[str, str, str]:
    """Return ``(hatena_id, blog_id, api_key)`` from the 0600 cred file.

    Raises ``DependencyError`` (→ unbound channel, not a publish failure)
    when the file is missing, has loose permissions, is corrupt, or omits a
    required field. The corrupt-file message is generic on purpose — never
    echo the raw parse error, which would leak a snippet of the secret file
    (mirrors PR #316 / the substack ``_load_cookies`` contract).
    """
    cred_file = config.config_dir / _CRED_FILENAME
    if not cred_file.exists():
        raise DependencyError(
            f"Hatena credentials not found: {cred_file}\n"
            'Write {"hatena_id": "...", "blog_id": "...", "api_key": "..."} '
            "(chmod 600). API key: Hatena Blog → Settings → Advanced → AtomPub."
        )
    mode = os.stat(cred_file).st_mode & 0o777
    if mode != 0o600:
        raise DependencyError(f"{_CRED_FILENAME} must be 0600 (found {oct(mode)})")
    try:
        raw = json.loads(cred_file.read_text())
    except (json.JSONDecodeError, OSError):
        raise DependencyError(
            "Cannot read Hatena credentials: file missing, corrupt, or unreadable"
        ) from None

    hatena_id = str(raw.get("hatena_id", "")).strip()
    blog_id = str(raw.get("blog_id", "")).strip()
    api_key = str(raw.get("api_key", "")).strip()
    if not (hatena_id and blog_id and api_key):
        raise DependencyError(
            "Hatena credentials incomplete: need non-empty hatena_id, blog_id, "
            "and api_key"
        )
    return hatena_id, blog_id, api_key


def _build_wsse_header(
    username: str,
    api_key: str,
    *,
    nonce: bytes | None = None,
    created: str | None = None,
) -> str:
    """Build the ``X-WSSE: UsernameToken ...`` header value.

    ``nonce``/``created`` are injectable for deterministic testing; in
    production both are generated fresh per request (random nonce + current
    UTC). Digest = ``base64(sha1(raw_nonce + created + api_key))``.
    """
    if nonce is None:
        nonce = secrets.token_bytes(16)
    if created is None:
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode("utf-8") + api_key.encode("utf-8")).digest()
    ).decode("ascii")
    nonce_b64 = base64.b64encode(nonce).decode("ascii")
    return (
        f'UsernameToken Username="{username}", '
        f'PasswordDigest="{digest}", '
        f'Nonce="{nonce_b64}", '
        f'Created="{created}"'
    )


def _build_entry_xml(title: str, content_markdown: str, *, draft: bool) -> str:
    """Serialize an Atom entry. Text nodes are XML-escaped (injection-safe)."""
    draft_flag = "yes" if draft else "no"
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<entry xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:app="http://www.w3.org/2007/app">'
        f"<title>{escape(title)}</title>"
        f'<content type="text/x-markdown">{escape(content_markdown)}</content>'
        f"<app:control><app:draft>{draft_flag}</app:draft></app:control>"
        "</entry>"
    )


def _parse_entry_url(xml_text: str) -> str:
    """Extract the public entry URL from a 201 response.

    Hatena returns the live page as ``<link rel="alternate"
    type="text/html" href="...">``. Falls back to the first ``alternate``
    link if the type attribute is absent.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    alt = ""
    for link in root.findall(f"{{{_ATOM_NS}}}link"):
        if link.get("rel") != "alternate":
            continue
        if link.get("type") == "text/html":
            return link.get("href", "")
        alt = alt or link.get("href", "")
    return alt


class HatenaAtomPubAdapter(Publisher):
    """Publishes a live Hatena Blog entry via AtomPub + WSSE."""

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        return (config.config_dir / _CRED_FILENAME).exists()

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="hatena", phase="start", id=article_id)))

        hatena_id, blog_id, api_key = _load_credentials(config)

        title = payload.get("title", "Untitled")
        content_markdown = payload.get("content_markdown", "")
        draft = mode != "publish"

        endpoint = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"
        body = _build_entry_xml(title, content_markdown, draft=draft)

        def execute() -> str:
            # WSSE nonce/created are regenerated per attempt (a stale Created
            # is rejected), so the header is built inside the retry closure.
            headers = {
                "X-WSSE": _build_wsse_header(hatena_id, api_key),
                "Content-Type": "application/atom+xml; charset=utf-8",
            }
            resp = requests.post(
                endpoint,
                headers=headers,
                data=body.encode("utf-8"),
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code in (401, 403):
                raise ExternalServiceError(
                    f"Hatena AtomPub rejected (HTTP {resp.status_code}) — check "
                    "hatena_id/blog_id and regenerate the API key in blog settings."
                )
            if resp.status_code == 429:
                raise ExternalServiceError("Hatena AtomPub rate-limited (HTTP 429)")
            if resp.status_code != 201:
                # Status only — never echo the response body (may carry account
                # detail); mirrors the PR #316 no-leak contract.
                raise ExternalServiceError(
                    f"Hatena AtomPub returned HTTP {resp.status_code} (expected 201)"
                )
            url = _parse_entry_url(resp.text)
            if not url:
                raise ExternalServiceError(
                    "Hatena AtomPub 201 had no alternate link (no entry URL)"
                )
            return url

        try:
            published_url = retry_transient_call(
                execute,
                # Retry ONLY a 429 (a pre-create rate-limit rejection — the POST
                # never reached create). A network error after the request left
                # the client is ambiguous on a non-idempotent create and could
                # duplicate the entry, so it is NOT retried (PR #323 contract).
                is_retryable=lambda exc: (
                    isinstance(exc, ExternalServiceError)
                    and any(
                        f"HTTP {code}" in str(exc) for code in RETRYABLE_HTTP_STATUSES
                    )
                ),
                adapter="hatena",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"Hatena publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(
            json.dumps(
                dict(
                    adapter="hatena",
                    phase="done",
                    id=article_id,
                    elapsed_ms=elapsed,
                )
            )
        )
        return AdapterResult(
            status="published" if mode == "publish" else "drafted",
            adapter="hatena",
            platform="hatena",
            published_url=published_url if mode == "publish" else "",
            draft_url="" if mode == "publish" else published_url,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
        )
