"""Tests for the three-URL ``[targets."x"]`` schema and the extended
``save_config`` — Plan 2026-05-13-004 Unit 3.

Covers:
- ``_parse_target_three_url`` schema parsing (happy + every error path).
- ``ThreeUrlConfig`` defaults (``DEFAULT_WORK_TEMPLATES`` + ``insecure_tls``).
- ``get_three_url_config`` scheme/trailing-slash tolerance.
- Maintenance-mode INFO log when ``[sites.x]`` and ``[targets.x]`` coexist.
- ``save_config(target_three_url=...)`` three-state semantics + round-trip.
- ``save_config`` preserves ``[blogger.oauth]`` (credential-retention regression).
- ``save_config`` preserves ``[sites.x]`` verbatim (P0 data-loss fix).
- Atomic write: a mid-write failure leaves the original file intact.
"""

from __future__ import annotations

import logging
import os
import stat
from unittest.mock import patch

import pytest

from backlink_publisher.config import (
    DEFAULT_WORK_TEMPLATES,
    ThreeUrlConfig,
    get_three_url_config,
    load_config,
    save_config,
)


# ── helpers ─────────────────────────────────────────────────────────────────


def _write_toml(tmp_path, body: str):
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return path


def _basic_three_url(
    *,
    main_url: str = "https://site.com/",
    list_url: str = "https://site.com/list",
    work_urls: list[str] | None = None,
    branded: list[str] | None = None,
    partial: list[str] | None = None,
    exact: list[str] | None = None,
    work_anchor_templates: list[str] | None = None,
    list_path_blocklist: list[str] | None = None,
    insecure_tls: bool = False,
) -> ThreeUrlConfig:
    return ThreeUrlConfig(
        main_url=main_url,
        list_url=list_url,
        work_urls=work_urls or [],
        branded_pool=branded or ["Site", "Site Hub"],
        partial_pool=partial or ["site hub partial"],
        exact_pool=exact or ["site"],
        work_anchor_templates=(
            work_anchor_templates
            if work_anchor_templates is not None
            else list(DEFAULT_WORK_TEMPLATES)
        ),
        list_path_blocklist=list_path_blocklist,
        insecure_tls=insecure_tls,
    )


# ═════════════════════════════════════════════════════════════════════════════
# _parse_target_three_url — schema happy paths
# ═════════════════════════════════════════════════════════════════════════════


class TestParseThreeUrlHappy:
    def test_full_schema_loads_all_fields(self, tmp_path):
        body = """
[targets."https://site.com/"]
main_url = "https://site.com/"
list_url = "https://site.com/list"
work_urls = ["https://site.com/work/1", "https://site.com/work/2"]
branded_pool = ["Brand A", "Brand B"]
partial_pool = ["brand partial"]
exact_pool = ["brand"]
work_anchor_templates = ["{title}", "{title} 详情"]
list_path_blocklist = ["/tag/", "/banned/"]
insecure_tls = true
"""
        cfg = load_config(_write_toml(tmp_path, body))
        assert "https://site.com" in cfg.target_three_url
        entry = cfg.target_three_url["https://site.com"]
        assert entry.main_url == "https://site.com/"
        assert entry.list_url == "https://site.com/list"
        assert entry.work_urls == [
            "https://site.com/work/1",
            "https://site.com/work/2",
        ]
        assert entry.branded_pool == ["Brand A", "Brand B"]
        assert entry.partial_pool == ["brand partial"]
        assert entry.exact_pool == ["brand"]
        assert entry.work_anchor_templates == ["{title}", "{title} 详情"]
        assert entry.list_path_blocklist == ["/tag/", "/banned/"]
        assert entry.insecure_tls is True

    def test_only_required_fields_applies_defaults(self, tmp_path):
        body = """
[targets."https://site.com/"]
main_url = "https://site.com/"
list_url = "https://site.com/list"
branded_pool = ["Brand"]
partial_pool = ["brand partial"]
exact_pool = ["brand"]
"""
        cfg = load_config(_write_toml(tmp_path, body))
        entry = cfg.target_three_url["https://site.com"]
        assert entry.work_urls == []
        assert entry.work_anchor_templates == list(DEFAULT_WORK_TEMPLATES)
        assert entry.list_path_blocklist is None
        assert entry.insecure_tls is False

    def test_default_work_templates_have_title_placeholder(self):
        # Documenting the contract — Unit 4 relies on `{title}` substitution.
        assert all("{title}" in t for t in DEFAULT_WORK_TEMPLATES)
        assert len(DEFAULT_WORK_TEMPLATES) >= 3

    def test_trailing_slash_in_key_is_normalized(self, tmp_path):
        body = """
[targets."https://site.com"]
main_url = "https://site.com/"
list_url = "https://site.com/list"
branded_pool = ["B"]
partial_pool = ["p"]
exact_pool = ["e"]
"""
        cfg = load_config(_write_toml(tmp_path, body))
        # Stored key has no trailing slash; lookup tolerates both forms.
        assert get_three_url_config(cfg, "https://site.com") is not None
        assert get_three_url_config(cfg, "https://site.com/") is not None

    def test_get_three_url_config_returns_none_for_unknown(self, tmp_path):
        cfg = load_config(_write_toml(tmp_path, ""))
        assert get_three_url_config(cfg, "https://nope.com") is None


# ═════════════════════════════════════════════════════════════════════════════
# _parse_target_three_url — error paths
# ═════════════════════════════════════════════════════════════════════════════


class TestParseThreeUrlErrors:
    def test_non_https_main_url_skips_with_warning(self, tmp_path, caplog):
        body = """
[targets."http://site.com/"]
main_url = "http://site.com/"
list_url = "https://site.com/list"
branded_pool = ["B"]
partial_pool = ["p"]
exact_pool = ["e"]
"""
        with caplog.at_level(logging.WARNING, logger="backlink_publisher.config"):
            cfg = load_config(_write_toml(tmp_path, body))
        assert cfg.target_three_url == {}
        assert any("main_url" in r.message for r in caplog.records)

    def test_missing_list_url_skips_with_warning(self, tmp_path, caplog):
        body = """
[targets."https://site.com/"]
main_url = "https://site.com/"
branded_pool = ["B"]
partial_pool = ["p"]
exact_pool = ["e"]
"""
        with caplog.at_level(logging.WARNING, logger="backlink_publisher.config"):
            cfg = load_config(_write_toml(tmp_path, body))
        assert cfg.target_three_url == {}
        assert any("list_url" in r.message for r in caplog.records)

    def test_empty_branded_pool_skips_with_warning(self, tmp_path, caplog):
        body = """
[targets."https://site.com/"]
main_url = "https://site.com/"
list_url = "https://site.com/list"
branded_pool = []
partial_pool = ["p"]
exact_pool = ["e"]
"""
        with caplog.at_level(logging.WARNING, logger="backlink_publisher.config"):
            cfg = load_config(_write_toml(tmp_path, body))
        assert cfg.target_three_url == {}
        assert any("branded_pool" in r.message for r in caplog.records)

    def test_partial_or_exact_pool_missing_skips(self, tmp_path, caplog):
        body = """
[targets."https://site.com/"]
main_url = "https://site.com/"
list_url = "https://site.com/list"
branded_pool = ["B"]
exact_pool = ["e"]
"""
        with caplog.at_level(logging.WARNING, logger="backlink_publisher.config"):
            cfg = load_config(_write_toml(tmp_path, body))
        assert cfg.target_three_url == {}

    def test_non_https_work_url_is_filtered_out(self, tmp_path, caplog):
        body = """
[targets."https://site.com/"]
main_url = "https://site.com/"
list_url = "https://site.com/list"
work_urls = ["https://site.com/work/1", "http://site.com/insecure"]
branded_pool = ["B"]
partial_pool = ["p"]
exact_pool = ["e"]
"""
        with caplog.at_level(logging.WARNING, logger="backlink_publisher.config"):
            cfg = load_config(_write_toml(tmp_path, body))
        entry = cfg.target_three_url["https://site.com"]
        assert entry.work_urls == ["https://site.com/work/1"]

    def test_anchor_keywords_only_entry_does_not_create_three_url(self, tmp_path):
        # Backward-compat: a legacy [targets."x"] with only anchor_keywords must
        # still parse cleanly into target_anchor_keywords (NOT target_three_url).
        body = """
[targets."https://legacy.com/"]
anchor_keywords = ["legacy"]
"""
        cfg = load_config(_write_toml(tmp_path, body))
        assert cfg.target_three_url == {}
        assert cfg.target_anchor_keywords["https://legacy.com"] == ["legacy"]


# ═════════════════════════════════════════════════════════════════════════════
# Maintenance-mode INFO log when [sites.x] + [targets.x] coexist
# ═════════════════════════════════════════════════════════════════════════════


class TestMaintenanceModeLog:
    def test_coexistence_emits_info_not_warn(self, tmp_path, caplog):
        body = """
[sites."https://site.com".url_categories]
home = "https://site.com/"

[targets."https://site.com/"]
main_url = "https://site.com/"
list_url = "https://site.com/list"
branded_pool = ["B"]
partial_pool = ["p"]
exact_pool = ["e"]
"""
        with caplog.at_level(logging.INFO, logger="backlink_publisher.config"):
            cfg = load_config(_write_toml(tmp_path, body))

        # New schema parses fine — both paths coexist
        assert "https://site.com" in cfg.target_three_url
        assert "https://site.com" in cfg.site_url_categories

        # An INFO (not WARN) log mentions maintenance mode
        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("maintenance" in r.message.lower() for r in info_records)

        # Critically: no WARN about maintenance/deprecated (avoid old-user alarm)
        warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("maintenance" in r.message.lower() for r in warn_records)
        assert not any("deprecated" in r.message.lower() for r in warn_records)


# ═════════════════════════════════════════════════════════════════════════════
# save_config — three-state target_three_url + round-trip
# ═════════════════════════════════════════════════════════════════════════════


class TestSaveConfigThreeUrl:
    def test_round_trip_writes_all_fields(self, tmp_path):
        path = tmp_path / "config.toml"
        cfg = load_config(path)  # empty config
        three_url = {"https://site.com": _basic_three_url(
            work_urls=["https://site.com/work/1"],
            branded=["Brand"],
            partial=["brand partial"],
            exact=["brand"],
            list_path_blocklist=["/banned/"],
            insecure_tls=True,
        )}
        save_config(cfg, path=path, target_three_url=three_url)

        # Round-trip cycle 1
        reloaded = load_config(path)
        entry = reloaded.target_three_url["https://site.com"]
        assert entry.main_url == "https://site.com/"
        assert entry.list_url == "https://site.com/list"
        assert entry.work_urls == ["https://site.com/work/1"]
        assert entry.branded_pool == ["Brand"]
        assert entry.partial_pool == ["brand partial"]
        assert entry.exact_pool == ["brand"]
        assert entry.list_path_blocklist == ["/banned/"]
        assert entry.insecure_tls is True

        # Round-trip cycle 2 — save again with no args → preserves
        save_config(reloaded, path=path)
        reloaded2 = load_config(path)
        entry2 = reloaded2.target_three_url["https://site.com"]
        assert entry2 == entry  # exact equality across save+load+save+load

    def test_none_preserves_existing_three_url(self, tmp_path):
        path = tmp_path / "config.toml"
        cfg = load_config(path)
        save_config(
            cfg,
            path=path,
            target_three_url={"https://site.com": _basic_three_url()},
        )
        reloaded = load_config(path)
        # call save_config with target_three_url=None — should preserve
        save_config(reloaded, path=path)
        again = load_config(path)
        assert "https://site.com" in again.target_three_url

    def test_empty_dict_clears(self, tmp_path):
        path = tmp_path / "config.toml"
        save_config(
            load_config(path),
            path=path,
            target_three_url={"https://site.com": _basic_three_url()},
        )
        # Now clear
        save_config(load_config(path), path=path, target_three_url={})
        reloaded = load_config(path)
        assert reloaded.target_three_url == {}

    def test_overwrites_with_new_dict(self, tmp_path):
        path = tmp_path / "config.toml"
        save_config(
            load_config(path),
            path=path,
            target_three_url={"https://old.com": _basic_three_url(
                main_url="https://old.com/", list_url="https://old.com/list",
            )},
        )
        save_config(
            load_config(path),
            path=path,
            target_three_url={"https://new.com": _basic_three_url(
                main_url="https://new.com/", list_url="https://new.com/list",
            )},
        )
        reloaded = load_config(path)
        assert "https://old.com" not in reloaded.target_three_url
        assert "https://new.com" in reloaded.target_three_url


# ═════════════════════════════════════════════════════════════════════════════
# CRITICAL: save_config must preserve [blogger.oauth] + [sites.x]
# (P0 data-loss regression guard)
# ═════════════════════════════════════════════════════════════════════════════


class TestSaveConfigPreservesCriticalSections:
    def test_preserves_blogger_oauth(self, tmp_path):
        body = """
[blogger]
"https://site.com" = "blog-id-123"

[blogger.oauth]
client_id     = "id.apps.googleusercontent.com"
client_secret = "secret-value"
"""
        path = _write_toml(tmp_path, body)
        cfg = load_config(path)

        # Save with new three-url payload — must NOT erase OAuth credentials
        save_config(
            cfg,
            path=path,
            target_three_url={"https://site.com": _basic_three_url()},
        )
        reloaded = load_config(path)
        assert reloaded.blogger_oauth is not None
        assert reloaded.blogger_oauth.client_id == "id.apps.googleusercontent.com"
        assert reloaded.blogger_oauth.client_secret == "secret-value"

    def test_preserves_sites_section_verbatim(self, tmp_path):
        # [sites."x"] is the load-bearing read-only schema for the legacy
        # zh-CN path. save_config historically nuked it (P0 data loss).
        body = """
[blogger]
"https://51acgs.com" = "1234567890"

[sites."https://51acgs.com".url_categories]
home = "https://51acgs.com/"
hot = "https://51acgs.com/comic/hot"

[sites."https://51acgs.com".anchor_pools.home]
branded = ["51漫画"]
partial = ["成人漫画站"]
exact = ["漫画"]
lsi = ["二次元资源"]
"""
        path = _write_toml(tmp_path, body)
        cfg = load_config(path)
        assert cfg.site_url_categories  # sanity: loaded once

        save_config(
            cfg,
            path=path,
            target_three_url={"https://51acgs.com": _basic_three_url(
                main_url="https://51acgs.com/",
                list_url="https://51acgs.com/list",
            )},
        )

        reloaded = load_config(path)
        # [sites.x].url_categories survived round-trip
        assert reloaded.site_url_categories["https://51acgs.com"]["home"] \
            == "https://51acgs.com/"
        assert reloaded.site_url_categories["https://51acgs.com"]["hot"] \
            == "https://51acgs.com/comic/hot"
        # [sites.x].anchor_pools.home survived too
        from backlink_publisher.config import get_anchor_pool_v2
        assert get_anchor_pool_v2(
            reloaded, "https://51acgs.com", "home", "branded"
        ) == ["51漫画"]

    def test_preserves_anchor_proportions_and_llm_section(self, tmp_path):
        body = """
[blogger]
"https://site.com" = "1"

[anchor.proportions]
preset = "safe_seo"

[llm.anchor_provider]
base_url = "https://api.openai.com/v1"
api_key = "k"
model = "gpt-4o-mini"
"""
        path = _write_toml(tmp_path, body)
        # NB: api_key is in toml; chmod 0600 already applied in _write_toml
        cfg = load_config(path)
        save_config(cfg, path=path, target_three_url={
            "https://site.com": _basic_three_url(),
        })
        rewritten = path.read_text(encoding="utf-8")
        assert "[anchor.proportions]" in rewritten
        assert "[llm.anchor_provider]" in rewritten

    def test_atomic_write_failure_leaves_original_intact(self, tmp_path):
        body = """
[blogger]
"https://site.com" = "blog-id-original"
"""
        path = _write_toml(tmp_path, body)
        original = path.read_text(encoding="utf-8")

        # Force the inner write step to raise — by patching os.replace
        # (the final rename step). The temp file may exist briefly; the
        # invariant is the ORIGINAL path is untouched.
        with patch(
            "backlink_publisher.config.os.replace",
            side_effect=OSError("simulated rename failure"),
        ):
            with pytest.raises(OSError):
                save_config(
                    load_config(path),
                    path=path,
                    target_three_url={
                        "https://site.com": _basic_three_url(),
                    },
                )

        assert path.read_text(encoding="utf-8") == original


# ═════════════════════════════════════════════════════════════════════════════
# Coexistence with legacy [targets."x"].anchor_keywords
# ═════════════════════════════════════════════════════════════════════════════


class TestCoexistenceWithLegacyAnchorKeywords:
    def test_anchor_keywords_and_three_url_in_same_domain_block(self, tmp_path):
        path = tmp_path / "config.toml"
        cfg = load_config(path)
        save_config(
            cfg,
            path=path,
            target_anchor_keywords={"https://site.com": ["site", "site hub"]},
            target_three_url={"https://site.com": _basic_three_url()},
        )
        reloaded = load_config(path)
        assert reloaded.target_anchor_keywords["https://site.com"] == [
            "site", "site hub",
        ]
        assert "https://site.com" in reloaded.target_three_url


# ═════════════════════════════════════════════════════════════════════════════
# upgrade_target_to_threeurl (Plan 2026-05-14-009 Unit 3)
# ═════════════════════════════════════════════════════════════════════════════


class TestUpgradeTargetToThreeUrl:
    """Pure-function helper that derives a ThreeUrlConfig from current
    Config state. Three migration paths: merge-existing, anchor_keywords,
    bootstrap. Caller writes the result back via save_config."""

    def test_domain_label_basic(self):
        from backlink_publisher.config import _domain_label
        assert _domain_label("https://51acgs.com/") == "51acgs"
        assert _domain_label("https://www.51acgs.com/") == "51acgs"
        assert _domain_label("https://a.b.c.com/") == "a"
        assert _domain_label("https://example.com") == "example"

    def test_bootstrap_no_prior_state(self, tmp_path):
        """Unknown main_url → all pools fall back to domain_label."""
        from backlink_publisher.config import upgrade_target_to_threeurl

        cfg = load_config(tmp_path / "config.toml")
        result = upgrade_target_to_threeurl(
            cfg,
            main_url="https://newsite.com/",
            category_url="https://newsite.com/category",
            work_url="https://newsite.com/article/1",
        )

        assert result.main_url == "https://newsite.com/"
        assert result.list_url == "https://newsite.com/category"
        assert result.branded_pool == ["newsite"]
        assert result.partial_pool == ["newsite"]
        assert result.exact_pool == ["newsite"]
        assert result.work_urls == ["https://newsite.com/article/1"]

    def test_bootstrap_only_main_url(self, tmp_path):
        """No category / work supplied — list_url falls back to main_url,
        work_urls is empty."""
        from backlink_publisher.config import upgrade_target_to_threeurl

        cfg = load_config(tmp_path / "config.toml")
        result = upgrade_target_to_threeurl(
            cfg, main_url="https://bare.com/",
        )
        assert result.list_url == "https://bare.com/"
        assert result.work_urls == []
        assert result.branded_pool == ["bare"]

    def test_legacy_anchor_keywords_migrated_to_branded_pool(self, tmp_path):
        """Pre-existing anchor_keywords (legacy schema) → branded_pool."""
        path = tmp_path / "config.toml"
        save_config(
            load_config(path), path=path,
            target_anchor_keywords={
                "https://legacy.com": ["LegacyBrand", "legacy hub", "legacy"],
            },
        )
        cfg = load_config(path)

        from backlink_publisher.config import upgrade_target_to_threeurl
        result = upgrade_target_to_threeurl(
            cfg,
            main_url="https://legacy.com",
            category_url="https://legacy.com/cat",
            work_url="https://legacy.com/work/9",
        )

        assert result.branded_pool == ["LegacyBrand", "legacy hub", "legacy"]
        # Other pools still fall back to domain_label (schema requires non-empty)
        assert result.partial_pool == ["legacy"]
        assert result.exact_pool == ["legacy"]
        assert result.list_url == "https://legacy.com/cat"
        assert result.work_urls == ["https://legacy.com/work/9"]

    def test_legacy_anchor_keywords_bare_domain_key_migrated(self, tmp_path):
        """Regression: a legacy pool keyed by the BARE domain (no scheme) must
        still migrate. Stored keys are rstrip('/')-normalised but keep whatever
        scheme the operator wrote; the upgrade path previously only matched the
        scheme-exact key, so a ``[targets."legacy.com"]`` pool was silently
        dropped and the target bootstrapped to just the domain label."""
        path = tmp_path / "config.toml"
        save_config(
            load_config(path), path=path,
            target_anchor_keywords={
                "legacy.com": ["LegacyBrand", "legacy hub", "legacy"],
            },
        )
        cfg = load_config(path)

        from backlink_publisher.config import upgrade_target_to_threeurl
        result = upgrade_target_to_threeurl(
            cfg, main_url="https://legacy.com",
        )

        # Found via the bare-domain variant → migrated, NOT bootstrapped.
        assert result.branded_pool == ["LegacyBrand", "legacy hub", "legacy"]

    def test_existing_threeurl_config_merges_only_provided_fields(self, tmp_path):
        """If a full ThreeUrlConfig already exists, only list_url and work_urls
        are overwritten when the corresponding kwargs are non-None. Other
        pools / templates / flags inherit from the existing entry."""
        path = tmp_path / "config.toml"
        existing = ThreeUrlConfig(
            main_url="https://full.com/",
            list_url="https://full.com/old-list",
            branded_pool=["FullBrand"],
            partial_pool=["partial1"],
            exact_pool=["exact1"],
            work_urls=["https://full.com/old-work"],
            insecure_tls=True,
        )
        save_config(
            load_config(path), path=path,
            target_three_url={"https://full.com": existing},
        )
        cfg = load_config(path)

        from backlink_publisher.config import upgrade_target_to_threeurl
        result = upgrade_target_to_threeurl(
            cfg,
            main_url="https://full.com/",
            category_url="https://full.com/new-list",
            work_url="https://full.com/new-work",
        )

        # list_url + work_urls overwritten; other fields preserved.
        assert result.list_url == "https://full.com/new-list"
        assert result.work_urls == ["https://full.com/new-work"]
        assert result.branded_pool == ["FullBrand"]
        assert result.partial_pool == ["partial1"]
        assert result.exact_pool == ["exact1"]
        assert result.insecure_tls is True

    def test_existing_threeurl_without_new_work_url_preserves_existing_work_urls(
        self, tmp_path,
    ):
        """If work_url is None, the existing entry's work_urls list is kept
        intact (operator may have curated it via /sites)."""
        path = tmp_path / "config.toml"
        existing = ThreeUrlConfig(
            main_url="https://x.com/",
            list_url="https://x.com/list",
            branded_pool=["X"],
            partial_pool=["x"],
            exact_pool=["x"],
            work_urls=["https://x.com/a", "https://x.com/b", "https://x.com/c"],
        )
        save_config(
            load_config(path), path=path,
            target_three_url={"https://x.com": existing},
        )
        cfg = load_config(path)

        from backlink_publisher.config import upgrade_target_to_threeurl
        result = upgrade_target_to_threeurl(
            cfg, main_url="https://x.com/",
        )
        assert result.work_urls == [
            "https://x.com/a", "https://x.com/b", "https://x.com/c",
        ]

    def test_integration_result_passes_schema_validation_after_roundtrip(
        self, tmp_path,
    ):
        """Upgrade → save_config → load_config: the upgraded ThreeUrlConfig
        survives the round-trip without the schema enforcement (three pools
        non-empty) stripping the entry."""
        from backlink_publisher.config import upgrade_target_to_threeurl

        path = tmp_path / "config.toml"
        cfg = load_config(path)
        result = upgrade_target_to_threeurl(
            cfg,
            main_url="https://roundtrip.com/",
            category_url="https://roundtrip.com/cat",
            work_url="https://roundtrip.com/w1",
        )
        save_config(
            cfg, path=path,
            target_three_url={"https://roundtrip.com": result},
        )

        reloaded = load_config(path)
        assert "https://roundtrip.com" in reloaded.target_three_url
        rt = reloaded.target_three_url["https://roundtrip.com"]
        assert rt.list_url == "https://roundtrip.com/cat"
        assert rt.work_urls == ["https://roundtrip.com/w1"]
        # All three pools non-empty (schema invariant).
        assert len(rt.branded_pool) >= 1
        assert len(rt.partial_pool) >= 1
        assert len(rt.exact_pool) >= 1


# ═════════════════════════════════════════════════════════════════════════════
# merge_site_url_categories — in-place TOML merge (Plan 009 deferred work)
# ═════════════════════════════════════════════════════════════════════════════


class TestMergeSiteUrlCategories:
    """In-place TOML merge for [sites."<main>".url_categories]. Closes
    brainstorm Q3: homepage form writes category_url to BOTH
    target_three_url.list_url AND sites.<main>.url_categories.category.
    Existing operator-curated keys (hot, animate, topic) are preserved."""

    def test_creates_new_section_when_absent(self, tmp_path):
        from backlink_publisher.config import (
            merge_site_url_categories,
        )

        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(
            '[blogger]\n"https://x.com" = "1"\n', encoding="utf-8",
        )
        merge_site_url_categories(
            "https://x.com/",
            {"home": "https://x.com/", "category": "https://x.com/cat"},
            path=cfg_path,
        )
        content = cfg_path.read_text(encoding="utf-8")
        assert '[sites."https://x.com".url_categories]' in content
        assert 'home = "https://x.com/"' in content
        assert 'category = "https://x.com/cat"' in content

    def test_preserves_existing_unrelated_keys(self, tmp_path):
        from backlink_publisher.config import merge_site_url_categories

        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(
            '[blogger]\n"https://x.com" = "1"\n\n'
            '[sites."https://x.com".url_categories]\n'
            'home = "https://x.com/"\n'
            'hot = "https://x.com/hot"\n'
            'animate = "https://x.com/animate"\n'
            'topic = "https://x.com/topic"\n',
            encoding="utf-8",
        )
        merge_site_url_categories(
            "https://x.com/",
            {"category": "https://x.com/cat"},
            path=cfg_path,
        )
        content = cfg_path.read_text(encoding="utf-8")
        # hot/animate/topic preserved verbatim
        assert 'hot = "https://x.com/hot"' in content
        assert 'animate = "https://x.com/animate"' in content
        assert 'topic = "https://x.com/topic"' in content
        # new key appended
        assert 'category = "https://x.com/cat"' in content

    def test_overwrites_existing_same_key(self, tmp_path):
        from backlink_publisher.config import merge_site_url_categories

        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(
            '[sites."https://x.com".url_categories]\n'
            'category = "https://x.com/old-cat"\n'
            'hot = "https://x.com/hot"\n',
            encoding="utf-8",
        )
        merge_site_url_categories(
            "https://x.com/",
            {"category": "https://x.com/NEW-cat"},
            path=cfg_path,
        )
        content = cfg_path.read_text(encoding="utf-8")
        assert 'category = "https://x.com/NEW-cat"' in content
        assert 'category = "https://x.com/old-cat"' not in content
        # Unrelated key still present
        assert 'hot = "https://x.com/hot"' in content

    def test_load_config_round_trip(self, tmp_path):
        """After the merge, load_config can parse the section back into
        Config.site_url_categories with all keys present."""
        from backlink_publisher.config import (
            load_config,
            merge_site_url_categories,
        )

        cfg_path = tmp_path / "config.toml"
        merge_site_url_categories(
            "https://x.com/",
            {"home": "https://x.com/", "category": "https://x.com/cat"},
            path=cfg_path,
        )
        cfg = load_config(cfg_path)
        cats = cfg.site_url_categories.get("https://x.com", {})
        assert cats.get("home") == "https://x.com/"
        assert cats.get("category") == "https://x.com/cat"

    def test_empty_additions_is_noop(self, tmp_path):
        from backlink_publisher.config import merge_site_url_categories

        cfg_path = tmp_path / "config.toml"
        original = '[blogger]\n"https://x.com" = "1"\n'
        cfg_path.write_text(original, encoding="utf-8")
        merge_site_url_categories(
            "https://x.com/", {}, path=cfg_path,
        )
        assert cfg_path.read_text(encoding="utf-8") == original

    def test_writes_to_nonexistent_file(self, tmp_path):
        """Operator may not have a config.toml yet — first write should
        create one rather than fail."""
        from backlink_publisher.config import merge_site_url_categories

        cfg_path = tmp_path / "fresh-config.toml"
        assert not cfg_path.exists()
        merge_site_url_categories(
            "https://x.com/",
            {"home": "https://x.com/"},
            path=cfg_path,
        )
        assert cfg_path.exists()
        content = cfg_path.read_text(encoding="utf-8")
        assert "[sites." in content
        assert 'home = "https://x.com/"' in content

    def test_snapshot_taken_before_overwrite(self, tmp_path):
        """When the file exists, _snapshot_config copies it into
        .config-history/ before our merge writes. Mirrors save_config's
        safety net."""
        from backlink_publisher.config import merge_site_url_categories

        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(
            '[sites."https://x.com".url_categories]\nold_key = "old"\n',
            encoding="utf-8",
        )
        merge_site_url_categories(
            "https://x.com/",
            {"category": "https://x.com/cat"},
            path=cfg_path,
        )
        history = (tmp_path / ".config-history")
        assert history.exists() and history.is_dir()
        snapshots = list(history.iterdir())
        assert len(snapshots) >= 1, "expected at least one snapshot"

    def test_control_char_in_main_url_rejected(self, tmp_path):
        """Defence against malformed main_url that would break the TOML
        basic string quoting. The webui handler validates main_url
        upstream, but defensive rejection at this layer is cheap."""
        from backlink_publisher.config import merge_site_url_categories
        from backlink_publisher._util.errors import InputValidationError

        cfg_path = tmp_path / "config.toml"
        with pytest.raises(InputValidationError):
            merge_site_url_categories(
                "https://x.com/\nmalicious=true",
                {"home": "x"},
                path=cfg_path,
            )
