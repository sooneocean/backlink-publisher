from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call


_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 60


def _load_cookies(config: Config) -> dict[str, str]:
    cred_file = config.config_dir / "substack-credentials.json"
    if not cred_file.exists():
        raise DependencyError(
            f"Substack credentials not found: {cred_file}\n"
            "Save cookies from a logged-in substack.com session. "
            "Format: {\"cookies\": [{\"name\": \"...\", \"value\": \"...\"}, ...]}"
        )
    mode = os.stat(cred_file).st_mode & 0o777
    if mode != 0o600:
        raise DependencyError(
            f"substack-credentials.json must be 0600 (found {oct(mode)})"
        )
    try:
        raw = json.loads(cred_file.read_text())
    except (json.JSONDecodeError, OSError):
        raise DependencyError(
            "Cannot read Substack credentials: file missing, corrupt, or unreadable"
        ) from None

    cookie_list = raw.get("cookies", [])
    if not isinstance(cookie_list, list):
        raise DependencyError("Substack credentials missing 'cookies' array")
    return {
        c["name"]: c["value"]
        for c in cookie_list
        if isinstance(c, dict) and "name" in c and "value" in c
    }


_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)


class SubstackAPIAdapter(Publisher):
    """Publishes to Substack via cookie-authenticated REST API.

    Authentication: Playwright-exported cookies from a logged-in
    ``substack.com`` session, stored in a 0600 JSON file.

    Substack's draft API (``POST /api/v1/drafts``) accepts HTML content
    and a title. The adapter creates a draft that can be manually
    published from the Substack dashboard.

    Registered ``dofollow="uncertain"`` (2026-05-26 audit): a 3rd-party
    live check found Substack post bodies carry no rel, but an OUR-pipeline
    canary hasn't confirmed it. Note: Substack's API is limited; this
    adapter only creates drafts (it reports ``status="drafted"``), and
    fully automated publishing would need a verified two-step flow or the
    RSS-import approach instead.
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        return (config.config_dir / "substack-credentials.json").exists()

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="substack", phase="start", id=article_id)))

        cookies = _load_cookies(config)

        title = payload.get("title", "Untitled")
        content = extract_publish_html(payload, "substack") or ""
        subtitle = payload.get("meta", {}).get("subtitle", "")

        body_json: dict[str, Any] = {
            "title": title,
            "body": {
                "html": content,
                "type": "html",
            },
            "subtitle": subtitle,
        }

        if mode == "publish":
            # Best-effort hint only. This endpoint is /api/v1/drafts and its
            # response does not confirm publication, so we cannot verify the
            # post actually went live — the reported status stays "drafted"
            # (see the return below). Operator confirms/publishes from the
            # Substack dashboard, per the class docstring.
            body_json["publish"] = True
            body_json["send"] = False  # don't email — backlinks are SEO, not broadcast

        headers = {
            "Content-Type": "application/json",
            "User-Agent": _UA,
            "Referer": "https://substack.com/dashboard",
            "x-requested-with": "XMLHttpRequest",
        }

        api_url = "https://substack.com/api/v1/drafts"

        def execute():
            resp = requests.post(
                api_url,
                headers=headers,
                cookies=cookies,
                json=body_json,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code in (401, 403):
                raise ExternalServiceError(
                    f"Substack API rejected (HTTP {resp.status_code}) — "
                    "cookies expired. Re-export cookies from substack.com."
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"Substack API returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                resp_body = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"Substack returned non-JSON response: {exc}"
                )
            draft_id = resp_body.get("id", "")
            if not draft_id:
                raise ExternalServiceError(
                    "Substack createDraft returned no ID"
                )
            draft_url = (
                resp_body.get("url")
                or resp_body.get("canonical_url")
                or f"https://substack.com/draft/{draft_id}"
            )
            return draft_url

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
                adapter="substack",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"Substack publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="substack", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            # Always "drafted": the only confirmed action is draft creation
            # via /api/v1/drafts. Reporting "published" in publish mode was a
            # lie — the response never confirms a live post, and the events
            # projector would miscount it as a published backlink.
            status="drafted",
            adapter="substack",
            platform="substack",
            published_url=published_url,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
        )
