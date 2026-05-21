"""Tests for browser_publish.recipes.devto — Plan 2026-05-21-001 Unit 4b.

Covers recipe registration, publish_flow happy path with tags, missing
fields, dispatch chain shape, and the rejection-map deletion path.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backlink_publisher.publishing.browser_publish import RECIPES
from backlink_publisher.publishing.browser_publish.recipes import (
    devto as devto_recipe,
)
from backlink_publisher.publishing.browser_publish.recipes import (
    _devto_selectors as sel,
)


class TestRecipeRegistered:
    def test_devto_in_recipes(self):
        assert "devto" in RECIPES
        assert RECIPES["devto"].channel == "devto"
        assert RECIPES["devto"].compose_url == sel.COMPOSE_URL

    def test_publish_flow_callable_matches(self):
        assert RECIPES["devto"].publish_flow is devto_recipe.devto_publish_flow


class TestSelectorsModule:
    @pytest.mark.parametrize(
        "name",
        [
            "COMPOSE_URL",
            "TITLE_INPUT",
            "BODY_EDITOR_TEXTAREA",
            "PUBLISH_BUTTON",
            "POST_PUBLISHED_URL_RE",
        ],
    )
    def test_selector_constant_defined(self, name):
        assert getattr(sel, name, None), f"{name} must be non-empty"


def _make_page(*, final_url: str = "https://dev.to/op/my-post-slug-12ab"):
    page = MagicMock(name="devto_page")
    body = MagicMock(name="devto_body")
    tags = MagicMock(name="devto_tags")

    # query_selector returns body for body selector, tags for tags selector.
    def query_dispatch(s):
        if s == sel.BODY_EDITOR_TEXTAREA:
            return body
        if s == sel.TAGS_INPUT:
            return tags
        return None

    page.query_selector.side_effect = query_dispatch
    page.url = final_url
    return page, body, tags


class TestDevtoPublishFlow:
    def test_happy_path_returns_post_url(self):
        page, body, tags = _make_page()
        url = devto_recipe.devto_publish_flow(
            page,
            {
                "title": "Dev.to test",
                "content_markdown": "# Hello",
                "tags": ["python", "rust", "go", "extra", "drop-me"],
            },
        )
        assert url == "https://dev.to/op/my-post-slug-12ab"
        page.goto.assert_called_once_with(sel.COMPOSE_URL)
        page.fill.assert_called_once_with(sel.TITLE_INPUT, "Dev.to test")
        body.fill.assert_called_once_with("# Hello")
        # Tags capped at 4 (dev.to limit).
        tags.fill.assert_called_once_with("python, rust, go, extra")
        page.click.assert_called_once_with(sel.PUBLISH_BUTTON)

    def test_no_tags_skips_tag_fill(self):
        page, body, tags = _make_page()
        devto_recipe.devto_publish_flow(
            page, {"title": "t", "content_markdown": "b"}
        )
        tags.fill.assert_not_called()

    def test_missing_title_raises_value_error(self):
        page, _, _ = _make_page()
        with pytest.raises(ValueError, match="title or content_markdown"):
            devto_recipe.devto_publish_flow(page, {"content_markdown": "b"})

    def test_missing_body_textarea_raises_runtime_error(self):
        page, _, _ = _make_page()
        page.query_selector.side_effect = lambda s: None
        with pytest.raises(RuntimeError, match="body textarea not found"):
            devto_recipe.devto_publish_flow(
                page, {"title": "t", "content_markdown": "b"}
            )


class TestDevtoChain:
    def test_devto_chain_uses_browser_only(self):
        import backlink_publisher.publishing.adapters  # noqa: F401
        from backlink_publisher.publishing.registry import _REGISTRY
        from backlink_publisher.publishing.browser_publish import (
            BrowserPublishDispatcher,
        )

        chain = _REGISTRY["devto"]
        assert len(chain) == 1
        assert isinstance(chain[0], BrowserPublishDispatcher)
        assert chain[0].channel == "devto"

    def test_devto_removed_from_rejection_map(self):
        from backlink_publisher.publishing.registry import _REJECTED_PLATFORMS

        assert "devto" not in _REJECTED_PLATFORMS

    def test_other_rejections_intact(self):
        """Removing devto must not perturb the rest of the rejection map.

        Mastodon was originally a sibling rejection but shipped as a
        chrome-publish channel in Unit 4c (PR stacked on top of this
        one); only wordpresscom remains as canonical-rejected.
        """
        from backlink_publisher.publishing.registry import _REJECTED_PLATFORMS

        assert "wordpresscom" in _REJECTED_PLATFORMS

    def test_devto_in_registered_platforms(self):
        import backlink_publisher.publishing.adapters  # noqa: F401
        from backlink_publisher.publishing.registry import registered_platforms

        assert "devto" in registered_platforms()


@pytest.mark.real_browser_publish_smoke
@pytest.mark.skip(
    reason="Opt-in: open dev.to/new in attached Chrome and verify "
    "_devto_selectors against live DOM."
)
def test_devto_selectors_match_live_dom():
    raise AssertionError("Operator-only smoke test")
