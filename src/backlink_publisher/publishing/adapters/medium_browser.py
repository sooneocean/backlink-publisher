"""Medium browser fallback adapter using Playwright.

Cookies-only hard-cut (Plan 2026-05-19-005 Unit 1):
The adapter loads its credential from ``<config_dir>/medium-cookies.json``
(velog-style schema, written by ``bind-channel medium`` /
``medium-login``). Replaces the Plan 003 Unit 6 contract that read
``medium-storage-state.json`` via ``new_context(storage_state=...)``;
that file is no longer written and no longer read. Operators upgrading
across this PR must re-run ``medium-login`` (or ``bind-channel medium``)
once to repopulate the cookies file — the friendly DependencyError below
spells out exactly what to do.

Load path: ``new_context()`` (no storage_state) then
``context.add_cookies([...])`` with the apex-filtered cookies recipe
post_persist wrote. No localStorage / IndexedDB / origins are consumed —
Medium's auth state lives entirely in HttpOnly cookies (Spike 3a
verdict).

On ``/m/signin`` redirect during publish: writes ``mark_expired('medium')``
(channel_status_store, Plan 001 Unit 1) inside a try/except so filesystem
failure doesn't mask the auth error, then raises ``AuthExpiredError(
channel='medium', reason=...)``. The operator re-runs ``medium-login``.

On successful publish: refreshes ``medium-cookies.json`` via
``context.cookies('https://medium.com')`` (apex filter) with atomic
temp+rename so Medium's rotated session cookies stay fresh.

Always runs headed (Medium detects headless aggressively).
"""

from __future__ import annotations

import json
import os
import platform
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backlink_publisher.config import Config
from backlink_publisher._util.errors import (
    AuthExpiredError,
    DependencyError,
    ExternalServiceError,
)
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.config.loader import _config_dir
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .link_attr_verifier import required_link_urls, verify_link_attributes
from .retry import retry_transient_call
from . import _medium_selectors as sel

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:  # pragma: no cover — tested via DependencyError path
    sync_playwright = None  # type: ignore[assignment]
    PlaywrightTimeoutError = Exception  # type: ignore[assignment,misc]


def _json_log(**kwargs: Any) -> str:
    return json.dumps(kwargs)


def _screenshot_path(config: Config, article_id: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    shots_dir = config.screenshot_dir
    shots_dir.mkdir(parents=True, exist_ok=True)
    return shots_dir / f"{article_id}-{ts}.png"


def _paste_key() -> str:
    return "Meta+V" if platform.system() == "Darwin" else "Control+V"


def _cookies_path() -> Path:
    """Plan 2026-05-19-005 Unit 1: ``<config_dir>/medium-cookies.json``.

    Single source of truth for Medium browser credentials. Written by
    ``bind-channel medium`` recipe post_persist (or ``medium-login``);
    read by this adapter via ``context.add_cookies([...])``; refreshed
    on every successful publish to keep up with Medium's session cookie
    rotation.
    """
    return _config_dir() / "medium-cookies.json"


def _load_medium_cookies() -> list[dict[str, Any]]:
    """Read ``medium-cookies.json`` and return the cookies list.

    Raises ``DependencyError`` if (a) the file is missing — operator
    needs to run ``medium-login``; (b) the file is not mode 0600 —
    refuse to load creds with leaky perms; (c) JSON is malformed.
    All three errors carry the absolute path so the operator can fix
    them without ambiguity.
    """
    path = _cookies_path()
    if not path.exists():
        raise DependencyError(
            f"medium-cookies.json not found at {path}. Run `medium-login` "
            f"(or `bind-channel --channel medium`) to bind your Medium "
            f"session. Pre-Plan-005 storage_state.json is no longer read."
        )
    try:
        mode = path.stat().st_mode & 0o777
    except OSError as exc:
        raise DependencyError(
            f"medium-cookies.json at {path} is unreadable: {exc}"
        ) from exc
    if mode != 0o600:
        raise DependencyError(
            f"medium-cookies.json at {path} has mode {oct(mode)}, expected "
            f"0o600. Run `chmod 600 {path}` (or re-run `medium-login` "
            f"which will write 0600)."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DependencyError(
            f"medium-cookies.json at {path} is invalid: "
            f"{type(exc).__name__}: {exc}. Re-run `medium-login`."
        ) from exc
    cookies = payload.get("cookies", [])
    if not isinstance(cookies, list):
        raise DependencyError(
            f"medium-cookies.json at {path} has malformed 'cookies' field "
            f"(expected list, got {type(cookies).__name__}). Re-run "
            f"`medium-login`."
        )
    return cookies


def _safe_mark_expired() -> None:
    """Call ``mark_expired('medium')`` swallowing filesystem errors.

    The caller is about to raise ``AuthExpiredError``; we must not let a
    secondary filesystem failure (disk full, permission denied) mask the
    primary auth-expired signal. Logs a warning on failure so the failure
    isn't completely silent."""
    try:
        from webui_store.channel_status import mark_expired
        mark_expired("medium")
    except Exception as exc:  # noqa: BLE001 — defensive
        log.warn(
            f"medium_browser: mark_expired('medium') failed during auth-expired "
            f"propagation: {type(exc).__name__}: {exc}"
        )


def _refresh_cookies(context: Any) -> None:
    """Atomically refresh ``medium-cookies.json`` from the current Playwright
    context's cookies (Medium rotates session cookies during publish flows).
    Best-effort: failure here is logged but does NOT fail the publish —
    the credentials are merely slightly stale, not invalid."""
    target = _cookies_path()
    try:
        # Apex-only filter (matches recipe host filter — defense in depth).
        try:
            live_cookies = context.cookies("https://medium.com") or []
        except Exception:
            live_cookies = []
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".medium-cookies.",
            suffix=".tmp",
            dir=str(target.parent),
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            tmp_path.write_text(
                json.dumps({"cookies": live_cookies}, ensure_ascii=False),
                encoding="utf-8",
            )
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, target)
        except Exception:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            raise
    except Exception as exc:  # noqa: BLE001 — best-effort refresh
        log.warn(
            f"medium_browser: failed to refresh medium-cookies.json: "
            f"{type(exc).__name__}: {exc}"
        )


class MediumBrowserAdapter(Publisher):
    """Fallback: publish to Medium via headed Playwright browser session."""

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        if sync_playwright is None:
            raise DependencyError(
                "Playwright is not installed. Run: playwright install chromium"
            )

        article_id = payload.get("id", "")
        t0 = time.monotonic()
        log.info(_json_log(adapter="medium-browser", phase="start", id=article_id))

        # Plan 2026-05-19-005 Unit 1: cookies.json is the credential. Load
        # + validate (0600 + JSON shape) BEFORE launching Playwright so the
        # operator sees a fast actionable error rather than a 30s page-goto
        # timeout. _load_medium_cookies raises DependencyError on missing /
        # wrong-mode / malformed — DependencyError lets the dispatcher try
        # the next adapter in the chain (Brave/Browser), but for missing
        # cookies that's pointless (they'd hit the same file) so a CLI/log
        # message with the medium-login command is more actionable.
        cookies = _load_medium_cookies()

        # Plan 2026-05-18-006 Unit 5 R9: medium is platform-tier (b)
        # (browser-paste WYSIWYG sanitize is lossy) — helper renders MD even
        # when content_html present. Defense in depth: validate-time gate
        # in Unit 6 rejects content_html-only medium rows before publish.
        html_content = extract_publish_html(payload, "medium")
        title = payload.get("title", "")
        tags = payload.get("tags", [])[:5]

        def _run_browser_publish() -> AdapterResult:
            """One full browser publish attempt — opens and closes its own context."""
            with sync_playwright() as pw:
                # Plan 2026-05-19-005 Unit 1: non-persistent launch + cookies
                # injected via add_cookies. Playwright manages an ephemeral
                # profile dir internally and cleans up on browser.close().
                browser = pw.chromium.launch(
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context = browser.new_context()
                if cookies:
                    context.add_cookies(cookies)
                page = context.new_page()
                try:
                    context.grant_permissions(
                        ["clipboard-read", "clipboard-write"],
                        origin="https://medium.com",
                    )

                    log.info(_json_log(adapter="medium-browser", phase="open", id=article_id))
                    try:
                        page.goto("https://medium.com/new-story", timeout=30_000)
                    except PlaywrightTimeoutError:
                        # CAPTCHA timing race mitigation: if the page partially loaded with a
                        # CAPTCHA present, raise ExternalServiceError (non-retryable) rather than
                        # retrying into the same locked session.
                        try:
                            if page.locator(sel.CAPTCHA_IFRAME_SELECTOR).count() > 0:
                                raise ExternalServiceError(
                                    "Medium CAPTCHA detected after timeout. "
                                    "Solve it manually at medium.com, then retry."
                                )
                        except ExternalServiceError:
                            raise
                        except Exception:
                            pass  # probe failed; let retry handle the timeout
                        raise  # re-raise PlaywrightTimeoutError for retry_transient_call

                    # Plan 2026-05-19-005 Unit 1: detect login redirect;
                    # mark expired + raise AuthExpiredError. Operator must
                    # re-run medium-login (or bind-channel medium).
                    if sel.LOGIN_PATH in page.url:
                        _safe_mark_expired()
                        raise AuthExpiredError(
                            channel="medium",
                            reason=(
                                "redirected to /m/signin during publish; "
                                "cookies in medium-cookies.json are no "
                                "longer valid — re-run `medium-login`"
                            ),
                        )

                    # Detect CAPTCHA
                    if page.locator(sel.CAPTCHA_IFRAME_SELECTOR).count() > 0:
                        raise ExternalServiceError(
                            "Medium CAPTCHA detected. "
                            "Solve it manually at medium.com, then retry."
                        )

                    # Fill title
                    log.info(_json_log(adapter="medium-browser", phase="fill-title", id=article_id))
                    page.locator(sel.TITLE).click()
                    page.keyboard.type(title)

                    # Paste HTML body via clipboard
                    log.info(_json_log(adapter="medium-browser", phase="fill-body", id=article_id))
                    page.locator(sel.BODY).click()
                    page.evaluate(
                        "async (html) => { await navigator.clipboard.writeText(html); }",
                        html_content,
                    )
                    page.keyboard.press(_paste_key())
                    page.wait_for_timeout(1500)

                    # Publish or save draft
                    if mode == "publish":
                        log.info(_json_log(adapter="medium-browser", phase="publish", id=article_id))
                        page.locator(sel.PUBLISH_MENU).click()
                        page.wait_for_timeout(1000)
                        try:
                            tag_input = page.locator(sel.TAGS_INPUT)
                            for tag in tags:
                                tag_input.type(tag)
                                page.keyboard.press("Enter")
                                page.wait_for_timeout(300)
                        except Exception as e:
                            log.debug(f"tag insertion failed (optional): {e}")  # tags are optional
                        page.locator(sel.PUBLISH_BUTTON).click()
                        page.wait_for_timeout(3000)
                    else:
                        try:
                            page.locator(sel.SAVE_DRAFT).click()
                            page.wait_for_timeout(2000)
                        except Exception:
                            page.wait_for_timeout(3000)

                    final_url = page.url
                    elapsed = int((time.monotonic() - t0) * 1000)
                    log.info(
                        _json_log(
                            adapter="medium-browser",
                            phase="done",
                            id=article_id,
                            elapsed_ms=elapsed,
                        )
                    )

                    # Plan 2026-05-19-005 Unit 1: refresh medium-cookies.json
                    # from the current context (Medium rotates session cookies
                    # during publish). Best-effort: failure here is logged
                    # but does NOT fail the publish — cookies are merely
                    # slightly stale, not invalid.
                    _refresh_cookies(context)

                    context.close()
                    browser.close()

                    if mode == "publish":
                        meta: dict = {}
                        if final_url:
                            attr_check = verify_link_attributes(
                                final_url, target_urls=required_link_urls(payload)
                            )
                            meta["link_attr_verification"] = attr_check
                            ratio = attr_check.get("blank_ratio", 1.0)
                            total = attr_check.get("total_anchors", 0)
                            if attr_check.get("verification") == "ok" and total > 0 and ratio < 0.5:
                                log.warn(
                                    f"Medium stripped target attributes: "
                                    f"{attr_check['blank_anchors']}/{total} anchors "
                                    "retain target=_blank"
                                )
                        return AdapterResult(
                            status="published",
                            adapter="medium-browser",
                            platform="medium",
                            published_url=final_url,
                            post_publish_delay_seconds=30,
                            _provider_meta=meta if meta else None,
                        )
                    return AdapterResult(
                        status="drafted",
                        adapter="medium-browser",
                        platform="medium",
                        draft_url=final_url,
                        post_publish_delay_seconds=30,
                    )

                except AuthExpiredError:
                    _save_screenshot(page, config, article_id)
                    context.close()
                    browser.close()
                    raise
                except ExternalServiceError:
                    _save_screenshot(page, config, article_id)
                    context.close()
                    browser.close()
                    raise
                except PlaywrightTimeoutError:
                    # Let PlaywrightTimeoutError propagate to retry_transient_call
                    # without wrapping as ExternalServiceError.
                    _save_screenshot(page, config, article_id)
                    context.close()
                    browser.close()
                    raise
                except Exception as exc:
                    _save_screenshot(page, config, article_id)
                    context.close()
                    browser.close()
                    raise ExternalServiceError(
                        f"Medium browser automation failed: {exc}"
                    ) from exc

        return retry_transient_call(
            _run_browser_publish,
            is_retryable=lambda exc: isinstance(exc, PlaywrightTimeoutError),
            adapter="medium-browser",
        )


def _save_screenshot(page: Any, config: Config, article_id: str) -> None:
    try:
        shot_path = _screenshot_path(config, article_id)
        page.screenshot(path=str(shot_path))
        import sys
        import json
        print(
            json.dumps({"level": "ERROR", "screenshot": str(shot_path)}),
            file=sys.stderr,
        )
    except Exception:
        pass
