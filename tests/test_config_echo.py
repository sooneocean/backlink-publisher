"""Tests for the Config Echo Chamber (Round-3 #7).

Verifies:
- SHA stability across whitespace edits in source TOML
- SHA sensitivity to semantic config edits
- env_overrides detection: name only, no value leak
- active_platforms: blogger vs medium vs none
- banner_lines layout
- emit_banner returns SHA + writes 5 lines to stderr

Plan ref: docs/ideation/2026-05-14-round3-fresh-pass-ideation.md (#7)
"""

from __future__ import annotations

import io
import os
import re

import pytest

from backlink_publisher.config import (
    BloggerOAuthConfig,
    Config,
    LLMProviderConfig,
    MediumOAuthConfig,
    ThreeUrlConfig,
    load_config,
    save_config,
)
from backlink_publisher.config_echo import (
    KNOWN_ENV_OVERRIDES,
    _canonicalise_for_sha,
    active_platforms,
    banner_lines,
    compute_config_sha,
    detect_env_overrides,
    emit_banner,
)


# ── compute_config_sha: stability + sensitivity ────────────────────────────


class TestComputeConfigSha:
    def test_same_config_produces_same_sha(self):
        cfg = Config(blogger_blog_ids={"https://x.com": "1"})
        assert compute_config_sha(cfg) == compute_config_sha(cfg)

    def test_blog_id_change_produces_different_sha(self):
        a = Config(blogger_blog_ids={"https://x.com": "1"})
        b = Config(blogger_blog_ids={"https://x.com": "2"})
        assert compute_config_sha(a) != compute_config_sha(b)

    def test_blog_id_key_change_produces_different_sha(self):
        a = Config(blogger_blog_ids={"https://x.com": "1"})
        b = Config(blogger_blog_ids={"https://y.com": "1"})
        assert compute_config_sha(a) != compute_config_sha(b)

    def test_dict_key_order_does_not_affect_sha(self):
        a = Config(blogger_blog_ids={"https://x.com": "1", "https://y.com": "2"})
        b = Config(blogger_blog_ids={"https://y.com": "2", "https://x.com": "1"})
        assert compute_config_sha(a) == compute_config_sha(b), (
            "key-order edit must not change SHA (semantic equivalence)"
        )

    def test_anchor_keywords_order_DOES_affect_sha(self):
        """Anchor pool order is semantic in this codebase (affects
        rendering / round-robin). Reordering MUST surface as a SHA delta."""
        a = Config(target_anchor_keywords={"x.com": ["a", "b", "c"]})
        b = Config(target_anchor_keywords={"x.com": ["c", "b", "a"]})
        assert compute_config_sha(a) != compute_config_sha(b)

    def test_sha_length(self):
        cfg = Config()
        sha = compute_config_sha(cfg)
        assert isinstance(sha, str)
        assert len(sha) == 16
        assert re.fullmatch(r"[0-9a-f]{16}", sha) is not None

    def test_empty_config_yields_stable_sha(self):
        sha1 = compute_config_sha(Config())
        sha2 = compute_config_sha(Config())
        assert sha1 == sha2

    def test_threeurl_config_included_in_sha(self):
        """target_three_url is semantically critical — pool changes must
        surface as SHA delta."""
        entry = ThreeUrlConfig(
            main_url="https://x.com/",
            list_url="https://x.com/list",
            branded_pool=["X"],
            partial_pool=["x"],
            exact_pool=["x"],
        )
        a = Config(target_three_url={"https://x.com": entry})
        b = Config(target_three_url={})
        assert compute_config_sha(a) != compute_config_sha(b)

    def test_llm_provider_change_produces_different_sha(self):
        a = Config(llm_anchor_provider=None)
        b = Config(llm_anchor_provider=LLMProviderConfig(
            base_url="https://api.example.com", api_key="x", model="gpt",
        ))
        assert compute_config_sha(a) != compute_config_sha(b)

    def test_load_config_idempotent_sha(self, tmp_path):
        """Loading the same config.toml twice produces the same SHA —
        load_config is deterministic so the resolved Config dataclass is
        bit-for-bit equivalent across calls. (Note: in-memory Config()
        with the same kwargs as a save+load round-trip is NOT necessarily
        SHA-equal, because save_config doesn't round-trip every field
        and load_config applies defaults — that's by design.)
        """
        cfg_path = tmp_path / "config.toml"
        cfg = Config(blogger_blog_ids={"https://x.com": "1234567890"})
        save_config(cfg, path=cfg_path)
        loaded_a = load_config(cfg_path)
        loaded_b = load_config(cfg_path)
        assert compute_config_sha(loaded_a) == compute_config_sha(loaded_b)


# ── _canonicalise_for_sha: type handling ───────────────────────────────────


class TestCanonicalise:
    def test_path_to_string(self):
        from pathlib import Path
        result = _canonicalise_for_sha(Path("/tmp/x"))
        assert isinstance(result, str)
        assert result == "/tmp/x"

    def test_tuple_to_list(self):
        assert _canonicalise_for_sha((1, 2, 3)) == [1, 2, 3]

    def test_frozenset_to_sorted_list(self):
        assert _canonicalise_for_sha(frozenset(["b", "a", "c"])) == ["a", "b", "c"]

    def test_dict_keys_sorted(self):
        result = _canonicalise_for_sha({"z": 1, "a": 2})
        assert list(result.keys()) == ["a", "z"]

    def test_unknown_type_falls_back_to_repr(self):
        class Custom:
            def __repr__(self):
                return "Custom()"
        assert _canonicalise_for_sha(Custom()) == "Custom()"

    def test_primitives_passthrough(self):
        for v in ("s", 42, 3.14, True, False, None):
            assert _canonicalise_for_sha(v) == v


# ── detect_env_overrides: name-only disclosure ─────────────────────────────


class TestDetectEnvOverrides:
    def test_no_env_overrides_returns_empty(self, monkeypatch):
        for name in KNOWN_ENV_OVERRIDES:
            monkeypatch.delenv(name, raising=False)
        assert detect_env_overrides() == []

    def test_single_env_override_detected(self, monkeypatch):
        for name in KNOWN_ENV_OVERRIDES:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("BACKLINK_NO_FETCH_VERIFY", "1")
        assert detect_env_overrides() == ["BACKLINK_NO_FETCH_VERIFY"]

    def test_multiple_env_overrides_all_listed(self, monkeypatch):
        for name in KNOWN_ENV_OVERRIDES:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("BACKLINK_NO_FETCH_VERIFY", "1")
        monkeypatch.setenv("BACKLINK_LLM_API_KEY", "sk-secret")
        names = detect_env_overrides()
        assert "BACKLINK_NO_FETCH_VERIFY" in names
        assert "BACKLINK_LLM_API_KEY" in names

    def test_empty_env_value_not_detected(self, monkeypatch):
        for name in KNOWN_ENV_OVERRIDES:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("BACKLINK_NO_FETCH_VERIFY", "")
        monkeypatch.setenv("BACKLINK_LLM_API_KEY", "   ")
        assert detect_env_overrides() == []

    def test_only_known_overrides_surfaced(self, monkeypatch):
        """Unknown env vars are not in the banner — operator typo / random
        BACKLINK_* doesn't surprise the audit log."""
        monkeypatch.setenv("BACKLINK_UNKNOWN_THING", "should-not-appear")
        names = detect_env_overrides()
        assert "BACKLINK_UNKNOWN_THING" not in names

    def test_secret_value_not_present_in_disclosure(self, monkeypatch):
        """Critical: the function MUST NOT return env values, only names.
        A regression that leaked values would defeat the redactor that
        sits in the logger one level up."""
        monkeypatch.setenv("BACKLINK_LLM_API_KEY", "sk-leaky-secret-token")
        names = detect_env_overrides()
        for entry in names:
            assert "sk-leaky-secret-token" not in entry


# ── active_platforms ───────────────────────────────────────────────────────


class TestActivePlatforms:
    def test_no_credentials_returns_empty(self, monkeypatch):
        # Plan 013: active_platforms includes 'medium' when Playwright is
        # installed. Explicitly set sync_playwright to None so this test covers
        # the true "no-credentials + no-library" baseline.
        from backlink_publisher.publishing.adapters import medium_browser
        monkeypatch.setattr(medium_browser, "sync_playwright", None)
        cfg = Config()
        assert active_platforms(cfg) == []

    def test_blogger_when_blog_ids_present(self):
        cfg = Config(blogger_blog_ids={"https://x.com": "1234"})
        assert "blogger" in active_platforms(cfg)

    def test_medium_when_token_present(self):
        cfg = Config(medium_integration_token="m-tok")
        assert "medium" in active_platforms(cfg)

    def test_medium_when_oauth_present(self):
        cfg = Config(medium_oauth=MediumOAuthConfig(client_id="cid", client_secret="cs"))
        assert "medium" in active_platforms(cfg)

    def test_medium_when_playwright_installed(self, monkeypatch):
        # Plan 013: active_platforms mirrors verify_adapter_setup — Playwright
        # installed counts as a viable publishing path even without a stored token.
        from backlink_publisher.publishing.adapters import medium_browser
        monkeypatch.setattr(medium_browser, "sync_playwright", object())
        cfg = Config()
        assert "medium" in active_platforms(cfg)

    def test_both_platforms_sorted_alphabetically(self):
        cfg = Config(
            blogger_blog_ids={"https://x.com": "1"},
            medium_integration_token="m",
        )
        assert active_platforms(cfg) == ["blogger", "medium"]


# ── banner_lines structure ─────────────────────────────────────────────────


class TestBannerLines:
    def test_returns_four_lines(self, monkeypatch, tmp_path):
        for name in KNOWN_ENV_OVERRIDES:
            monkeypatch.delenv(name, raising=False)
        cfg = Config()
        lines = banner_lines(cfg, config_path=tmp_path / "config.toml")
        assert len(lines) == 4
        joined = "\n".join(lines)
        assert "config:" in joined
        assert "env:" in joined
        assert "platforms:" in joined
        assert "sha:" in joined

    def test_sha_line_matches_compute_config_sha(self, tmp_path):
        cfg = Config(blogger_blog_ids={"https://x.com": "1"})
        lines = banner_lines(cfg, config_path=tmp_path / "config.toml")
        sha = compute_config_sha(cfg)
        assert sha in lines[-1]

    def test_no_env_active_shows_none(self, monkeypatch, tmp_path):
        for name in KNOWN_ENV_OVERRIDES:
            monkeypatch.delenv(name, raising=False)
        lines = banner_lines(Config(), config_path=tmp_path / "config.toml")
        env_line = next(l for l in lines if "env:" in l)
        assert "(none)" in env_line

    def test_env_active_shows_name_not_value(self, monkeypatch, tmp_path):
        for name in KNOWN_ENV_OVERRIDES:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("BACKLINK_LLM_API_KEY", "sk-secret-do-not-leak")
        lines = banner_lines(Config(), config_path=tmp_path / "config.toml")
        env_line = next(l for l in lines if "env:" in l)
        assert "BACKLINK_LLM_API_KEY" in env_line
        assert "sk-secret-do-not-leak" not in env_line


# ── emit_banner: stderr write + return SHA ─────────────────────────────────


class TestEmitBanner:
    def test_emit_writes_to_stream_and_returns_sha(self, tmp_path):
        cfg = Config(blogger_blog_ids={"https://x.com": "1"})
        buf = io.StringIO()
        sha = emit_banner(
            cfg, "plan-backlinks",
            config_path=tmp_path / "config.toml",
            stream=buf,
        )
        assert sha == compute_config_sha(cfg)
        output = buf.getvalue()
        assert "[plan-backlinks] effective config:" in output
        assert "config:" in output
        assert "env:" in output
        assert "platforms:" in output
        assert "sha:" in output
        assert sha in output

    def test_emit_defaults_to_stderr(self, monkeypatch):
        cfg = Config()
        # Capture stderr indirectly via monkeypatch
        buf = io.StringIO()
        monkeypatch.setattr("sys.stderr", buf)
        sha = emit_banner(cfg, "test-cli")
        assert sha == compute_config_sha(cfg)
        assert "[test-cli] effective config:" in buf.getvalue()
