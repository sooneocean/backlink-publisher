"""Mastodon chrome publish recipe — Plan 2026-05-21-001 Unit 4c.

Single-instance: ``[mastodon] instance_url`` in config.toml picks the
Fediverse host. Multi-instance is a follow-up (per-instance bind state
+ per-instance Chrome profile).

Mastodon hardcodes ``rel="nofollow noopener noreferrer"`` on outbound
links across all instances. Backlinks here are referral-traffic only.
Operator dashboard chip surfaces the nofollow status (Unit 5).

**Security**: per plan §security-review F4 — do NOT bind a personal
Mastodon account for publishing. Use a throwaway account on a separate
instance. Per-channel Chrome profile isolates the bind, but operators
must not manually browse personal Mastodon in the publish-profile
Chrome.
"""

from __future__ import annotations

from typing import Any

from backlink_publisher._util.errors import DependencyError
from backlink_publisher.config import load_config

from ..chrome_session import BrowserPublishRecipe
from . import RECIPES
from . import _mastodon_selectors as sel


def _resolve_compose_url() -> str:
    """Lazy resolve at publish time per [[embed-banner-lazy-config-load]].

    Honors ``BACKLINK_PUBLISHER_CONFIG_DIR`` env rebinds.
    """
    cfg = load_config()
    mast = cfg.mastodon
    if mast is None or not mast.instance_url:
        raise DependencyError(
            "mastodon instance URL not configured; set [mastodon] "
            "instance_url in config.toml (e.g., https://mastodon.social)"
        )
    base = mast.instance_url.rstrip("/")
    return f"{base}{sel.COMPOSE_PATH}"


def mastodon_publish_flow(page: Any, payload: dict[str, Any]) -> str:
    """Drive the Mastodon compose UI and return the published toot URL."""
    body = payload.get("content_markdown") or payload.get("body")
    if not body:
        raise ValueError(
            "mastodon publish payload missing content_markdown/body"
        )

    compose_url = _resolve_compose_url()
    page.goto(compose_url)

    title = payload.get("title")
    status_text = f"{title}\n\n{body}" if title else body

    page.wait_for_selector(
        sel.COMPOSE_TEXTAREA, timeout=sel.COMPOSE_TEXTAREA_TIMEOUT_MS
    )
    textarea_handle = page.query_selector(sel.COMPOSE_TEXTAREA)
    if textarea_handle is None:
        raise RuntimeError("mastodon compose textarea not found")
    textarea_handle.fill(status_text)

    page.wait_for_selector(
        sel.PUBLISH_BUTTON, timeout=sel.PUBLISH_BUTTON_TIMEOUT_MS
    )
    page.click(sel.PUBLISH_BUTTON)

    page.wait_for_url(
        sel.POST_PUBLISHED_URL_RE,
        timeout=sel.POST_PUBLISH_REDIRECT_TIMEOUT_MS,
    )
    return page.url


# ``compose_url`` field on BrowserPublishRecipe is informational only —
# the dispatcher doesn't consume it. We populate with the
# default-Mastodon-social URL so the recipe metadata is reasonable, but
# the real navigation target is computed lazily inside publish_flow via
# _resolve_compose_url(). This keeps the recipe immutable while allowing
# config.toml edits to take effect without re-registration.
RECIPES["mastodon"] = BrowserPublishRecipe(
    channel="mastodon",
    compose_url=f"https://mastodon.social{sel.COMPOSE_PATH}",
    publish_flow=mastodon_publish_flow,
)


__all__ = ["mastodon_publish_flow", "_resolve_compose_url"]
