"""``[image_gen]`` section parser — Plan 2026-05-20-001 Unit 1.

Parses the AI banner image-gen configuration. The API key is NOT
parsed here — it lives in ``~/.config/backlink-publisher/frw-token.json``
(0600) per SEC-3 and is read at use time via
``backlink_publisher._util.secrets.load_frw_token``.

When ``[image_gen]`` is absent (or empty), returns ``None`` — image-gen
is fully opt-in. When the section is present, missing required fields
become ``InputValidationError`` to mirror the pattern in
``parsers/llm.py`` (silent partial config has historically caused
subtler bugs than loud rejection).
"""

from __future__ import annotations

import re
from typing import Any

from ..._util.errors import InputValidationError
from ..types import ImageGenConfig

# ``WIDTHxHEIGHT`` — both integers, ``x`` separator (DALL-E-style).
_BANNER_SIZE_RE = re.compile(r"^\d+x\d+$")


def _parse_image_gen(section: Any) -> ImageGenConfig | None:
    """Parse ``[image_gen]`` section into ``ImageGenConfig``.

    Returns ``None`` when the section is absent or has no
    operator-set fields.  Returns a fully-populated dataclass when
    the section is present — missing required fields raise
    ``InputValidationError``.
    """
    if not isinstance(section, dict) or not section:
        return None

    base_url = section.get("base_url")
    if not isinstance(base_url, str) or not base_url:
        raise InputValidationError(
            "[image_gen].base_url is required when the section is present"
        )
    if not base_url.startswith("https://"):
        raise InputValidationError(
            f"[image_gen].base_url must use https:// (got {base_url!r}). "
            "Insecure endpoints are rejected to prevent prompt-injection "
            "and key-exfiltration via a hostile host."
        )

    model = section.get("model")
    if not isinstance(model, str) or not model:
        raise InputValidationError(
            "[image_gen].model is required when the section is present"
        )

    banner_size = section.get("banner_size", "1200x630")
    if not isinstance(banner_size, str) or not _BANNER_SIZE_RE.match(banner_size):
        raise InputValidationError(
            f"[image_gen].banner_size must look like 'WIDTHxHEIGHT' "
            f"(got {banner_size!r}). Common values: 1200x630 (OG default), "
            "1024x1024 (DALL-E square), 1792x1024 (16:9 cover)."
        )

    # Caps may legitimately be 0 (operator kill-switch without
    # rewriting use_image_gen); use_image_gen is the primary toggle.
    daily_cap = _coerce_nonneg_int(
        section.get("daily_cap", 50), "[image_gen].daily_cap"
    )
    per_run_cap = _coerce_nonneg_int(
        section.get("per_run_cap", 10), "[image_gen].per_run_cap"
    )
    timeout_s = section.get("timeout_s", 30.0)
    if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise InputValidationError(
            f"[image_gen].timeout_s must be a positive number, got {timeout_s!r}"
        )
    max_retries = _coerce_nonneg_int(
        section.get("max_retries", 3), "[image_gen].max_retries"
    )
    auto_disable_threshold = _coerce_positive_int(
        section.get("auto_disable_threshold", 5),
        "[image_gen].auto_disable_threshold",
    )

    strict = bool(section.get("strict", False))
    use_image_gen = bool(section.get("use_image_gen", True))

    return ImageGenConfig(
        base_url=base_url,
        model=model,
        banner_size=banner_size,
        daily_cap=daily_cap,
        per_run_cap=per_run_cap,
        timeout_s=float(timeout_s),
        max_retries=max_retries,
        strict=strict,
        auto_disable_threshold=auto_disable_threshold,
        use_image_gen=use_image_gen,
    )


def _coerce_positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InputValidationError(
            f"{name} must be a positive integer, got {value!r}"
        )
    return value


def _coerce_nonneg_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InputValidationError(
            f"{name} must be a non-negative integer, got {value!r}"
        )
    return value
