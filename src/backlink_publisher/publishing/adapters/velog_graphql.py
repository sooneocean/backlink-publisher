"""Velog adapter — GraphQL writePost via cookie auth.

**Canonical URL support: N/A** (Plan 2026-05-21-003 Unit 2). Velog's
``WRITE_POST_MUTATION`` (see ``_BUILD_MUTATION`` constants below) is fixed
at the 7 fields the server accepts (title, body, tags, is_markdown,
is_temp, is_private, url_slug, thumbnail, meta, series_id, token). There
is no ``originalArticleURL`` / ``canonical_url`` equivalent; adding one
to the mutation would be rejected server-side. ``payload.seo.canonical_url``
is therefore ignored by this adapter, by design. Rows that need
syndication-mode canonical should route to a different platform.

Authentication model (P0-2 + P0-3 spike results):
- Credentials stored as cookies-only JSON (0600); produced by ``velog-login``.
- ``access_token`` TTL: 24 h (initial login) or 1 h (after implicit refresh).
- ``refresh_token`` TTL: 30 days.
- Implicit refresh: sending any request with a valid ``refresh_token`` causes
  the server to issue a new ``access_token`` via ``Set-Cookie``.
  ``requests.Session`` captures this automatically.
- Retry-on-silent-drop: if ``writePost`` returns ``null`` on the first attempt,
  the access_token may have just been refreshed; retry once.

Error model (P0-1b spike result):
- velog does NOT return ``errors[].extensions.code``.
- All failure states surface as ``{"data": {"writePost": null}}``.
- A successful publish always returns ``{"data": {"writePost": {id, url_slug}}}``.
- The ``_KNOWN_EXTENSIONS_CODES`` mechanism is inapplicable; not implemented.

Rate limiting (R18):
- Per-machine daily cap enforced via a count JSON file + ``fcntl`` polling lock.
- Phase 1 cap: 5/day (``_VELOG_DAILY_CAP_INITIAL``).
- Phase 2 cap (after ``UNLOCK_DATE_UTC``): 30/day (``_VELOG_DAILY_CAP_PROD``).
- To change the cap or unlock date, open a PR — the diff is the audit trail.

P0-1 correction (R10):
- ``url_slug`` MUST be non-null; null triggers silent-drop. Generated from
  title via ``_slugify()``.
"""

from __future__ import annotations

import fcntl
import json
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from backlink_publisher.config import Config
from backlink_publisher._util.errors import (
    AuthExpiredError,
    ContentRejectedError,
    DependencyError,
    ExternalServiceError,
)
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .link_attr_verifier import required_link_urls, verify_link_attributes
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call

# ── Module constants ──────────────────────────────────────────────────────────

_VELOG_GRAPHQL_ENDPOINT = "https://v2.velog.io/graphql"

# From P0-1 spike: required headers to avoid silent-drop
_VELOG_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)
_VELOG_REQUIRED_HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
    "origin": "https://velog.io",
    "referer": "https://velog.io/",
    "sec-fetch-site": "same-site",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
    "user-agent": _VELOG_UA,
}

# GraphQL mutation (7-field minimal set, P0-1 confirmed)
WRITE_POST_MUTATION = (
    "mutation WritePost("
    "$title: String, $body: String, $tags: [String], "
    "$is_markdown: Boolean, $is_temp: Boolean, $is_private: Boolean, "
    "$url_slug: String, $thumbnail: String, $meta: JSON, "
    "$series_id: ID, $token: String"
    ") { writePost("
    "title: $title, body: $body, tags: $tags, "
    "is_markdown: $is_markdown, is_temp: $is_temp, is_private: $is_private, "
    "url_slug: $url_slug, thumbnail: $thumbnail, meta: $meta, "
    "series_id: $series_id, token: $token"
    ") { id user { id username __typename } url_slug __typename } }"
)

# Phase 1 graduated rollout (R18) — change via PR, diff = audit trail
_VELOG_DAILY_CAP_INITIAL: int = 5
_VELOG_DAILY_CAP_PROD: int = 30
# Set to (Unit 4 merge date + 14 days). PR changing this value = unlock event.
UNLOCK_DATE_UTC: datetime = datetime(2026, 6, 2, 0, 0, tzinfo=timezone.utc)

# Jitter window between posts (P0-5b: 30 s interval was clean; plan: 60-180 s)
_VELOG_JITTER_MIN_S: int = 60
_VELOG_JITTER_MAX_S: int = 180

_TIMEOUT: int = 30  # seconds per HTTP request
_PROBE_TIMEOUT: int = 10  # seconds — lightweight liveness check
_LOCK_POLL_INTERVAL: float = 0.5  # seconds
_LOCK_TIMEOUT: float = 60.0  # seconds

# Fields to mask in debug artifacts (never log token values)
_TOKEN_FIELDS = frozenset({"access_token", "refresh_token", "token"})

# Velog liveness probe — currentUser is the most common GraphQL field name.
# If velog renames or removes it, fall back to verifying cookie via writePost
# getting a non-null response on the next natural publish cycle.
_PROBE_QUERY = "{ currentUser { id username } }"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    """Convert *text* to a URL-safe slug (lowercase, hyphens, ASCII-ish)."""
    slug = text.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)  # remove punctuation except - and _
    slug = re.sub(r"[\s_]+", "-", slug)   # spaces → hyphens
    slug = re.sub(r"-+", "-", slug)        # collapse repeated hyphens
    slug = slug.strip("-")
    return slug or "post"


def _json_log(**kwargs: Any) -> str:
    return json.dumps(kwargs)


def _mask_cookies(cookies: dict[str, str]) -> dict[str, str]:
    """Return a copy of *cookies* with token values replaced by '<masked>'."""
    return {k: ("<masked>" if k in _TOKEN_FIELDS else v) for k, v in cookies.items()}


def _save_null_artifact(
    resp_json: dict[str, Any],
    resp_headers: dict[str, str],
    article_id: str,
    config: Config,
) -> str | None:
    """Persist the full null-after-retry response to a debug artifact file.

    Writes ``<config_dir>/debug/velog-null-<article_id>.json`` (0600).
    Returns the artifact path on success, ``None`` if the write fails.
    Never raises — I/O errors are swallowed so a debug write cannot break
    the publish path.
    """
    try:
        debug_dir = config.config_dir / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = debug_dir / f"velog-null-{article_id}.json"
        payload = {
            "adapter": "velog-graphql",
            "article_id": article_id,
            "response_body": resp_json,
            "response_headers": dict(resp_headers),
            "gql_errors": resp_json.get("errors") or [],
        }
        old_umask = os.umask(0o077)
        try:
            tmp = artifact_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2))
            os.chmod(tmp, 0o600)
            os.replace(tmp, artifact_path)
            os.chmod(artifact_path, 0o600)
        finally:
            os.umask(old_umask)
        return str(artifact_path)
    except Exception:
        return None


def _probe_session_alive(session: requests.Session) -> tuple[bool, str]:
    """Check whether *session*'s cookies are still valid via currentUser probe.

    Returns ``(True, username)`` when velog confirms the session is authenticated.
    Returns ``(False, reason)`` on any failure — network error, HTTP error, or
    a null/missing ``currentUser`` in the response.

    Fail-safe: network errors return ``(False, "probe_unreachable")`` so that a
    probe failure during a flaky network does not silently downgrade a real auth
    expiry into a content-rejected classification.
    """
    probe_payload = {"query": _PROBE_QUERY}
    try:
        resp = session.post(
            _VELOG_GRAPHQL_ENDPOINT,
            json=probe_payload,
            headers=_VELOG_REQUIRED_HEADERS,
            verify=True,
            timeout=_PROBE_TIMEOUT,
        )
    except requests.RequestException:
        return False, "probe_unreachable"

    if not resp.ok:
        return False, f"probe_http_{resp.status_code}"

    try:
        data = resp.json()
    except ValueError:
        return False, "probe_invalid_json"

    current_user = (data.get("data") or {}).get("currentUser")
    if not current_user or not current_user.get("id"):
        return False, "no_current_user"

    username = current_user.get("username", "")
    return True, username


# ── Cookie loading ─────────────────────────────────────────────────────────────

def _load_cookies(cookies_path: Path) -> dict[str, str]:
    """Load velog cookies from *cookies_path* (must be 0600).

    Returns a ``{name: value}`` dict suitable for ``requests`` ``cookies=``.

    Raises:
        DependencyError: file missing, wrong permissions, or unparseable.
    """
    source_path = cookies_path
    if not source_path.exists():
        legacy_path = cookies_path.with_name("velog-storage-state.json")
        if legacy_path.exists():
            source_path = legacy_path
        else:
            raise DependencyError(
                f"velog cookies not found: {cookies_path}\n"
                "Run: velog-login"
            )

    mode = os.stat(source_path).st_mode & 0o777
    if mode != 0o600:
        raise DependencyError(
            f"velog-cookies.json must be 0600 (found {oct(mode)})\n"
            f"Run: chmod 600 {source_path}"
        )

    try:
        raw = json.loads(source_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise DependencyError(
            f"Cannot read velog cookies: {exc}\n"
            "Run: velog-login"
        ) from None

    cookie_list = raw.get("cookies", [])
    if not isinstance(cookie_list, list):
        cookie_list = []

    cookies = {
        c["name"]: c["value"]
        for c in cookie_list
        if isinstance(c, dict) and "name" in c and "value" in c
    }

    # Velog may persist auth in browser localStorage instead of cookies.
    # Preserve compatibility with both shapes by mining the captured
    # storage_state payload for an account token if needed.
    if not cookies or "access_token" not in cookies:
        origins = raw.get("origins", [])
        if isinstance(origins, list):
            for origin in origins:
                if not isinstance(origin, dict):
                    continue
                if "velog.io" not in str(origin.get("origin", "")):
                    continue
                local_storage = origin.get("localStorage", [])
                if not isinstance(local_storage, list):
                    continue
                for entry in local_storage:
                    if not isinstance(entry, dict):
                        continue
                    key = str(entry.get("name", ""))
                    val = str(entry.get("value", ""))
                    if key == "account":
                        try:
                            account = json.loads(val)
                        except Exception:
                            continue
                        for token_key in ("access_token", "refresh_token", "token"):
                            token_val = account.get(token_key)
                            if token_val and token_key not in cookies:
                                cookies[token_key] = str(token_val)
                    elif key in {"access_token", "refresh_token", "token"} and val and key not in cookies:
                        cookies[key] = val

    if not cookies:
        raise DependencyError(
            "velog-cookies.json is empty or has no usable auth data.\n"
            "Run: velog-login"
        )
    if not (cookies.get("access_token") or cookies.get("refresh_token")):
        raise AuthExpiredError(
            channel="velog",
            reason="velog credential file has no access_token or refresh_token",
        )
    return cookies


# ── Rate-limit lock + count file ──────────────────────────────────────────────

def _effective_cap() -> int:
    if datetime.now(timezone.utc) >= UNLOCK_DATE_UTC:
        return _VELOG_DAILY_CAP_PROD
    return _VELOG_DAILY_CAP_INITIAL


def _acquire_lock(lock_path: Path) -> int:
    """Open and ``LOCK_EX`` *lock_path*, polling up to 60 s.

    Returns the open file descriptor (caller must close + release).
    Raises ExternalServiceError if lock cannot be acquired within timeout.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    old_umask = os.umask(0o077)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    finally:
        os.umask(old_umask)
    # Ensure 0600 even if file pre-existed with wrong perms
    os.chmod(lock_path, 0o600)

    deadline = time.monotonic() + _LOCK_TIMEOUT
    while time.monotonic() < deadline:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            time.sleep(_LOCK_POLL_INTERVAL)

    os.close(fd)
    raise ExternalServiceError(
        "velog rate-limit lock held > 60 s; check for stale process"
    )


def _release_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    except OSError:
        pass


def _utc_today_iso() -> str:
    """Return today's date in UTC as ISO-8601 — the canonical reset boundary.

    The count file's ``date_utc`` field and the user-facing daily-cap error
    define UTC as the reset boundary. Using ``date.today()`` (local time)
    would let the cap reset at local midnight on machines in non-UTC
    timezones, either blocking publishes for hours after UTC midnight
    or opening an early second quota window.
    """
    return datetime.now(timezone.utc).date().isoformat()


def _read_count(count_path: Path) -> tuple[int, float]:
    """Read ``(count, last_publish_at)`` from *count_path*, resetting on new UTC day."""
    today = _utc_today_iso()
    try:
        data = json.loads(count_path.read_text())
        if data.get("date_utc") != today:
            return 0, 0.0
        return int(data.get("count", 0)), float(data.get("last_publish_at", 0.0))
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return 0, 0.0


def _write_count(count_path: Path, count: int, last_publish_at: float) -> None:
    today = _utc_today_iso()
    payload = {"date_utc": today, "count": count, "last_publish_at": last_publish_at}
    tmp = count_path.with_suffix(".tmp")
    old_umask = os.umask(0o077)
    try:
        tmp.write_text(json.dumps(payload))
        os.chmod(tmp, 0o600)
        os.replace(tmp, count_path)
        os.chmod(count_path, 0o600)
    finally:
        os.umask(old_umask)


# ── Adapter ───────────────────────────────────────────────────────────────────

class _TransientHTTPError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class VelogGraphQLAdapter(Publisher):
    """Publishes to velog.io via internal GraphQL writePost mutation.

    Authentication: cookie jar loaded from a 0600 JSON file produced by
    ``velog-login``.  Credentials are NEVER logged.

    Raises DependencyError  — missing/expired credentials, daily cap reached.
    Raises ExternalServiceError — network failure, GraphQL silent-drop, TTL
                                  exhaustion after retry.
    """

    def embed_banner(self, artifact_path: Path, alt: str) -> str | None:
        """Return ``None`` — route to dispatcher's source_url fallback.

        Plan 2026-05-20-004 Unit 5.  The plan originally proposed a
        two-step ``image_upload_url`` GraphQL mutation + PUT to the
        returned presigned URL.  Probe at implementation time
        (2026-05-20) found:

        * Velog's GraphQL endpoint at ``v2.velog.io/graphql`` disables
          schema introspection
          (``GRAPHQL_VALIDATION_FAILED: introspection is not allowed``).
        * Direct probes of likely mutation names — ``imageUploadUrl``,
          ``createImageUploadUrl``, ``uploadImage``, ``imageUpload`` —
          all return ``Cannot query field`` errors.  The
          ``image_upload_url`` snake_case form in the plan is also
          rejected (GraphQL uses camelCase by convention; the validator
          checked).
        * REST probes at ``v2.velog.io/upload``, ``/upload-image``,
          ``/api/upload``, ``/images`` and the legacy
          ``api.velog.io/upload-file`` / ``/files`` all return HTTP 404.

        Velog's editor likely uploads via a path that's only reachable
        from an authenticated browser session (a temp-signed S3 PUT
        URL obtained through a non-introspectable GraphQL field, or a
        cookie-gated REST endpoint not at any standard path).  Without
        a Playwright-driven scrape of the editor's image-upload
        network traffic to confirm the contract, shipping an
        unverified upload path would land dead code raising
        ``BannerUploadError`` on every row in non-strict mode.

        Returning ``None`` is the explicit "considered but can't"
        signal (distinct from Medium's not-implementing): dispatcher
        prepends ``![alt](source_url)`` from ``banner.source_url`` and
        emits ``banner.source_url_fallback`` with ``reason=
        adapter_returned_none``.  The banner still appears in the
        published post — hosted on the upstream image-gen provider's
        CDN — at the cost of link rot when that CDN expires.

        When/if a follow-up plan supplies a verified upload contract
        (e.g., from inspecting velog's editor source or a
        Playwright-recorded HAR of a real image paste), swap this
        ``None`` for a real implementation.  Do NOT relitigate the
        probe findings here.
        """
        del alt
        return None

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        article_id = payload.get("id", "")
        t0 = time.monotonic()
        log.info(_json_log(adapter="velog-graphql", phase="start", id=article_id))

        # 1. Load credentials (fail-closed)
        velog_cfg = config.velog
        cookies_path = (
            velog_cfg.cookies_path if velog_cfg else
            config.config_dir / "velog-cookies.json"
        )
        cookies = _load_cookies(cookies_path)

        # 2. Phase 1 graduated cap
        cap = _effective_cap()
        log.info(_json_log(
            adapter="velog-graphql",
            phase="cap-check",
            id=article_id,
            effective_cap=cap,
            unlock_date=UNLOCK_DATE_UTC.date().isoformat(),
        ))

        # 3. Acquire rate-limit lock + enforce daily cap + jitter
        lock_path = config.config_dir / "velog-rate-limit.lock"
        count_path = config.config_dir / "velog-rate-limit.json"

        fd = _acquire_lock(lock_path)
        try:
            count, last_publish_at = _read_count(count_path)
            if count >= cap:
                raise DependencyError(
                    f"velog daily cap reached: {count}/{cap}. "
                    "Retry after midnight UTC."
                )

            # Jitter: ensure minimum gap between posts
            if last_publish_at > 0:
                elapsed = time.time() - last_publish_at
                jitter = random.uniform(_VELOG_JITTER_MIN_S, _VELOG_JITTER_MAX_S)
                if elapsed < jitter:
                    wait = jitter - elapsed
                    log.info(_json_log(
                        adapter="velog-graphql", phase="jitter",
                        id=article_id, wait_s=round(wait, 1),
                    ))
                    time.sleep(wait)

            # 4. Build GraphQL variables (7-field minimal set, P0-1 confirmed)
            title = payload.get("title", "")
            body = payload.get("content_markdown", "")
            tags = payload.get("tags", [])
            url_slug = _slugify(title) or f"post-{article_id}"

            gql_vars = {
                "title": title,
                "body": body,
                "tags": tags,
                "is_markdown": True,
                "is_temp": False,
                "is_private": False,
                "url_slug": url_slug,
                "thumbnail": None,
                "meta": {},
                "series_id": None,
                "token": None,
            }
            gql_payload = {
                "operationName": "WritePost",
                "query": WRITE_POST_MUTATION,
                "variables": gql_vars,
            }

            # 5. Send with retry; Session captures Set-Cookie refresh automatically
            session = requests.Session()
            session.cookies.update(cookies)

            def _do_post() -> requests.Response:
                resp = session.post(
                    _VELOG_GRAPHQL_ENDPOINT,
                    json=gql_payload,
                    headers=_VELOG_REQUIRED_HEADERS,
                    verify=True,
                    timeout=_TIMEOUT,
                )
                if resp.status_code in RETRYABLE_HTTP_STATUSES:
                    raise _TransientHTTPError(resp.status_code)
                return resp

            try:
                resp = retry_transient_call(
                    _do_post,
                    is_retryable=lambda exc: isinstance(
                        exc, (requests.Timeout, requests.ConnectionError, _TransientHTTPError)
                    ),
                    adapter="velog-graphql",
                )
            except requests.RequestException:
                raise ExternalServiceError(
                    "velog GraphQL endpoint unreachable"
                ) from None

            if not resp.ok:
                raise ExternalServiceError(
                    f"velog returned HTTP {resp.status_code}"
                )

            # 6. Parse GraphQL response (velog error model: silent-drop only)
            try:
                resp_json = resp.json()
            except ValueError:
                raise ExternalServiceError(
                    "velog response was not valid JSON"
                ) from None

            write_post = (resp_json.get("data") or {}).get("writePost")

            # 7. Retry-on-silent-drop: access_token may have been refreshed via
            #    Set-Cookie on the first request; Session now has the new token.
            if write_post is None:
                log.info(_json_log(
                    adapter="velog-graphql",
                    phase="silent-drop-retry",
                    id=article_id,
                ))
                try:
                    resp2 = retry_transient_call(
                        _do_post,
                        is_retryable=lambda exc: isinstance(
                            exc, (requests.Timeout, requests.ConnectionError, _TransientHTTPError)
                        ),
                        adapter="velog-graphql",
                    )
                except requests.RequestException:
                    raise ExternalServiceError(
                        "velog GraphQL endpoint unreachable on retry"
                    ) from None

                if not resp2.ok:
                    raise ExternalServiceError(
                        f"velog returned HTTP {resp2.status_code} on retry"
                    )

                try:
                    resp_json2 = resp2.json()
                except ValueError:
                    raise ExternalServiceError(
                        "velog retry response was not valid JSON"
                    ) from None

                write_post = (resp_json2.get("data") or {}).get("writePost")
                if write_post is None:
                    artifact_path = _save_null_artifact(
                        resp_json2,
                        dict(resp2.headers),
                        article_id,
                        config,
                    )
                    gql_errors = resp_json2.get("errors") or []
                    gql_errors_summary = (
                        f"{len(gql_errors)} error(s): "
                        + "; ".join(str(e.get("message", "")) for e in gql_errors[:3])
                        if gql_errors else "0"
                    )
                    alive, probe_reason = _probe_session_alive(session)
                    if alive:
                        log.error(_json_log(
                            adapter="velog-graphql",
                            phase="null-after-retry",
                            verdict="content_rejected",
                            id=article_id,
                            gql_errors_summary=gql_errors_summary,
                            probe=probe_reason,
                            artifact=artifact_path,
                        ))
                        raise ContentRejectedError(
                            channel="velog",
                            reason=(
                                f"writePost null after retry; cookie alive ({probe_reason}); "
                                f"gql_errors={gql_errors_summary}"
                            ),
                        )
                    log.error(_json_log(
                        adapter="velog-graphql",
                        phase="null-after-retry",
                        verdict="auth_expired",
                        id=article_id,
                        gql_errors_summary=gql_errors_summary,
                        probe=probe_reason,
                        artifact=artifact_path,
                    ))
                    raise AuthExpiredError(
                        channel="velog",
                        reason=(
                            f"writePost null after retry; cookie dead ({probe_reason})"
                        ),
                    )

            url_slug_returned = (write_post or {}).get("url_slug", "")
            post_id = (write_post or {}).get("id", "")
            username = (
                ((write_post or {}).get("user") or {}).get("username", "")
            )

            if not url_slug_returned:
                raise AuthExpiredError(
                    channel="velog",
                    reason="velog writePost succeeded but returned no url_slug",
                )

            published_url = f"https://velog.io/@{username}/{url_slug_returned}"

            # 8. Update count file
            now = time.time()
            _write_count(count_path, count + 1, now)

            log.info(_json_log(
                adapter="velog-graphql",
                phase="published",
                id=article_id,
                post_id=post_id,
                url_slug=url_slug_returned,
                elapsed_s=round(time.monotonic() - t0, 2),
            ))

        finally:
            _release_lock(fd)

        # 9. Link attribute verification (R3 — warn only, does not change status)
        #    verify_publish (link presence) is handled by the CLI layer.
        link_attr = verify_link_attributes(
            published_url, target_urls=required_link_urls(payload)
        )
        if link_attr.get("verification") != "ok":
            log.warning(_json_log(
                adapter="velog-graphql",
                phase="link-attr-warn",
                id=article_id,
                verification=link_attr,
            ))

        return AdapterResult(
            status="published",
            adapter="velog-graphql",
            platform="velog",
            draft_url="",
            published_url=published_url,
            _provider_meta={
                "post_id": post_id,
                "url_slug": url_slug_returned,
                "link_attr_verification": link_attr,
            },
        )
