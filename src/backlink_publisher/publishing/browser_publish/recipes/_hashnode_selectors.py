"""Hashnode editor DOM selectors — Plan 2026-05-21-001 Unit 3.

Best-guess selectors against hashnode.com/new (public DOM inspection
2026-05-21; no real publish). Refresh procedure: opt-in
``real_browser_publish_smoke`` marker test.

Hashnode's compose UI uses tiptap (ProseMirror) for the body editor and
a plain ``<input>`` for the title. Cover-image upload is optional and
omitted from the recipe v1.
"""

from __future__ import annotations


COMPOSE_URL = "https://hashnode.com/new"

# Hashnode signin lives at /signin (not /login). Dispatcher's
# _SIGNIN_PATTERNS already matches /signin generally; this constant is
# documentary.
SIGNIN_HOST = "https://hashnode.com/"

# Title: large input/textarea at top of editor.
TITLE_INPUT = (
    "textarea[placeholder*='Article title'], "
    "input[placeholder*='Article title'], "
    "textarea[placeholder*='Title']"
)

# Body: ProseMirror contenteditable surface.
BODY_EDITOR = "div.ProseMirror[contenteditable='true']"

# Publish dialog: top-right "Publish" button opens a dialog with confirm.
OPEN_PUBLISH_DIALOG_BUTTON = (
    "button:has-text('Publish'):not(:has-text('Publish later'))"
)
CONFIRM_PUBLISH_BUTTON_IN_DIALOG = (
    "div[role='dialog'] button:has-text('Publish'), "
    "section[aria-label*='publish' i] button:has-text('Publish now'), "
    "button:has-text('Publish now')"
)

# Post-publish URL pattern. Hashnode redirects to either
# ``https://<subdomain>.hashnode.dev/<slug>`` (free tier) or a custom
# domain. Match both shapes.
POST_PUBLISHED_URL_RE = (
    r"^https://(?:[A-Za-z0-9_-]+\.hashnode\.dev|"
    r"[A-Za-z0-9.-]+)/[A-Za-z0-9_-]+(?:[/?#]|$)"
)

TITLE_FILL_TIMEOUT_MS = 15_000
BODY_FILL_TIMEOUT_MS = 15_000
PUBLISH_DIALOG_TIMEOUT_MS = 20_000
POST_PUBLISH_REDIRECT_TIMEOUT_MS = 45_000  # Hashnode is slow on free tier


__all__ = [
    "COMPOSE_URL",
    "SIGNIN_HOST",
    "TITLE_INPUT",
    "BODY_EDITOR",
    "OPEN_PUBLISH_DIALOG_BUTTON",
    "CONFIRM_PUBLISH_BUTTON_IN_DIALOG",
    "POST_PUBLISHED_URL_RE",
    "TITLE_FILL_TIMEOUT_MS",
    "BODY_FILL_TIMEOUT_MS",
    "PUBLISH_DIALOG_TIMEOUT_MS",
    "POST_PUBLISH_REDIRECT_TIMEOUT_MS",
]
