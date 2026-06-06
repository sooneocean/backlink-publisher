"""auto-retire — Auto-demote degrading adapters to retired visibility.

Reads the canary-health store and the adapter registry, identifies platforms
whose consecutive canary failures exceed a configurable threshold, and emits
retire/keep recommendations as JSONL.

Advisory read-only by default (exit 0). With ``--apply``, updates the registry
visibility to ``"retired"`` for qualifying platforms — use with caution.

Exit codes:
  0 — success (advisory; may recommend zero retirements)
  1 — usage error

Plan: ulw Wave 4 — Auto-Retire Degrading Adapters.
"""

from __future__ import annotations

import sys

from .. import config_echo
from .._util.errors import emit_error
from .._util.logger import get_logger
from ..config import load_config

_log = get_logger("auto_retire")

#: Consecutive failures before recommending retirement.
_DEFAULT_THRESHOLD = 5


def _load_canary_health() -> dict[str, dict]:
    """Load the canary-health store, returning {platform: record}."""
    from backlink_publisher.canary.store import list_all
    return list_all()


def _load_registry_platforms() -> list[tuple[str, str | None]]:
    """Return list of (platform_name, visibility) for all registered platforms."""
    from backlink_publisher.publishing.registry import platforms_with_visibility
    return platforms_with_visibility()


def _recommend_retirements(
    health: dict[str, dict],
    threshold: int,
    *,
    include_already_retired: bool = False,
) -> list[dict]:
    """Emit retire/keep recommendations based on canary health.

    Args:
        health: Output of ``list_all()``.
        threshold: Consecutive failures before retirement is recommended.
        include_already_retired: If True, re-recommend already-retired platforms.

    Returns sorted list, worst first.
    """
    from backlink_publisher.publishing.registry import visibility as get_visibility

    recommendations: list[dict] = []

    for platform, rec in health.items():
        consecutive = rec.get("consecutive_failures", 0)
        cur_vis = get_visibility(platform)

        if cur_vis == "retired" and not include_already_retired:
            continue

        if consecutive >= threshold:
            recommendations.append({
                "platform": platform,
                "action": "retire",
                "reason": f"{consecutive} consecutive canary failures (threshold: {threshold})",
                "current_visibility": cur_vis,
                "consecutive_failures": consecutive,
                "last_drift_at": rec.get("last_drift_at"),
                "last_ok_at": rec.get("last_ok_at"),
            })
        elif consecutive > 0:
            # Warn but don't retire
            recommendations.append({
                "platform": platform,
                "action": "warn",
                "reason": f"{consecutive} consecutive failures (below threshold {threshold})",
                "current_visibility": cur_vis,
                "consecutive_failures": consecutive,
                "last_drift_at": rec.get("last_drift_at"),
            })

    recommendations.sort(key=lambda r: -r.get("consecutive_failures", 0))
    return recommendations


def _apply_retirement(platform: str) -> bool:
    """Set a platform's registry visibility to retired.

    Uses ``register()`` with visibility override mechanism.
    Returns True on success.
    """
    from backlink_publisher.publishing.registry import set_visibility
    set_visibility(platform, "retired")
    return True


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="auto-retire",
        description=(
            "Auto-retire degrading adapters: read canary health, identify "
            "platforms with consecutive failures, and emit retire/keep "
            "recommendations. Read-only advisory by default."
        ),
    )
    parser.add_argument(
        "--threshold", type=int, default=_DEFAULT_THRESHOLD, metavar="N",
        help=f"Consecutive failures before retirement (default: {_DEFAULT_THRESHOLD}).",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply retirements (set visibility='retired' for qualifying platforms).",
    )
    parser.add_argument(
        "--include-already-retired", action="store_true",
        help="Include already-retired platforms in recommendations.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Alias for default behavior (read-only advisory).",
    )
    parser.add_argument(
        "--log-level", default="WARN",
        help="Log verbosity (DEBUG/INFO/WARN/ERROR).",
    )
    args = parser.parse_args(argv)

    _log.setLevel(args.log_level.upper())

    cfg = load_config()
    config_echo.emit_banner(cfg, "auto-retire")

    health = _load_canary_health()
    if not health:
        print(json.dumps({"info": "no canary health data found", "platforms_checked": 0}, ensure_ascii=False))
        return

    recommendations = _recommend_retirements(
        health,
        args.threshold,
        include_already_retired=args.include_already_retired,
    )

    retire_count = sum(1 for r in recommendations if r["action"] == "retire")

    if args.apply:
        applied = 0
        for r in recommendations:
            if r["action"] == "retire":
                try:
                    _apply_retirement(r["platform"])
                    r["applied"] = True
                    applied += 1
                except Exception as exc:
                    r["applied"] = False
                    r["error"] = str(exc)
        print(
            f"RECON auto_retire apply=true platforms_checked={len(health)} "
            f"recommendations={len(recommendations)} retired={applied}",
            file=sys.stderr,
        )
    else:
        print(
            f"RECON auto_retire apply=false platforms_checked={len(health)} "
            f"recommendations={len(recommendations)} would_retire={retire_count}",
            file=sys.stderr,
        )

    if recommendations:
        import json
        for r in recommendations:
            sys.stdout.write(json.dumps(r, ensure_ascii=False) + "\n")
    else:
        import json
        sys.stdout.write(
            json.dumps(
                {"info": "all platforms healthy", "platforms_checked": len(health)},
                ensure_ascii=False,
            )
            + "\n"
        )


if __name__ == "__main__":
    main()
