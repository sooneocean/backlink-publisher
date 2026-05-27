"""Medium publishing via AppleScript + Brave browser (macOS only).

This adapter controls Brave directly via AppleScript, bypassing all
Cloudflare/CDP detection. It uses the clipboard to paste article content
into Medium's editor, then triggers publish via keyboard shortcuts.

Tab identity strategy: we capture (window_id, tab_id) at creation — these
are Brave's stable opaque integers, unaffected by tab reordering or other
tabs opening/closing. All helpers resolve the current (win_idx, tab_idx)
from these IDs before each operation. This eliminates the positional-index
drift that caused "-1719 index out of range" errors when tabs shift.
"""

from __future__ import annotations

import platform
import subprocess
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
from .link_attr_verifier import required_link_urls, verify_link_attributes


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


# ---------------------------------------------------------------------------
# Stable-ID tab helpers
# ---------------------------------------------------------------------------

def _open_new_story_in_brave(wait_secs: int = 12) -> tuple[str, str, str]:
    """Open medium.com/new-story; return (win_id, tab_id, settled_url).

    IDs are kept as strings — Brave's AppleScript `id` property returns TEXT,
    not integer. Comparing a text id with an integer literal always evaluates
    to false, which caused id-based tab lookups to fail even when the tab existed.
    """
    script = f"""
tell application "Brave Browser"
    activate
    set newWin to front window
    set newWinId to (id of newWin) as string
    set newTab to make new tab at end of tabs of newWin with properties {{URL:"https://medium.com/new-story"}}
    set newTabId to (id of newTab) as string
    set active tab index of newWin to (count tabs of newWin)
    set deadline to (current date) + {wait_secs}
    set settledURL to ""
    repeat while (current date) < deadline
        try
            set settledURL to URL of newTab
        on error
            set settledURL to ""
        end try
        if settledURL is not "" and settledURL is not "about:blank" then
            if settledURL contains "medium.com" then exit repeat
        end if
        delay 0.5
    end repeat
    return newWinId & "|" & newTabId & "|" & settledURL
end tell
"""
    raw = _run_applescript(script, timeout=30 + wait_secs)
    parts = raw.split("|", 2)
    if len(parts) != 3:
        raise ExternalServiceError(
            f"Unexpected response from open-story script: {raw!r}"
        )
    return parts[0], parts[1], parts[2]


_TAB_GONE_SENTINEL = "__BP_TAB_GONE__"


def _tab_gone_error(win_id: str, tab_id: str) -> ExternalServiceError:
    return ExternalServiceError(
        f"Tab (win_id={win_id}, tab_id={tab_id}) no longer exists in Brave."
    )


def _get_tab_url(win_id: str, tab_id: str) -> str:
    """Read URL of (win_id, tab_id) atomically — no positional index, no race."""
    script = f'''
tell application "Brave Browser"
    set theURL to "{_TAB_GONE_SENTINEL}"
    repeat with w in windows
        if (id of w as string) is "{win_id}" then
            repeat with t in tabs of w
                if (id of t as string) is "{tab_id}" then
                    set theURL to URL of t
                    exit repeat
                end if
            end repeat
            exit repeat
        end if
    end repeat
    return theURL
end tell
'''
    result = _run_applescript(script, timeout=10)
    if result == _TAB_GONE_SENTINEL:
        raise _tab_gone_error(win_id, tab_id)
    return result


def _focus_tab(win_id: str, tab_id: str) -> None:
    """Bring Brave + our window forward and activate our tab — atomic resolve+act.

    The previous implementation resolved (win_id, tab_id) → positional
    (win_idx, tab_idx) in one AppleScript call, then issued a *second*
    AppleScript using those positions. Any tab opening/closing between the
    two calls shifted indices and triggered errAEIllegalIndex (-1719). Now
    the resolve and the action happen inside a single `tell` block, so
    Brave's AppleScript engine evaluates them against one consistent
    snapshot of the tab list.
    """
    script = f'''
tell application "Brave Browser"
    activate
    set foundIdx to 0
    set foundWin to missing value
    repeat with w in windows
        if (id of w as string) is "{win_id}" then
            set tIdx to 0
            repeat with t in tabs of w
                set tIdx to tIdx + 1
                if (id of t as string) is "{tab_id}" then
                    set foundIdx to tIdx
                    set foundWin to w
                    exit repeat
                end if
            end repeat
            exit repeat
        end if
    end repeat
    if foundIdx is 0 then return "{_TAB_GONE_SENTINEL}"
    set index of foundWin to 1
    set active tab index of foundWin to foundIdx
    return "OK"
end tell
delay 0.3
'''
    result = _run_applescript(script, timeout=10)
    if result == _TAB_GONE_SENTINEL:
        raise _tab_gone_error(win_id, tab_id)


def _tab_js(win_id: str, tab_id: str, js: str) -> str:
    """Execute JS in (win_id, tab_id) atomically.

    `tab whose id is X` filters by stable id inside the same `tell` block,
    so there is no window between resolution and execution where another
    tab can open/close and shift positional indices.
    """
    escaped = js.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
tell application "Brave Browser"
    set theResult to "{_TAB_GONE_SENTINEL}"
    repeat with w in windows
        if (id of w as string) is "{win_id}" then
            repeat with t in tabs of w
                if (id of t as string) is "{tab_id}" then
                    set theResult to (execute t javascript "{escaped}")
                    exit repeat
                end if
            end repeat
            exit repeat
        end if
    end repeat
    return theResult
end tell
'''
    result = _run_applescript(script, timeout=30)
    if result == _TAB_GONE_SENTINEL:
        raise _tab_gone_error(win_id, tab_id)
    return result


def _set_clipboard(text: str) -> None:
    proc = subprocess.run(["pbcopy"], input=text.encode("utf-8"), timeout=10)
    if proc.returncode != 0:
        raise ExternalServiceError("Failed to copy content to clipboard")


# ---------------------------------------------------------------------------
# Editor interaction helpers
# ---------------------------------------------------------------------------

def _wait_for_editor(win_id: str, tab_id: str, max_wait: int = 20) -> bool:
    for _ in range(max_wait):
        try:
            url = _get_tab_url(win_id, tab_id)
            if "medium.com/m/signin" in url or "medium.com/signin" in url:
                return False
            result = _tab_js(
                win_id, tab_id,
                "document.querySelector('[data-testid=\"post-title\"], "
                "[class*=\"graf--title\"], h3[class*=\"title\"]') ? 'ready' : 'wait'"
            )
            if result == "ready":
                return True
        except Exception as exc:  # noqa: BLE001
            log.debug("page-ready probe failed: %s", exc)
        time.sleep(1)
    return False


def _fill_title(win_id: str, tab_id: str, title: str) -> None:
    _tab_js(
        win_id, tab_id,
        "var el = document.querySelector('[data-testid=\"post-title\"], "
        "[class*=\"graf--title\"], h3[class*=\"title\"]'); "
        "if(el){ el.click(); el.focus(); }"
    )
    time.sleep(0.3)
    _focus_tab(win_id, tab_id)
    time.sleep(0.2)
    escaped = title.replace('"', '\\"').replace("\\", "\\\\")
    subprocess.run(
        ["osascript", "-e",
         f'tell application "System Events" to tell process "Brave Browser"'
         f' to keystroke "{escaped}"'],
        timeout=15,
    )
    time.sleep(0.3)
    subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to tell process "Brave Browser"'
         ' to key code 36'],
        timeout=5,
    )
    time.sleep(0.5)


def _paste_body(win_id: str, tab_id: str, html_content: str) -> None:
    _set_clipboard(html_content)
    time.sleep(0.3)
    _tab_js(
        win_id, tab_id,
        "var b = document.querySelector('[data-testid=\"post-body\"], "
        ".section-inner, [class*=\"graf--p\"]'); "
        "if(b){ b.click(); b.focus(); }"
    )
    time.sleep(0.3)
    _focus_tab(win_id, tab_id)
    time.sleep(0.2)
    subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to tell process "Brave Browser"'
         ' to keystroke "v" using command down'],
        timeout=10,
    )
    time.sleep(2)


def _click_publish_menu(win_id: str, tab_id: str) -> None:
    clicked = _tab_js(
        win_id, tab_id,
        "var btns = Array.from(document.querySelectorAll('button'));"
        "var pub = btns.find(b => b.textContent.trim() === 'Publish');"
        "if(pub){ pub.click(); return 'clicked'; } return 'notfound';"
    )
    if clicked != "clicked":
        raise ExternalServiceError(
            "Could not find Publish button — editor may not have loaded correctly."
        )
    time.sleep(2)


def _click_publish_now(win_id: str, tab_id: str) -> None:
    _tab_js(
        win_id, tab_id,
        "var btns = Array.from(document.querySelectorAll('button'));"
        "var pub = btns.find(b => "
        "b.textContent.includes('Publish now') || b.textContent.includes('Publish'));"
        "if(pub){ pub.click(); return 'clicked'; } return 'notfound';"
    )
    time.sleep(3)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class MediumBraveAdapter(Publisher):
    """Publish to Medium via AppleScript-controlled Brave browser (macOS only)."""

    @classmethod
    def available(cls, config) -> bool:
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
        content_html = extract_publish_html(payload, "medium")

        log.info(_json_log(adapter="medium-brave", phase="start", id=article_id))

        try:
            _run_applescript('tell application "Brave Browser" to return name', timeout=5)
        except Exception:
            raise ExternalServiceError(
                "Brave Browser is not running. Please open Brave and log in to Medium."
            )

        log.info(_json_log(adapter="medium-brave", phase="open-new-story", id=article_id))
        win_id, tab_id, url = _open_new_story_in_brave(wait_secs=12)
        log.info(_json_log(
            adapter="medium-brave", phase="tab-located",
            win_id=win_id, tab_id=tab_id, url=url, id=article_id,
        ))

        if not url or "medium.com" not in url:
            raise ExternalServiceError(
                f"New tab did not settle on a medium.com URL within 12s "
                f"(got {url!r}). Brave may be slow or a CAPTCHA intercepted."
            )
        if "signin" in url or "login" in url:
            raise ExternalServiceError(
                "Medium login required. Log in to medium.com in Brave, then retry."
            )
        if "medium.com/new-story" not in url and "medium.com/p/" not in url:
            raise ExternalServiceError(
                f"Unexpected URL after opening new story: {url}. "
                "Medium may have changed its URL structure or is showing a CAPTCHA."
            )

        log.info(_json_log(adapter="medium-brave", phase="wait-editor", id=article_id))
        if not _wait_for_editor(win_id, tab_id, max_wait=20):
            time.sleep(5)

        log.info(_json_log(adapter="medium-brave", phase="fill-title", id=article_id))
        _fill_title(win_id, tab_id, title)

        log.info(_json_log(adapter="medium-brave", phase="paste-body", id=article_id))
        _paste_body(win_id, tab_id, content_html)

        if mode == "publish":
            log.info(_json_log(adapter="medium-brave", phase="publish", id=article_id))
            try:
                _click_publish_menu(win_id, tab_id)
                _click_publish_now(win_id, tab_id)
            except ExternalServiceError:
                log.info(_json_log(
                    adapter="medium-brave", phase="publish-fallback",
                    note="Publish button not found; story saved as draft",
                    id=article_id,
                ))
        else:
            log.info(_json_log(adapter="medium-brave", phase="save-draft", id=article_id))
            time.sleep(3)

        # Wait up to 20s for Medium to redirect away from /new-story.
        final_url = ""
        for _ in range(20):
            try:
                final_url = _get_tab_url(win_id, tab_id)
            except ExternalServiceError:
                break
            if mode == "publish":
                if "/new-story" not in final_url and "medium.com" in final_url:
                    break
            else:
                if "/p/" in final_url or "/edit" in final_url:
                    break
            time.sleep(1)
        log.info(_json_log(
            adapter="medium-brave", phase="done", id=article_id, url=final_url,
        ))

        if mode == "publish" and (
            "/new-story" in final_url or "medium.com" not in final_url
        ):
            raise ExternalServiceError(
                f"Medium did not redirect to a published-story URL "
                f"(still at {final_url!r}). The article may exist as a draft — "
                f"check medium.com/me/stories. Likely causes: 'Allow JavaScript "
                f"from Apple Events' disabled in Brave's View → Developer menu, "
                f"or Medium UI change."
            )

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
                    log.warn(_json_log(
                        adapter="medium-brave", phase="attr-warn", id=article_id,
                        msg=(
                            f"Medium stripped target attributes: "
                            f"{attr_check['blank_anchors']}/{total} anchors "
                            "retain target=_blank"
                        ),
                    ))
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
