"""LLM anchor provider parser."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from ..._util.errors import InputValidationError
from ..types import (
    LLMProviderConfig,
)

_LLM_API_KEY_ENV_VAR = "BACKLINK_LLM_API_KEY"
_LLM_BASE_URL_ENV_VAR = "BACKLINK_LLM_BASE_URL"
_LLM_MODEL_ENV_VAR = "BACKLINK_LLM_MODEL"
_LLM_TEMPERATURE_ENV_VAR = "BACKLINK_LLM_TEMPERATURE"
_LLM_SYSTEM_PROMPT_ENV_VAR = "BACKLINK_LLM_SYSTEM_PROMPT"
_LLM_USE_ARTICLE_GEN_ENV_VAR = "BACKLINK_LLM_USE_ARTICLE_GEN"
_LLM_ARTICLE_SYSTEM_PROMPT_ENV_VAR = "BACKLINK_LLM_ARTICLE_SYSTEM_PROMPT"
_LLM_USE_IMAGE_GEN_ENV_VAR = "BACKLINK_LLM_USE_IMAGE_GEN"
_LLM_IMAGE_GEN_API_KEY_ENV_VAR = "BACKLINK_LLM_IMAGE_GEN_API_KEY"

_log = logging.getLogger(__name__)


def _parse_llm_anchor_provider(
    section: Any,
    *,
    config_path: Path | None = None,
) -> LLMProviderConfig | None:
    """Parse ``[llm.anchor_provider]`` and resolve fields from env.

    Returns ``None`` when the section is empty or missing required fields —
    LLM is optional; absence simply means the anchor resolver will only use
    config-pinned typed pools.

    Enforces ``https://`` on ``base_url``. Permission warning for config.toml
    credential sections is now handled centrally in ``load_config()`` (SEC-6).
    """
    if not isinstance(section, dict):
        # Even if section is missing, we check env vars for a full override
        section = {}

    env_api_key = os.environ.get(_LLM_API_KEY_ENV_VAR)
    env_base_url = os.environ.get(_LLM_BASE_URL_ENV_VAR)
    env_model = os.environ.get(_LLM_MODEL_ENV_VAR)
    env_temp = os.environ.get(_LLM_TEMPERATURE_ENV_VAR)
    env_system = os.environ.get(_LLM_SYSTEM_PROMPT_ENV_VAR)
    env_use_article = os.environ.get(_LLM_USE_ARTICLE_GEN_ENV_VAR)
    env_article_system = os.environ.get(_LLM_ARTICLE_SYSTEM_PROMPT_ENV_VAR)
    env_use_image = os.environ.get(_LLM_USE_IMAGE_GEN_ENV_VAR)
    env_image_key = os.environ.get(_LLM_IMAGE_GEN_API_KEY_ENV_VAR)

    toml_api_key_raw = section.get("api_key")
    toml_has_api_key = isinstance(toml_api_key_raw, str) and bool(toml_api_key_raw)

    base_url = env_base_url or section.get("base_url")
    model = env_model or section.get("model")
    timeout_s = section.get("timeout_s", 30.0)
    
    # Resolve temperature: env > toml > default
    temperature = 0.7
    toml_temp = section.get("temperature")
    if env_temp:
        try:
            temperature = float(env_temp)
        except ValueError:
            pass
    elif isinstance(toml_temp, (int, float)):
        temperature = float(toml_temp)

    system_prompt = env_system or section.get("system_prompt")
    
    use_article_gen = False
    if env_use_article:
        use_article_gen = env_use_article.lower() in ("1", "true", "yes")
    elif "use_article_gen" in section:
        use_article_gen = bool(section["use_article_gen"])
        
    article_system_prompt = env_article_system or section.get("article_system_prompt")

    use_image_gen = False
    if env_use_image:
        use_image_gen = env_use_image.lower() in ("1", "true", "yes")
    elif "use_image_gen" in section:
        use_image_gen = bool(section["use_image_gen"])

    image_gen_api_key = env_image_key or section.get("image_gen_api_key")

    api_key = env_api_key or (toml_api_key_raw if toml_has_api_key else None)

    if not base_url and not model and not api_key:
        # Section absent or fully empty — silent no-op.
        return None

    # Beyond this point we treat a section with ANY content as an explicit
    # intent to configure the provider, so missing fields become errors.
    if not isinstance(base_url, str) or not base_url:
        raise InputValidationError(
            "[llm.anchor_provider].base_url is required when the section is present"
        )
    if not base_url.startswith("https://"):
        raise InputValidationError(
            f"[llm.anchor_provider].base_url must use https:// "
            f"(got {base_url!r}). Insecure endpoints are rejected to prevent "
            f"prompt-injection and credential exfiltration via a hostile host."
        )
    if not isinstance(model, str) or not model:
        raise InputValidationError(
            "[llm.anchor_provider].model is required when the section is present"
        )
    if not api_key:
        raise InputValidationError(
            f"LLM provider is configured but no api_key is available — set "
            f"the {_LLM_API_KEY_ENV_VAR} env var or [llm.anchor_provider].api_key"
        )
    if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise InputValidationError(
            f"[llm.anchor_provider].timeout_s must be a positive number, got {timeout_s!r}"
        )

    return LLMProviderConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_s=float(timeout_s),
        temperature=temperature,
        system_prompt=system_prompt,
        use_article_gen=use_article_gen,
        article_system_prompt=article_system_prompt,
        use_image_gen=use_image_gen,
        image_gen_api_key=image_gen_api_key,
    )


#: Filename of the WebUI's LLM settings sidecar, written by
#: ``webui_app.services.settings_service`` into the config dir.  Read here as a
#: lowest-priority fallback so settings entered in the WebUI actually drive
#: real pipeline runs (Pro Mode article generation).
_LLM_SIDECAR_FILENAME = "llm-settings.json"


def _opt_str_field(value: object) -> str | None:
    """Return a non-empty stripped string, else ``None``.

    Drops non-string values (a hand-edited sidecar could hold a list/number)
    so a bad optional field degrades to "use the built-in default" rather than
    propagating a non-string into the chat-message payload and failing at
    publish time.
    """
    return value.strip() or None if isinstance(value, str) else None


def _llm_provider_from_sidecar(config_dir: Path) -> LLMProviderConfig | None:
    """Best-effort fallback: build ``LLMProviderConfig`` from the WebUI's
    ``llm-settings.json`` sidecar in ``config_dir``.

    Unlike :func:`_parse_llm_anchor_provider` — which **raises** on a partial or
    invalid ``[llm.anchor_provider]`` TOML section because that signals operator
    misconfiguration — this reader is **fail-soft**: a missing file, malformed
    JSON, blank required field (``endpoint``/``api_key``/``model``), or a
    non-``https://`` endpoint all yield ``None`` (Pro Mode simply stays off) and
    **never raise**, so a bad settings file can't break ``load_config()`` for
    unrelated pipeline runs.

    This is the **lowest-priority** provider source. ``load_config`` calls it
    only when env vars and the TOML section produced nothing, giving the
    precedence env > TOML > sidecar.

    The ``endpoint`` field (WebUI key) maps to ``base_url`` (config field). The
    two image fields are carried through but are inert for article generation —
    image gen runs off a separate ``[image_gen]`` config + ``frw-token.json``.
    """
    sidecar = config_dir / _LLM_SIDECAR_FILENAME
    if not sidecar.exists():
        return None

    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _log.info(
            "%s present but unreadable/malformed; ignoring (Pro Mode off)",
            _LLM_SIDECAR_FILENAME,
        )
        return None
    if not isinstance(data, dict):
        return None

    endpoint = data.get("endpoint")
    api_key = data.get("api_key")
    model = data.get("model")
    # Required trio — all must be non-empty strings, else the sidecar is not a
    # usable provider config and we stay silent (the file ships with these blank
    # by default until the operator fills them in).
    if not (
        isinstance(endpoint, str) and endpoint.strip()
        and isinstance(api_key, str) and api_key.strip()
        and isinstance(model, str) and model.strip()
    ):
        return None

    endpoint = endpoint.strip().rstrip("/")
    if not endpoint.startswith("https://"):
        # Same https requirement the TOML parser enforces (prompt-injection /
        # credential-exfiltration posture) — but degrade instead of raising.
        _log.info(
            "%s endpoint is not https:// — ignoring (Pro Mode off). Re-save the "
            "LLM settings with an https endpoint to enable it.",
            _LLM_SIDECAR_FILENAME,
        )
        return None

    # temperature: coerce from JSON number; fall back to the parser's default on
    # anything non-numeric. ``bool`` is an ``int`` subclass, so exclude it
    # explicitly — ``"temperature": true`` should fall back, not become 1.0.
    temperature = 0.7
    raw_temp = data.get("temperature")
    if isinstance(raw_temp, (int, float)) and not isinstance(raw_temp, bool):
        temperature = float(raw_temp)

    return LLMProviderConfig(
        base_url=endpoint,
        api_key=api_key.strip(),
        model=model.strip(),
        timeout_s=30.0,
        temperature=temperature,
        # Empty strings (the WebUI defaults) and non-strings collapse to None so
        # the provider falls back to its built-in system prompts.
        system_prompt=_opt_str_field(data.get("system_prompt")),
        use_article_gen=bool(data.get("use_article_gen", False)),
        article_system_prompt=_opt_str_field(data.get("article_system_prompt")),
        use_image_gen=bool(data.get("use_image_gen", False)),
        image_gen_api_key=_opt_str_field(data.get("image_gen_api_key")),
    )
