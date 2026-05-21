"""Tests for browser_publish.recipes.mastodon — Plan 2026-05-21-001 Unit 4c."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backlink_publisher._util.errors import DependencyError
from backlink_publisher.publishing.browser_publish import RECIPES
from backlink_publisher.publishing.browser_publish.recipes import (
    mastodon as mast_recipe,
)
from backlink_publisher.publishing.browser_publish.recipes import (
    _mastodon_selectors as sel,
)


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Isolate BACKLINK_PUBLISHER_CONFIG_DIR to tmp_path; clear caches."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _write_config_with_mastodon(config_dir: Path, instance_url: str) -> Path:
    cfg_path = config_dir / "config.toml"
    cfg_path.write_text(
        f"[blogger]\n"
        f"[medium]\n"
        f"[mastodon]\ninstance_url = \"{instance_url}\"\n"
    )
    return cfg_path


def _write_config_without_mastodon(config_dir: Path) -> Path:
    cfg_path = config_dir / "config.toml"
    cfg_path.write_text("[blogger]\n[medium]\n")
    return cfg_path


class TestResolveComposeUrl:
    def test_resolves_with_instance_url(self, isolated_config):
        _write_config_with_mastodon(isolated_config, "https://mastodon.social")
        assert (
            mast_recipe._resolve_compose_url()
            == "https://mastodon.social/publish"
        )

    def test_strips_trailing_slash(self, isolated_config):
        _write_config_with_mastodon(isolated_config, "https://m.example.com/")
        assert (
            mast_recipe._resolve_compose_url()
            == "https://m.example.com/publish"
        )

    def test_missing_section_raises_dependency_error(self, isolated_config):
        _write_config_without_mastodon(isolated_config)
        with pytest.raises(DependencyError, match="instance URL not configured"):
            mast_recipe._resolve_compose_url()

    def test_empty_instance_url_raises_dependency_error(self, isolated_config):
        _write_config_with_mastodon(isolated_config, "")
        with pytest.raises(DependencyError, match="instance URL not configured"):
            mast_recipe._resolve_compose_url()


def _make_page(*, final_url: str = "https://mastodon.social/@op/123456789"):
    page = MagicMock(name="mastodon_page")
    textarea = MagicMock(name="mastodon_textarea")
    page.query_selector.return_value = textarea
    page.url = final_url
    return page, textarea


class TestMastodonPublishFlow:
    def test_happy_path_returns_toot_url(self, isolated_config):
        _write_config_with_mastodon(isolated_config, "https://mastodon.social")
        page, textarea = _make_page()
        url = mast_recipe.mastodon_publish_flow(
            page,
            {"title": "Hi", "content_markdown": "Hello world"},
        )
        assert url == "https://mastodon.social/@op/123456789"
        page.goto.assert_called_once_with("https://mastodon.social/publish")
        # Title + body joined by blank line.
        textarea.fill.assert_called_once_with("Hi\n\nHello world")
        page.click.assert_called_once_with(sel.PUBLISH_BUTTON)
        page.wait_for_url.assert_called_once_with(
            sel.POST_PUBLISHED_URL_RE,
            timeout=sel.POST_PUBLISH_REDIRECT_TIMEOUT_MS,
        )

    def test_body_only_omits_title_prefix(self, isolated_config):
        _write_config_with_mastodon(isolated_config, "https://mastodon.social")
        page, textarea = _make_page()
        mast_recipe.mastodon_publish_flow(page, {"content_markdown": "just body"})
        textarea.fill.assert_called_once_with("just body")

    def test_missing_body_raises_value_error(self, isolated_config):
        _write_config_with_mastodon(isolated_config, "https://mastodon.social")
        page, _ = _make_page()
        with pytest.raises(ValueError, match="content_markdown/body"):
            mast_recipe.mastodon_publish_flow(page, {"title": "no body"})

    def test_missing_textarea_raises_runtime_error(self, isolated_config):
        _write_config_with_mastodon(isolated_config, "https://mastodon.social")
        page, _ = _make_page()
        page.query_selector.return_value = None
        with pytest.raises(RuntimeError, match="compose textarea not found"):
            mast_recipe.mastodon_publish_flow(
                page, {"content_markdown": "body"}
            )

    def test_missing_instance_url_raises_dependency_error(self, isolated_config):
        _write_config_without_mastodon(isolated_config)
        page, _ = _make_page()
        with pytest.raises(DependencyError, match="instance URL not configured"):
            mast_recipe.mastodon_publish_flow(
                page, {"content_markdown": "body"}
            )


class TestRecipeRegistered:
    def test_mastodon_in_recipes(self):
        assert "mastodon" in RECIPES
        assert RECIPES["mastodon"].channel == "mastodon"


class TestMastodonChain:
    def test_mastodon_chain_uses_browser_only(self):
        import backlink_publisher.publishing.adapters  # noqa: F401
        from backlink_publisher.publishing.registry import _REGISTRY
        from backlink_publisher.publishing.browser_publish import (
            BrowserPublishDispatcher,
        )

        chain = _REGISTRY["mastodon"]
        assert len(chain) == 1
        assert isinstance(chain[0], BrowserPublishDispatcher)
        assert chain[0].channel == "mastodon"

    def test_mastodon_removed_from_rejection_map(self):
        from backlink_publisher.publishing.registry import _REJECTED_PLATFORMS

        assert "mastodon" not in _REJECTED_PLATFORMS

    def test_wordpresscom_still_rejected(self):
        """Removing mastodon must not perturb the remaining rejection entry."""
        from backlink_publisher.publishing.registry import _REJECTED_PLATFORMS

        assert "wordpresscom" in _REJECTED_PLATFORMS

    def test_mastodon_in_registered_platforms(self):
        import backlink_publisher.publishing.adapters  # noqa: F401
        from backlink_publisher.publishing.registry import registered_platforms

        assert "mastodon" in registered_platforms()


@pytest.mark.real_browser_publish_smoke
@pytest.mark.skip(
    reason="Opt-in: open <instance>/publish in attached Chrome with "
    "MASTODON_SMOKE_INSTANCE_URL env set; verify _mastodon_selectors."
)
def test_mastodon_selectors_match_live_dom():
    raise AssertionError("Operator-only smoke test")
