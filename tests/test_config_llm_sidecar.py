"""Tests for the WebUI ``llm-settings.json`` -> ``LLMProviderConfig`` bridge.

Two layers:

- :func:`_llm_provider_from_sidecar` — the tolerant sidecar reader (Unit 1).
- :func:`load_config` fallback wiring + precedence env > TOML > sidecar (Unit 2).

The reader must be fail-soft: any malformed/incomplete/non-https sidecar yields
``None`` and never raises, so a bad settings file can't break config loading for
unrelated pipeline runs.
"""

import json
import os

import pytest

from backlink_publisher.config import load_config
from backlink_publisher.config.parsers.llm import _llm_provider_from_sidecar
from backlink_publisher.config.types import LLMProviderConfig


# Env vars the parser consults — cleared per test so the sidecar path is
# deterministic (env would otherwise win and shadow the sidecar).
_LLM_ENV_VARS = (
    "BACKLINK_LLM_API_KEY",
    "BACKLINK_LLM_BASE_URL",
    "BACKLINK_LLM_MODEL",
    "BACKLINK_LLM_TEMPERATURE",
    "BACKLINK_LLM_SYSTEM_PROMPT",
    "BACKLINK_LLM_USE_ARTICLE_GEN",
    "BACKLINK_LLM_ARTICLE_SYSTEM_PROMPT",
    "BACKLINK_LLM_USE_IMAGE_GEN",
    "BACKLINK_LLM_IMAGE_GEN_API_KEY",
)


@pytest.fixture(autouse=True)
def _clear_llm_env(monkeypatch):
    for var in _LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _write_sidecar(config_dir, payload):
    path = config_dir / "llm-settings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


_FULL_SETTINGS = {
    "endpoint": "https://api.openai.com/v1",
    "api_key": "sk-test-123",
    "model": "gpt-4o-mini",
    "temperature": 0.5,
    "system_prompt": "anchor prompt",
    "use_article_gen": True,
    "article_system_prompt": "article prompt",
    "image_gen_api_key": "frw-key",
    "use_image_gen": True,
}


# ── Unit 1: _llm_provider_from_sidecar ──────────────────────────────────────


def test_sidecar_happy_path_maps_all_fields(tmp_path):
    _write_sidecar(tmp_path, _FULL_SETTINGS)
    cfg = _llm_provider_from_sidecar(tmp_path)
    assert isinstance(cfg, LLMProviderConfig)
    # endpoint -> base_url is the only rename.
    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.api_key == "sk-test-123"
    assert cfg.model == "gpt-4o-mini"
    assert cfg.temperature == 0.5
    assert cfg.system_prompt == "anchor prompt"
    assert cfg.use_article_gen is True
    assert cfg.article_system_prompt == "article prompt"
    assert cfg.use_image_gen is True
    assert cfg.image_gen_api_key == "frw-key"


def test_sidecar_absent_timeout_defaults_to_30(tmp_path):
    _write_sidecar(tmp_path, _FULL_SETTINGS)
    cfg = _llm_provider_from_sidecar(tmp_path)
    assert cfg is not None
    assert cfg.timeout_s == 30.0


def test_sidecar_minimal_required_trio_only(tmp_path):
    _write_sidecar(
        tmp_path,
        {"endpoint": "https://x.test/v1", "api_key": "k", "model": "m"},
    )
    cfg = _llm_provider_from_sidecar(tmp_path)
    assert cfg is not None
    # Toggles default off; prompts collapse to None.
    assert cfg.use_article_gen is False
    assert cfg.use_image_gen is False
    assert cfg.system_prompt is None
    assert cfg.article_system_prompt is None
    assert cfg.temperature == 0.7


def test_sidecar_strips_and_rstrips_endpoint(tmp_path):
    _write_sidecar(
        tmp_path,
        {"endpoint": "  https://x.test/v1/  ", "api_key": "k", "model": "m"},
    )
    cfg = _llm_provider_from_sidecar(tmp_path)
    assert cfg is not None
    assert cfg.base_url == "https://x.test/v1"


def test_sidecar_missing_file_returns_none(tmp_path):
    assert _llm_provider_from_sidecar(tmp_path) is None


@pytest.mark.parametrize("blank_field", ["endpoint", "api_key", "model"])
def test_sidecar_blank_required_field_returns_none(tmp_path, blank_field):
    payload = {"endpoint": "https://x.test/v1", "api_key": "k", "model": "m"}
    payload[blank_field] = "   "  # whitespace-only counts as blank
    _write_sidecar(tmp_path, payload)
    assert _llm_provider_from_sidecar(tmp_path) is None


def test_sidecar_defaults_only_file_returns_none(tmp_path):
    # The shape the WebUI ships before the operator fills anything in.
    _write_sidecar(
        tmp_path,
        {
            "api_key": "",
            "endpoint": "",
            "model": "",
            "temperature": 0.7,
            "system_prompt": "",
            "use_article_gen": False,
            "article_system_prompt": "",
            "image_gen_api_key": "",
            "use_image_gen": False,
        },
    )
    assert _llm_provider_from_sidecar(tmp_path) is None


def test_sidecar_malformed_json_returns_none(tmp_path):
    (tmp_path / "llm-settings.json").write_text("{not json", encoding="utf-8")
    # Must NOT raise.
    assert _llm_provider_from_sidecar(tmp_path) is None


def test_sidecar_non_dict_json_returns_none(tmp_path):
    (tmp_path / "llm-settings.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert _llm_provider_from_sidecar(tmp_path) is None


def test_sidecar_http_endpoint_returns_none(tmp_path):
    _write_sidecar(
        tmp_path,
        {"endpoint": "http://insecure.test/v1", "api_key": "k", "model": "m"},
    )
    assert _llm_provider_from_sidecar(tmp_path) is None


def test_sidecar_bad_temperature_falls_back_to_default(tmp_path):
    _write_sidecar(
        tmp_path,
        {
            "endpoint": "https://x.test/v1",
            "api_key": "k",
            "model": "m",
            "temperature": "hot",
        },
    )
    cfg = _llm_provider_from_sidecar(tmp_path)
    assert cfg is not None
    assert cfg.temperature == 0.7


def test_sidecar_boolean_temperature_falls_back_to_default(tmp_path):
    # bool is an int subclass — a JSON boolean must NOT become 1.0/0.0.
    _write_sidecar(
        tmp_path,
        {
            "endpoint": "https://x.test/v1",
            "api_key": "k",
            "model": "m",
            "temperature": True,
        },
    )
    cfg = _llm_provider_from_sidecar(tmp_path)
    assert cfg is not None
    assert cfg.temperature == 0.7


@pytest.mark.parametrize("field", ["system_prompt", "article_system_prompt", "image_gen_api_key"])
def test_sidecar_non_string_optional_field_collapses_to_none(tmp_path, field):
    # A hand-edited sidecar with a non-string optional field must not propagate
    # the bad value into the provider (it would fail at publish time); degrade
    # to None so the built-in default is used.
    payload = {"endpoint": "https://x.test/v1", "api_key": "k", "model": "m"}
    payload[field] = ["not", "a", "string"]
    _write_sidecar(tmp_path, payload)
    cfg = _llm_provider_from_sidecar(tmp_path)
    assert cfg is not None
    assert getattr(cfg, field) is None


def test_sidecar_loose_perms_still_read(tmp_path):
    path = _write_sidecar(tmp_path, _FULL_SETTINGS)
    os.chmod(path, 0o644)
    cfg = _llm_provider_from_sidecar(tmp_path)
    assert cfg is not None  # readable regardless of perms; never raises


# ── Unit 2: load_config() fallback + precedence ─────────────────────────────


def test_load_config_uses_sidecar_when_no_toml_no_env(tmp_path):
    # No config.toml at all → load_config returns empty Config, but the sidecar
    # next to it should still populate the provider.
    config_toml = tmp_path / "config.toml"
    _write_sidecar(tmp_path, _FULL_SETTINGS)
    cfg = load_config(config_toml)
    assert cfg.llm_anchor_provider is not None
    assert cfg.llm_anchor_provider.base_url == "https://api.openai.com/v1"
    assert cfg.llm_anchor_provider.use_article_gen is True


def test_load_config_toml_section_wins_over_sidecar(tmp_path):
    config_toml = tmp_path / "config.toml"
    config_toml.write_text(
        "[llm.anchor_provider]\n"
        'base_url = "https://toml.example/v1"\n'
        'api_key = "***"\n'  # "***" placeholder: satisfies the leak-check hook
        'model = "toml-model"\n',
        encoding="utf-8",
    )
    _write_sidecar(tmp_path, _FULL_SETTINGS)
    cfg = load_config(config_toml)
    assert cfg.llm_anchor_provider is not None
    # TOML is the operator's explicit intent — it wins; sidecar ignored.
    assert cfg.llm_anchor_provider.base_url == "https://toml.example/v1"
    assert cfg.llm_anchor_provider.model == "toml-model"


def test_load_config_env_wins_over_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_LLM_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("BACKLINK_LLM_API_KEY", "env-key")
    monkeypatch.setenv("BACKLINK_LLM_MODEL", "env-model")
    config_toml = tmp_path / "config.toml"
    _write_sidecar(tmp_path, _FULL_SETTINGS)
    cfg = load_config(config_toml)
    assert cfg.llm_anchor_provider is not None
    assert cfg.llm_anchor_provider.base_url == "https://env.example/v1"
    assert cfg.llm_anchor_provider.model == "env-model"


def test_load_config_no_sources_leaves_provider_none(tmp_path):
    config_toml = tmp_path / "config.toml"
    config_toml.write_text("[blogger]\n", encoding="utf-8")
    cfg = load_config(config_toml)
    assert cfg.llm_anchor_provider is None


def test_load_config_bad_sidecar_does_not_break_load(tmp_path):
    config_toml = tmp_path / "config.toml"
    config_toml.write_text("[blogger]\n", encoding="utf-8")
    (tmp_path / "llm-settings.json").write_text("{bad json", encoding="utf-8")
    # Regression: a malformed sidecar must not make load_config raise.
    cfg = load_config(config_toml)
    assert cfg.llm_anchor_provider is None
