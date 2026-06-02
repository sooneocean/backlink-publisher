"""Zenn adapter — commits Markdown articles to a Zenn-connected GitHub repo.

Zenn (zenn.dev) is Japan's leading developer-content platform (DA ~90+).
Publishing model: the operator connects a GitHub repository to their Zenn
account; articles pushed as Markdown to ``articles/<slug>.md`` are
auto-published by Zenn when the GitHub repo is updated.

All outbound links carry ``rel="nofollow noopener noreferrer"`` — confirmed
on 36 real Zenn articles (36/137 non-nofollow links, all Zenn-internal) in
the 2026-06-01 discovery run. This adapter is registered as ``dofollow=False``.

Value rationale:
  - **Entity signal**: Zenn has DA ~90+ and is a primary JP developer reading
    platform. Articles are indexed quickly and appear in JP search.
  - **Referral traffic**: Zenn is the canonical JP dev content channel.
  - **Topical authority**: co-citation with JP dev topics.

Zenn Markdown front-matter (required):
  title, emoji, type (tech|idea), topics (list, max 5), published (bool).

API: GitHub Contents API (same as ghpages) — one PUT per article.
Auth: GitHub PAT stored in ``zenn-token.json`` (0600), NOT in config.toml.

Config required (in config.toml):
  [zenn]
  github_repo = "owner/zenn-articles-repo"
  username = "your-zenn-username"
"""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.config import Config, load_zenn_token
from backlink_publisher.http import get as http_get
from backlink_publisher.http import put as http_put
from backlink_publisher.publishing.registry import Publisher

from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call

_GITHUB_API = "https://api.github.com"
_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 10


def _slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:60] or "article"


def _required_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _load_token(config: Config) -> str:
    token_path = config.zenn_token_path
    data = load_zenn_token(token_path)
    token = (data or {}).get("token", "").strip()
    if not token:
        raise DependencyError(
            "Zenn GitHub PAT not configured. "
            f'Write {{"token": "<pat>"}} to {token_path} (chmod 600). '
            "Generate at github.com → Settings → Developer settings → "
            "Personal access tokens → New token (contents:write on your "
            "Zenn-connected repo)."
        )
    return token


def _build_zenn_markdown(payload: dict[str, Any]) -> tuple[str, str]:
    """Return (slug, full_markdown_with_frontmatter)."""
    title = payload.get("title") or "Untitled"
    body = (
        payload.get("content_markdown") or payload.get("content_md") or ""
    )
    slug = _slugify(title)

    raw_tags = payload.get("tags", []) or ["programming"]
    topics = []
    for t in raw_tags[:5]:
        cleaned = re.sub(r"[^a-zA-Z0-9]", "", str(t).lower())
        if cleaned:
            topics.append(cleaned)
    if not topics:
        topics = ["programming"]

    frontmatter = (
        "---\n"
        f'title: "{title.replace(chr(34), chr(39))}"\n'
        'emoji: "📝"\n'
        'type: "tech"\n'
        f"topics: [{', '.join(repr(t) for t in topics[:5])}]\n"
        "published: true\n"
        "---\n\n"
    )
    return slug, frontmatter + body


def _get_file_sha(repo: str, path: str, branch: str, headers: dict) -> str | None:
    """Return the SHA of an existing file, or None if it doesn't exist."""
    try:
        resp = http_get(
            f"{_GITHUB_API}/repos/{repo}/contents/{path}",
            headers=headers,
            params={"ref": branch},
            timeout=_HTTP_TIMEOUT_S,
        )
        if resp.status_code == 200:
            return resp.json().get("sha")
        return None
    except Exception:  # noqa: BLE001
        return None


class ZennGitHubAdapter(Publisher):
    """Publishes Markdown articles to a Zenn-connected GitHub repository.

    NOFOLLOW NOTICE: Zenn applies rel="nofollow noopener noreferrer" to all
    outbound links server-side (confirmed 2026-06-01 discovery, 36/137 ratio).
    This adapter's value is entity signal + JP referral traffic, not PageRank.

    PRECONDITION: The operator must have:
    1. A GitHub repo connected to their Zenn account (zenn.dev → Dashboard → Books)
    2. [zenn] section in config.toml with github_repo + username
    3. A GitHub PAT at zenn-token.json (contents:write scope)
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        if not config.zenn:
            return False
        if not config.zenn.github_repo or not config.zenn.username:
            return False
        data = load_zenn_token(config.zenn_token_path)
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
        log.info(json.dumps(dict(adapter="zenn", phase="start", id=article_id)))

        if not config.zenn or not config.zenn.github_repo or not config.zenn.username:
            raise DependencyError(
                "Zenn not configured. Add [zenn] section to config.toml with "
                "github_repo = \"owner/repo\" and username = \"your-zenn-username\"."
            )

        token = _load_token(config)
        repo = config.zenn.github_repo
        branch = config.zenn.branch or "main"
        zenn_username = config.zenn.username

        slug, markdown_content = _build_zenn_markdown(payload)
        file_path = f"articles/{slug}.md"
        published_url = f"https://zenn.dev/{zenn_username}/articles/{slug}"

        if mode == "draft":
            log.info(json.dumps(dict(adapter="zenn", phase="draft-skip", id=article_id)))
            return AdapterResult(
                status="drafted",
                adapter="zenn-github",
                platform="zenn",
                draft_url=published_url,
            )

        headers = _required_headers(token)
        encoded = base64.b64encode(markdown_content.encode()).decode()

        def execute():
            # Idempotency: if the file already exists, update (PUT with sha).
            existing_sha = _get_file_sha(repo, file_path, branch, headers)
            body: dict[str, Any] = {
                "message": f"feat: add {slug}",
                "content": encoded,
                "branch": branch,
            }
            if existing_sha:
                body["sha"] = existing_sha

            resp = http_put(
                f"{_GITHUB_API}/repos/{repo}/contents/{file_path}",
                headers=headers,
                json=body,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code == 401:
                raise ExternalServiceError(
                    "GitHub PAT rejected (HTTP 401) — regenerate and re-save to "
                    "zenn-token.json (chmod 600). Ensure contents:write scope."
                )
            if resp.status_code == 403:
                raise ExternalServiceError(
                    "GitHub PAT lacks permission (HTTP 403) — ensure the PAT has "
                    "contents:write scope on the Zenn-connected repository."
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"GitHub Contents API returned unexpected status {resp.status_code}: "
                    f"{resp.text[:200]}"
                )
            return published_url

        result_url = retry_transient_call(
            execute,
            is_retryable=lambda exc: (
                isinstance(exc, ExternalServiceError)
                and any(f"HTTP {code}" in str(exc) for code in RETRYABLE_HTTP_STATUSES)
            ),
            adapter="zenn",
        )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            json.dumps(
                dict(
                    adapter="zenn",
                    phase="done",
                    id=article_id,
                    url=result_url,
                    elapsed_ms=elapsed_ms,
                )
            )
        )

        return AdapterResult(
            status="published",
            adapter="zenn-github",
            platform="zenn",
            published_url=result_url,
        )
