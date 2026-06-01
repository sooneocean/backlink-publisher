"""LiveJournal XML-RPC publishing adapter (Plan 2026-05-25-001 Unit 6).

The dofollow **keystone**: an end-to-end sample that validates the whole
dofollow-tiering pipeline (register → plan tier mark → report segmentation)
against a platform that actually transfers link equity. Phase 0 probe found
LiveJournal post-body links render as ``rel="noopener noreferrer"`` (no
``nofollow`` token = dofollow). Registered ``dofollow="uncertain"`` pending the
R4 canary loop (publish → ``verify_link_attributes`` → amend register()).

Authentication — honest threat model
------------------------------------
LiveJournal auth is RFC-2617 challenge-response over XML-RPC; Phase 0 confirmed
there is **no OAuth / app-specific-password path**. Each ``postevent`` recomputes
``auth_response = md5(challenge + md5(password))``. The inner ``md5(password)``
("hpassword") is a *password-equivalent* authenticator that must be persisted to
publish unattended — it is NOT a mitigation, it is a full credential. Therefore:

* The credential file at rest is **plaintext-equivalent**. Use a THROWAWAY
  account only — the secret cannot be revoked except by changing the password
  (there is no token to rotate).
* Storage: ``<config_dir>/livejournal-credentials.json`` (``0o600`` via
  ``safe_write.atomic_write`` + post-write stat re-check), schema
  ``{username, hpassword}``. The literal plaintext password is never written;
  ``hpassword`` is derived once in :func:`store_credentials`.

Canonical URL (R12): N/A — LiveJournal renders body-only and strips head
``<meta>``/``<link>``, so it cannot carry ``rel=canonical`` (same constraint as
``telegraph_api``). ``payload.seo.canonical_url`` is read but not emitted.

Logging: no username / password / hpassword / challenge / auth_response ever
appears in a log ``msg`` or exception message (the structured-log scrubber only
redacts ``extra``-dict keys, not ``msg`` f-strings — see ``_util.logger``).
XML-RPC fault strings can echo back submitted params, so fault details are
NEVER interpolated into raised messages.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from xmlrpc.client import Fault, ProtocolError, SafeTransport, ServerProxy

from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.persistence import safe_write
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher

from .base import AdapterResult
from .http_form_post import attach_link_verification
from .link_attr_verifier import required_link_urls

log = logging.getLogger(__name__)

LIVEJOURNAL_XMLRPC = "https://www.livejournal.com/interface/xmlrpc"
_HTTP_TIMEOUT_S = 15
_CRED_FILENAME = "livejournal-credentials.json"

#: faultString fragments LiveJournal returns for credential failures. Matched
#: case-insensitively. The raw faultString is never logged (it can echo the
#: submitted auth params), only this boolean classification.
_AUTH_FAULT_MARKERS: tuple[str, ...] = (
    "invalid password",
    "invalid auth",
    "bad password",
    "client error: invalid",
    "incorrect password",
)


# ── Credential storage ─────────────────────────────────────────────────────


def _credentials_path(config: Config) -> Path:
    return config.config_dir / _CRED_FILENAME


def _hpassword(password: str) -> str:
    """LiveJournal's ``hpassword`` = hex md5 of the UTF-8 password.

    This is the value persisted instead of the literal plaintext. It is still a
    password-equivalent LJ authenticator (see module docstring) — derived here
    so the plaintext never reaches disk.
    """
    return hashlib.md5(password.encode("utf-8")).hexdigest()


def store_credentials(config: Config, username: str, password: str) -> Path:
    """Bootstrap **or** rotate LiveJournal credentials — single mutation site.

    Both first-time bootstrap and operator password rotation route through this
    one function and one atomic write, so the two state-mutation paths share a
    single primitive (no divergent torn-write windows). ``safe_write.atomic_write``
    writes ``0o600`` via tmp → chmod → ``os.replace``; we then re-stat to defend
    against a pre-existing group/world-readable file whose mode a plain rename
    would preserve.

    Returns the credential path. Raises ``DependencyError`` on empty input.
    """
    if not username or not password:
        raise DependencyError(
            "LiveJournal credentials require both username and password"
        )
    path = _credentials_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_write.atomic_write(
        path,
        json.dumps({"username": username, "hpassword": _hpassword(password)}, indent=2),
        mode=0o600,
    )
    # Post-write stat re-check (telegraph_api precedent): atomic_write sets the
    # tmp file to 0o600 before replace, but a pre-existing destination written
    # by older code could have left an inode whose mode we must confirm.
    mode = os.stat(path).st_mode & 0o777
    if mode != 0o600:
        os.chmod(path, 0o600)
    log.info("livejournal_credentials_stored username_set=%s", bool(username))
    return path


def _load_credentials(config: Config) -> dict[str, str]:
    """Load ``{username, hpassword}`` fail-loud. Raises ``DependencyError``.

    Never mints or guesses credentials — a missing/corrupt/over-permissive file
    is an explicit operator-fix condition, not something to paper over.
    """
    path = _credentials_path(config)
    if not path.exists():
        raise DependencyError(
            f"LiveJournal credentials not found: {path}\n"
            "Store them with livejournal_api.store_credentials(config, username, "
            "password) — use a throwaway account (the secret is not revocable)."
        )
    mode = os.stat(path).st_mode & 0o777
    if mode != 0o600:
        raise DependencyError(
            f"{_CRED_FILENAME} must be 0600 (found {oct(mode)})\nRun: chmod 600 {path}"
        )
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        raise DependencyError(
            "Cannot parse LiveJournal credentials: file corrupt or unreadable"
        ) from None
    if not data.get("username") or not data.get("hpassword"):
        raise DependencyError(
            f"{_CRED_FILENAME} missing 'username' or 'hpassword' field"
        )
    return data


# ── XML-RPC transport ───────────────────────────────────────────────────────


class _TimeoutTransport(SafeTransport):
    """HTTPS transport that applies a socket timeout to XML-RPC calls.

    stdlib ``ServerProxy`` has no timeout knob; without this a hung LiveJournal
    endpoint would block the publish indefinitely.
    """

    def __init__(self, timeout: float) -> None:
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):  # type: ignore[no-untyped-def]
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


def _is_auth_fault(fault: Fault) -> bool:
    """True iff the XML-RPC fault denotes a credential failure (not a transient).

    Matches on the lowercased faultString fragments only to classify; the raw
    faultString is never surfaced or logged (it can echo submitted auth params).
    """
    text = str(getattr(fault, "faultString", "")).lower()
    return any(marker in text for marker in _AUTH_FAULT_MARKERS)


# ── Adapter ─────────────────────────────────────────────────────────────────


class LivejournalAPIAdapter(Publisher):
    """Single-path LiveJournal XML-RPC publisher (dofollow keystone)."""

    def _proxy(self) -> ServerProxy:
        return ServerProxy(
            LIVEJOURNAL_XMLRPC,
            transport=_TimeoutTransport(_HTTP_TIMEOUT_S),
            allow_none=True,
        )

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        article_id = payload.get("id", "")
        title = (payload.get("title") or "").strip() or "Untitled"
        body_html = extract_publish_html(payload, "livejournal")
        if not body_html.strip():
            raise ExternalServiceError("LiveJournal payload is empty after rendering")

        # Lazy credential load inside the method (never cached on __init__).
        creds = _load_credentials(config)
        username = creds["username"]
        hpassword = creds["hpassword"]

        proxy = self._proxy()
        log.info("livejournal_publish_start id=%s", article_id)
        try:
            challenge_resp = proxy.LJ.XMLRPC.getchallenge()
            challenge = str((challenge_resp or {}).get("challenge", ""))
            if not challenge:
                raise ExternalServiceError(
                    "LiveJournal getchallenge returned no challenge"
                )
            auth_response = hashlib.md5(
                (challenge + hpassword).encode("utf-8")
            ).hexdigest()
            now = time.localtime()
            event = {
                "username": username,
                "auth_method": "challenge",
                "auth_challenge": challenge,
                "auth_response": auth_response,
                "event": body_html,
                "subject": title,
                "year": now.tm_year,
                "mon": now.tm_mon,
                "day": now.tm_mday,
                "hour": now.tm_hour,
                "min": now.tm_min,
                "ver": 1,
                "lineendings": "unix",
                "security": "public",
                "props": {},
            }
            result = proxy.LJ.XMLRPC.postevent(event) or {}
        except Fault as fault:
            # Do NOT include fault.faultString — it can echo submitted auth params.
            if _is_auth_fault(fault):
                raise DependencyError(
                    "LiveJournal rejected credentials (invalid username/password) — "
                    "re-store via livejournal_api.store_credentials. "
                    f"(faultCode={getattr(fault, 'faultCode', '?')})"
                ) from None
            raise ExternalServiceError(
                f"LiveJournal postevent fault (faultCode="
                f"{getattr(fault, 'faultCode', '?')})"
            ) from None
        except ProtocolError as exc:
            raise ExternalServiceError(
                f"LiveJournal XML-RPC protocol error (HTTP {exc.errcode})"
            ) from exc
        except (OSError, ConnectionError) as exc:
            raise ExternalServiceError(
                f"LiveJournal XML-RPC transport error ({type(exc).__name__})"
            ) from exc

        published_url = str(result.get("url", "")).strip()
        if not published_url:
            raise ExternalServiceError(
                "LiveJournal postevent returned no url — check XML-RPC response shape"
            )
        log.info("livejournal_publish_done id=%s url=%s", article_id, published_url)

        if mode == "draft":
            # LiveJournal has no API draft state; expose the URL as draft_url.
            return AdapterResult(
                status="drafted",
                adapter="livejournal-api",
                platform="livejournal",
                draft_url=published_url,
            )
        # R4 "measure": fire-and-forget verify of the live dofollow status.
        meta = attach_link_verification(published_url, target_urls=required_link_urls(payload))
        return AdapterResult(
            status="published",
            adapter="livejournal-api",
            platform="livejournal",
            published_url=published_url,
            _provider_meta=meta or None,
        )
