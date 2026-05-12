"""Medium browser fallback adapter using Playwright.

Used when no Medium Integration Token is available.
Reuses a persistent Chrome profile to keep the user logged in.
Always runs headed (Medium detects headless aggressively).
"""

from __future__ import annotations

import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Config
from ..errors import DependencyError, ExternalServiceError
from ..logger import opencli_logger as log
from ..markdown_utils import render_to_html
from .base import AdapterResult
from .retry import retry_transient_call
from . import _medium_selectors as sel

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:  # pragma: no cover — tested via DependencyError path
    sync_playwright = None  # type: ignore[assignment]
    PlaywrightTimeoutError = Exception  # type: ignore[assignment,misc]


def _json_log(**kwargs: Any) -> str:
    import json
    return json.dumps(kwargs)


def _screenshot_path(config: Config, article_id: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    shots_dir = config.screenshot_dir
    shots_dir.mkdir(parents=True, exist_ok=True)
    return shots_dir / f"{article_id}-{ts}.png"


def _paste_key() -> str:
    return "Meta+V" if platform.system() == "Darwin" else "Control+V"


class MediumBrowserAdapter:
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

        user_data_dir = config.medium_user_data_dir or (
            config.config_dir / "chrome-profile-default"
        )
        user_data_dir.mkdir(parents=True, exist_ok=True)

        html_content = render_to_html(payload.get("content_markdown", ""))
        title = payload.get("title", "")
        tags = payload.get("tags", [])[:5]

        def _run_browser_publish() -> AdapterResult:
            """One full browser publish attempt — opens and closes its own context."""
            with sync_playwright() as pw:
                context = pw.chromium.launch_persistent_context(
                    str(user_data_dir),
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled"],
                )
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

                    # Detect login redirect
                    if sel.LOGIN_PATH in page.url:
                        raise ExternalServiceError(
                            "Medium login expired. "
                            "Please log in to Medium in your Chrome profile and retry. "
                            f"Profile: {user_data_dir}"
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
                        except Exception:
                            pass  # tags are optional
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

                    context.close()

                    if mode == "publish":
                        return AdapterResult(
                            status="published",
                            adapter="medium-browser",
                            platform="medium",
                            published_url=final_url,
                        )
                    return AdapterResult(
                        status="drafted",
                        adapter="medium-browser",
                        platform="medium",
                        draft_url=final_url,
                    )

                except ExternalServiceError:
                    _save_screenshot(page, config, article_id)
                    context.close()
                    raise
                except PlaywrightTimeoutError:
                    # Let PlaywrightTimeoutError propagate to retry_transient_call
                    # without wrapping as ExternalServiceError.
                    _save_screenshot(page, config, article_id)
                    context.close()
                    raise
                except Exception as exc:
                    _save_screenshot(page, config, article_id)
                    context.close()
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
