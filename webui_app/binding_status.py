"""Channel binding status dispatcher (Plan 2026-05-19-006 Unit 4).

Single ``get_channel_status(name, config) -> dict`` function with per-platform
inline branches. ABC abstraction (``ChannelStatusProvider``) deferred to Unit 6
per Q-F decision — design ABC from 3 concrete patterns instead of guessing
from N=1.

Returned dict shape mirrors what dashboard cards need to render:

    {
      "channel": "blogger",
      "bound": False,
      "identity": None,
      "last_verified_at": None,
      "last_verify_result": "never",
      "dofollow": True,
      "blockers": ["Blogger OAuth not configured. ..."]
    }

Live verify (``mode='live'``) and dry-run (``mode='dry-run'``) reuse
``verify_adapter_setup`` from ``publishing/adapters/__init__.py`` (Unit 2).
This module only owns the offline / status dispatch.
"""

from __future__ import annotations

from typing import Any

from backlink_publisher._util.errors import DependencyError
from backlink_publisher.config import Config


# Dofollow / nofollow knowledge per platform. Updated from External References
# in Plan 2026-05-19-006. ``None`` = empirically uncertain.
_DOFOLLOW_BY_CHANNEL: dict[str, bool | None] = {
    "blogger": True,
    "medium": True,  # historically dofollow on member-tier accounts
    "telegraph": True,
    "velog": True,  # confirmed via GraphQL post inspection (PR #75)
    # Phase 3 ghpages-first wave (Plan 006 Q-A resolved):
    "ghpages": True,   # Jekyll default — highest SEO value
    "hashnode": None,  # empirically uncertain; verify after first publish
    "writeas": None,   # undocumented; assume nofollow until proven
    # Phase 4 conditional (deferred):
    "devto": False,    # rel="nofollow ugc" since ~2022
    "mastodon": False, # hardcoded nofollow noopener noreferrer
    "wordpresscom": False,  # free tier; paid tier dofollow (see Unit 11)
}


def get_channel_status(name: str, config: Config) -> dict[str, Any]:
    """Cheap offline status — never hits the network.

    Use ``verify_adapter_setup(name, config, mode='live')`` for the live
    API ping. Use ``mode='dry-run'`` to validate payload without sending.
    """
    # Lazy import to avoid circular: webui_app → publishing → webui_app helpers
    from backlink_publisher.publishing.adapters import verify_adapter_setup

    base: dict[str, Any] = {
        "channel": name,
        "bound": False,
        "identity": _identity_for(name, config),
        "last_verified_at": None,
        "last_verify_result": "never",
        "dofollow": _DOFOLLOW_BY_CHANNEL.get(name),
        "blockers": [],
    }

    try:
        verify_adapter_setup(name, config)  # mode='offline' default
        base["bound"] = True
        return base
    except DependencyError as e:
        base["blockers"] = [str(e)]
        return base


def _identity_for(name: str, config: Config) -> str | None:
    """Per-channel identity summary for dashboard cards (Plan R2).

    Stubs for blogger / medium / velog return None today — populated in Unit 6
    backfill from per-channel config blocks. Telegraph reads short_name from
    token file when present.
    """
    if name == "telegraph":
        # Telegraph token file may not exist (anonymous account is created
        # on first publish), so we can't always show identity offline.
        try:
            from backlink_publisher.publishing.adapters.telegraph_api import (
                _load_telegraph_token,
            )

            token_data = _load_telegraph_token(config)
            return token_data.get("short_name") if token_data else None
        except Exception:
            return None
    # Other channels: identity surfacing happens in Unit 6 backfill.
    return None
