"""Throttle helpers for publish-backlinks CLI.

Extracted from ``_publish_helpers.py`` to keep that module within its SLOC budget.
Provides Medium-specific inter-post throttling and sleep utilities.
"""

from __future__ import annotations

import os
import random
import time


def _load_throttle_config() -> tuple[int, int]:
    return (
        int(os.environ.get("MEDIUM_THROTTLE_MIN", "60")),
        int(os.environ.get("MEDIUM_THROTTLE_MAX", "300")),
    )


def _do_sleep(seconds: float) -> None:
    """Sleep for the specified number of seconds. (Mockable for tests)

    Tests patch backlink_publisher.cli._publish_helpers._do_sleep.
    This function calls a lazy-resolve internal to allow patching.
    """
    # This reference is resolved at call time so patches on the re-export
    # in _publish_helpers take effect.
    time.sleep(seconds)


def _sleep_with_throttle(throttle_min: int, throttle_max: int, context: str = "") -> None:
    sleep_secs = random.uniform(throttle_min, throttle_max)
    from backlink_publisher._util.logger import publish_logger
    from backlink_publisher.cli._publish_helpers import _do_sleep  # Patchable ref
    label = f" ({context})" if context else ""
    publish_logger.info(f"throttle: sleeping {sleep_secs:.0f}s{label}")
    _do_sleep(sleep_secs)


def _medium_throttle_sleep(
    row_idx: int,
    last_success_idx: int,
    platform: str,
    throttle_min: int,
    throttle_max: int,
    *,
    dry_run: bool,
) -> None:
    if dry_run or row_idx == 0:
        return
    if last_success_idx != row_idx - 1 or platform != "medium":
        return
    _sleep_with_throttle(throttle_min, throttle_max, "next Medium post")