"""GitLab Pages adapter — commits static HTML via the Repository Files API.

Plan 2026-06-01-007 Unit 3 (Wave 1 dofollow channels). Sibling of the GitHub
Pages adapter (``ghpages.py``), reusing its ``_slugify`` helper, but the
publish model diverges on four verified axes (GitLab docs, 2026):

  1. **Auth** — ``PRIVATE-TOKEN: <pat>`` header (NOT ``Authorization: Bearer``);
     PAT scope ``api`` (or a project-scoped write_repository).
  2. **Create vs update** — ``POST /projects/:id/repository/files/:path`` creates,
     ``PUT`` updates. Duplicate-create → HTTP 400 + body "A file with this name
     already exists". Idempotency = POST → on-400-marker → PUT. A byte-identical
     re-publish PUT also 400s (no-op commit) — treated as success/skip.
  3. **No auto-build** — GitLab Pages serves nothing without a CI job named
     ``pages`` emitting a ``public/`` artifact (no auto-Jekyll). v1 commits
     pre-rendered static HTML straight to ``public/<slug>/index.html``. The
     target project MUST already have a ``pages`` job — the adapter does not
     create ``.gitlab-ci.yml``.
  4. **Async publish** — the API write returns the instant the commit lands;
     the URL goes live only after the ``pages`` pipeline finishes. The returned
     ``published_url`` is therefore *predicted*, not confirmed-200.

DOFOLLOW NOTICE:

    The rel is operator-controlled (GitLab Pages serves our own HTML verbatim,
    no nofollow injection). But the adapter ships ``dofollow="uncertain"``
    because *.gitlab.io indexation is only "partial" (2026-06-01 discovery),
    publish is async, and a shared free subdomain carries search-trust risk.
    An OUR-post canary confirming the served page is index,follow gates the
    flip to ``dofollow=True``.
"""

from __future__ import annotations

import base64
import html
import json
import os
import stat
import time
from datetime import datetime, timezone
from urllib.parse import quote
from typing import Any

from backlink_publisher.http import post as http_post, put as http_put

from backlink_publisher.config import Config, load_gitlabpages_token
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .ghpages import _slugify
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call


GITLAB_API = "https://gitlab.com/api/v4"
_HTTP_TIMEOUT_S = 30
_ALREADY_EXISTS = "already exists"  # GitLab 400 marker (create-on-existing AND no-op identical commit)


def _utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _required_headers(token: str) -> dict[str, str]:
    """GitLab's required headers — PRIVATE-TOKEN (NOT Authorization: Bearer)."""
    return {
        "PRIVATE-TOKEN": token,
        "Content-Type": "application/json",
    }


def _require_secure_mode(path) -> None:
    """R10: refuse a group/world-readable token file (mirrors telegraph/livejournal).

    Tokens are written 0o600 by safe-write, but an operator-hand-created file may
    land 0o644 (world-readable secret). Fail loud rather than load it silently.
    """
    if path.exists():
        mode = os.stat(path).st_mode & 0o777
        if mode != 0o600:
            raise DependencyError(
                f"GitLab token file {path} has mode {oct(mode)}; must be 0o600. "
                f"Run: chmod 600 {path}"
            )


def _load_token(config: Config) -> str:
    """Return the PAT, raising DependencyError when not configured.

    The message names the ``pages`` CI-job precondition loudly — committing a
    file does nothing without it.
    """
    _require_secure_mode(config.gitlabpages_token_path)
    data = load_gitlabpages_token(config.gitlabpages_token_path)
    token = (data or {}).get("token", "").strip()
    if not token:
        raise DependencyError(
            "GitLab Pages PAT not configured. "
            f"Write {{\"token\": \"<pat>\"}} to {config.gitlabpages_token_path} "
            "(chmod 600). PAT needs `api` scope. NOTE: the target project must "
            "already have a `pages` CI job emitting public/ — the adapter does "
            "not create .gitlab-ci.yml, and committing a file does not publish "
            "without it."
        )
    return token


def _build_html_body(payload: dict[str, Any]) -> str:
    """Compose a minimal static HTML document for the public/ tree.

    GitLab Pages serves files verbatim (no Jekyll), so we emit a complete HTML
    page rather than Markdown front-matter. The body is the negotiated HTML.
    """
    title = payload.get("title", "Untitled")
    body_html = extract_publish_html(payload, "gitlabpages") or ""
    # Escape the title text node — an unescaped "<" or "</title>" in the title
    # would break the served page (and is an injection vector). body_html is the
    # already-negotiated HTML island and is intentionally NOT escaped.
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        f"<title>{html.escape(title)}</title>\n"
        "</head>\n<body>\n"
        f"{body_html}\n"
        "</body>\n</html>\n"
    )


def _files_api_url(project: str, file_path: str) -> str:
    """Build the Repository Files API URL with %2F-encoded :id and :file_path."""
    proj = quote(project, safe="")
    path = quote(file_path, safe="")
    return f"{GITLAB_API}/projects/{proj}/repository/files/{path}"


def _published_url(cfg, file_path: str) -> str:
    """Public Pages URL for a file committed under public/.

    Three cases:
      (a) ``pages_base_url`` override set (unique-domain) → use it.
      (b) project named ``<namespace>.gitlab.io`` → served at the domain root.
      (c) default → ``https://<namespace>.gitlab.io/<project-path>``.

    The in-site path strips the leading ``public/`` and collapses a trailing
    ``index.html`` to a directory URL.
    """
    in_site = file_path
    if in_site.startswith("public/"):
        in_site = in_site[len("public/"):]
    if in_site.endswith("/index.html"):
        in_site = in_site[: -len("index.html")]
    elif in_site == "index.html":
        in_site = ""
    in_site = in_site.lstrip("/")

    if cfg.pages_base_url:
        base = cfg.pages_base_url.rstrip("/")
        return f"{base}/{in_site}" if in_site else f"{base}/"

    parts = [p for p in cfg.project.split("/") if p]
    namespace = parts[0] if parts else ""
    project_name = parts[-1] if parts else ""
    rest_path = "/".join(parts[1:])  # subgroup/project path after the namespace

    if project_name == f"{namespace}.gitlab.io":
        base = f"https://{namespace}.gitlab.io"
        return f"{base}/{in_site}" if in_site else f"{base}/"

    base = f"https://{namespace}.gitlab.io/{rest_path}" if rest_path else f"https://{namespace}.gitlab.io"
    return f"{base}/{in_site}" if in_site else f"{base}/"


class GitLabPagesAPIAdapter(Publisher):
    """Commits static HTML to a GitLab Pages project via the Repository Files API.

    dofollow="uncertain" pending an OUR-post index,follow canary — see docstring.
    """

    @classmethod
    def available(cls, config: Config) -> bool:
        """True when [gitlabpages] config has a project AND the token file exists."""
        cfg = config.gitlabpages
        if cfg is None or not cfg.project:
            return False
        data = load_gitlabpages_token(config.gitlabpages_token_path)
        return bool(data and (data.get("token") or "").strip())

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="gitlabpages", phase="start", id=article_id)))

        cfg = config.gitlabpages
        if cfg is None or not cfg.project:
            raise DependencyError(
                "GitLab Pages config missing. Add [gitlabpages] project=\"namespace/name\" "
                "to config.toml. The project must have a `pages` CI job emitting public/."
            )
        token = _load_token(config)

        slug = _slugify(payload.get("slug") or payload.get("title", ""))
        file_path = cfg.path_template.format(date=_utc_date(), slug=slug)
        html = _build_html_body(payload)
        content_b64 = base64.b64encode(html.encode("utf-8")).decode("ascii")
        commit_message = f"backlink-publisher: add post {slug}"
        url = _files_api_url(cfg.project, file_path)

        if mode == "draft":
            log.info(json.dumps(dict(
                adapter="gitlabpages", phase="draft-skip", id=article_id,
                file_path=file_path,
            )))
            return AdapterResult(
                status="drafted",
                adapter="gitlabpages",
                platform="gitlabpages",
                draft_url=_published_url(cfg, file_path),
            )

        body = {
            "branch": cfg.branch,
            "content": content_b64,
            "encoding": "base64",
            "commit_message": commit_message,
        }

        def _handle_status(resp, verb: str):
            """Map non-2xx to typed errors. Returns True on success, False to fall through."""
            if resp.status_code in (200, 201):
                return True
            if resp.status_code == 401:
                raise ExternalServiceError(
                    "GitLab PAT rejected (HTTP 401) — regenerate with `api` scope "
                    "and re-save to gitlabpages-token.json"
                )
            if resp.status_code == 403:
                retry_after = resp.headers.get("retry-after")
                suffix = f" (retry-after={retry_after}s)" if retry_after else ""
                raise ExternalServiceError(
                    f"GitLab {verb} forbidden (HTTP 403){suffix} — token missing "
                    "scope or hit a secondary rate limit"
                )
            return False  # caller decides (400 markers / generic)

        def execute():
            # POST creates; on 400 "already exists" → PUT updates (idempotent overwrite).
            resp = http_post(
                url, headers=_required_headers(token), json=body, timeout=_HTTP_TIMEOUT_S,
            )
            if _handle_status(resp, "POST"):
                return _published_url(cfg, file_path)
            if resp.status_code == 400 and _ALREADY_EXISTS in resp.text.lower():
                put_resp = http_put(
                    url, headers=_required_headers(token), json=body, timeout=_HTTP_TIMEOUT_S,
                )
                if _handle_status(put_resp, "PUT"):
                    return _published_url(cfg, file_path)
                # Byte-identical re-publish: GitLab 400s the no-op commit with an
                # "already exists" marker. Only THAT 400 is an idempotent skip — any
                # other 400 (permission, bad path, locked file) is a real failure.
                if put_resp.status_code == 400 and _ALREADY_EXISTS in put_resp.text.lower():
                    log.info(json.dumps(dict(
                        adapter="gitlabpages", phase="noop-skip", id=article_id,
                    )))
                    return _published_url(cfg, file_path)
                raise ExternalServiceError(
                    f"GitLab PUT contents returned HTTP {put_resp.status_code}: "
                    f"{put_resp.text[:200]}"
                )
            raise ExternalServiceError(
                f"GitLab POST contents returned HTTP {resp.status_code}: {resp.text[:200]}"
            )

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
                adapter="gitlabpages",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"GitLab Pages publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="gitlabpages", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            status="published",
            adapter="gitlabpages",
            platform="gitlabpages",
            published_url=published_url,
        )
