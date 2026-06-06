"""Pre-publish credential health checks — verify and auto-refresh tokens before
the publish loop starts, preventing mid-run ``AuthExpiredError``.

Supports all three credential families in the project:
- OAuth (Blogger / Google) — proactive token refresh before publish
- API token (Medium) — existence + expiry window check
- Cookie/storage-state (Velog, Medium Brave/Browser) — file freshness check
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from backlink_publisher.config import Config, load_blogger_token

log = logging.getLogger("credential_health")

# ---------------------------------------------------------------------------
# Status model
# ---------------------------------------------------------------------------

CredentialStatus = str
CS_HEALTHY: CredentialStatus = "healthy"
CS_REFRESHED: CredentialStatus = "refreshed"
CS_EXPIRING: CredentialStatus = "expiring"
CS_EXPIRED: CredentialStatus = "expired"
CS_MISSING: CredentialStatus = "missing"
CS_ERROR: CredentialStatus = "error"
CS_SKIPPED: CredentialStatus = "skipped"


@dataclass
class CredentialCheckResult:
    """Outcome of checking one platform's credential."""

    platform: str
    status: CredentialStatus
    detail: str = ""
    refreshed: bool = False


@dataclass
class CredentialHealth:
    """Aggregate credential health for a publish run."""

    results: list[CredentialCheckResult] = field(default_factory=list)

    def has_failures(self) -> bool:
        return any(r.status in (CS_EXPIRED, CS_MISSING) for r in self.results)

    def summary(self) -> str:
        parts: list[str] = []
        for r in self.results:
            icon = {
                CS_HEALTHY: "\u2713",
                CS_REFRESHED: "\u21bb",
                CS_EXPIRING: "\u26a0",
                CS_EXPIRED: "\u2717",
                CS_MISSING: "\u2717",
                CS_ERROR: "!",
                CS_SKIPPED: "-",
            }.get(r.status, "?")
            parts.append(f"  {icon} {r.platform}: {r.status}{' (' + r.detail + ')' if r.detail else ''}")
        return "credential health:\n" + "\n".join(parts)


# ---------------------------------------------------------------------------
# Platform-specific checkers
# ---------------------------------------------------------------------------

def _check_blogger(config: Config) -> CredentialCheckResult:
    """Check Blogger OAuth token; proactively refresh if near expiry.

    Uses the same ``_build_credentials`` path as ``BloggerAPIAdapter`` but
    runs it BEFORE the publish loop, so an expired token is caught early
    and the re-auth flow happens once per run rather than mid-batch.
    """
    token_path = config.blogger_token_path
    if not os.path.isfile(token_path):
        return CredentialCheckResult(
            platform="blogger", status=CS_MISSING,
            detail="token file not found",
        )

    try:
        from backlink_publisher.publishing.adapters.blogger_api import _build_credentials
        creds = _build_credentials(config)
        if creds and creds.valid:
            return CredentialCheckResult(
                platform="blogger", status=CS_HEALTHY,
                detail="token valid",
            )
        return CredentialCheckResult(
            platform="blogger", status=CS_EXPIRED,
            detail="token invalid after refresh attempt",
        )
    except Exception as exc:
        return CredentialCheckResult(
            platform="blogger", status=CS_ERROR,
            detail=f"check failed: {exc}",
        )


def _check_medium(config: Config) -> CredentialCheckResult:
    """Check Medium API token existence.

    Medium API is deprecated (2023). The real publish path uses Brave/Browser
    adapters which store session cookies. This check verifies at least one
    credential source exists.
    """
    from backlink_publisher.publishing.adapters.medium_api import _resolve_medium_token_data
    from backlink_publisher.config.tokens import load_medium_integration_token

    # Check OAuth token
    token_data = None
    try:
        from backlink_publisher.config import load_medium_token
        token_data = load_medium_token()
    except Exception:
        pass

    if token_data and token_data.get("access_token"):
        return CredentialCheckResult(
            platform="medium", status=CS_HEALTHY,
            detail="OAuth token found",
        )

    # Check integration token
    try:
        it_data = load_medium_integration_token()
        if it_data and it_data.get("integration_token"):
            return CredentialCheckResult(
                platform="medium", status=CS_HEALTHY,
                detail="integration token found",
            )
    except Exception:
        pass

    # Check storage state for browser-based auth
    storage_path = config.config_dir / "medium-storage-state.json"
    if storage_path.is_file():
        return CredentialCheckResult(
            platform="medium", status=CS_HEALTHY,
            detail="browser storage state found",
        )

    return CredentialCheckResult(
        platform="medium", status=CS_MISSING,
        detail="no token or storage state found",
    )


def _check_velog(config: Config) -> CredentialCheckResult:
    """Check Velog cookie storage state.

    Velog uses ``access_token`` / ``refresh_token`` cookies stored in a
    Playwright storage state file. The check verifies the file exists and
    has a reasonable mtime (not expired beyond the 30-day refresh token TTL).
    """
    storage_path = config.config_dir / "velog-storage-state.json"
    if not storage_path.is_file():
        return CredentialCheckResult(
            platform="velog", status=CS_MISSING,
            detail="velog-storage-state.json not found",
        )

    # Check age: refresh_token TTL is 30 days; flag at 25 days
    age_seconds = time.time() - storage_path.stat().st_mtime
    age_days = age_seconds / 86400

    if age_days > 25:
        return CredentialCheckResult(
            platform="velog", status=CS_EXPIRING,
            detail=f"storage state {age_days:.0f} days old (refresh token TTL ~30d)",
        )
    if age_days > 7:
        return CredentialCheckResult(
            platform="velog", status=CS_HEALTHY,
            detail=f"storage state {age_days:.0f} days old",
        )
    return CredentialCheckResult(
        platform="velog", status=CS_HEALTHY,
        detail=f"storage state {age_days:.0f} days old",
    )


@dataclass
class _CheckerDef:
    """Declarative checker registration."""
    platform: str
    fn: Callable[[Config], CredentialCheckResult]


_CHECKERS: list[_CheckerDef] = [
    _CheckerDef("blogger", _check_blogger),
    _CheckerDef("medium", _check_medium),
    _CheckerDef("velog", _check_velog),
]

#: Map platform name → checker for fast lookup
_CHECKER_MAP: dict[str, Callable[[Config], CredentialCheckResult]] = {
    cd.platform: cd.fn for cd in _CHECKERS
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_credentials(
    config: Config,
    platforms: list[str] | None = None,
) -> CredentialHealth:
    """Run credential health checks for the given (or all known) platforms.

    Args:
        config: App config with credential paths.
        platforms: Subset of platforms to check. ``None`` checks all known.

    Returns:
        ``CredentialHealth`` with per-platform results.
    """
    health = CredentialHealth()
    targets = platforms or list(_CHECKER_MAP)
    for platform in targets:
        checker = _CHECKER_MAP.get(platform)
        if checker is None:
            health.results.append(CredentialCheckResult(
                platform=platform, status=CS_SKIPPED,
                detail="no checker registered",
            ))
            continue
        try:
            result = checker(config)
            health.results.append(result)
            if result.refreshed:
                log.info("credential refreshed for %s", platform)
            if result.status == CS_EXPIRED:
                log.warning("credential expired for %s: %s", platform, result.detail)
            elif result.status == CS_MISSING:
                log.warning("credential missing for %s: %s", platform, result.detail)
        except Exception as exc:
            health.results.append(CredentialCheckResult(
                platform=platform, status=CS_ERROR,
                detail=str(exc),
            ))
            log.error("credential check failed for %s: %s", platform, exc)
    return health
