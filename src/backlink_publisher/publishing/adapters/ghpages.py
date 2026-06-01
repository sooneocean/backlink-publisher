"""GitHub Pages adapter — Contents API (no git push) + Bearer PAT.

Plan 2026-05-19-006 Unit 7 (originally Unit 12, promoted Q-A resolution).
Highest SEO value of the Phase 3 wave: dofollow confirmed (Jekyll default),
DA 100, operator owns the repo so no de-platforming risk.

Design choices:

  - **Contents API, not git push** — keeps the runtime dependency surface to
    ``requests`` only (no git sub-process, no SSH key management). Authoring
    a post is one ``PUT /repos/{owner}/{repo}/contents/{path}`` call.
  - **Bearer PAT** (Authorization header) — matches the auth model of the
    other Phase 3 platforms (Hashnode, Write.as). The token lives in
    ``~/.config/backlink-publisher/ghpages-token.json`` (0600 file, never in
    ``config.toml``) per SEC-3.
  - **Update path** — if the target path already exists, the API returns 422
    "sha required". The adapter handles this by ``GET``ing the file's sha
    then re-``PUT``ing with the sha. v1 ships idempotent overwrite (operator
    re-publishing the same slug). Not aimed at multi-author conflict
    resolution — that's a Pages-repo policy question, not the adapter's.
  - **Secondary rate limit** — GitHub returns 403 with a
    ``x-ratelimit-remaining: 0`` or ``retry-after`` header on secondary
    (per-route) limits. The adapter splits 401 (auth-fixable) from 403
    (rate-limit / scope) so live verify never falsely flags a tokens as
    expired when the operator is just publishing too fast.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from backlink_publisher.http import get as http_get, put as http_put

from backlink_publisher.config import Config, load_ghpages_token
from backlink_publisher._util.errors import (
    BannerUploadError,
    DependencyError,
    ExternalServiceError,
)
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call


GITHUB_API = "https://api.github.com"
_GITHUB_API_VERSION = "2022-11-28"
_HTTP_TIMEOUT_S = 30  # generous — Contents API can be slow for large repos


def _required_headers(token: str) -> dict[str, str]:
    """The header trio GitHub mandates for stable API access."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
    }


def _load_token(config: Config) -> str:
    """Return the PAT, raising DependencyError when not configured.

    Mirrors the telegraph/blogger pattern — fail loud at adapter entry rather
    than returning ``None`` and bumping the failure deeper into publish path.
    """
    data = load_ghpages_token(config.ghpages_token_path)
    token = (data or {}).get("token")
    if not token:
        raise DependencyError(
            "GitHub Pages PAT not configured. "
            f"Write {{\"token\": \"<pat>\"}} to {config.ghpages_token_path} "
            "(chmod 600). PAT needs Contents:Read+Write on the target repo."
        )
    return token


def _slugify(value: str) -> str:
    """Lowercase, ASCII-only slug suitable for both Jekyll filenames and URLs.

    Keep it deliberately minimal — Pages cares about three things:
      1. No spaces (breaks URL routing)
      2. No path separators (would create unintended sub-paths)
      3. No leading dot (would hide the file from ``jekyll build``)
    """
    cleaned = []
    last_dash = False
    for ch in value.lower():
        if ch.isalnum():
            cleaned.append(ch)
            last_dash = False
        elif not last_dash:
            cleaned.append("-")
            last_dash = True
    slug = "".join(cleaned).strip("-.")
    return slug or "post"


def _render_target_path(template: str, *, slug: str, date_iso: str | None = None) -> str:
    """Resolve ``{date}`` / ``{slug}`` placeholders. UTC-only dates."""
    if date_iso is None:
        date_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return template.format(date=date_iso, slug=slug)


def _build_markdown_body(payload: dict[str, Any]) -> str:
    """Compose a Jekyll-compatible post body with YAML front matter.

    Front matter carries ``title`` + ``date`` (UTC) + ``tags``. The body is
    the Markdown source if present; otherwise the rendered HTML from
    ``extract_publish_html`` (works as an HTML island inside a Markdown post —
    Jekyll passes HTML through unchanged).
    """
    title = payload.get("title", "Untitled")
    tags = payload.get("tags", [])[:20]
    date_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S +0000")

    front_matter_lines = [
        "---",
        "layout: post",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"date: {date_iso}",
    ]
    if tags:
        # YAML flow-style list — safe for any string content via json.dumps.
        front_matter_lines.append(
            "tags: [" + ", ".join(json.dumps(t, ensure_ascii=False) for t in tags) + "]"
        )
    # Mixed canonical (Plan 003 R2): emit Jekyll-compatible ``canonical_url:``
    # only when payload carries a non-empty schema-validated URL. ``json.dumps``
    # gives us a YAML-safe quoted scalar even though the regex already
    # rejected newlines / quotes / control chars at schema time.
    canonical = payload.get("seo", {}).get("canonical_url") or None
    if canonical:
        front_matter_lines.append(
            f"canonical_url: {json.dumps(canonical, ensure_ascii=False)}"
        )
    front_matter_lines.append("---")
    front_matter = "\n".join(front_matter_lines)

    body = payload.get("content_markdown") or extract_publish_html(payload, "ghpages")
    return f"{front_matter}\n\n{body}\n"


def _get_existing_sha(repo: str, branch: str, path: str, token: str) -> str | None:
    """Return the file's current sha, or None if it doesn't exist yet.

    Used only on the 422 retry path. 404 here is the *happy* outcome
    (file is new); any other error is propagated so the caller can decide.
    """
    resp = http_get(
        f"{GITHUB_API}/repos/{repo}/contents/{path}",
        headers=_required_headers(token),
        params={"ref": branch},
        timeout=_HTTP_TIMEOUT_S,
    )
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise ExternalServiceError(
            f"GitHub GET contents returned HTTP {resp.status_code} — unexpected response"
        )
    body = resp.json()
    return body.get("sha")


def _put_contents(
    repo: str,
    branch: str,
    path: str,
    markdown: str,
    commit_message: str,
    token: str,
    sha: str | None = None,
) -> dict[str, Any]:
    """PUT the file. Returns the API response body on success.

    422 → caller's responsibility to fetch sha + retry once. We do NOT loop
    here to keep the failure mode auditable in the publish() function.
    """
    body: dict[str, Any] = {
        "message": commit_message,
        "content": base64.b64encode(markdown.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha is not None:
        body["sha"] = sha

    resp = http_put(
        f"{GITHUB_API}/repos/{repo}/contents/{path}",
        headers=_required_headers(token),
        json=body,
        timeout=_HTTP_TIMEOUT_S,
    )
    if resp.status_code in (200, 201):
        return resp.json()
    if resp.status_code == 401:
        raise ExternalServiceError(
            "GitHub PAT rejected (HTTP 401) — re-bind with Contents:Read+Write scope"
        )
    if resp.status_code == 403:
        # Could be scope OR secondary rate limit. Surface both for ops triage.
        retry_after = resp.headers.get("retry-after")
        suffix = f" (retry-after={retry_after}s)" if retry_after else ""
        raise ExternalServiceError(
            f"GitHub PUT forbidden (HTTP 403){suffix} — token missing scope or rate-limited"
        )
    if resp.status_code == 422:
        # Surface a typed signal so publish() knows to fetch sha + retry.
        raise _ShaRequired(None)
    raise ExternalServiceError(
        f"GitHub PUT contents returned HTTP {resp.status_code} — unexpected response"
    )


class _ShaRequired(Exception):
    """Internal sentinel — 422 from PUT contents means the file exists."""


_JEKYLL_POST_RE = __import__("re").compile(
    r"^_posts/(\d{4})-(\d{2})-(\d{2})-(.+?)\.(?:md|markdown)$"
)


def _published_url(repo: str, path: str) -> str:
    """Public URL for the committed post.

    ``_posts/YYYY-MM-DD-slug.md`` is processed by Jekyll and served at
    ``/YYYY/MM/DD/slug.html`` (default permalink ``/:year/:month/:day/:title``).
    Verified live: HTTP 200 at that shape; the raw ``_posts/`` path returns 404.

    For non-``_posts`` paths (custom layout or ``.nojekyll`` repo) the raw
    file path is used verbatim — Pages serves those at face value.
    """
    owner, _, name = repo.partition("/")
    m = _JEKYLL_POST_RE.match(path)
    if m:
        year, month, day, slug = m.group(1), m.group(2), m.group(3), m.group(4)
        url_path = f"{year}/{month}/{day}/{slug}.html"
    else:
        url_path = path
    return f"https://{owner}.github.io/{name}/{url_path}"


def _banner_raw_url(repo: str, branch: str, target_path: str) -> str:
    """Compose the public ``raw.githubusercontent.com`` URL for a committed
    banner file.  Used by both the upload path and the idempotent-skip
    branch so the two cases return byte-identical URLs."""
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{target_path}"


def _put_binary_contents(
    repo: str,
    branch: str,
    path: str,
    data: bytes,
    commit_message: str,
    token: str,
    sha: str | None = None,
) -> dict[str, Any]:
    """PUT raw bytes via the Contents API.

    Distinct from :func:`_put_contents` (which is markdown-text-only and
    re-encodes via ``markdown.encode("utf-8")``).  Banner uploads are
    binary — PNG / WebP / JPEG — and must NOT be passed through
    ``.encode("utf-8")``.  The Contents API itself is content-type
    agnostic; what matters is that the ``content`` field carries a
    pure base64 of the raw bytes.

    422 surfaces as the same ``_ShaRequired`` sentinel as the text
    path, but the banner caller does NOT retry with sha — the
    content-addressed file path means a 422 is a sha collision under
    different content, which is a genuine error worth surfacing.
    """
    body: dict[str, Any] = {
        "message": commit_message,
        "content": base64.b64encode(data).decode("ascii"),
        "branch": branch,
    }
    if sha is not None:
        body["sha"] = sha

    resp = http_put(
        f"{GITHUB_API}/repos/{repo}/contents/{path}",
        headers=_required_headers(token),
        json=body,
        timeout=_HTTP_TIMEOUT_S,
    )
    if resp.status_code in (200, 201):
        return resp.json()
    if resp.status_code == 422:
        raise _ShaRequired(None)
    raise BannerUploadError(
        f"ghpages banner PUT returned HTTP {resp.status_code} — unexpected response"
    )


class GitHubPagesAPIAdapter(Publisher):
    """Publishes Markdown to a Pages-enabled repo via the Contents API."""

    def embed_banner(self, artifact_path: Path, alt: str) -> str | None:
        """Commit the banner bytes to the operator's Pages repo at
        ``assets/banners/<sha>.<ext>`` and return the
        ``raw.githubusercontent.com`` URL.

        Plan 2026-05-20-004 Unit 6.  Reuses the existing GitHub
        Contents API path that ghpages uses for post commits: same
        auth dialect (``Bearer <pat>``), same API version header,
        same idempotency model (probe via GET, skip PUT on 200).

        Why ``raw.githubusercontent.com`` and NOT ``<owner>.github.io``:
        Pages serves built artifacts (i.e., files that Jekyll renders),
        but raw assets under ``assets/banners/`` are also served by
        ``raw.githubusercontent.com`` without any build delay or
        Jekyll permalink rewriting.  The raw URL is also cache-
        invalidated by sha — every banner with a new content sha gets
        a fresh URL, no CDN purge needed.

        Content-addressed path: ``assets/banners/<sha16>.<ext>`` where
        ``sha16`` is the first 16 hex chars of sha256(bytes).  16 chars
        ≈ 2^64 entropy — far more than enough to distinguish banner
        contents.  Same content → same path → idempotent probe.

        Lazy config load: the dispatcher contract is
        ``embed_banner(self, artifact_path, alt)`` — no ``config``
        argument.  We call ``load_config()`` inside the method so the
        adapter can resolve repo / branch / token.  This is the same
        config the publish path uses; if the operator runs
        ``embed_banner`` outside the publish loop (e.g., a future
        CLI), it picks up the same configuration honoring
        ``BACKLINK_PUBLISHER_CONFIG_DIR``.

        Raises ``BannerUploadError`` on any failure (missing config,
        missing token, file-read OSError, network error, HTTP 4xx/5xx,
        sha collision under different content).  Channel-status
        ``mark_expired`` must NOT fire on a banner-upload 401 — that's
        the publish path's job — so we never raise ``AuthExpiredError``
        from here.
        """
        del alt  # consumed by dispatcher's ![alt](url) prepend

        # Lazy import keeps the registry init lean — banner work is opt-in.
        from backlink_publisher.config.loader import load_config

        try:
            config = load_config()
        except DependencyError as exc:
            raise BannerUploadError(
                f"ghpages banner: config load failed: {exc.message}"
            ) from exc

        gh_cfg = config.ghpages
        if gh_cfg is None or not gh_cfg.repo:
            # Dispatcher only invokes embed_banner when the adapter chain
            # selected this adapter (so available() returned True), but a
            # race between available() check and embed_banner call could
            # in theory invalidate the config.  Defensive.
            raise BannerUploadError(
                "ghpages banner: [ghpages] config missing or empty repo"
            )

        try:
            token = _load_token(config)
        except DependencyError as exc:
            raise BannerUploadError(
                f"ghpages banner: token unavailable: {exc.message}"
            ) from exc

        try:
            data = artifact_path.read_bytes()
        except OSError as exc:
            raise BannerUploadError(
                f"ghpages banner read failed: {artifact_path}: {exc}"
            ) from exc

        sha16 = hashlib.sha256(data).hexdigest()[:16]
        # Preserve the file extension so the raw URL has the right
        # content-type when GitHub serves it.  Default to .png for
        # extension-less files (matches the telegraph/blogger fallback).
        ext = artifact_path.suffix or ".png"
        target_path = f"assets/banners/{sha16}{ext}"

        # Idempotent probe: if the file already exists at this exact
        # content sha, skip the PUT entirely.  GitHub's GET contents
        # endpoint returns the file's git sha (NOT our content sha16) —
        # we only need to know "does this path exist".
        try:
            existing_sha = _get_existing_sha(
                gh_cfg.repo, gh_cfg.branch, target_path, token
            )
        except ExternalServiceError as exc:
            raise BannerUploadError(
                f"ghpages banner probe failed: {exc.message}"
            ) from exc
        except requests.RequestException as exc:
            raise BannerUploadError(
                f"ghpages banner probe network: {exc}"
            ) from exc

        if existing_sha is not None:
            # Same content sha → same bytes (sha256 is preimage-resistant).
            # Skip the PUT to avoid an empty commit on GitHub's side.
            return _banner_raw_url(gh_cfg.repo, gh_cfg.branch, target_path)

        commit_message = f"backlink-publisher: add banner {sha16}{ext}"
        try:
            _put_binary_contents(
                gh_cfg.repo, gh_cfg.branch, target_path, data,
                commit_message, token,
            )
        except _ShaRequired:
            # The probe said "no file" but PUT said "file exists" — must
            # be an eventual-consistency window between the GET and PUT.
            # Surface as a banner error; operator can retry the row.
            raise BannerUploadError(
                f"ghpages banner: 422 sha-required after probe found none "
                f"at {target_path} — eventual-consistency race; retry the row"
            )
        except requests.RequestException as exc:
            raise BannerUploadError(
                f"ghpages banner network: {exc}"
            ) from exc

        return _banner_raw_url(gh_cfg.repo, gh_cfg.branch, target_path)

    @classmethod
    def available(cls, config: Config) -> bool:
        # Match the BloggerAPIAdapter / TelegraphAPIAdapter pattern — config
        # presence check, not auth check. Real auth verification happens at
        # publish() time (or live verify).
        return config.ghpages is not None and bool(config.ghpages.repo)

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(
            json.dumps(dict(adapter="ghpages", phase="start", id=article_id))
        )

        gh_cfg = config.ghpages
        if gh_cfg is None or not gh_cfg.repo:
            raise DependencyError(
                "GitHub Pages config missing. Add [ghpages] repo=\"owner/name\" "
                "to config.toml."
            )

        token = _load_token(config)

        # mode='draft' has no clean GitHub Pages analogue (Pages publishes
        # everything in the branch). Operators who want drafts should use a
        # ``_drafts/`` directory + jekyll's ``--drafts`` flag, but that's a
        # site-side config. v1 treats draft mode as "build the body, do not
        # PUT" — useful for dry-run-via-mode-flag without requiring the
        # full dry_run_intercept harness.
        slug = _slugify(payload.get("slug") or payload.get("title", ""))
        target_path = _render_target_path(gh_cfg.path_template, slug=slug)
        markdown = _build_markdown_body(payload)
        commit_message = f"backlink-publisher: add post {slug}"

        if mode == "draft":
            log.info(
                json.dumps(dict(
                    adapter="ghpages", phase="draft-skip", id=article_id,
                    target_path=target_path,
                ))
            )
            return AdapterResult(
                status="drafted",
                adapter="ghpages",
                platform="ghpages",
                draft_url=_published_url(gh_cfg.repo, target_path),
            )

        def _attempt(sha: str | None = None):
            return _put_contents(
                gh_cfg.repo, gh_cfg.branch, target_path, markdown,
                commit_message, token, sha=sha,
            )

        # First attempt assumes the file is new. On 422 we fetch the existing
        # sha and retry once. Beyond that we fail loud — three writes to the
        # same path inside one publish call is almost certainly a bug.
        def execute():
            try:
                return _attempt(sha=None)
            except _ShaRequired:
                existing_sha = _get_existing_sha(
                    gh_cfg.repo, gh_cfg.branch, target_path, token
                )
                if existing_sha is None:
                    raise ExternalServiceError(
                        "GitHub PUT returned 422 sha-required but GET found no file — "
                        "either an eventual-consistency race or a repo config drift; "
                        "retry once manually."
                    )
                return _attempt(sha=existing_sha)

        try:
            retry_transient_call(
                execute,
                is_retryable=lambda exc: (
                    isinstance(exc, ExternalServiceError)
                    and any(
                        f"HTTP {code}" in str(exc) for code in RETRYABLE_HTTP_STATUSES
                    )
                ),
                adapter="ghpages",
            )
        except DependencyError:
            raise
        except ExternalServiceError:
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"GitHub Pages publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(
            json.dumps(dict(
                adapter="ghpages", phase="done", id=article_id,
                elapsed_ms=elapsed,
            ))
        )

        # The Contents API echoes back ``content.html_url`` (a github.com link
        # to the source file) — not the Pages-published URL. We surface our
        # computed Pages URL as the canonical published URL, but also stash
        # the source link in adapter_meta for ops debugging.
        published = _published_url(gh_cfg.repo, target_path)
        return AdapterResult(
            status="published",
            adapter="ghpages",
            platform="ghpages",
            published_url=published,
        )
