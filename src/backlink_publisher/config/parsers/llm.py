"""LLM anchor provider parser."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from ...errors import InputValidationError
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

    Enforces ``https://`` on ``base_url`` and warns if config.toml contains
    ``api_key`` but its file permissions are not 0600.
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

    if toml_has_api_key and config_path is not None and config_path.exists():
        from ..loader import _warn_if_loose_config_permissions
        _warn_if_loose_config_permissions(config_path)

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
