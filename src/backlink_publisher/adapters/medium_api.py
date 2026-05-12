"""Medium API v1 adapter — primary publishing path for Medium platform."""

from __future__ import annotations

import time
from typing import Any

import requests

from ..config import Config
from ..errors import DependencyError, ExternalServiceError
from ..logger import opencli_logger as log
from ..markdown_utils import render_to_html
from .base import AdapterResult
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


class MediumAPIAdapter:
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
        token = config.medium_integration_token
        if not token:
            raise DependencyError("medium integration token not configured")

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
            resp = requests.get(f"{_API_BASE}/me", headers=headers, timeout=_TIMEOUT)
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
            raise ExternalServiceError(
                "Medium integration token invalid (401). "
                "Generate a new token at medium.com/me/settings/security."
            )
        if not me_resp.ok:
            raise ExternalServiceError(
                f"Medium /me returned HTTP {me_resp.status_code}"
            )

        user_id = me_resp.json()["data"]["id"]
        log.info(_json_log(adapter="medium-api", phase="lookup", id=article_id))

        tags = payload.get("tags", [])[:5]
        content_html = render_to_html(payload.get("content_markdown", ""))
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
            resp = requests.post(
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
            raise ExternalServiceError(
                "Medium integration token invalid (401). "
                "Generate a new token at medium.com/me/settings/security."
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
            return AdapterResult(
                status="published",
                adapter="medium-api",
                platform="medium",
                published_url=url,
            )
        return AdapterResult(
            status="drafted",
            adapter="medium-api",
            platform="medium",
            draft_url=url,
        )
