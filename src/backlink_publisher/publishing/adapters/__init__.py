"""Adapter dispatcher — table-driven registry (Plan Unit 7).

Replaced the if/elif chain in the previous ``publish()`` with a
single ``dispatch()`` call into ``publishing.registry``. The Medium
fallback chain (MediumAPI → MediumBrave on macOS → MediumBrowser
on Playwright) is now expressed as registration order, and the
macOS gate lives on ``MediumBraveAdapter.available()``.

Behaviour preserved verbatim:

  - Blogger: ``BloggerAPIAdapter`` only.
  - Medium:
      1. ``MediumAPIAdapter`` (Integration Token; deprecated by Medium 2023)
      2. ``MediumBraveAdapter`` (AppleScript + Brave; macOS only;
         ``available()`` short-circuits elsewhere)
      3. ``MediumBrowserAdapter`` (Playwright headed Chrome — terminal)
  - ``DependencyError`` from one adapter → try the next.
  - ``ExternalServiceError`` (401 / 429 / network) → propagate, no fall.
  - ``dry_run=True`` → sentinel ``AdapterResult`` without publishing.
  - Unknown platform → ``ExternalServiceError("unsupported platform: …")``.
"""

from __future__ import annotations

from typing import Any

from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError
from ..registry import dispatch, register
from .base import AdapterResult
from .blogger_api import BloggerAPIAdapter
from .medium_api import MediumAPIAdapter
from .medium_brave import MediumBraveAdapter
from .medium_browser import MediumBrowserAdapter


# Register the fallback chain per platform. Adding a new platform = one
# more ``register(...)`` call — no dispatcher changes.
register("blogger", BloggerAPIAdapter)
register("medium", MediumAPIAdapter, MediumBraveAdapter, MediumBrowserAdapter)


def publish(
    payload: dict[str, Any],
    mode: str,
    config: Config,
    dry_run: bool = False,
) -> AdapterResult:
    """Public dispatch entry point — preserved as a function for backward
    compatibility (CLI / tests / WebUI all call ``publish(...)``)."""
    return dispatch(payload, mode, config, dry_run=dry_run)


def verify_adapter_setup(platform: str, config: Config) -> None:
    """Raise ``DependencyError`` if the adapter for this platform cannot
    function. Called before the publish loop when not in dry-run mode.

    Kept as a module function (not on the ABC) per Plan D8 — only ``publish``
    needs to be ABC-bound today; promoting this to the ABC waits for the
    third platform that actually needs it.
    """
    if platform == "blogger":
        if not config.blogger_oauth:
            raise DependencyError(
                "Blogger OAuth not configured. "
                "Add [blogger.oauth] to ~/.config/backlink-publisher/config.toml"
            )
        return

    if platform == "medium":
        # verify_adapter_setup is a library-availability check, not an auth
        # check — the four-state badge in /settings is the real auth signal.
        has_token = bool(config.medium_integration_token)
        from backlink_publisher.config import load_medium_token
        has_oauth = bool(load_medium_token())   # existing medium-token.json
        from .medium_browser import sync_playwright as _spw
        has_playwright = _spw is not None
        # has_brave intentionally excluded: MediumBraveAdapter.available()
        # only checks platform.system(), not whether Brave.app is installed.
        # AppleScript failure raises ExternalServiceError (not DependencyError),
        # which does NOT fall through the chain — so counting Brave as ready
        # here would let verify pass but publish crash non-recoverably.

        if not (has_token or has_oauth or has_playwright):
            raise DependencyError(
                "Medium adapter not ready: no integration_token, no OAuth token file, "
                "and Playwright is not installed. "
                "Run 'playwright install chromium' or configure a token in /settings."
            )
        return

    raise DependencyError(f"No adapter configured for platform: {platform}")
