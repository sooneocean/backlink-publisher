"""BrowserPublishDispatcher — Plan 2026-05-21-001 Unit 2.

One ``Publisher`` subclass + ``for_channel`` classmethod factory drives
every chrome-publish channel. Recipes (per-channel ``publish_flow``
callables) plug in via the ``RECIPES`` dict, not by subclassing — this
avoids the dynamic-class-creation pattern that earlier plan drafts
proposed (per plan §D6).
"""

from __future__ import annotations

import re
from typing import Any

from backlink_publisher._util.errors import (
    AuthExpiredError,
    DependencyError,
    ExternalServiceError,
)
from backlink_publisher.config.loader import Config
from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.publishing.adapters.link_attr_verifier import (
    required_link_urls,
    verify_link_attributes,
)
from backlink_publisher.publishing.registry import Publisher

from .chrome_session import BrowserPublishRecipe, ChromeAttachSession
from .recipes import RECIPES


_SIGNIN_PATTERNS = (
    re.compile(r"/(?:signin|sign-in|log-?in|m/signin)(?:[/?#]|$)", re.IGNORECASE),
)


class BrowserPublishDispatcher(Publisher):
    """One dispatcher instance per channel, configured via ``for_channel``.

    ``BrowserPublishDispatcher`` accepts a ``recipe`` at construction time
    (instance attribute, not class attribute — keeps registry registrations
    from sharing mutable state). ``publish`` opens a ``ChromeAttachSession``,
    runs the recipe's ``publish_flow``, and wraps the returned URL into an
    ``AdapterResult``.
    """

    def __init__(self, channel: str, recipe: BrowserPublishRecipe) -> None:
        if channel != recipe.channel:
            raise ValueError(
                f"channel/recipe mismatch: dispatcher channel={channel!r} "
                f"but recipe.channel={recipe.channel!r}"
            )
        self.channel = channel
        self.recipe = recipe

    @classmethod
    def for_channel(cls, channel: str) -> "BrowserPublishDispatcher":
        """Build a dispatcher for ``channel`` using its registered recipe.

        Raises ``DependencyError`` if no recipe is registered — this is
        the import-time signal that ``register()`` calls in
        ``adapters/__init__.py`` should be ordered after recipe modules
        have populated ``RECIPES``.
        """
        recipe = RECIPES.get(channel)
        if recipe is None:
            raise DependencyError(
                f"no BrowserPublishRecipe registered for channel {channel!r}; "
                f"ensure the recipe module is imported before "
                f"`register(...)` runs"
            )
        return cls(channel, recipe)

    @classmethod
    def available(cls, config: Config) -> bool:
        """Playwright + Chrome must be importable. Per-channel state is not
        consulted (the gate is environment, not channel — plan body §D6)."""
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return False
        from .chrome_session import _chrome_binary

        return _chrome_binary() is not None

    def publish(
        self, payload: dict[str, Any], mode: str, config: Config
    ) -> AdapterResult:
        adapter_name = f"{self.channel}-browser-attach"

        try:
            with ChromeAttachSession(self.channel) as page:
                # URL-level signin detection. DOM-level captcha is the
                # recipe's responsibility (plan body L293, feasibility F8).
                self._raise_if_signin(page)

                final_url = self.recipe.publish_flow(page, payload)

                attr_check = self._verify_link_attrs(final_url, payload)

                return AdapterResult(
                    status="published",
                    adapter=adapter_name,
                    platform=self.channel,
                    published_url=final_url,
                    _provider_meta=(
                        {"link_attr_verification": attr_check}
                        if attr_check is not None
                        else None
                    ),
                )
        except AuthExpiredError:
            self._safe_mark_expired()
            raise
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            # Wrap recipe-internal failures as ExternalServiceError so the
            # dispatcher chain does NOT fall through (matches legacy
            # adapter contract — see registry.py "ExternalServiceError
            # propagates" semantics).
            raise ExternalServiceError(
                f"{self.channel} browser publish failed: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _raise_if_signin(self, page: Any) -> None:
        try:
            url = page.url
        except Exception:
            return
        if not isinstance(url, str):
            return
        for pattern in _SIGNIN_PATTERNS:
            if not pattern.search(url):
                continue
            # AuthExpiredError validates channel against bind CHANNELS
            # (blogger/medium/velog). Publish-only channels (hashnode,
            # …) live outside that set, so fall back to a generic
            # DependencyError with the same dispatch-chain fall-through
            # semantics.
            from backlink_publisher.cli._bind.channels import CHANNELS

            if self.channel in CHANNELS:
                raise AuthExpiredError(
                    channel=self.channel,
                    reason=f"landed on signin URL ({url})",
                )
            raise DependencyError(
                f"{self.channel}: landed on signin URL ({url}); "
                f"operator must re-authenticate via attached Chrome"
            )

    def _verify_link_attrs(
        self, final_url: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        if not final_url:
            return None
        try:
            return verify_link_attributes(
                final_url, target_urls=required_link_urls(payload)
            )
        except Exception:
            return None

    def _safe_mark_expired(self) -> None:
        """Mark the channel binding as expired; degrade gracefully on IO error.

        Skipped when channel is not in the bind CHANNELS set (publish-only
        channels have no bind state to mark). Failure to mark_expired must
        NOT mask the original AuthExpiredError.
        """
        try:
            from backlink_publisher.cli._bind.channels import CHANNELS

            if self.channel not in CHANNELS:
                return
            from webui_store.channel_status import mark_expired
            mark_expired(self.channel)
        except Exception:
            # State sync degraded — the operator UI may show a stale
            # binding. The caller will still see the AuthExpiredError.
            pass


__all__ = ["BrowserPublishDispatcher"]
