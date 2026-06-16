"""Tests for per-target GEO config (Plan 2026-05-29-006 Unit 1).

Covers per-target ``probe_queries`` / ``brand_aliases`` parsing (tolerant
skip+warn on malformed entries, trailing-slash key normalisation) and the
``save_config`` round-trip that must preserve ``[geo.probe_provider]``, the
per-target GEO keys, AND every pre-existing section through the writer's
``[targets.*]`` regeneration loop (F2).

Credential-shaped fixture values are assembled at runtime via concatenation so
the literal ``api_key = "<value>"`` never appears in source (leak-check hook).
"""

from __future__ import annotations

import logging

import pytest

from backlink_publisher.config import Config, load_config, save_config
from backlink_publisher.config.parsers.target import (
    _parse_target_string_list_field,
)

# Fake credential VALUES built at runtime so the ``api_key = "<value>"`` shape
# never lands in source (git-leak-check hook).
_GEO_KEY = "pk-" + "geofixture"
_LLM_KEY = "lk-" + "llmfixture"
_MEDIUM_ATOK = "at-" + "mediumfixture"


# ── Per-target field parsing ─────────────────────────────────────────────────


def test_probe_queries_happy_path() -> None:
    section = {
        "https://example.com": {
            "probe_queries": ["best widgets", "widget reviews"],
        },
    }
    result = _parse_target_string_list_field(section, "probe_queries")
    assert result == {"https://example.com": ["best widgets", "widget reviews"]}


def test_brand_aliases_happy_path() -> None:
    section = {
        "https://example.com": {"brand_aliases": ["Acme", "Acme Corp"]},
    }
    result = _parse_target_string_list_field(section, "brand_aliases")
    assert result == {"https://example.com": ["Acme", "Acme Corp"]}


def test_trailing_slash_key_normalised() -> None:
    section = {
        "https://example.com/": {"probe_queries": ["q1"]},
    }
    result = _parse_target_string_list_field(section, "probe_queries")
    assert result == {"https://example.com": ["q1"]}


def test_missing_field_skipped_silently() -> None:
    """An entry with no probe_queries key contributes nothing (no warning)."""
    section = {"https://example.com": {"anchor_keywords": ["a"]}}
    result = _parse_target_string_list_field(section, "probe_queries")
    assert result == {}


def test_empty_strings_dropped_after_strip() -> None:
    section = {
        "https://example.com": {"probe_queries": ["  spaced  ", "", "   "]},
    }
    result = _parse_target_string_list_field(section, "probe_queries")
    assert result == {"https://example.com": ["spaced"]}


def test_malformed_entry_skipped_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-list / non-string-list value is skipped with a WARN; valid
    sibling entries are kept (tolerant contract)."""
    section = {
        "https://bad.com": {"probe_queries": "not-a-list"},
        "https://mixed.com": {"probe_queries": ["ok", 123]},
        "https://good.com": {"probe_queries": ["valid query"]},
    }
    with caplog.at_level(logging.WARNING):
        result = _parse_target_string_list_field(section, "probe_queries")
    assert result == {"https://good.com": ["valid query"]}
    msgs = " ".join(rec.getMessage() for rec in caplog.records)
    assert "probe_queries" in msgs
    assert "https://bad.com" in msgs or "bad.com" in msgs


def test_non_table_entry_skipped_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    section = {"https://notatable.com": "scalar"}
    with caplog.at_level(logging.WARNING):
        result = _parse_target_string_list_field(section, "brand_aliases")
    assert result == {}
    assert any("not a table" in rec.getMessage() for rec in caplog.records)


def test_non_dict_section_returns_empty() -> None:
    assert _parse_target_string_list_field(None, "probe_queries") == {}
    assert _parse_target_string_list_field("nope", "brand_aliases") == {}


# ── load_config end-to-end ───────────────────────────────────────────────────


def test_load_config_per_target_geo_keys(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[targets."https://example.com"]\n'
        'anchor_keywords = ["kw"]\n'
        'probe_queries = ["q1", "q2"]\n'
        'brand_aliases = ["Acme"]\n',
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    assert cfg.target_probe_queries == {"https://example.com": ["q1", "q2"]}
    assert cfg.target_brand_aliases == {"https://example.com": ["Acme"]}


# ── save_config round-trip preservation (F2) ────────────────────────────────


def test_round_trip_preserves_geo_and_pre_existing_sections(tmp_path) -> None:
    """save_config → load → save → load preserves [geo.probe_provider]
    (unknown root, verbatim), per-target probe_queries / brand_aliases
    (emitted in the writer's [targets.*] loop), AND every pre-existing
    section (blogger / medium.oauth / anchor_alarm / llm.anchor_provider)."""
    config_path = tmp_path / "config.toml"
    # Seed a realistic file with a mix of managed + unmanaged sections.
    config_path.write_text(
        "[blogger]\n"
        '"https://example.com" = "111"\n'
        "\n"
        "[medium]\n"
        "\n"
        "[medium.oauth]\n"
        f'access_token = "{_MEDIUM_ATOK}"\n'
        "\n"
        "[anchor_alarm]\n"
        "entropy_floor = 1.5\n"
        "\n"
        "[llm.anchor_provider]\n"
        'base_url = "https://api.openai.com/v1"\n'
        'model = "gpt-4o-mini"\n'
        f'api_key = "{_LLM_KEY}"\n'
        "\n"
        "[geo.probe_provider]\n"
        'base_url = "https://api.perplexity.ai"\n'
        f'api_key = "{_GEO_KEY}"\n'
        'model = "sonar"\n'
        "\n"
        '[targets."https://example.com"]\n'
        'anchor_keywords = ["kw"]\n'
        'probe_queries = ["best widgets", "widget reviews"]\n'
        'brand_aliases = ["Acme", "Acme Corp"]\n',
        encoding="utf-8",
    )

    # Cycle 1: load then save threading per-target GEO keys by keyword.
    cfg1 = load_config(config_path)
    save_config(
        cfg1,
        path=config_path,
        target_anchor_keywords=cfg1.target_anchor_keywords,
        target_probe_queries=cfg1.target_probe_queries,
        target_brand_aliases=cfg1.target_brand_aliases,
    )

    # Cycle 2: save again with no kwargs — must preserve from existing on disk.
    cfg2 = load_config(config_path)
    save_config(cfg2, path=config_path)

    final = load_config(config_path)
    text = config_path.read_text(encoding="utf-8")

    # [geo.probe_provider] survives (unknown root, preserved verbatim).
    assert final.geo_probe_provider is not None
    assert final.geo_probe_provider.base_url == "https://api.perplexity.ai"
    assert final.geo_probe_provider.model == "sonar"
    assert "[geo.probe_provider]" in text

    # Per-target probe_queries / brand_aliases survive the writer loop.
    assert final.target_probe_queries == {
        "https://example.com": ["best widgets", "widget reviews"]
    }
    assert final.target_brand_aliases == {
        "https://example.com": ["Acme", "Acme Corp"]
    }

    # Every pre-existing section survives.
    assert final.blogger_blog_ids["https://example.com"] == "111"
    assert "[medium.oauth]" in text and _MEDIUM_ATOK in text
    assert "[anchor_alarm]" in text and "entropy_floor" in text
    assert "[llm.anchor_provider]" in text
    assert final.target_anchor_keywords == {"https://example.com": ["kw"]}


def test_round_trip_geo_keys_without_three_url(tmp_path) -> None:
    """A target that has ONLY probe_queries / brand_aliases (no anchor_keywords
    and no three-URL config) still gets a [targets.<domain>] block emitted so
    the GEO keys survive the writer regeneration."""
    config_path = tmp_path / "config.toml"
    save_config(
        Config(),
        path=config_path,
        target_probe_queries={"https://geo-only.com": ["q1"]},
        target_brand_aliases={"https://geo-only.com": ["Brand"]},
    )
    reloaded = load_config(config_path)
    assert reloaded.target_probe_queries == {"https://geo-only.com": ["q1"]}
    assert reloaded.target_brand_aliases == {"https://geo-only.com": ["Brand"]}

    # Save again with no kwargs — preserved from on-disk values.
    save_config(reloaded, path=config_path)
    again = load_config(config_path)
    assert again.target_probe_queries == {"https://geo-only.com": ["q1"]}
    assert again.target_brand_aliases == {"https://geo-only.com": ["Brand"]}


def test_empty_dict_clears_per_target_geo(tmp_path) -> None:
    """Passing an empty dict clears per-target GEO keys (three-state)."""
    config_path = tmp_path / "config.toml"
    save_config(
        Config(),
        path=config_path,
        target_probe_queries={"https://example.com": ["q1"]},
    )
    assert load_config(config_path).target_probe_queries == {
        "https://example.com": ["q1"]
    }
    save_config(load_config(config_path), path=config_path, target_probe_queries={})
    assert load_config(config_path).target_probe_queries == {}
