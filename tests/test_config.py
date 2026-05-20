"""Tests for config loader."""

import json
import os
import stat
from pathlib import Path

import pytest

from backlink_publisher.config import (
    Config,
    get_anchor_keywords,
    load_config,
    load_blogger_token,
    resolve_blog_id,
    save_blogger_token,
    save_config,
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
    assert loaded == {**data, "token_rev": 1}


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


# ---------------------------------------------------------------------------
# target_anchor_keywords parsing
# ---------------------------------------------------------------------------

ANCHOR_TOML = b"""
[targets."https://my-site.com"]
anchor_keywords = ["brand", "head term", "long tail phrase"]

[targets."https://other-site.org/"]
anchor_keywords = ["other-brand", "industry word"]
"""


def test_target_anchor_keywords_parsed(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(ANCHOR_TOML)
    cfg = load_config(cfg_path)

    assert cfg.target_anchor_keywords["https://my-site.com"] == [
        "brand", "head term", "long tail phrase",
    ]
    # Trailing slash stripped at parse time
    assert cfg.target_anchor_keywords["https://other-site.org"] == [
        "other-brand", "industry word",
    ]


def test_get_anchor_keywords_normalises_trailing_slash(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(ANCHOR_TOML)
    cfg = load_config(cfg_path)

    # Lookup tolerates either form
    assert get_anchor_keywords(cfg, "https://my-site.com") == [
        "brand", "head term", "long tail phrase",
    ]
    assert get_anchor_keywords(cfg, "https://my-site.com/") == [
        "brand", "head term", "long tail phrase",
    ]


def test_get_anchor_keywords_missing_returns_empty(tmp_path):
    cfg = Config()
    assert get_anchor_keywords(cfg, "https://unknown.com") == []


def test_target_anchor_keywords_section_absent(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(SAMPLE_TOML)  # no [targets] section
    cfg = load_config(cfg_path)
    assert cfg.target_anchor_keywords == {}


def test_target_anchor_keywords_invalid_type_skipped(tmp_path, caplog):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(b"""
[targets."https://bad.com"]
anchor_keywords = "should-be-a-list"

[targets."https://good.com"]
anchor_keywords = ["foo"]
""")
    with caplog.at_level("WARNING"):
        cfg = load_config(cfg_path)

    # Bad entry skipped, good entry kept
    assert "https://bad.com" not in cfg.target_anchor_keywords
    assert cfg.target_anchor_keywords["https://good.com"] == ["foo"]
    # Warning emitted for the malformed entry
    assert any("anchor_keywords" in rec.message for rec in caplog.records)


def test_target_anchor_keywords_empty_list_kept(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(b"""
[targets."https://empty.com"]
anchor_keywords = []
""")
    cfg = load_config(cfg_path)
    # Empty list is preserved verbatim — caller (selector) treats as "no pool"
    assert cfg.target_anchor_keywords["https://empty.com"] == []
    assert get_anchor_keywords(cfg, "https://empty.com") == []


def test_anchor_keyword_unsafe_chars_stripped(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(b"""
[targets."https://example.com"]
anchor_keywords = ["legit keyword", "bad](link", "has<tag>here"]
""")
    cfg = load_config(cfg_path)
    kws = cfg.target_anchor_keywords["https://example.com"]
    assert "legit keyword" in kws
    # Dangerous chars stripped — ]( and < must not appear in any keyword
    assert all("](" not in k and "<" not in k for k in kws)


def test_save_config_preserves_targets_section(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(b"""
[blogger]
"https://site.com" = "111"

[targets."https://site.com"]
anchor_keywords = ["brand", "head term"]
""")
    from backlink_publisher.config import save_config
    cfg = load_config(cfg_path)
    # Saving blogger token should NOT wipe [targets]
    save_config(cfg, path=cfg_path, medium_token="tok123")
    cfg2 = load_config(cfg_path)
    assert get_anchor_keywords(cfg2, "https://site.com") == ["brand", "head term"]


def test_get_anchor_keywords_tolerates_scheme_mismatch(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(b"""
[targets."https://example.com"]
anchor_keywords = ["kw1", "kw2"]
""")
    cfg = load_config(cfg_path)
    # http:// lookup against https:// config entry should succeed
    assert get_anchor_keywords(cfg, "http://example.com") == ["kw1", "kw2"]
    # Bare domain lookup should also succeed
    assert get_anchor_keywords(cfg, "https://example.com/") == ["kw1", "kw2"]


def test_save_config_explicit_target_keywords(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(b"""
[blogger]
"https://site.com" = "111"
""")
    cfg = load_config(cfg_path)
    # Explicit dict → write those pools
    save_config(
        cfg,
        path=cfg_path,
        target_anchor_keywords={"https://site.com": ["brand", "head term"]},
    )
    cfg2 = load_config(cfg_path)
    assert get_anchor_keywords(cfg2, "https://site.com") == ["brand", "head term"]


def test_save_config_empty_dict_clears_targets(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(b"""
[blogger]
"https://site.com" = "111"

[targets."https://site.com"]
anchor_keywords = ["brand"]
""")
    cfg = load_config(cfg_path)
    # {} → clear targets section
    save_config(cfg, path=cfg_path, target_anchor_keywords={})
    cfg2 = load_config(cfg_path)
    assert cfg2.target_anchor_keywords == {}


def test_save_config_none_preserves_existing_targets(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(b"""
[blogger]
"https://site.com" = "111"

[targets."https://site.com"]
anchor_keywords = ["brand"]
""")
    cfg = load_config(cfg_path)
    # None (default) → preserve disk
    save_config(cfg, path=cfg_path, medium_token="tok")
    cfg2 = load_config(cfg_path)
    assert get_anchor_keywords(cfg2, "https://site.com") == ["brand"]


def test_save_config_explicit_overwrites_existing(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(b"""
[blogger]
"https://site.com" = "111"

[targets."https://site.com"]
anchor_keywords = ["old-kw"]
""")
    cfg = load_config(cfg_path)
    save_config(
        cfg,
        path=cfg_path,
        target_anchor_keywords={"https://site.com": ["new-kw1", "new-kw2"]},
    )
    cfg2 = load_config(cfg_path)
    assert get_anchor_keywords(cfg2, "https://site.com") == ["new-kw1", "new-kw2"]


def test_config_dir_honors_env_var_override(tmp_path, monkeypatch):
    """``BACKLINK_PUBLISHER_CONFIG_DIR`` overrides the default location.

    Locks the contract that ``tests/conftest.py:_isolate_user_dirs`` relies
    on. If this test fails, the session-scope isolation fixture is silently
    broken and the operator's real ``~/.config/backlink-publisher/`` will
    bleed back into test runs.
    """
    from backlink_publisher.config import _config_dir

    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    assert _config_dir() == tmp_path


def test_cache_dir_honors_env_var_override(tmp_path, monkeypatch):
    """``BACKLINK_PUBLISHER_CACHE_DIR`` overrides the default location.

    Symmetric with the config-dir override. Same isolation contract.
    """
    from backlink_publisher.config import _cache_dir

    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(tmp_path))
    assert _cache_dir() == tmp_path


def test_config_dir_falls_back_when_env_var_unset(tmp_path, monkeypatch):
    """Empty/unset env var falls back to the platform default.

    Defends against a regression where empty-string env var would resolve
    to ``Path("")``, silently writing into the CWD.
    """
    from backlink_publisher.config import _config_dir

    monkeypatch.delenv("BACKLINK_PUBLISHER_CONFIG_DIR", raising=False)
    assert _config_dir().name == "backlink-publisher"
    assert "backlink-publisher" in str(_config_dir())

    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", "")
    # Empty string is falsy → falls back to platform default.
    assert _config_dir().name == "backlink-publisher"
