"""Medium API v1 adapter — primary publishing path for Medium platform."""

from __future__ import annotations

import time
from typing import Any

import requests
from backlink_publisher.http import get as http_get, post as http_post

from backlink_publisher.config import Config
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

_API_BASE = "https://api.medium.com/v1"
_TIMEOUT = 30  # seconds


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
        from backlink_publisher.config import load_medium_token

        # 优先使用 OAuth token，其次 Integration Token
        medium_token_data = load_medium_token()
        token = medium_token_data.get("access_token") if medium_token_data else None
        if not token:
            token = config.medium_integration_token

        if not token:
            raise DependencyError("medium access token or integration token not configured"
                                 " — please authorize via Settings → Medium 授权")

        # Pre-flight expiry check for OAuth tokens that carry an expires_at field.
        # Integration tokens and pre-fix OAuth tokens lack expires_at — skipped (fail-open).
        # expires_at = 0 is a sentinel meaning "unknown"; treated as absent.
        if medium_token_data and "expires_at" in medium_token_data:
            expires_at = medium_token_data["expires_at"]
            if expires_at > 0 and time.time() >= expires_at - 300:
                raise ExternalServiceError(
                    "Medium OAuth token expires in < 5 minutes — re-authorize via Settings → Medium 授权"
                )

        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(_json_log(adapter="medium-api", phase="start", id=article_id))

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # One-time user_id lookup (retried on connection errors and 429/5xx)
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
            raise AuthExpiredError(
                channel="medium",
                reason="Medium /me HTTP 401",
            )
        if not me_resp.ok:
            raise ExternalServiceError(
                f"Medium /me returned HTTP {me_resp.status_code}"
            )

        user_id = me_resp.json()["data"]["id"]
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

        # Create post (retried on connection errors and 429/5xx)
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
                is_retryable=lambda exc: isinstance(
                    exc, (requests.Timeout, requests.ConnectionError, _TransientHTTPError)
                ),
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
            raise AuthExpiredError(
                channel="medium",
                reason="Medium /posts HTTP 401",
            )
        if post_resp.status_code == 429:
            raise ExternalServiceError("Medium API rate-limited (429)")
        if not post_resp.ok:
            raise ExternalServiceError(
                f"Medium /posts returned HTTP {post_resp.status_code}: "
                f"{post_resp.text[:200]}"
            )

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
