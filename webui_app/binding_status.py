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


# Channels registered in `publishing.registry` but intentionally hidden from
# the WebUI binding dashboard. Used by `_settings_context` to filter
# `dashboard_channels`, and by the drift-check test in
# `test_settings_dashboard_rendering.py`. Adapter source stays in the repo
# so CLI / tests continue to exercise the registry pattern; only the UI
# surface is suppressed.
#
# Plan 2026-05-25-002 Unit 2a — derived dynamically from
# ``registry.visibility(name)`` instead of a hand-maintained frozenset.
# A platform is hidden iff its manifest declares ``visibility="hidden"``
# (UI hidden, existing bound configs still work — PR #136 write.as
# pattern) or ``visibility="retired"`` (UI hidden + config sections no
# longer round-tripped, Unit 2b). Default ``"active"`` => not hidden.
#
# Two access paths preserved:
#   hidden_from_ui()        — explicit function call, recomputed each
#                             call (cheap; the registry has ≤20 entries)
#   HIDDEN_FROM_UI          — module-level attribute via PEP 562
#                             ``__getattr__``. Existing call sites that
#                             do ``from .binding_status import HIDDEN_FROM_UI``
#                             keep working unchanged; each import binds
#                             the *current* dynamic frozenset.
#
# Per ``feedback_invert_drift_check_when_invariant_becomes_dynamic``:
# any module-import-time assertion against ``HIDDEN_FROM_UI`` would now
# run before ``adapters/__init__.py`` finishes registering platforms,
# so callers MUST use function-local or test-time imports. The drift
# test in ``test_settings_dashboard_rendering.py`` already follows that
# pattern (function-local import inside each test method).


def hidden_from_ui() -> frozenset[str]:
    """Return platforms whose manifest hides them from the WebUI."""
    # Local import: registry → webui_app inversion is forbidden, so
    # binding_status imports registry (not the other way around). Top-
    # level import would still work but stays local for symmetry with
    # ``get_channel_status`` below and to avoid pinning module-load
    # order against ``adapters/__init__.py``.
    from backlink_publisher.publishing.registry import (
        registered_platforms,
        visibility,
    )

    return frozenset(
        name
        for name in registered_platforms()
        if visibility(name) in ("hidden", "retired")
    )


def __getattr__(name: str) -> object:
    """PEP 562 module-level attribute hook.

    Preserves the legacy ``HIDDEN_FROM_UI`` module-level name so existing
    callers don't need to update their import + call syntax in lock-step
    with Unit 2a. Each ``from .binding_status import HIDDEN_FROM_UI``
    triggers this hook and gets a freshly computed frozenset bound to
    the caller's namespace.
    """
    if name == "HIDDEN_FROM_UI":
        return hidden_from_ui()
    raise AttributeError(
        f"module 'webui_app.binding_status' has no attribute {name!r}"
    )

# Dofollow / nofollow knowledge moved to publishing.registry (Plan 2026-05-20-009
# U5): per-adapter declaration via register(..., dofollow=...) is the single
# source of truth. Previously-rejected nofollow platforms (devto / mastodon /
# wordpresscom) live in publishing.registry._REJECTED_PLATFORMS and re-attempts
# at those names raise RegistryError at import time.

# Backlink outcome taxonomy — mirrors publishing._verify_html.RenderedLinkResult.
_BACKLINK_OUTCOME_VALUES: frozenset[str] = frozenset(
    {"effective_backlink", "published_but_ineffective", "needs_canary", "failed"}
)
_BACKLINK_DETAIL_KEYS: frozenset[str] = frozenset({
    "backlink_outcome",
    "backlink_outcome_reason",
    "posteasy_expires_at",
    "brewpage_expires_at",
    "brewpage_ttl_days",
    "expires_at",
})


def _get_latest_backlink_outcome(platform: str) -> str | None:
    """Return the most recent backlink outcome for *platform* from publish history.

    Reads from the WebUI history store (in-memory JSON file) and finds the newest
    entry whose ``backlink_outcome`` field is set.  Returns ``None`` when no
    outcome has been recorded yet (first publish, or only failures before the
    verification hook was added).
    """
    try:
        from webui_store import history_store
        hist = history_store.load()
    except Exception:
        return None
    for entry in hist:
        if entry.get("platform") == platform:
            outcome = entry.get("backlink_outcome")
            if outcome in _BACKLINK_OUTCOME_VALUES:
                return outcome
    return None


def _get_latest_backlink_outcome_details(platform: str) -> dict[str, Any]:
    """Return latest backlink outcome metadata for *platform*.

    Keeps ``_get_latest_backlink_outcome`` stable for existing callers while
    giving health/settings templates enough detail to explain ineffective
    backlinks and short-lived anonymous pages.
    """
    try:
        from webui_store import history_store
        hist = history_store.load()
    except Exception:
        return {}
    for entry in hist:
        if entry.get("platform") != platform:
            continue
        outcome = entry.get("backlink_outcome")
        if outcome not in _BACKLINK_OUTCOME_VALUES:
            continue
        details = {
            key: entry.get(key)
            for key in _BACKLINK_DETAIL_KEYS
            if entry.get(key) not in (None, "")
        }
        return details
    return {}


def get_channel_status(name: str, config: Config) -> dict[str, Any]:
    """Cheap offline status — never hits the network.

    Use ``verify_adapter_setup(name, config, mode='live')`` for the live
    API ping. Use ``mode='dry-run'`` to validate payload without sending.
    """
    # Lazy import to avoid circular: webui_app → publishing → webui_app helpers
    from backlink_publisher.publishing.adapters import verify_adapter_setup
    from backlink_publisher.publishing.registry import auth_type, dofollow_status

    base: dict[str, Any] = {
        "channel": name,
        "bound": False,
        "identity": _identity_for(name, config),
        "last_verified_at": None,
        "last_verify_result": "never",
        "dofollow": dofollow_status(name),
        "publish_backend": _publish_backend_for(name),
        # Plan 2026-05-26-002 Unit 2/3 — drives binding-UI template selection.
        "auth_type": auth_type(name),
        "blockers": [],
    }

    try:
        verify_adapter_setup(name, config)  # mode='offline' default
        base["bound"] = True
        return base
    except DependencyError as e:
        base["blockers"] = [str(e)]
        return base


def _publish_backend_for(name: str) -> str:
    """Classify a channel's publish chain (Plan 2026-05-21-001 Unit 5).

    Reads ``publishing.registry._REGISTRY`` and returns one of:
      - ``"api"``        every chain entry is an API-class adapter
      - ``"chrome"``     every chain entry is a BrowserPublishDispatcher
      - ``"api+chrome"`` mixed chain (API primary, Chrome fallback)
      - ``"unknown"``    channel not registered or import failure

    Drives the dashboard pill in ``_channel_card_macro.html``. Read-only
    in this unit — per-channel backend selector is deferred to a
    follow-up plan (per plan body §Unit 5 "Out of scope").
    """
    try:
        from backlink_publisher.publishing.registry import _REGISTRY
        from backlink_publisher.publishing.browser_publish import (
            BrowserPublishDispatcher,
        )
    except Exception:
        return "unknown"

    entry = _REGISTRY.get(name)
    if not entry:
        return "unknown"

    chain = entry.publishers

    has_chrome = any(isinstance(e, BrowserPublishDispatcher) for e in chain)
    has_api = any(
        not isinstance(e, BrowserPublishDispatcher) for e in chain
    )
    if has_chrome and has_api:
        return "api+chrome"
    if has_chrome:
        return "chrome"
    return "api"


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
