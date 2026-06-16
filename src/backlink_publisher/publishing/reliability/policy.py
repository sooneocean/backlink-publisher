"""Coordinated publish policy layer — Plan 2026-05-28-001 Units 2–3.

``publish_with_policy`` is the single entry point.  It wraps ``adapter_publish``
(the real dispatch) for browser-tier channels with:

1. **Health gate** — checks ``channel_status.get_status(platform)``; non-"bound"
   status returns a ``skipped_policy`` sentinel without dispatching.

2. **Circuit breaker** — checks per-platform flock-based state; tripped circuit
   returns a ``skipped_circuit_open`` sentinel without dispatching.

3. **Observability** — emits a structured ``publish_attempt`` event on every
   dispatch outcome (success, auth_expired, auth_banned, external_error).

Non-browser-tier channels bypass all policy and delegate directly to
``adapter_publish``.

**Activation flag**: set ``BACKLINK_PUBLISHER_RELIABILITY_POLICY_ENABLED=1`` to
activate the full policy (health gate + circuit breaker).  When unset the function
is a transparent passthrough — identical to calling ``adapter_publish`` directly.
This mirrors the PR #279 observe → enforce rollout pattern.

**Dry-run callers must NOT route through this function** — the dry-run call at
``publish_backlinks.py:233`` remains a direct ``adapter_publish(…, dry_run=True)``
call.  This function ignores ``dry_run=True`` if somehow called with it, but the
convention is: callers are responsible for not routing dry-runs here.

Plan: docs/plans/2026-05-28-001-feat-publish-reliability-policy-plan.md
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Callable

from backlink_publisher._util.errors import AuthExpiredError, ExternalServiceError
from backlink_publisher.publishing.adapters import publish as adapter_publish
from backlink_publisher.publishing.adapters.base import AdapterResult

from .circuit import is_ban_signal, is_tripped, trip
from .events import Outcome, emit_attempt, now_ms

if TYPE_CHECKING:
    from backlink_publisher.config import Config


# Browser-tier channels activated in v1 (Plan 2026-05-28-001 Key Decision 1)
_BROWSER_TIER: frozenset[str] = frozenset({"medium", "velog", "devto", "mastodon"})

#: Activation env var — mirrors BACKLINK_PUBLISHER_DEDUP_ENFORCE from PR #279.
#: When unset / not "1": publish_with_policy is a transparent passthrough.
#: When "1": full policy (health gate + circuit breaker + events) is active.
POLICY_ENV = "BACKLINK_PUBLISHER_RELIABILITY_POLICY_ENABLED"


def policy_enabled() -> bool:
    """True iff the operator opted into the reliability policy layer."""
    return os.environ.get(POLICY_ENV) == "1"


def _is_browser_tier(platform: str) -> bool:
    return platform in _BROWSER_TIER


def publish_with_policy(
    platform: str,
    payload: dict[str, Any],
    config: Config,
    *,
    mode: str = "draft",
    banner_emit: Callable[[str, dict[str, Any]], None] | None = None,
) -> AdapterResult:
    """Policy-wrapped dispatch for browser-tier channels.

    Non-browser-tier platforms bypass all policy and delegate directly to
    ``adapter_publish``.  This function must NOT be called with dry-run
    payloads — the dry-run seam in ``publish_backlinks.py:233`` remains
    a direct ``adapter_publish(…, dry_run=True)`` call.
    """
    full_payload = {**payload, "platform": platform}

    # Passthrough when policy is disabled (default) or for non-browser-tier.
    if not policy_enabled() or not _is_browser_tier(platform):
        return adapter_publish(
            payload=full_payload,
            mode=mode,
            config=config,
            dry_run=False,
            banner_emit=banner_emit,
        )

    # --- Policy active (BACKLINK_PUBLISHER_RELIABILITY_POLICY_ENABLED=1) ---

    # 1. Health gate (already fail-CLOSED: JSONDecodeError → {} → "unbound")
    try:
        from webui_store.channel_status import get_status
        status_info = get_status(platform)
        channel_status = status_info.get("status", "unbound")
    except Exception:  # noqa: BLE001
        channel_status = "unbound"

    if channel_status != "bound":
        return AdapterResult(
            status="skipped_policy",
            adapter="policy",
            platform=platform,
            error=f"channel not bound (status={channel_status!r})",
        )

    # 2. Circuit breaker (fail-CLOSED: corrupt state → is_tripped returns True)
    if is_tripped(platform, config):
        return AdapterResult(
            status="skipped_circuit_open",
            adapter="policy",
            platform=platform,
            error=f"circuit open for {platform}",
        )

    # 3. Dispatch + observe
    t0 = now_ms()
    try:
        result = adapter_publish(
            payload=full_payload,
            mode=mode,
            config=config,
            dry_run=False,
            banner_emit=banner_emit,
        )
        emit_attempt(platform, Outcome.SUCCESS, now_ms() - t0)
        return result

    except AuthExpiredError as exc:
        duration = now_ms() - t0
        if is_ban_signal(exc):
            trip(platform, config)
            emit_attempt(platform, Outcome.AUTH_BANNED, duration)
        else:
            emit_attempt(platform, Outcome.AUTH_EXPIRED, duration)
        raise

    except ExternalServiceError as exc:
        emit_attempt(platform, Outcome.EXTERNAL_ERROR, now_ms() - t0)
        raise

    except Exception as exc:
        emit_attempt(platform, Outcome.TRANSIENT, now_ms() - t0)
        raise
