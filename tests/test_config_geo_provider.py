"""Tests for the GEO probe-provider parser (Plan 2026-05-29-006 Unit 1).

Covers ``[geo.probe_provider]`` three-state parsing, ``https://``-only
enforcement, ``BACKLINK_GEO_*`` env precedence, and D0's hard rule that a
populated LLM key with no GEO section never enables probing — there is no
fallback to ``BACKLINK_LLM_API_KEY``.

Credential-shaped fixture values are built at runtime via concatenation so the
literal ``api_key = "<value>"`` never appears in source (git-leak-check hook).
"""

from __future__ import annotations

import logging

import pytest

from backlink_publisher.config import Config, load_config
from backlink_publisher.config.types import GeoProbeConfig
from backlink_publisher.config.parsers.geo import (
    _GEO_API_KEY_ENV_VAR,
    _GEO_BASE_URL_ENV_VAR,
    _GEO_MODEL_ENV_VAR,
    _parse_geo_probe_provider,
)
from backlink_publisher._util.errors import InputValidationError

_LLM_API_KEY_ENV_VAR = "BACKLINK_LLM_API_KEY"

# Fake credential VALUES assembled at runtime so the literal
# ``api_key = "<value>"`` shape never lands in source (leak-check hook).
_GEO_KEY = "pk-" + "geofixture"
_LLM_KEY = "lk-" + "llmfixture"


@pytest.fixture(autouse=True)
def _clear_geo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from a clean GEO/LLM env baseline.

    Uses ``monkeypatch.delenv`` (never bare ``del os.environ``) so the
    restoration is automatic and order-independent.
    """
    for var in (
        _GEO_API_KEY_ENV_VAR,
        _GEO_BASE_URL_ENV_VAR,
        _GEO_MODEL_ENV_VAR,
        _LLM_API_KEY_ENV_VAR,
    ):
        monkeypatch.delenv(var, raising=False)


def _full_section() -> dict[str, object]:
    return {
        "base_url": "https://api.perplexity.ai",
        "api_key": _GEO_KEY,
        "model": "sonar",
    }


# ── Happy path ───────────────────────────────────────────────────────────────


def test_full_section_returns_populated_config() -> None:
    cfg = _parse_geo_probe_provider(_full_section())
    assert isinstance(cfg, GeoProbeConfig)
    assert cfg.base_url == "https://api.perplexity.ai"
    assert cfg.api_key == _GEO_KEY
    assert cfg.model == "sonar"
    assert cfg.timeout_s == 30.0  # default


def test_custom_timeout_preserved() -> None:
    section = _full_section()
    section["timeout_s"] = 12.5
    cfg = _parse_geo_probe_provider(section)
    assert cfg is not None
    assert cfg.timeout_s == 12.5


# ── Three-state: absent / empty ──────────────────────────────────────────────


def test_section_absent_returns_none() -> None:
    """A non-dict / missing section with no env → silent no-op (None)."""
    assert _parse_geo_probe_provider(None) is None


def test_empty_section_returns_none_distinct_from_populated() -> None:
    """An empty ``{}`` is a distinct outcome from a populated config: None."""
    result = _parse_geo_probe_provider({})
    assert result is None
    # Distinctness witness: a populated section is NOT None.
    assert _parse_geo_probe_provider(_full_section()) is not None


# ── Missing-field / non-https errors ─────────────────────────────────────────


def test_missing_base_url_with_others_present_raises() -> None:
    section = {"api_key": _GEO_KEY, "model": "sonar"}
    with pytest.raises(InputValidationError, match="base_url is required"):
        _parse_geo_probe_provider(section)


def test_missing_model_with_others_present_raises() -> None:
    section = {"base_url": "https://api.perplexity.ai", "api_key": _GEO_KEY}
    with pytest.raises(InputValidationError, match="model is required"):
        _parse_geo_probe_provider(section)


def test_missing_api_key_with_others_present_raises() -> None:
    section = {"base_url": "https://api.perplexity.ai", "model": "sonar"}
    with pytest.raises(InputValidationError, match="no api_key is available"):
        _parse_geo_probe_provider(section)


def test_non_https_base_url_raises() -> None:
    section = _full_section()
    section["base_url"] = "http://api.perplexity.ai"
    with pytest.raises(InputValidationError, match="must use https://"):
        _parse_geo_probe_provider(section)


def test_non_positive_timeout_raises() -> None:
    section = _full_section()
    section["timeout_s"] = 0
    with pytest.raises(InputValidationError, match="positive number"):
        _parse_geo_probe_provider(section)


# ── BACKLINK_GEO_* env precedence ────────────────────────────────────────────


def test_geo_env_overrides_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_GEO_BASE_URL_ENV_VAR, "https://env.example.com")
    monkeypatch.setenv(_GEO_API_KEY_ENV_VAR, _GEO_KEY + "-env")
    monkeypatch.setenv(_GEO_MODEL_ENV_VAR, "env-model")
    cfg = _parse_geo_probe_provider(_full_section())
    assert cfg is not None
    assert cfg.base_url == "https://env.example.com"
    assert cfg.api_key == _GEO_KEY + "-env"
    assert cfg.model == "env-model"


def test_geo_env_alone_enables_with_no_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env vars alone (no TOML section) populate the config (full override)."""
    monkeypatch.setenv(_GEO_BASE_URL_ENV_VAR, "https://env.example.com")
    monkeypatch.setenv(_GEO_API_KEY_ENV_VAR, _GEO_KEY)
    monkeypatch.setenv(_GEO_MODEL_ENV_VAR, "env-model")
    cfg = _parse_geo_probe_provider({})
    assert cfg is not None
    assert cfg.base_url == "https://env.example.com"


# ── D0: LLM key alone does NOT enable GEO ────────────────────────────────────


def test_llm_key_alone_does_not_enable_geo(monkeypatch: pytest.MonkeyPatch) -> None:
    """A populated BACKLINK_LLM_API_KEY with no GEO config returns None (D0)."""
    monkeypatch.setenv(_LLM_API_KEY_ENV_VAR, _LLM_KEY)
    assert _parse_geo_probe_provider({}) is None
    assert _parse_geo_probe_provider(None) is None


def test_llm_key_never_fills_geo_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """With base_url+model present but only an LLM key set, the GEO key is not
    borrowed from the LLM env var — the missing-key error fires instead (D0)."""
    monkeypatch.setenv(_LLM_API_KEY_ENV_VAR, _LLM_KEY)
    section = {"base_url": "https://api.perplexity.ai", "model": "sonar"}
    with pytest.raises(InputValidationError, match="no api_key is available"):
        _parse_geo_probe_provider(section)


# ── Loose-permission warning fires for the GEO key (S4) ──────────────────────


def test_loose_permission_warning_fires_for_geo_key(
    tmp_path, caplog: pytest.LogCaptureFixture,
) -> None:
    """When config.toml carries a GEO api_key but is not 0600, the shared
    ``_warn_if_loose_config_permissions`` warning fires (S4)."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("# placeholder\n", encoding="utf-8")
    config_path.chmod(0o644)  # deliberately loose
    with caplog.at_level(logging.WARNING):
        cfg = _parse_geo_probe_provider(_full_section(), config_path=config_path)
    assert cfg is not None
    assert any("0600" in rec.getMessage() for rec in caplog.records)


# ── End-to-end via load_config ───────────────────────────────────────────────


def test_load_config_with_geo_section(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[geo.probe_provider]\n"
        'base_url = "https://api.perplexity.ai"\n'
        f'api_key = "{_GEO_KEY}"\n'
        'model = "sonar"\n',
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    assert cfg.geo_probe_provider is not None
    assert cfg.geo_probe_provider.base_url == "https://api.perplexity.ai"
    assert cfg.geo_probe_provider.model == "sonar"


def test_load_config_no_geo_is_none(tmp_path) -> None:
    """No-credential install: load with no GEO config / no GEO env succeeds."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[blogger]\n"
        '"https://example.com" = "111"\n',
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    assert cfg.geo_probe_provider is None


def test_load_config_llm_only_does_not_enable_geo(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config with a full [llm.anchor_provider] but no [geo.*] never enables
    GEO — end-to-end through load_config (D0)."""
    monkeypatch.setenv(_LLM_API_KEY_ENV_VAR, _LLM_KEY)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[llm.anchor_provider]\n"
        'base_url = "https://api.openai.com/v1"\n'
        'model = "gpt-4o-mini"\n',
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    assert cfg.geo_probe_provider is None


def test_missing_file_load_has_no_geo(tmp_path) -> None:
    """Missing config file → empty Config with geo_probe_provider None."""
    cfg = load_config(tmp_path / "nonexistent.toml")
    assert cfg.geo_probe_provider is None
    assert isinstance(cfg, Config)
