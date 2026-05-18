"""Blogger API v3 adapter — primary publishing path for Blogger platform."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from backlink_publisher.config import Config, BloggerOAuthConfig, resolve_blog_id, load_blogger_token, save_blogger_token
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call

_SCOPES = ["https://www.googleapis.com/auth/blogger"]


def _near_expiry(creds, window_secs: int) -> bool:
    """Return True when creds are already expired or expire within window_secs.

    Uses datetime.now(timezone.utc).replace(tzinfo=None) to produce a naive UTC
    datetime that is safe to subtract from google-auth's naive creds.expiry.
    Returns False when creds.expiry is None (no expiry info → no proactive refresh).
    """
    if creds.expired:
        return True
    if creds.expiry is None:
        return False
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    return (creds.expiry - now_naive).total_seconds() <= window_secs


def _build_credentials(config: Config):
    """Return valid google.oauth2.credentials.Credentials, running OAuth if needed."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_data = load_blogger_token(config.blogger_token_path)

    creds: Credentials | None = None
    if token_data:
        try:
            creds = Credentials.from_authorized_user_info(token_data, _SCOPES)
        except Exception:
            creds = None

    if creds and _near_expiry(creds, 300) and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_blogger_token(json_from_creds(creds), config.blogger_token_path)
            return creds
        except Exception as exc:
            log.warn(f"Token refresh failed: {exc}. Re-authenticating.")
            creds = None

    if not creds or not creds.valid:
        oauth = config.blogger_oauth
        if oauth is None:
            raise DependencyError(
                "Blogger OAuth credentials not configured. "
                "Add [blogger.oauth] client_id and client_secret to "
                "~/.config/backlink-publisher/config.toml"
            )
        client_config = {
            "installed": {
                "client_id": oauth.client_id,
                "client_secret": oauth.client_secret,
                "redirect_uris": ["http://localhost"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        flow = InstalledAppFlow.from_client_config(client_config, _SCOPES)
        creds = flow.run_local_server(port=0)
        config.blogger_token_path.parent.mkdir(parents=True, exist_ok=True)
        save_blogger_token(json_from_creds(creds), config.blogger_token_path)

    return creds


def json_from_creds(creds) -> dict[str, Any]:
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else _SCOPES,
    }


class BloggerAPIAdapter(Publisher):
    """Publishes to Blogger via the official API v3."""

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        platform = "blogger"
        article_id = payload.get("id", "")
        log.info(
            json_log(adapter="blogger-api", phase="start", id=article_id)
        )

        blog_id = resolve_blog_id(config, payload.get("main_domain", ""))

        try:
            creds = _build_credentials(config)
        except DependencyError:
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"Blogger authentication failed: {exc}"
            ) from exc

        log.info(json_log(adapter="blogger-api", phase="auth", id=article_id))

        try:
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError
            import google.auth.transport.requests as google_requests

            service = build("blogger", "v3", credentials=creds)
            # Plan 2026-05-18-006 Unit 5 R9: extract_publish_html selects the
            # source format per platform tier. blogger is tier (a) — accepts
            # operator-supplied content_html directly. Sanitize is delegated
            # to Google Blogger's server-side filter (locked by
            # tests/test_adapter_blogger_api_xss_contract.py). If
            # content_html is absent, falls back to rendering content_markdown.
            body = {
                "title": payload.get("title", ""),
                "content": extract_publish_html(payload, "blogger"),
                "labels": payload.get("tags", [])[:20],
            }
            is_draft = mode == "draft"

            # Wrap execute() with timeout for consistency with Medium adapter (30 seconds)
            def execute_with_timeout():
                import socket
                old_timeout = socket.getdefaulttimeout()
                try:
                    socket.setdefaulttimeout(30)
                    return service.posts().insert(blogId=blog_id, isDraft=is_draft, body=body).execute()
                finally:
                    socket.setdefaulttimeout(old_timeout)

            result = retry_transient_call(
                execute_with_timeout,
                is_retryable=lambda exc: (
                    isinstance(exc, HttpError)
                    and exc.resp is not None
                    and exc.resp.status in RETRYABLE_HTTP_STATUSES
                ),
                adapter="blogger-api",
            )
        except Exception as exc:
            _class = type(exc).__name__
            try:
                from googleapiclient.errors import HttpError
                if isinstance(exc, HttpError):
                    status = exc.resp.status if exc.resp else 0
                    if status in (401, 403):
                        raise ExternalServiceError(
                            f"Blogger authentication failed (HTTP {status}); "
                            "re-run OAuth flow by deleting "
                            "~/.config/backlink-publisher/blogger-token.json"
                        ) from exc
                    if status == 429:
                        raise ExternalServiceError(
                            "Blogger API rate-limited (HTTP 429)"
                        ) from exc
                    raise ExternalServiceError(
                        f"Blogger API error (HTTP {status}): {exc}"
                    ) from exc
            except ImportError:
                pass
            raise ExternalServiceError(
                f"Blogger publish failed ({_class}): {exc}"
            ) from exc

        url = result.get("url", "")
        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(
            json_log(adapter="blogger-api", phase="done", id=article_id, elapsed_ms=elapsed)
        )

        if mode == "draft":
            return AdapterResult(
                status="drafted",
                adapter="blogger-api",
                platform=platform,
                draft_url=url,
            )
        return AdapterResult(
            status="published",
            adapter="blogger-api",
            platform=platform,
            published_url=url,
        )


def json_log(**kwargs: Any) -> str:
    import json
    return json.dumps(kwargs)
