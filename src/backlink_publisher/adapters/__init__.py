"""Adapter dispatcher — API-first with browser fallback for Medium.

Medium fallback chain (macOS):
  1. MediumAPIAdapter (Integration Token — deprecated by Medium in 2023)
  2. MediumBraveAdapter (AppleScript + Brave — bypasses Cloudflare)
  3. MediumBrowserAdapter (Playwright headed Chrome — legacy fallback)
"""

from __future__ import annotations

import platform as _platform
from typing import Any

from ..config import Config, load_config
from ..errors import DependencyError, ExternalServiceError
from .base import AdapterResult
from .blogger_api import BloggerAPIAdapter
from .medium_api import MediumAPIAdapter
from .medium_browser import MediumBrowserAdapter
from .medium_brave import MediumBraveAdapter


def publish(
    payload: dict[str, Any],
    mode: str,
    config: Config,
    dry_run: bool = False,
) -> AdapterResult:
    """Dispatch to the correct adapter.

    Blogger: BloggerAPIAdapter (only path).
    Medium:  MediumAPIAdapter first; on DependencyError (no token) →
             MediumBraveAdapter (macOS, Brave) → MediumBrowserAdapter (Playwright).
             ExternalServiceError (401, 429, network) does NOT fall through.

    dry_run: returns a sentinel AdapterResult without publishing.
    """
    plat = payload.get("platform", "")

    if dry_run:
        return AdapterResult(
            status="draft",
            adapter=f"{plat}-api",
            platform=plat,
            _dry_run=True,
            _command=f"publish to {plat} --mode {mode} (dry-run)",
        )

    if plat == "blogger":
        return BloggerAPIAdapter().publish(payload, mode, config)

    if plat == "medium":
        # 1. Try Integration Token API (may be deprecated)
        try:
            return MediumAPIAdapter().publish(payload, mode, config)
        except DependencyError:
            pass  # No token configured → try browser adapters
        except ExternalServiceError:
            raise  # 401/429/network → do not fall through

        # 2. Try AppleScript + Brave (macOS only, bypasses Cloudflare)
        if _platform.system() == "Darwin":
            try:
                return MediumBraveAdapter().publish(payload, mode, config)
            except DependencyError:
                pass  # Brave not running → fall to Playwright

        # 3. Playwright headed Chrome fallback
        return MediumBrowserAdapter().publish(payload, mode, config)

    raise ExternalServiceError(f"unsupported platform: {plat}")


def verify_adapter_setup(platform: str, config: Config) -> None:
    """Raise DependencyError if the adapter for this platform cannot function.

    Called before the publish loop when not in dry-run mode.
    """
    if platform == "blogger":
        if not config.blogger_oauth:
            raise DependencyError(
                "Blogger OAuth not configured. "
                "Add [blogger.oauth] to ~/.config/backlink-publisher/config.toml"
            )
        return

    if platform == "medium":
        has_token = bool(config.medium_integration_token)
        from .medium_browser import sync_playwright as _spw
        has_playwright = _spw is not None

        if not has_token and not has_playwright:
            raise DependencyError(
                "Medium requires either an integration_token in config.toml "
                "or Playwright installed (run: playwright install chromium)."
            )
        return

    raise DependencyError(f"No adapter configured for platform: {platform}")
