"""Blogger API v3 adapter — primary publishing path for Blogger platform."""

from __future__ import annotations

import time
from typing import Any

from ..config import Config, BloggerOAuthConfig, resolve_blog_id, load_blogger_token, save_blogger_token
from ..errors import DependencyError, ExternalServiceError
from ..logger import opencli_logger as log
from ..markdown_utils import render_to_html
from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call

_SCOPES = ["https://www.googleapis.com/auth/blogger"]


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

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_blogger_token(json_from_creds(creds), config.blogger_token_path)
            return creds
        except Exception as exc:
            log.warning(f"Token refresh failed: {exc}. Re-authenticating.")
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


class BloggerAPIAdapter:
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

            service = build("blogger", "v3", credentials=creds)
            body = {
                "title": payload.get("title", ""),
                "content": render_to_html(payload.get("content_markdown", "")),
                "labels": payload.get("tags", [])[:20],
            }
            is_draft = mode == "draft"
            result = retry_transient_call(
                lambda: (
                    service.posts()
                    .insert(blogId=blog_id, isDraft=is_draft, body=body)
                    .execute()
                ),
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
