"""Mastodon editor DOM selectors — Plan 2026-05-21-001 Unit 4c.

Best-guess selectors against the canonical Mastodon Web UI
(public DOM inspection 2026-05-21; instance is identified by
``[mastodon] instance_url`` in config.toml). Refresh procedure: opt-in
``real_browser_publish_smoke`` marker test.

Mastodon's compose UI is a single textarea + post button — short-form,
no title field. The recipe puts the title (if any) on the first line
followed by the body markdown.
"""

from __future__ import annotations


COMPOSE_PATH = "/publish"
SIGNIN_PATHS = ("/auth/sign_in", "/auth/sign_up")

# Compose textarea: aria-labelled "What's on your mind?" / placeholder.
COMPOSE_TEXTAREA = (
    "textarea[placeholder*='What' i], "
    "textarea[aria-label*='What' i], "
    "textarea[name='status']"
)

# Publish / toot button.
PUBLISH_BUTTON = (
    "button:has-text('Publish'), "
    "button:has-text('Toot'), "
    "button[type='submit']:has-text('Publish')"
)

# Post-publish URL. Mastodon redirects to
# ``https://<instance>/@<handle>/<status_id>`` after toot. Generic
# pattern that accepts any host (host validated at recipe layer via
# config.mastodon.instance_url substring).
POST_PUBLISHED_URL_RE = r"^https://[^/]+/@[^/]+/\d+(?:[/?#]|$)"

COMPOSE_TEXTAREA_TIMEOUT_MS = 15_000
PUBLISH_BUTTON_TIMEOUT_MS = 20_000
POST_PUBLISH_REDIRECT_TIMEOUT_MS = 30_000


__all__ = [
    "COMPOSE_PATH",
    "SIGNIN_PATHS",
    "COMPOSE_TEXTAREA",
    "PUBLISH_BUTTON",
    "POST_PUBLISHED_URL_RE",
    "COMPOSE_TEXTAREA_TIMEOUT_MS",
    "PUBLISH_BUTTON_TIMEOUT_MS",
    "POST_PUBLISH_REDIRECT_TIMEOUT_MS",
]
