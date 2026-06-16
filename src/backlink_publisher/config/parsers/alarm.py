"""Anchor alarm threshold parser."""
from __future__ import annotations

import logging
import math
from typing import Any

from ..._util.errors import InputValidationError
from ..types import (
    AnchorAlarmConfig,
    AnchorAlarmOverride,
)

_log = logging.getLogger(__name__)

_ANCHOR_ALARM_THRESHOLD_FIELDS: tuple[str, ...] = (
    "entropy_floor",
    "exact_ratio_ceiling",
    "top3_concentration_ceiling",
)

#: Keys an ``[[anchor_alarm.override]]`` row may carry. Anything else is an
#: operator typo — rejected loudly, mirroring the global-scope unknown-key
#: guard so the override path is not silently more permissive.
_ANCHOR_ALARM_OVERRIDE_KEYS: frozenset[str] = frozenset(
    {"match", "scope", *_ANCHOR_ALARM_THRESHOLD_FIELDS}
)


def _coerce_threshold(section_label: str, key: str, value: Any) -> float:
    """Coerce a threshold scalar; raise ``InputValidationError`` on bad input.

    Anchor-alarm thresholds are non-load-bearing for publish-flow correctness,
    but silent fall-through still masks operator typos — we mirror
    ``_parse_anchor_proportions``'s raise-loud posture. Better to surface a
    config bug at load time than to ship with the operator's intent silently
    ignored.
    """
    if isinstance(value, bool):
        # bool is a subclass of int — reject explicitly to catch obvious typos.
        raise InputValidationError(
            f"[{section_label}].{key} must be a number, got bool ({value!r})"
        )
    if not isinstance(value, (int, float)):
        raise InputValidationError(
            f"[{section_label}].{key} must be a number, got {type(value).__name__}"
        )
    f = float(value)
    if not math.isfinite(f):
        raise InputValidationError(
            f"[{section_label}].{key} must be finite, got {value!r}"
        )
    if key == "entropy_floor":
        if f < 0:
            raise InputValidationError(
                f"[{section_label}].entropy_floor must be ≥ 0, got {f!r}"
            )
    else:
        # Ratio / concentration fields are bounded to [0, 1].
        if not (0.0 <= f <= 1.0):
            raise InputValidationError(
                f"[{section_label}].{key} must be in [0.0, 1.0], got {f!r}"
            )
    return f


def _parse_anchor_alarm(section: Any) -> AnchorAlarmConfig:
    """Parse ``[anchor_alarm]`` section. Missing → defaults (no overrides).

    Raises ``InputValidationError`` on malformed input — typos in a threshold
    key, non-numeric values, unknown scope, or an override row whose every
    threshold field is absent (a row with no effect is almost certainly a
    config mistake).
    """
    if section is None:
        return AnchorAlarmConfig()
    if not isinstance(section, dict):
        raise InputValidationError(
            f"[anchor_alarm] must be a table, got {type(section).__name__}"
        )

    cfg = AnchorAlarmConfig()
    overrides_raw = section.get("override")

    # Pull globals out of the section.
    for key, value in section.items():
        if key == "override":
            continue
        if key not in _ANCHOR_ALARM_THRESHOLD_FIELDS:
            raise InputValidationError(
                f"[anchor_alarm].{key} is not a known threshold "
                f"(expected one of {_ANCHOR_ALARM_THRESHOLD_FIELDS} or 'override')"
            )
        coerced = _coerce_threshold("anchor_alarm", key, value)
        setattr(cfg, key, coerced)

    # Parse overrides. TOML maps [[anchor_alarm.override]] to a list of dicts.
    if overrides_raw is not None:
        if not isinstance(overrides_raw, list):
            raise InputValidationError(
                "[[anchor_alarm.override]] must be an array of tables"
            )
        parsed: list[AnchorAlarmOverride] = []
        for i, row in enumerate(overrides_raw):
            if not isinstance(row, dict):
                raise InputValidationError(
                    f"[[anchor_alarm.override]] row {i} must be a table"
                )
            for key in row:
                if key not in _ANCHOR_ALARM_OVERRIDE_KEYS:
                    raise InputValidationError(
                        f"[[anchor_alarm.override]] row {i}: '{key}' is not a "
                        f"known field (expected 'match', 'scope', or one of "
                        f"{_ANCHOR_ALARM_THRESHOLD_FIELDS})"
                    )
            match = row.get("match")
            scope = row.get("scope")
            if not isinstance(match, str) or not match:
                raise InputValidationError(
                    f"[[anchor_alarm.override]] row {i}: 'match' is required (non-empty string)"
                )
            if scope not in ("url", "domain"):
                raise InputValidationError(
                    f"[[anchor_alarm.override]] row {i}: 'scope' must be 'url' or 'domain', got {scope!r}"
                )
            kwargs: dict[str, float | None] = {}
            for f_name in _ANCHOR_ALARM_THRESHOLD_FIELDS:
                if f_name in row:
                    kwargs[f_name] = _coerce_threshold(
                        f"anchor_alarm.override[{i}]", f_name, row[f_name]
                    )
            if not kwargs:
                raise InputValidationError(
                    f"[[anchor_alarm.override]] row {i} sets no threshold fields — "
                    f"row would have no effect. Add at least one of "
                    f"{_ANCHOR_ALARM_THRESHOLD_FIELDS}, or delete the row."
                )
            for f_name in _ANCHOR_ALARM_THRESHOLD_FIELDS:
                kwargs.setdefault(f_name, None)
            parsed.append(
                AnchorAlarmOverride(match=match, scope=scope, **kwargs)
            )
        cfg.overrides = parsed

    return cfg
