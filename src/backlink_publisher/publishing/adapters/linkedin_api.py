from __future__ import annotations

import json
import time
from typing import Any

import requests

from backlink_publisher.config import Config, load_linkedin_token
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call


LINKEDIN_API_BASE = "https://api.linkedin.com/v2"
_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 60


class LinkedInAPIAdapter(Publisher):
    """Publishes to LinkedIn via OAuth 2.0 REST API (v2/posts).

    Authentication: LinkedIn OAuth 2.0 access token with ``w_member_social``
    scope, stored in a 0600 JSON file (``linkedin-token.json``).

    The operator obtains the token via the LinkedIn Developer Portal:
      - Create an app at https://www.linkedin.com/developers/apps
      - Add the ``w_member_social`` product (requires verification)
      - Generate an access token via the OAuth 2.0 playground or manual flow
      - Save it as::

            { "token": "<access_token>", "person_id": "urn:li:person:<id>" }

      to ``~/.config/backlink-publisher/linkedin-token.json`` (chmod 600).

    The adapter creates a post via ``POST /v2/posts`` with article commentary
    containing the backlink URL. LinkedIn links are followed by crawlers and
    pass link equity, so registered with ``dofollow=True``. The platform has
    very high DA and strong SEO value for quality content.

    Note: LinkedIn restricts ``w_member_social`` to approved developers.
    Platform access is operator-responsibility; the adapter will raise
    ``DependencyError`` if the token is missing or has insufficient scope.
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        data = load_linkedin_token(config.linkedin_token_path)
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
        log.info(json.dumps(dict(adapter="linkedin", phase="start", id=article_id)))

        token_data = load_linkedin_token(config.linkedin_token_path)
        if not token_data:
            raise DependencyError(
                "LinkedIn token not configured.\n"
                f"Write {{\"token\": \"<access_token>\", \"person_id\": \"urn:li:person:<id>\"}} "
                f"to {config.linkedin_token_path} (chmod 600).\n"
                "Get a token via LinkedIn Developer Portal with w_member_social scope."
            )
        access_token = (token_data.get("token") or "").strip()
        person_urn = (token_data.get("person_id") or "").strip()
        if not access_token or not person_urn:
            raise DependencyError(
                "LinkedIn token file must contain both 'token' and 'person_id' fields"
            )

        title = payload.get("title", "Untitled")
        content = (
            payload.get("content_markdown")
            or extract_publish_html(payload, "linkedin")
            or ""
        )
        tags = payload.get("tags", [])

        commentary = f"{title}\n\n{content}"
        if len(commentary) > 3000:
            commentary = commentary[:2997] + "..."

        hashtags = " ".join(f"#{t.replace(' ', '')}" for t in tags[:5])

        # LinkedIn posts with "article" format — we share as a text post
        # with the backlink content as commentary.  This is the most
        # reliable path for SEO-driven publishing.
        body: dict[str, Any] = {
            "author": person_urn,
            "commentary": commentary + ("\n\n" + hashtags if hashtags else ""),
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }

        if mode == "draft":
            body["lifecycleState"] = "DRAFT"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        }

        api_url = f"{LINKEDIN_API_BASE}/posts"

        def execute():
            resp = requests.post(
                api_url,
                headers=headers,
                json=body,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code == 401:
                raise ExternalServiceError(
                    "LinkedIn API rejected (HTTP 401) — token expired or revoked. "
                    "Regenerate the access token via LinkedIn Developer Portal."
                )
            if resp.status_code == 403:
                data = {}
                try:
                    data = resp.json()
                except ValueError as exc:
                    log.debug(f"Failed to decode JSON response for HTTP 403 error: {exc}")
                    data = {}
                err = (data.get("message") or resp.text)[:200]
                raise ExternalServiceError(
                    f"LinkedIn API rejected (HTTP 403): {err}. "
                    "Check that w_member_social scope is enabled."
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"LinkedIn API returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            # LinkedIn POST /v2/posts returns 201 with an EMPTY body; the
            # created post URN is in the ``x-restli-id`` response header, not
            # the JSON body. (requests' headers are case-insensitive.)
            # Reading resp.json()["id"] therefore failed on every success.
            post_id = (resp.headers.get("x-restli-id") or "").strip()
            if not post_id:
                # Fallback: some API versions / proxies echo an id in a body.
                try:
                    post_id = ((resp.json() or {}).get("id") or "").strip()
                except ValueError:
                    post_id = ""
            if not post_id:
                raise ExternalServiceError(
                    "LinkedIn createPost returned no post URN "
                    "(x-restli-id header absent and no body id)"
                )
            return f"https://www.linkedin.com/feed/update/{post_id}"

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
                adapter="linkedin",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"LinkedIn publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="linkedin", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            status="drafted" if mode == "draft" else "published",
            adapter="linkedin",
            platform="linkedin",
            published_url=published_url,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
        )
