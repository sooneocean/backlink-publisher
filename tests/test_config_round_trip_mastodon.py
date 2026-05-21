"""Config round-trip tests for [mastodon] section — Plan 2026-05-21-001 Unit 4c.

Verifies that ``save_config(load_config(toml))`` preserves the
``[mastodon] instance_url`` entry. Closes the known save_config bug
(``CLAUDE.md`` § Config and environment) for one more section.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backlink_publisher.config.loader import load_config
from backlink_publisher.config.writer import save_config


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    return tmp_path


def test_load_then_save_preserves_mastodon_section(isolated_config):
    cfg_path = isolated_config / "config.toml"
    cfg_path.write_text(
        "[blogger]\n"
        "[medium]\n"
        "[mastodon]\n"
        "instance_url = \"https://mastodon.social\"\n"
    )

    cfg = load_config(cfg_path)
    assert cfg.mastodon is not None
    assert cfg.mastodon.instance_url == "https://mastodon.social"

    save_config(cfg, cfg_path)

    contents = cfg_path.read_text()
    assert "[mastodon]" in contents
    assert "https://mastodon.social" in contents

    # Reload to confirm the saved file is structurally valid + value preserved.
    cfg2 = load_config(cfg_path)
    assert cfg2.mastodon is not None
    assert cfg2.mastodon.instance_url == "https://mastodon.social"


def test_no_section_persists_no_section(isolated_config):
    cfg_path = isolated_config / "config.toml"
    cfg_path.write_text("[blogger]\n[medium]\n")

    cfg = load_config(cfg_path)
    assert cfg.mastodon is None

    save_config(cfg, cfg_path)

    cfg2 = load_config(cfg_path)
    assert cfg2.mastodon is None


def test_round_trip_does_not_duplicate_section(isolated_config):
    """The pre-Unit-4c bug: writer's _preserve_unknown_sections copied
    the existing [mastodon] block AND our new write emitted another one,
    producing duplicate TOML headings."""
    cfg_path = isolated_config / "config.toml"
    cfg_path.write_text(
        "[blogger]\n"
        "[medium]\n"
        "[mastodon]\n"
        "instance_url = \"https://chaos.social\"\n"
    )
    cfg = load_config(cfg_path)
    save_config(cfg, cfg_path)
    contents = cfg_path.read_text()
    assert contents.count("[mastodon]") == 1, (
        f"expected exactly one [mastodon] header, got:\n{contents}"
    )


def test_save_config_explicit_mastodon_override(isolated_config):
    """save_config(mastodon_config=...) overrides the loaded config field."""
    from backlink_publisher.config.types import MastodonConfig

    cfg_path = isolated_config / "config.toml"
    cfg_path.write_text(
        "[blogger]\n[medium]\n[mastodon]\ninstance_url = \"https://old.example\"\n"
    )
    cfg = load_config(cfg_path)

    save_config(
        cfg,
        cfg_path,
        mastodon_config=MastodonConfig(instance_url="https://new.example"),
    )
    cfg2 = load_config(cfg_path)
    assert cfg2.mastodon is not None
    assert cfg2.mastodon.instance_url == "https://new.example"
