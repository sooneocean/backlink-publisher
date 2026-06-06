"""Unit tests for config/parsers/llm.py — _parse_llm_anchor_provider + helpers.

No file I/O: _parse_llm_anchor_provider takes a plain dict and env vars.
Env vars are injected via monkeypatch so tests are hermetic.
"""

from __future__ import annotations

import pytest

from backlink_publisher._util.errors import InputValidationError
from backlink_publisher.config.parsers.llm import (
    _parse_bool_flag,
    _parse_llm_anchor_provider,
    _parse_temperature,
)
from backlink_publisher.config.types import LLMProviderConfig


# ── _parse_bool_flag ──────────────────────────────────────────────────────────


class TestParseBoolFlag:
    def test_env_true_values(self) -> None:
        for v in ("1", "true", "yes", "TRUE", "YES"):
            assert _parse_bool_flag(v, {}, "key") is True

    def test_env_false_values(self) -> None:
        for v in ("0", "false", "no", ""):
            assert _parse_bool_flag(v or None, {}, "key") is False

    def test_env_wins_over_section(self) -> None:
        # env says False, section says True → env wins
        assert _parse_bool_flag("0", {"key": True}, "key") is False

    def test_section_fallback_when_no_env(self) -> None:
        assert _parse_bool_flag(None, {"key": True}, "key") is True
        assert _parse_bool_flag(None, {"key": False}, "key") is False

    def test_default_false_when_absent(self) -> None:
        assert _parse_bool_flag(None, {}, "key") is False


# ── _parse_temperature ────────────────────────────────────────────────────────


class TestParseTemperature:
    def test_env_float_used(self) -> None:
        assert _parse_temperature("0.5", None) == 0.5

    def test_env_invalid_falls_through_to_toml(self) -> None:
        assert _parse_temperature("bad", 0.3) == 0.3

    def test_env_invalid_no_toml_gives_default(self) -> None:
        assert _parse_temperature("bad", None) == 0.7

    def test_toml_float_used_when_no_env(self) -> None:
        assert _parse_temperature(None, 0.2) == 0.2

    def test_toml_int_coerced_to_float(self) -> None:
        assert _parse_temperature(None, 1) == 1.0

    def test_default_when_both_absent(self) -> None:
        assert _parse_temperature(None, None) == 0.7

    def test_toml_string_ignored_falls_to_default(self) -> None:
        assert _parse_temperature(None, "0.5") == 0.7


# ── _parse_llm_anchor_provider ────────────────────────────────────────────────


_VALID = {
    "base_url": "https://api.example.com/v1",
    "api_key": "sk-test",
    "model": "gpt-4",
}


class TestParseLlmAnchorProviderNone:
    def test_empty_section_no_env_returns_none(self, monkeypatch) -> None:
        for k in (
            "BACKLINK_LLM_API_KEY", "BACKLINK_LLM_BASE_URL", "BACKLINK_LLM_MODEL",
        ):
            monkeypatch.delenv(k, raising=False)
        assert _parse_llm_anchor_provider({}) is None

    def test_non_dict_section_treated_as_empty(self, monkeypatch) -> None:
        for k in ("BACKLINK_LLM_API_KEY", "BACKLINK_LLM_BASE_URL", "BACKLINK_LLM_MODEL"):
            monkeypatch.delenv(k, raising=False)
        assert _parse_llm_anchor_provider(None) is None

    def test_missing_section_no_env_returns_none(self, monkeypatch) -> None:
        for k in ("BACKLINK_LLM_API_KEY", "BACKLINK_LLM_BASE_URL", "BACKLINK_LLM_MODEL"):
            monkeypatch.delenv(k, raising=False)
        assert _parse_llm_anchor_provider({}) is None


class TestParseLlmAnchorProviderHappyPath:
    def test_valid_section_returns_config(self, monkeypatch) -> None:
        for k in ("BACKLINK_LLM_API_KEY", "BACKLINK_LLM_BASE_URL", "BACKLINK_LLM_MODEL",
                  "BACKLINK_LLM_TEMPERATURE"):
            monkeypatch.delenv(k, raising=False)
        result = _parse_llm_anchor_provider(_VALID)
        assert isinstance(result, LLMProviderConfig)

    def test_base_url_and_model_and_api_key_in_result(self, monkeypatch) -> None:
        for k in ("BACKLINK_LLM_API_KEY", "BACKLINK_LLM_BASE_URL", "BACKLINK_LLM_MODEL"):
            monkeypatch.delenv(k, raising=False)
        r = _parse_llm_anchor_provider(_VALID)
        assert r.base_url == "https://api.example.com/v1"
        assert r.model == "gpt-4"
        assert r.api_key == "sk-test"

    def test_env_api_key_wins_over_toml(self, monkeypatch) -> None:
        monkeypatch.setenv("BACKLINK_LLM_API_KEY", "env-key")
        for k in ("BACKLINK_LLM_BASE_URL", "BACKLINK_LLM_MODEL"):
            monkeypatch.delenv(k, raising=False)
        r = _parse_llm_anchor_provider(_VALID)
        assert r.api_key == "env-key"

    def test_env_base_url_wins(self, monkeypatch) -> None:
        monkeypatch.setenv("BACKLINK_LLM_BASE_URL", "https://env.example.com/v1")
        for k in ("BACKLINK_LLM_API_KEY", "BACKLINK_LLM_MODEL"):
            monkeypatch.delenv(k, raising=False)
        r = _parse_llm_anchor_provider(_VALID)
        assert r.base_url == "https://env.example.com/v1"

    def test_env_model_wins(self, monkeypatch) -> None:
        monkeypatch.setenv("BACKLINK_LLM_MODEL", "claude-3")
        for k in ("BACKLINK_LLM_API_KEY", "BACKLINK_LLM_BASE_URL"):
            monkeypatch.delenv(k, raising=False)
        r = _parse_llm_anchor_provider(_VALID)
        assert r.model == "claude-3"

    def test_default_temperature(self, monkeypatch) -> None:
        monkeypatch.delenv("BACKLINK_LLM_TEMPERATURE", raising=False)
        r = _parse_llm_anchor_provider(_VALID)
        assert r.temperature == 0.7

    def test_env_temperature_used(self, monkeypatch) -> None:
        monkeypatch.setenv("BACKLINK_LLM_TEMPERATURE", "0.3")
        for k in ("BACKLINK_LLM_API_KEY", "BACKLINK_LLM_BASE_URL", "BACKLINK_LLM_MODEL"):
            monkeypatch.delenv(k, raising=False)
        r = _parse_llm_anchor_provider(_VALID)
        assert r.temperature == pytest.approx(0.3)

    def test_toml_temperature_used(self, monkeypatch) -> None:
        monkeypatch.delenv("BACKLINK_LLM_TEMPERATURE", raising=False)
        r = _parse_llm_anchor_provider({**_VALID, "temperature": 0.1})
        assert r.temperature == pytest.approx(0.1)

    def test_use_article_gen_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("BACKLINK_LLM_USE_ARTICLE_GEN", "true")
        for k in ("BACKLINK_LLM_API_KEY", "BACKLINK_LLM_BASE_URL", "BACKLINK_LLM_MODEL"):
            monkeypatch.delenv(k, raising=False)
        r = _parse_llm_anchor_provider(_VALID)
        assert r.use_article_gen is True

    def test_use_article_gen_from_section(self, monkeypatch) -> None:
        monkeypatch.delenv("BACKLINK_LLM_USE_ARTICLE_GEN", raising=False)
        r = _parse_llm_anchor_provider({**_VALID, "use_article_gen": True})
        assert r.use_article_gen is True

    def test_use_image_gen_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("BACKLINK_LLM_USE_IMAGE_GEN", "1")
        for k in ("BACKLINK_LLM_API_KEY", "BACKLINK_LLM_BASE_URL", "BACKLINK_LLM_MODEL"):
            monkeypatch.delenv(k, raising=False)
        r = _parse_llm_anchor_provider(_VALID)
        assert r.use_image_gen is True

    def test_timeout_s_preserved(self, monkeypatch) -> None:
        monkeypatch.delenv("BACKLINK_LLM_TEMPERATURE", raising=False)
        r = _parse_llm_anchor_provider({**_VALID, "timeout_s": 60})
        assert r.timeout_s == 60.0


class TestParseLlmAnchorProviderValidation:
    def test_http_base_url_raises(self, monkeypatch) -> None:
        for k in ("BACKLINK_LLM_API_KEY", "BACKLINK_LLM_BASE_URL", "BACKLINK_LLM_MODEL"):
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(InputValidationError, match="https://"):
            _parse_llm_anchor_provider({**_VALID, "base_url": "http://api.example.com"})

    def test_missing_base_url_raises(self, monkeypatch) -> None:
        for k in ("BACKLINK_LLM_API_KEY", "BACKLINK_LLM_BASE_URL", "BACKLINK_LLM_MODEL"):
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(InputValidationError, match="base_url"):
            _parse_llm_anchor_provider({**_VALID, "base_url": ""})

    def test_missing_model_raises(self, monkeypatch) -> None:
        for k in ("BACKLINK_LLM_API_KEY", "BACKLINK_LLM_BASE_URL", "BACKLINK_LLM_MODEL"):
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(InputValidationError, match="model"):
            _parse_llm_anchor_provider({**_VALID, "model": ""})

    def test_missing_api_key_raises(self, monkeypatch) -> None:
        for k in ("BACKLINK_LLM_API_KEY",):
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(InputValidationError, match="api_key"):
            _parse_llm_anchor_provider({**_VALID, "api_key": ""})

    def test_invalid_timeout_raises(self, monkeypatch) -> None:
        for k in ("BACKLINK_LLM_API_KEY", "BACKLINK_LLM_BASE_URL", "BACKLINK_LLM_MODEL"):
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(InputValidationError, match="timeout_s"):
            _parse_llm_anchor_provider({**_VALID, "timeout_s": -1})

    def test_zero_timeout_raises(self, monkeypatch) -> None:
        for k in ("BACKLINK_LLM_API_KEY", "BACKLINK_LLM_BASE_URL", "BACKLINK_LLM_MODEL"):
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(InputValidationError, match="timeout_s"):
            _parse_llm_anchor_provider({**_VALID, "timeout_s": 0})
