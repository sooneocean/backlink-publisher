"""Tests for config loader."""

import json
import os
import stat
from pathlib import Path

import pytest

from backlink_publisher.config import (
    Config,
    load_config,
    load_blogger_token,
    resolve_blog_id,
    save_blogger_token,
)
from backlink_publisher.errors import DependencyError


SAMPLE_TOML = b"""
[blogger]
"https://my-site.com" = "1234567890"
"https://other-site.org/" = "9876543210"

[blogger.oauth]
client_id = "my-client-id"
client_secret = "my-client-secret"

[medium]
integration_token = "abc-token"
"""


def test_load_config_missing_file(tmp_path):
    cfg = load_config(tmp_path / "nonexistent.toml")
    assert cfg.blogger_blog_ids == {}
    assert cfg.blogger_oauth is None
    assert cfg.medium_integration_token is None


def test_load_config_parses_blogger_and_medium(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(SAMPLE_TOML)
    cfg = load_config(cfg_path)

    assert cfg.blogger_blog_ids["https://my-site.com"] == "1234567890"
    assert cfg.blogger_oauth is not None
    assert cfg.blogger_oauth.client_id == "my-client-id"
    assert cfg.medium_integration_token == "abc-token"


def test_load_config_corrupt_toml(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(b"[[not valid toml")
    with pytest.raises(DependencyError, match="Failed to parse"):
        load_config(cfg_path)


def test_resolve_blog_id_found(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(SAMPLE_TOML)
    cfg = load_config(cfg_path)

    assert resolve_blog_id(cfg, "https://my-site.com") == "1234567890"
    # trailing slash normalisation
    assert resolve_blog_id(cfg, "https://my-site.com/") == "1234567890"


def test_resolve_blog_id_with_trailing_slash_in_config(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(SAMPLE_TOML)
    cfg = load_config(cfg_path)
    # Configured as "https://other-site.org/" (with slash)
    assert resolve_blog_id(cfg, "https://other-site.org") == "9876543210"
    assert resolve_blog_id(cfg, "https://other-site.org/") == "9876543210"


def test_resolve_blog_id_missing(tmp_path):
    cfg = Config()
    with pytest.raises(DependencyError, match="https://unknown.com"):
        resolve_blog_id(cfg, "https://unknown.com")


def test_save_and_load_blogger_token(tmp_path):
    token_path = tmp_path / "blogger-token.json"
    data = {"token": "tok123", "refresh_token": "ref456"}
    save_blogger_token(data, token_path)

    loaded = load_blogger_token(token_path)
    assert loaded == data


def test_save_blogger_token_mode_0600(tmp_path):
    token_path = tmp_path / "blogger-token.json"
    save_blogger_token({"token": "x"}, token_path)

    mode = oct(os.stat(token_path).st_mode)[-3:]
    # 600 on Unix; skip check on Windows
    if os.name != "nt":
        assert mode == "600", f"Expected 600, got {mode}"


def test_load_blogger_token_missing(tmp_path):
    result = load_blogger_token(tmp_path / "nonexistent.json")
    assert result is None
