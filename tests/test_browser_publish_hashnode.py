"""Tests for browser_publish.recipes.hashnode — Plan 2026-05-21-001 Unit 3.

Covers recipe registration, publish_flow happy/edge paths, selector
constants, dispatch chain fallthrough from HashnodeAPIAdapter
(paywalled → DependencyError) into BrowserPublishDispatcher.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backlink_publisher._util.errors import DependencyError
from backlink_publisher.publishing.browser_publish import RECIPES
from backlink_publisher.publishing.browser_publish import (
    dispatcher as disp_mod,
)
from backlink_publisher.publishing.browser_publish.recipes import (
    hashnode as hn_recipe,
)
from backlink_publisher.publishing.browser_publish.recipes import (
    _hashnode_selectors as sel,
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRecipeRegistered:
    def test_hashnode_in_recipes(self):
        assert "hashnode" in RECIPES
        assert RECIPES["hashnode"].channel == "hashnode"
        assert RECIPES["hashnode"].compose_url == sel.COMPOSE_URL

    def test_recipe_publish_flow_is_module_callable(self):
        assert RECIPES["hashnode"].publish_flow is hn_recipe.hashnode_publish_flow


# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------


class TestSelectorsModule:
    @pytest.mark.parametrize(
        "name",
        [
            "COMPOSE_URL",
            "TITLE_INPUT",
            "BODY_EDITOR",
            "OPEN_PUBLISH_DIALOG_BUTTON",
            "CONFIRM_PUBLISH_BUTTON_IN_DIALOG",
            "POST_PUBLISHED_URL_RE",
        ],
    )
    def test_selector_constant_defined(self, name):
        value = getattr(sel, name, None)
        assert value, f"{name} must be non-empty"


# ---------------------------------------------------------------------------
# publish_flow
# ---------------------------------------------------------------------------


def _make_page(*, final_url: str = "https://op.hashnode.dev/my-post-abc123"):
    page = MagicMock(name="hashnode_page")
    body = MagicMock(name="hashnode_body")
    page.query_selector.return_value = body
    page.url = final_url
    return page, body


class TestHashnodePublishFlow:
    def test_happy_path_returns_post_url(self):
        page, body = _make_page()
        url = hn_recipe.hashnode_publish_flow(
            page,
            {"title": "Hello Hashnode", "content_markdown": "# Body"},
        )
        assert url == "https://op.hashnode.dev/my-post-abc123"
        page.goto.assert_called_once_with(sel.COMPOSE_URL)
        page.fill.assert_called_once_with(sel.TITLE_INPUT, "Hello Hashnode")
        body.fill.assert_called_once_with("# Body")
        assert page.click.call_count == 2  # open + confirm publish
        page.wait_for_url.assert_called_once_with(
            sel.POST_PUBLISHED_URL_RE,
            timeout=sel.POST_PUBLISH_REDIRECT_TIMEOUT_MS,
        )

    def test_accepts_body_alias(self):
        page, body = _make_page()
        hn_recipe.hashnode_publish_flow(page, {"title": "T", "body": "alt"})
        body.fill.assert_called_once_with("alt")

    def test_missing_title_raises_value_error(self):
        page, _ = _make_page()
        with pytest.raises(ValueError, match="title or content_markdown"):
            hn_recipe.hashnode_publish_flow(page, {"content_markdown": "b"})

    def test_missing_body_raises_value_error(self):
        page, _ = _make_page()
        with pytest.raises(ValueError, match="title or content_markdown"):
            hn_recipe.hashnode_publish_flow(page, {"title": "t"})

    def test_missing_body_editor_raises_runtime_error(self):
        page, _ = _make_page()
        page.query_selector.return_value = None
        with pytest.raises(RuntimeError, match="body editor not found"):
            hn_recipe.hashnode_publish_flow(
                page, {"title": "t", "content_markdown": "b"}
            )


# ---------------------------------------------------------------------------
# Dispatch chain
# ---------------------------------------------------------------------------


class TestHashnodeChainFallthrough:
    @pytest.fixture
    def fake_config(self):
        return MagicMock(name="fake_config")

    def test_hashnode_chain_has_both_adapters(self):
        import backlink_publisher.publishing.adapters  # noqa: F401
        from backlink_publisher.publishing.registry import _REGISTRY
        from backlink_publisher.publishing.adapters.hashnode import (
            HashnodeAPIAdapter,
        )
        from backlink_publisher.publishing.browser_publish import (
            BrowserPublishDispatcher,
        )

        chain = _REGISTRY["hashnode"]
        assert len(chain) == 2
        assert chain[0] is HashnodeAPIAdapter
        assert isinstance(chain[1], BrowserPublishDispatcher)
        assert chain[1].channel == "hashnode"

    def test_paywalled_api_falls_through_to_browser(
        self, monkeypatch, fake_config
    ):
        """HashnodeAPIAdapter raises DependencyError (paywall) → browser fires."""
        import backlink_publisher.publishing.adapters  # noqa: F401
        from backlink_publisher.publishing.registry import dispatch
        from backlink_publisher.publishing.adapters.base import AdapterResult
        from backlink_publisher.publishing.adapters.hashnode import (
            HashnodeAPIAdapter,
        )
        from backlink_publisher.publishing.browser_publish import (
            BrowserPublishDispatcher,
        )

        def fake_api_publish(self, payload, mode, config):
            raise DependencyError("hashnode GraphQL paywalled since 2026-05-13")

        monkeypatch.setattr(HashnodeAPIAdapter, "publish", fake_api_publish)
        monkeypatch.setattr(
            HashnodeAPIAdapter, "available", classmethod(lambda cls, cfg: True)
        )
        monkeypatch.setattr(
            BrowserPublishDispatcher,
            "available",
            classmethod(lambda cls, cfg: True),
        )

        page, _ = _make_page(final_url="https://op.hashnode.dev/fallback-slug")

        class FakeSession:
            def __init__(self, channel, **kwargs):
                self.channel = channel

            def __enter__(self):
                return page

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(disp_mod, "ChromeAttachSession", FakeSession)
        monkeypatch.setattr(
            disp_mod, "verify_link_attributes", lambda url: {"verification": "ok"}
        )

        result = dispatch(
            {
                "platform": "hashnode",
                "title": "x",
                "content_markdown": "y",
                "target_url": "https://t.example",
            },
            "publish",
            fake_config,
        )
        assert isinstance(result, AdapterResult)
        assert result.adapter == "hashnode-browser-attach"
        assert result.published_url == "https://op.hashnode.dev/fallback-slug"

    def test_registered_platforms_still_includes_hashnode(self):
        """No CLI / schema changes — R9 extension readiness preserved."""
        import backlink_publisher.publishing.adapters  # noqa: F401
        from backlink_publisher.publishing.registry import registered_platforms

        assert "hashnode" in registered_platforms()


# ---------------------------------------------------------------------------
# Live smoke (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.real_browser_publish_smoke
@pytest.mark.skip(
    reason="Opt-in: open hashnode.com/new in attached Chrome and verify "
    "_hashnode_selectors. Note Cloudflare may challenge — fail-soft per plan."
)
def test_hashnode_selectors_match_live_dom():
    raise AssertionError("Operator-only smoke test — see skip reason.")
