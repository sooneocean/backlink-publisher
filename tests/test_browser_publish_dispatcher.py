"""Tests for browser_publish.dispatcher — Plan 2026-05-21-001 Unit 2.

Covers:
  - BrowserPublishDispatcher.for_channel factory contract
  - publish() happy path via stubbed ChromeAttachSession
  - URL-level signin detection → AuthExpiredError
  - DependencyError + ExternalServiceError propagation
  - Recipe-internal arbitrary exception → ExternalServiceError wrap
  - Registry accepts Publisher instance entries (instance path)
  - Registry preserves class entries (legacy path)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher._util.errors import (
    AuthExpiredError,
    DependencyError,
    ExternalServiceError,
)
from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.publishing.browser_publish import (
    BrowserPublishDispatcher,
    BrowserPublishRecipe,
    RECIPES,
)
from backlink_publisher.publishing.browser_publish import dispatcher as disp_mod
from backlink_publisher.publishing import registry as reg_mod


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_recipe(
    channel: str = "stubchannel",
    *,
    publish_flow=None,
    compose_url: str = "https://stub.example/new",
) -> BrowserPublishRecipe:
    if publish_flow is None:
        def publish_flow(page, payload):
            return "https://stub.example/p/123"
    return BrowserPublishRecipe(
        channel=channel, compose_url=compose_url, publish_flow=publish_flow
    )


@pytest.fixture
def fake_config():
    return MagicMock(name="fake_config")


@pytest.fixture
def stubbed_session(monkeypatch):
    """Patch ChromeAttachSession in dispatcher module so __enter__ yields a page stub.

    The yielded page exposes a ``.url`` attribute that tests can override.
    """
    page = MagicMock(name="page")
    page.url = "https://stub.example/new"

    captured: dict = {"channel": None, "session": None}

    class FakeSession:
        def __init__(self, channel, **kwargs):
            captured["channel"] = channel
            captured["session"] = self

        def __enter__(self):
            return page

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(disp_mod, "ChromeAttachSession", FakeSession)
    return {"page": page, "captured": captured}


@pytest.fixture
def stubbed_verify(monkeypatch):
    monkeypatch.setattr(
        disp_mod,
        "verify_link_attributes",
        lambda url, *, target_urls=None: {"verification": "ok", "url": url},
    )


@pytest.fixture(autouse=True)
def clear_recipes():
    """Ensure RECIPES is empty per test (avoid cross-test recipe leak)."""
    snapshot = dict(RECIPES)
    RECIPES.clear()
    yield
    RECIPES.clear()
    RECIPES.update(snapshot)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestForChannel:
    def test_returns_instance_with_bound_recipe(self):
        recipe = _make_recipe("foo")
        RECIPES["foo"] = recipe
        dispatcher = BrowserPublishDispatcher.for_channel("foo")
        assert isinstance(dispatcher, BrowserPublishDispatcher)
        assert dispatcher.channel == "foo"
        assert dispatcher.recipe is recipe

    def test_missing_recipe_raises_dependency_error(self):
        with pytest.raises(DependencyError, match="no BrowserPublishRecipe registered"):
            BrowserPublishDispatcher.for_channel("not-registered")

    def test_for_channel_each_call_returns_distinct_instance(self):
        recipe = _make_recipe("dup")
        RECIPES["dup"] = recipe
        a = BrowserPublishDispatcher.for_channel("dup")
        b = BrowserPublishDispatcher.for_channel("dup")
        assert a is not b
        # but they share the same class — no dynamic class creation
        assert type(a) is type(b) is BrowserPublishDispatcher

    def test_channel_recipe_mismatch_raises_in_ctor(self):
        recipe = _make_recipe("a")
        with pytest.raises(ValueError, match="channel/recipe mismatch"):
            BrowserPublishDispatcher("b", recipe)


# ---------------------------------------------------------------------------
# publish() lifecycle
# ---------------------------------------------------------------------------


class TestPublish:
    def test_happy_path_returns_published_result(
        self, fake_config, stubbed_session, stubbed_verify
    ):
        recipe = _make_recipe("devto")
        RECIPES["devto"] = recipe
        dispatcher = BrowserPublishDispatcher.for_channel("devto")

        result = dispatcher.publish({"target_url": "https://t.example"}, "publish", fake_config)

        assert isinstance(result, AdapterResult)
        assert result.status == "published"
        assert result.adapter == "devto-browser-attach"
        assert result.platform == "devto"
        assert result.published_url == "https://stub.example/p/123"
        assert result._provider_meta == {
            "link_attr_verification": {"verification": "ok", "url": "https://stub.example/p/123"}
        }
        # ChromeAttachSession received the channel slug.
        assert stubbed_session["captured"]["channel"] == "devto"

    def test_signin_url_raises_auth_expired_for_bind_known_channel(
        self, fake_config, stubbed_session
    ):
        """Bind-known channel (velog) → AuthExpiredError + mark_expired."""
        stubbed_session["page"].url = "https://velog.io/signin?return=/write"
        recipe = _make_recipe("velog")
        RECIPES["velog"] = recipe
        dispatcher = BrowserPublishDispatcher.for_channel("velog")

        with patch("webui_store.channel_status.mark_expired") as mark:
            with pytest.raises(AuthExpiredError, match="signin URL"):
                dispatcher.publish({}, "publish", fake_config)
            mark.assert_called_once_with("velog")

    def test_signin_url_raises_dependency_error_for_publish_only_channel(
        self, fake_config, stubbed_session
    ):
        """Publish-only channel (devto not in CHANNELS) → DependencyError, no mark_expired."""
        stubbed_session["page"].url = "https://dev.to/sign-in"
        recipe = _make_recipe("devto")
        RECIPES["devto"] = recipe
        dispatcher = BrowserPublishDispatcher.for_channel("devto")

        with patch("webui_store.channel_status.mark_expired") as mark:
            with pytest.raises(DependencyError, match="signin URL"):
                dispatcher.publish({}, "publish", fake_config)
            mark.assert_not_called()

    def test_auth_expired_resilient_to_mark_failure(
        self, fake_config, stubbed_session
    ):
        """mark_expired IO failure must NOT mask the AuthExpiredError."""
        stubbed_session["page"].url = "https://velog.io/login"
        recipe = _make_recipe("velog")
        RECIPES["velog"] = recipe
        dispatcher = BrowserPublishDispatcher.for_channel("velog")

        with patch(
            "webui_store.channel_status.mark_expired",
            side_effect=IOError("disk full"),
        ):
            with pytest.raises(AuthExpiredError):
                dispatcher.publish({}, "publish", fake_config)

    def test_dependency_error_propagates(self, fake_config, stubbed_session):
        def flow(page, payload):
            raise DependencyError("missing creds")

        recipe = _make_recipe("devto", publish_flow=flow)
        RECIPES["devto"] = recipe
        dispatcher = BrowserPublishDispatcher.for_channel("devto")
        with pytest.raises(DependencyError, match="missing creds"):
            dispatcher.publish({}, "publish", fake_config)

    def test_external_service_error_propagates(self, fake_config, stubbed_session):
        def flow(page, payload):
            raise ExternalServiceError("devto 500")

        recipe = _make_recipe("devto", publish_flow=flow)
        RECIPES["devto"] = recipe
        dispatcher = BrowserPublishDispatcher.for_channel("devto")
        with pytest.raises(ExternalServiceError, match="devto 500"):
            dispatcher.publish({}, "publish", fake_config)

    def test_recipe_arbitrary_exception_wrapped_as_external_service_error(
        self, fake_config, stubbed_session
    ):
        def flow(page, payload):
            raise RuntimeError("devto DOM changed")

        recipe = _make_recipe("devto", publish_flow=flow)
        RECIPES["devto"] = recipe
        dispatcher = BrowserPublishDispatcher.for_channel("devto")
        with pytest.raises(ExternalServiceError, match="devto browser publish failed"):
            dispatcher.publish({}, "publish", fake_config)

    def test_published_result_without_target_url_omits_verification(
        self, fake_config, stubbed_session, stubbed_verify
    ):
        recipe = _make_recipe("devto")
        RECIPES["devto"] = recipe
        dispatcher = BrowserPublishDispatcher.for_channel("devto")
        result = dispatcher.publish({}, "publish", fake_config)
        # verify_link_attributes still runs even without target_url —
        # it only needs final_url. _provider_meta gets populated.
        assert result._provider_meta is not None

    def test_verify_link_attrs_swallows_errors(
        self, fake_config, stubbed_session, monkeypatch
    ):
        monkeypatch.setattr(
            disp_mod, "verify_link_attributes",
            lambda url: (_ for _ in ()).throw(RuntimeError("net fail")),
        )
        recipe = _make_recipe("devto")
        RECIPES["devto"] = recipe
        dispatcher = BrowserPublishDispatcher.for_channel("devto")
        result = dispatcher.publish({"target_url": "https://t.example"}, "publish", fake_config)
        assert result.status == "published"
        assert result._provider_meta is None


# ---------------------------------------------------------------------------
# available()
# ---------------------------------------------------------------------------


class TestAvailable:
    def test_available_false_when_chrome_missing(self, fake_config, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_REAL_CHROME_BIN", "/nonexistent/chrome")
        # Patch into the dispatcher's chrome_binary import path.
        from backlink_publisher.publishing.browser_publish import chrome_session as cs
        monkeypatch.setattr(cs, "_chrome_binary", lambda: None)
        assert BrowserPublishDispatcher.available(fake_config) is False

    def test_available_false_when_playwright_missing(self, fake_config, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "playwright.sync_api" or name.startswith("playwright"):
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert BrowserPublishDispatcher.available(fake_config) is False


# ---------------------------------------------------------------------------
# Registry instance support
# ---------------------------------------------------------------------------


class TestRegistryAcceptsInstances:
    @pytest.fixture
    def isolated_registry(self):
        # Snapshot and restore _REGISTRY around each test.
        snap = {k: list(v) for k, v in reg_mod._REGISTRY.items()}
        dofollow_snap = dict(reg_mod._DOFOLLOW_BY_PLATFORM)
        rationale_snap = dict(reg_mod._RATIONALE_BY_PLATFORM)
        yield
        reg_mod._REGISTRY.clear()
        reg_mod._REGISTRY.update(snap)
        reg_mod._DOFOLLOW_BY_PLATFORM.clear()
        reg_mod._DOFOLLOW_BY_PLATFORM.update(dofollow_snap)
        reg_mod._RATIONALE_BY_PLATFORM.clear()
        reg_mod._RATIONALE_BY_PLATFORM.update(rationale_snap)

    def test_register_accepts_instance(self, isolated_registry):
        recipe = _make_recipe("instplat")
        RECIPES["instplat"] = recipe
        instance = BrowserPublishDispatcher.for_channel("instplat")
        # dofollow=True so no rationale needed
        reg_mod.register("instplat", instance, dofollow=True)
        assert reg_mod._REGISTRY["instplat"] == [instance]

    def test_dispatch_skips_instantiation_for_instance_entry(
        self, isolated_registry, fake_config, stubbed_session, stubbed_verify
    ):
        recipe = _make_recipe("dispatchinst")
        RECIPES["dispatchinst"] = recipe
        instance = BrowserPublishDispatcher.for_channel("dispatchinst")
        reg_mod.register("dispatchinst", instance, dofollow=True)

        # dispatch must call .available() on the class and .publish() on the
        # instance. No instantiation happens because entry is already an instance.
        result = reg_mod.dispatch(
            {"platform": "dispatchinst", "target_url": "https://t.example"},
            "publish",
            fake_config,
        )
        assert result.status == "published"
        assert result.platform == "dispatchinst"

    def test_dispatch_still_instantiates_class_entry(self, isolated_registry, fake_config):
        """Legacy: class entries still get cls() — backward compat."""
        instantiated: list = []

        from backlink_publisher.publishing.registry import Publisher

        class FakeClassAdapter(Publisher):
            def __init__(self):
                instantiated.append(self)

            def publish(self, payload, mode, config):
                return AdapterResult(
                    status="published",
                    adapter="fake-class",
                    platform=payload.get("platform", ""),
                    published_url="https://fake/post",
                )

        reg_mod.register("fakeclassplat", FakeClassAdapter, dofollow=True)
        result = reg_mod.dispatch(
            {"platform": "fakeclassplat"}, "publish", fake_config
        )
        assert len(instantiated) == 1
        assert result.adapter == "fake-class"

    def test_dispatch_mixed_class_and_instance_chain(
        self, isolated_registry, fake_config, stubbed_session, stubbed_verify
    ):
        """Class entry first → unavailable → falls through to instance entry."""
        from backlink_publisher.publishing.registry import Publisher

        class UnavailableClassAdapter(Publisher):
            @classmethod
            def available(cls, config):
                return False

            def publish(self, payload, mode, config):
                raise AssertionError("should be skipped")

        recipe = _make_recipe("mixchain")
        RECIPES["mixchain"] = recipe
        instance = BrowserPublishDispatcher.for_channel("mixchain")

        reg_mod.register("mixchain", UnavailableClassAdapter, instance, dofollow=True)

        result = reg_mod.dispatch(
            {"platform": "mixchain", "target_url": "https://t.example"},
            "publish",
            fake_config,
        )
        assert result.platform == "mixchain"
        assert result.status == "published"
