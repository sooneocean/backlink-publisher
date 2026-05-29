"""GEO citation-probe provider parser."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from ..._util.errors import InputValidationError
from ..types import (
    GeoProbeConfig,
)

_GEO_API_KEY_ENV_VAR = "BACKLINK_GEO_API_KEY"
_GEO_BASE_URL_ENV_VAR = "BACKLINK_GEO_BASE_URL"
_GEO_MODEL_ENV_VAR = "BACKLINK_GEO_MODEL"

_log = logging.getLogger(__name__)


def _parse_geo_probe_provider(
    section: Any,
    *,
    config_path: Path | None = None,
) -> GeoProbeConfig | None:
    """Parse ``[geo.probe_provider]`` and resolve fields from env.

    Returns ``None`` when the section is empty or fully unconfigured — GEO
    citation probing is an operator-invoked capability and the tool is fully
    runnable without it.

    Credentials come from ``BACKLINK_GEO_*`` env vars or the TOML section
    **only**; there is **no** fallback to ``BACKLINK_LLM_API_KEY`` (D0). A
    populated LLM key with no ``[geo.probe_provider]`` section therefore never
    enables GEO probing.

    Enforces ``https://`` on ``base_url`` and warns if config.toml contains
    ``api_key`` but its file permissions are not 0600.
    """
    if not isinstance(section, dict):
        # Even if section is missing, we check env vars for a full override
        section = {}

    env_api_key = os.environ.get(_GEO_API_KEY_ENV_VAR)
    env_base_url = os.environ.get(_GEO_BASE_URL_ENV_VAR)
    env_model = os.environ.get(_GEO_MODEL_ENV_VAR)

    toml_api_key_raw = section.get("api_key")
    toml_has_api_key = isinstance(toml_api_key_raw, str) and bool(toml_api_key_raw)

    if toml_has_api_key and config_path is not None and config_path.exists():
        from ..loader import _warn_if_loose_config_permissions
        _warn_if_loose_config_permissions(config_path)

    base_url = env_base_url or section.get("base_url")
    model = env_model or section.get("model")
    timeout_s = section.get("timeout_s", 30.0)

    api_key = env_api_key or (toml_api_key_raw if toml_has_api_key else None)

    if not base_url and not model and not api_key:
        # Section absent or fully empty — silent no-op.
        return None

    # Beyond this point we treat a section with ANY content as an explicit
    # intent to configure the provider, so missing fields become errors.
    if not isinstance(base_url, str) or not base_url:
        raise InputValidationError(
            "[geo.probe_provider].base_url is required when the section is present"
        )
    if not base_url.startswith("https://"):
        raise InputValidationError(
            f"[geo.probe_provider].base_url must use https:// "
            f"(got {base_url!r}). Insecure endpoints are rejected to prevent "
            f"prompt-injection and credential exfiltration via a hostile host."
        )
    if not isinstance(model, str) or not model:
        raise InputValidationError(
            "[geo.probe_provider].model is required when the section is present"
        )
    if not api_key:
        raise InputValidationError(
            f"GEO probe provider is configured but no api_key is available — set "
            f"the {_GEO_API_KEY_ENV_VAR} env var or [geo.probe_provider].api_key"
        )
    if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise InputValidationError(
            f"[geo.probe_provider].timeout_s must be a positive number, got {timeout_s!r}"
        )

    return GeoProbeConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_s=float(timeout_s),
    )
