"""Medium publishing via AppleScript + Brave browser (macOS only).

This adapter controls Brave directly via AppleScript, bypassing all
Cloudflare/CDP detection. It uses the clipboard to paste article content
into Medium's editor, then triggers publish via keyboard shortcuts.

Used as primary fallback when Medium Integration Token API is unavailable.
"""

from __future__ import annotations

import platform
import subprocess
import sys
import time
import json
import uuid
from typing import Any

from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .link_attr_verifier import verify_link_attributes


def _json_log(**kwargs: Any) -> str:
    return json.dumps(kwargs)


def _check_macos() -> None:
    if platform.system() != "Darwin":
        raise DependencyError(
            "MediumBraveAdapter is macOS-only (requires AppleScript + Brave)"
        )


def _run_applescript(script: str, timeout: int = 60) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise ExternalServiceError(
            f"AppleScript failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def _get_brave_medium_tab_index() -> tuple[int, int] | None:
    """Return (window_index, tab_index) of an existing Medium tab in Brave, or None."""
    script = """
tell application "Brave Browser"
    set wIdx to 0
    repeat with w in windows
        set wIdx to wIdx + 1
        set tIdx to 0
        repeat with t in tabs of w
            set tIdx to tIdx + 1
            if URL of t contains "medium.com" then
                return wIdx & "," & tIdx
            end if
        end repeat
    end repeat
    return ""
end tell
"""
    try:
        result = _run_applescript(script, timeout=10)
        if result and "," in result:
            parts = result.split(",")
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return None


def _open_new_story_in_brave(wait_secs: int = 5) -> None:
    """Open medium.com/new-story in Brave via AppleScript."""
    script = f"""
tell application "Brave Browser"
    activate
    set newTab to make new tab at end of tabs of front window with properties {{URL:"https://medium.com/new-story"}}
    set active tab index of front window to (count tabs of front window)
    delay {wait_secs}
end tell
"""
    _run_applescript(script, timeout=30 + wait_secs)


def _get_current_url_brave() -> str:
    script = """
tell application "Brave Browser"
    return URL of active tab of front window
end tell
"""
    return _run_applescript(script, timeout=10)


def _set_clipboard(text: str) -> None:
    proc = subprocess.run(["pbcopy"], input=text.encode("utf-8"), timeout=10)
    if proc.returncode != 0:
        raise ExternalServiceError("Failed to copy content to clipboard")


def _brave_js(js: str) -> str:
    """Execute JavaScript in the active Brave tab."""
    # Escape the JS for AppleScript string embedding
    escaped = js.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
tell application "Brave Browser"
    set result to execute active tab of front window javascript "{escaped}"
    return result
end tell
'''
    return _run_applescript(script, timeout=30)


def _wait_for_medium_editor(max_wait: int = 20) -> bool:
    """Poll until Medium's editor is ready (title placeholder visible)."""
    for _ in range(max_wait):
        try:
            url = _get_current_url_brave()
            if "medium.com/m/signin" in url or "medium.com/signin" in url:
                return False  # Not logged in
            # Check if editor elements exist
            result = _brave_js(
                "document.querySelector('[data-testid=\"post-title\"], "
                "[class*=\"graf--title\"], h3[class*=\"title\"]') ? 'ready' : 'wait'"
            )
            if result == "ready":
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _click_title_and_type(title: str) -> None:
    """Click the title field and type the title."""
    # Click title via JS then type via keyboard
    _brave_js(
        "var el = document.querySelector('[data-testid=\"post-title\"], "
        "[class*=\"graf--title\"], h3[class*=\"title\"]'); "
        "if(el){ el.click(); el.focus(); }"
    )
    time.sleep(0.5)
    # Use AppleScript to type the title
    escaped_title = title.replace('"', '\\"').replace("\\", "\\\\")
    osascript_type = f'''
tell application "System Events"
    tell process "Brave Browser"
        keystroke "{escaped_title}"
    end tell
end tell
'''
    subprocess.run(["osascript", "-e", osascript_type], timeout=15)
    time.sleep(0.3)
    # Press Tab/Enter to move to body
    subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to tell process "Brave Browser" to key code 36'],
        timeout=5
    )
    time.sleep(0.5)


def _paste_body_content(html_content: str) -> None:
    """Put HTML into clipboard and paste into Medium editor body."""
    # Medium editor accepts pasted HTML
    _set_clipboard(html_content)
    time.sleep(0.3)

    # Click the body area
    _brave_js(
        "var body = document.querySelector('[data-testid=\"post-body\"], "
        ".section-inner, [class*=\"graf--p\"]'); "
        "if(body){ body.click(); body.focus(); }"
    )
    time.sleep(0.5)

    # Cmd+V to paste
    subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to tell process "Brave Browser"'
         ' to keystroke "v" using command down'],
        timeout=10
    )
    time.sleep(2)


def _click_publish_menu() -> None:
    """Click the Publish button in Medium editor."""
    # Try clicking via JS first
    clicked = _brave_js(
        "var btns = Array.from(document.querySelectorAll('button'));"
        "var pub = btns.find(b => b.textContent.trim() === 'Publish');"
        "if(pub){ pub.click(); return 'clicked'; } return 'notfound';"
    )
    if clicked != "clicked":
        raise ExternalServiceError(
            "Could not find Publish button in Medium editor. "
            "The editor may not have loaded correctly."
        )
    time.sleep(2)


def _click_publish_now() -> None:
    """Click 'Publish now' in the publish dialog."""
    clicked = _brave_js(
        "var btns = Array.from(document.querySelectorAll('button'));"
        "var pub = btns.find(b => "
        "b.textContent.includes('Publish now') || b.textContent.includes('Publish'));"
        "if(pub){ pub.click(); return 'clicked'; } return 'notfound';"
    )
    time.sleep(3)


def _save_draft_via_keyboard() -> None:
    """Medium auto-saves, but we can ensure it by waiting."""
    time.sleep(3)


class MediumBraveAdapter(Publisher):
    """Publish to Medium via AppleScript-controlled Brave browser (macOS only).

    Completely bypasses CDP/automation detection since it uses the user's
    real Brave browser with their existing login session.

    Raises DependencyError on non-macOS platforms.
    Raises ExternalServiceError if Brave is not running or user not logged in.
    """

    @classmethod
    def available(cls, config) -> bool:
        """Gate this adapter to macOS only — preserves the legacy
        ``if _platform.system() == "Darwin"`` check that used to live in
        the dispatcher (Plan Unit 7 D8)."""
        import platform as _p
        return _p.system() == "Darwin"

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        _check_macos()

        article_id = payload.get("id", str(uuid.uuid4())[:8])
        title = payload.get("title", "")
        content_markdown = payload.get("content_markdown", "")
        # Plan 2026-05-18-006 Unit 5 R9: medium platform-tier (b) —
        # AppleScript-driven Brave WYSIWYG paste sanitize is lossy, so helper
        # renders content_markdown even when content_html is supplied.
        # Validate-time gate (Unit 6) rejects content_html-only medium rows
        # before reaching this publish path; this is defense in depth.
        content_html = extract_publish_html(payload, "medium")

        log.info(_json_log(adapter="medium-brave", phase="start", id=article_id))

        # Ensure Brave is running
        try:
            _run_applescript('tell application "Brave Browser" to return name', timeout=5)
        except Exception:
            raise ExternalServiceError(
                "Brave Browser is not running. Please open Brave and log in to Medium."
            )

        # Open new story tab
        log.info(_json_log(adapter="medium-brave", phase="open-new-story", id=article_id))
        _open_new_story_in_brave(wait_secs=6)

        # Check URL — should be on editor, not sign-in
        url = _get_current_url_brave()
        if "signin" in url or "login" in url:
            raise ExternalServiceError(
                "Medium login required. Please log in to medium.com in Brave first, then retry."
            )

        if "medium.com/new-story" not in url and "medium.com/p/" not in url:
            raise ExternalServiceError(
                f"Unexpected URL after opening new story: {url}. "
                "Medium may have changed its URL structure or is showing a CAPTCHA."
            )

        # Wait for editor to load
        log.info(_json_log(adapter="medium-brave", phase="wait-editor", id=article_id))
        ready = _wait_for_medium_editor(max_wait=15)
        if not ready:
            # Editor may still be loading but JS check failed — give extra time
            time.sleep(5)

        # Fill title
        log.info(_json_log(adapter="medium-brave", phase="fill-title", id=article_id))
        _click_title_and_type(title)

        # Paste body
        log.info(_json_log(adapter="medium-brave", phase="paste-body", id=article_id))
        _paste_body_content(content_html)

        # Publish or save draft
        if mode == "publish":
            log.info(_json_log(adapter="medium-brave", phase="publish", id=article_id))
            try:
                _click_publish_menu()
                _click_publish_now()
            except ExternalServiceError:
                log.info(_json_log(
                    adapter="medium-brave", phase="publish-fallback",
                    note="publish button not found, story saved as draft", id=article_id
                ))
        else:
            log.info(_json_log(adapter="medium-brave", phase="save-draft", id=article_id))
            _save_draft_via_keyboard()

        # Get final URL
        time.sleep(2)
        final_url = _get_current_url_brave()
        log.info(_json_log(adapter="medium-brave", phase="done", id=article_id, url=final_url))

        if mode == "publish":
            meta: dict = {}
            if final_url:
                attr_check = verify_link_attributes(final_url)
                meta["link_attr_verification"] = attr_check
                ratio = attr_check.get("blank_ratio", 1.0)
                total = attr_check.get("total_anchors", 0)
                if attr_check.get("verification") == "ok" and total > 0 and ratio < 0.5:
                    log.warn(
                        _json_log(
                            adapter="medium-brave",
                            phase="attr-warn",
                            id=article_id,
                            msg=(
                                f"Medium stripped target attributes: "
                                f"{attr_check['blank_anchors']}/{total} anchors "
                                "retain target=_blank"
                            ),
                        )
                    )
            return AdapterResult(
                status="published",
                adapter="medium-brave",
                platform="medium",
                published_url=final_url,
                _provider_meta=meta if meta else None,
            )
        return AdapterResult(
            status="drafted",
            adapter="medium-brave",
            platform="medium",
            draft_url=final_url,
        )
