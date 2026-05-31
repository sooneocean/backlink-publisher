"""Medium API v1 adapter — primary publishing path for Medium platform."""

from __future__ import annotations

import time
from typing import Any

import requests
from backlink_publisher.http import get as http_get, post as http_post

from backlink_publisher.config import Config
from backlink_publisher.config.types import MEDIUM_API_BASE, MEDIUM_API_TIMEOUT
from backlink_publisher._util.errors import (
    AuthExpiredError,
    DependencyError,
    ExternalServiceError,
)
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .link_attr_verifier import required_link_urls, verify_link_attributes
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call

_API_BASE = MEDIUM_API_BASE
_TIMEOUT = MEDIUM_API_TIMEOUT


class _TransientHTTPError(Exception):
    """Sentinel raised when an HTTP response status warrants a retry.

    Module-private — not exported. Does not extend ExternalServiceError so it
    is not caught by the retry guard in retry_transient_call.
    """
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


def _json_log(**kwargs: Any) -> str:
    import json
    return json.dumps(kwargs)


def _resolve_medium_token_data(config: Config) -> tuple[str, dict | None]:
    """Return (token, medium_token_data); raise DependencyError if no token found.

    Priority: OAuth access_token → Integration Token file → TOML ``medium_integration_token``.
    ``medium_token_data`` is ``None`` for non-OAuth sources (Integration Token / TOML).
    """
    from backlink_publisher.config import load_medium_token
    from backlink_publisher.config.tokens import load_medium_integration_token

    medium_token_data = load_medium_token()
    token = medium_token_data.get("access_token") if medium_token_data else None
    if not token:
        it_data = load_medium_integration_token()
        token = (it_data or {}).get("integration_token", "").strip() or None
    if not token:
        token = config.medium_integration_token
    if not token:
        raise DependencyError(
            "medium access token or integration token not configured"
            " — please authorize via Settings → Medium 授权"
        )
    return token, medium_token_data


def _check_medium_token_expiry(medium_token_data: dict | None) -> None:
    """Raise ExternalServiceError if the OAuth token expires within 5 minutes.

    No-ops for non-OAuth sources (``medium_token_data`` is None) and for
    tokens whose ``expires_at`` is 0 (the "unknown" sentinel) or absent.
    """
    if not medium_token_data or "expires_at" not in medium_token_data:
        return
    expires_at = medium_token_data["expires_at"]
    if expires_at > 0 and time.time() >= expires_at - 300:
        raise ExternalServiceError(
            "Medium OAuth token expires in < 5 minutes — re-authorize via Settings → Medium 授权"
        )


def _fetch_medium_user_id(headers: dict) -> str:
    """Call ``GET /me``, retrying transient errors; return ``user_id`` or raise."""
    def _do_me() -> requests.Response:
        resp = http_get(f"{_API_BASE}/me", headers=headers, timeout=_TIMEOUT)
        if resp.status_code in RETRYABLE_HTTP_STATUSES:
            raise _TransientHTTPError(resp.status_code)
        return resp

    try:
        me_resp = retry_transient_call(
            _do_me,
            is_retryable=lambda exc: isinstance(
                exc, (requests.Timeout, requests.ConnectionError, _TransientHTTPError)
            ),
            adapter="medium-api",
        )
    except requests.RequestException as exc:
        raise ExternalServiceError(
            f"Medium API unreachable (/me): {exc}"
        ) from None
    except _TransientHTTPError as exc:
        raise ExternalServiceError(
            f"Medium /me returned HTTP {exc.status_code} after retries"
        ) from None

    if me_resp.status_code == 401:
        raise AuthExpiredError(channel="medium", reason="Medium /me HTTP 401")
    if not me_resp.ok:
        raise ExternalServiceError(f"Medium /me returned HTTP {me_resp.status_code}")
    return me_resp.json()["data"]["id"]


def _create_medium_post(
    user_id: str,
    headers: dict,
    body: dict,
) -> requests.Response:
    """POST to ``/users/{user_id}/posts``; retry only on 429; raise on errors.

    Non-idempotent: network errors (Timeout/ConnectionError) after the request
    leaves the client are *not* retried — a silent duplicate would result.
    Only a 429 rate-limit rejection (pre-create refusal) is safe to retry.
    """
    def _do_post() -> requests.Response:
        resp = http_post(
            f"{_API_BASE}/users/{user_id}/posts",
            headers=headers,
            json=body,
            timeout=_TIMEOUT,
        )
        if resp.status_code in RETRYABLE_HTTP_STATUSES:
            raise _TransientHTTPError(resp.status_code)
        return resp

    try:
        post_resp = retry_transient_call(
            _do_post,
            is_retryable=lambda exc: isinstance(exc, _TransientHTTPError),
            adapter="medium-api",
        )
    except requests.RequestException as exc:
        raise ExternalServiceError(
            f"Medium API unreachable (create post): {exc}"
        ) from None
    except _TransientHTTPError as exc:
        raise ExternalServiceError(
            f"Medium /posts returned HTTP {exc.status_code} after retries"
        ) from None

    if post_resp.status_code == 401:
        raise AuthExpiredError(channel="medium", reason="Medium /posts HTTP 401")
    if post_resp.status_code == 429:
        raise ExternalServiceError("Medium API rate-limited (429)")
    if not post_resp.ok:
        raise ExternalServiceError(
            f"Medium /posts returned HTTP {post_resp.status_code}: "
            f"{post_resp.text[:200]}"
        )
    return post_resp


class MediumAPIAdapter(Publisher):
    """Publishes to Medium via the official API v1 (Integration Token auth).

    Raises DependencyError if no token configured — dispatcher falls through
    to the browser fallback adapter.
    Raises ExternalServiceError for auth failures or rate-limits — no fallthrough.
    """

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        token, medium_token_data = _resolve_medium_token_data(config)
        _check_medium_token_expiry(medium_token_data)

        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(_json_log(adapter="medium-api", phase="start", id=article_id))

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        user_id = _fetch_medium_user_id(headers)
        log.info(_json_log(adapter="medium-api", phase="lookup", id=article_id))

        tags = payload.get("tags", [])[:5]
        # Plan 2026-05-18-006 Unit 5 R9: extract_publish_html selects the
        # source format per platform tier. medium is **platform-tier (b)**
        # per the most-restrictive-tier rollup (MediumAPI alone is tier (a)
        # but the dispatcher falls back to MediumBrave/MediumBrowser whose
        # WYSIWYG paste sanitize is lossy). v1 conservative: helper renders
        # content_markdown for medium even when content_html is present —
        # validate-time gate (Unit 6) rejects content_html-only rows for
        # medium before they reach this code path.
        content_html = extract_publish_html(payload, "medium")
        canonical_url = payload.get("seo", {}).get("canonical_url") or None

        publish_status = "public" if mode == "publish" else "draft"
        body: dict[str, Any] = {
            "title": payload.get("title", ""),
            "contentFormat": "html",
            "content": content_html,
            "tags": tags,
            "publishStatus": publish_status,
        }
        if canonical_url:
            body["canonicalUrl"] = canonical_url

        post_resp = _create_medium_post(user_id, headers, body)

        data = post_resp.json().get("data", {})
        url = data.get("url", "")
        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(
            _json_log(adapter="medium-api", phase="done", id=article_id, elapsed_ms=elapsed)
        )

        if mode == "publish":
            meta: dict[str, Any] = {}
            if url:
                attr_check = verify_link_attributes(
                    url, target_urls=required_link_urls(payload)
                )
                meta["link_attr_verification"] = attr_check
                ratio = attr_check.get("blank_ratio", 1.0)
                total = attr_check.get("total_anchors", 0)
                if attr_check.get("verification") == "ok" and total > 0 and ratio < 0.5:
                    log.warn(
                        _json_log(
                            adapter="medium-api",
                            phase="attr-warn",
                            id=article_id,
                            msg=(
                                f"Medium stripped target attributes: "
                                f"{attr_check['blank_anchors']}/{total} anchors "
                                "retain target=_blank"
                            ),
                        )
                    )
            return AdapterResult(
                status="published",
                adapter="medium-api",
                platform="medium",
                published_url=url,
                post_publish_delay_seconds=30,
                _provider_meta=meta if meta else None,
            )
        return AdapterResult(
            status="drafted",
            adapter="medium-api",
            platform="medium",
            draft_url=url,
            post_publish_delay_seconds=30,
        )
