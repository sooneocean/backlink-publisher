"""Canary gate helpers for publish-backlinks CLI.

Extracted from ``_publish_helpers.py`` to keep that module within its SLOC budget.
Provides the read-side canary health gate for the publish row loop.
Plan 2026-05-27-001 Unit 4.
"""

from __future__ import annotations

from typing import Any


def _canary_gate(
    platform: str,
    *,
    warned: set[str],
) -> tuple[bool, str | None]:
    """Read-side canary health gate for the publish row loop.

    Returns ``(skip, reason)``:

    - ``(True, reason)`` → the row must be filtered out of the payload. This
      ONLY happens when the platform is **quarantined** AND its
      ``[canary.<platform>]`` config opts in with ``hard_skip = true``.
    - ``(False, None)`` → proceed. If the platform is merely *degraded*
      (drift-confirmed / quarantined-but-not-opted-in) a single advisory
      WARNING is emitted to stderr — deduped per platform within this
      invocation via ``warned`` so it doesn't spam every row.

    Fail-open: a platform with no canary health (never run / not configured)
    or any error reading the store is treated as healthy → never blocks, no
    spurious warning. The WARNING payload carries ONLY non-sensitive fields
    (platform name, verdict, debounce counts) — never credentials/URLs.
    """
    from backlink_publisher._util.logger import publish_logger

    if not platform:
        return False, None
    try:
        from backlink_publisher.canary.store import (
            get_health,
            is_degraded,
            is_quarantined,
            read_canary_config,
        )

        if not is_degraded(platform):
            return False, None

        if is_quarantined(platform):
            cfg = read_canary_config(platform)
            if cfg is not None and cfg.get("hard_skip"):
                return (
                    True,
                    f"因 canary 漂移已隔離(quarantined),且該平台配置 hard_skip=true → "
                    f"略過 {platform} 的本行發布",
                )

        # Degraded but not hard-skipped → advisory WARNING (deduped per platform).
        if platform not in warned:
            warned.add(platform)
            rec = get_health(platform)
            publish_logger.warn(
                f"[canary] platform={platform} status={rec.get('status')} "
                f"consecutive_failures={rec.get('consecutive_failures')} "
                f"quarantined={rec.get('quarantined')} — "
                f"canary 偵測到契約漂移(advisory,仍照常發布);"
                f"請複查 adapter / 重新 seed canary,或 flip 成 hard_skip"
            )
    except Exception as exc:  # noqa: BLE001 — fail-open: never block publish on canary read error
        publish_logger.debug(f"[canary] gate read failed for {platform!r}: {exc}")
        return False, None
    return False, None