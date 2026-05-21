"""Hashnode chrome publish recipe — Plan 2026-05-21-001 Unit 3.

Bypasses the 2026-05-13 GraphQL paywall by driving the Web editor at
hashnode.com/new. Free-tier operators retain a working publish path so
long as their attached Chrome has a Hashnode session.

Registers as the second entry in the ``hashnode`` dispatch chain after
``HashnodeAPIAdapter`` — the API path raises ``DependencyError`` on
paywalled accounts, so the chain falls through to this recipe
automatically. Operators with a Pro account keep using the API path
without code changes (just provide a token).

``dofollow`` declaration on the chain stays ``False`` (existing
rationale in ``adapters/__init__.py``) until ``link_attr_verifier``
empirically measures the live anchor — Hashnode currently injects
``rel="nofollow"`` on outbound links unless the account is verified.
"""

from __future__ import annotations

from typing import Any

from ..chrome_session import BrowserPublishRecipe
from . import RECIPES
from . import _hashnode_selectors as sel


def hashnode_publish_flow(page: Any, payload: dict[str, Any]) -> str:
    """Drive hashnode.com/new and return final published URL."""
    title = payload.get("title")
    body = payload.get("content_markdown") or payload.get("body")
    if not title or not body:
        raise ValueError(
            "hashnode publish payload missing title or content_markdown/body"
        )

    page.goto(sel.COMPOSE_URL)

    # Title.
    page.wait_for_selector(sel.TITLE_INPUT, timeout=sel.TITLE_FILL_TIMEOUT_MS)
    page.fill(sel.TITLE_INPUT, title)

    # Body: ProseMirror contenteditable. Playwright ``fill`` works on
    # contenteditable in modern versions.
    page.wait_for_selector(sel.BODY_EDITOR, timeout=sel.BODY_FILL_TIMEOUT_MS)
    body_handle = page.query_selector(sel.BODY_EDITOR)
    if body_handle is None:
        raise RuntimeError("hashnode body editor not found")
    body_handle.fill(body)

    # Open publish dialog.
    page.wait_for_selector(
        sel.OPEN_PUBLISH_DIALOG_BUTTON, timeout=sel.PUBLISH_DIALOG_TIMEOUT_MS
    )
    page.click(sel.OPEN_PUBLISH_DIALOG_BUTTON)

    # Confirm publish.
    page.wait_for_selector(
        sel.CONFIRM_PUBLISH_BUTTON_IN_DIALOG,
        timeout=sel.PUBLISH_DIALOG_TIMEOUT_MS,
    )
    page.click(sel.CONFIRM_PUBLISH_BUTTON_IN_DIALOG)

    # Wait for redirect to post URL (slow on free tier, up to ~45s).
    page.wait_for_url(
        sel.POST_PUBLISHED_URL_RE, timeout=sel.POST_PUBLISH_REDIRECT_TIMEOUT_MS
    )
    return page.url


RECIPES["hashnode"] = BrowserPublishRecipe(
    channel="hashnode",
    compose_url=sel.COMPOSE_URL,
    publish_flow=hashnode_publish_flow,
)


__all__ = ["hashnode_publish_flow"]
