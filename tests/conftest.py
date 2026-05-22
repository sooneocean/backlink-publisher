"""Top-level pytest fixtures.

Plan 2026-05-14-001 Unit 5: prevents new test files from accidentally firing
real HTTP via the new ``publish_backlinks.check_url`` consumer reference.
Existing tests carry per-file autouse mocks (per
``feedback_test-autouse-verify-mock``); this conftest is additive and does
not mass-migrate them.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_user_dirs(tmp_path_factory: pytest.TempPathFactory):
    """Isolate the operator's config and cache dirs from the test session.

    Without this fixture, ``backlink_publisher.config._config_dir()`` resolves
    to ``~/.config/backlink-publisher/`` and any ``[targets."<domain>"]`` /
    ``[sites."<domain>".url_categories]`` entries in the operator's real
    ``config.toml`` silently leak into test runs. Reason: 2026-05-18 bug
    sweep (PR #40) traced ``test_plan_no_synthesized_categories_url`` to
    exactly this coupling — a configured ``[targets."https://51acgs.com"]``
    routed ``_dispatch_row`` to the work-themed branch, ``work_scraper`` then
    failed under pytest-socket, and the test got empty stdout.

    Mechanism: set ``BACKLINK_PUBLISHER_CONFIG_DIR`` / ``..._CACHE_DIR``
    (supported in ``config.py`` since 2026-05-18) to fresh tmp dirs for the
    whole session. Tests that need a populated config can write into the
    pointed-at directory via ``save_config`` or write their own monkeypatch.
    """
    config_dir = tmp_path_factory.mktemp("bp-config-isolated")
    cache_dir = tmp_path_factory.mktemp("bp-cache-isolated")
    previous_config = os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR")
    previous_cache = os.environ.get("BACKLINK_PUBLISHER_CACHE_DIR")
    os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = str(config_dir)
    os.environ["BACKLINK_PUBLISHER_CACHE_DIR"] = str(cache_dir)

    # ``webui_store`` singletons are ``_LazyStore`` proxies that resolve
    # their backing path on first access. We *also* force a reset here
    # because pytest collection (which imports test modules before this
    # fixture runs) could touch a store via an import-time side effect
    # and cache it against the operator's real ``~/.config/`` path.
    # Belt-and-suspenders against the channel-status.json contamination
    # incident on 2026-05-22 — see project_test_isolation_leak_2026_05_22.
    try:
        from webui_store import _refresh_paths as _bp_refresh_paths
        _bp_refresh_paths()
    except Exception:
        # _refresh_paths may not exist on older branches; that's fine —
        # the lazy-resolve-on-first-access path still works on the
        # happy path.
        pass

    yield
    if previous_config is None:
        os.environ.pop("BACKLINK_PUBLISHER_CONFIG_DIR", None)
    else:
        os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = previous_config
    if previous_cache is None:
        os.environ.pop("BACKLINK_PUBLISHER_CACHE_DIR", None)
    else:
        os.environ["BACKLINK_PUBLISHER_CACHE_DIR"] = previous_cache


@pytest.fixture(autouse=True)
def _mock_publish_check_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``publish_backlinks.check_url`` at the consumer reference.

    Per ``feedback_test-autouse-verify-mock`` + the
    ``ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13``
    solution doc, mocking at the *consumer* module's reference catches calls
    that would otherwise bypass module-level patches.

    Default behavior: every URL is considered reachable. Tests that need to
    drive specific failure paths can re-patch within their own scope.
    """
    # check_url promoted to module-level in _publish_helpers.py;
    # patch at the consumer reference per feedback_test-autouse-verify-mock.
    monkeypatch.setattr(
        "backlink_publisher.cli._publish_helpers.check_url",
        lambda _url: (True, None),
        raising=True,
    )


@pytest.fixture(autouse=True)
def _mock_content_fetch(request, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default-pass the content-fetch gate in every test.

    Plan 2026-05-14-007 Unit 6: the gate fires inside ``_build_links`` at
    plan time. Without this autouse fixture, every existing plan-backlinks
    test would either hit the network (blocked by ``_disable_real_network``)
    or trip the cache, depending on test order.

    Patches at both the producer module (``backlink_publisher.content_fetch``)
    and the consumer reference in ``plan_backlinks`` so tests that import the
    function either way see the mock. Also clears the in-run cache before
    each test so cache state never leaks across scenarios.

    Tests in ``tests/test_content_fetch.py`` exercise the real functions
    against mocked ``urlopen`` — that file declares ``pytestmark =
    pytest.mark.real_content_fetch`` at module level, and this fixture
    honors the marker to skip patching so the assertions hit the production
    code path. Other test files that want to drive specific gate-failure
    paths re-patch ``backlink_publisher.content_fetch.verify_urls_batch``
    within their own scope (last-wins monkeypatch semantics).

    Mirrors the ``real_ssrf_check`` opt-in pattern. Marker registration is
    in ``pyproject.toml [tool.pytest.ini_options] markers``.
    """
    # Reset cache state up front so previous tests don't contaminate this one.
    from backlink_publisher.content import fetch as _content_fetch

    _content_fetch.reset_cache()

    # Tests marked ``real_content_fetch`` exercise the real functions
    # against mocked ``urlopen`` and must not see the default-pass mock.
    if request.node.get_closest_marker("real_content_fetch"):
        return

    def _ok_batch(urls, max_workers=5):
        return {u: (True, None, "mock title") for u in urls}

    def _ok_single(_url):
        return (True, None, "mock title")

    monkeypatch.setattr(
        "backlink_publisher.content.fetch.verify_urls_batch",
        _ok_batch,
        raising=True,
    )
    monkeypatch.setattr(
        "backlink_publisher.content.fetch.verify_url_has_content",
        _ok_single,
        raising=True,
    )


try:
    import pytest_socket  # noqa: F401
except ImportError:  # pragma: no cover
    _HAS_SOCKET = False
else:
    _HAS_SOCKET = True


@pytest.fixture(autouse=True)
def _disable_real_network() -> None:
    """Block real network access in tests so missed mocks fail loud.

    If pytest-socket is available we use it as a hard CI safety net (any
    test that bypasses the autouse ``check_url`` patch and tries to open
    a real socket will raise). If pytest-socket is not installed (e.g.,
    dev environment without dev-deps), the fixture is a no-op and the
    ``_mock_publish_check_url`` fixture above is the only line of defense.
    """
    if _HAS_SOCKET:
        from pytest_socket import disable_socket, enable_socket
        disable_socket(allow_unix_socket=True)
        try:
            yield
        finally:
            enable_socket()
    else:
        yield


# ── Shared registry fixture (Plan 2026-05-19-002 U2: promoted from
#    tests/test_r9_extension_readiness.py to prevent copy-paste drift,
#    per adversarial F7).
#
#    Provides a ``fake_platform_registered`` fixture that registers a
#    ``FakeAdapter`` under the slug ``"fake"`` for the test duration and
#    restores ``_REGISTRY`` on teardown.  Used by R9 acceptance tests and
#    by ``test_webui_platforms_context.py`` to prove the registry → WebUI
#    reverse-driven contract holds for any future ``register(...)`` call.


from typing import Any as _Any  # noqa: E402

from backlink_publisher.publishing.adapters.base import (  # noqa: E402
    AdapterResult as _AdapterResult,
)
from backlink_publisher.publishing.registry import (  # noqa: E402
    Publisher as _Publisher,
    register as _register,
    _REGISTRY as __REGISTRY,
    _DOFOLLOW_BY_PLATFORM as __DOFOLLOW_BY_PLATFORM,
    _RATIONALE_BY_PLATFORM as __RATIONALE_BY_PLATFORM,
)


class FakeAdapter(_Publisher):
    """Stub publisher shared across registry/WebUI contract tests."""

    @classmethod
    def available(cls, config: _Any) -> bool:
        return True

    def publish(self, payload: dict[str, _Any], mode: str, config: _Any) -> _AdapterResult:
        return _AdapterResult(
            status="drafted",
            adapter="fake",
            platform="fake",
            draft_url="https://fake.example/p/1",
        )


@pytest.fixture
def fake_platform_registered():
    """Register ``FakeAdapter`` as platform ``"fake"`` for one test.

    Snapshots and restores the prior ``_REGISTRY["fake"]`` entry so
    parallel/repeat test runs cannot leak adapter state across cases.

    Plan 2026-05-20-009 U3: also snapshot+restore the matching key in
    the new parallel dofollow/rationale dicts so the per-key fixture
    pattern stays internally consistent across all three registry maps.
    """
    previous = __REGISTRY.get("fake")
    previous_dofollow = __DOFOLLOW_BY_PLATFORM.get("fake")
    previous_rationale = __RATIONALE_BY_PLATFORM.get("fake")
    _register("fake", FakeAdapter, dofollow=True)
    try:
        yield
    finally:
        if previous is None:
            __REGISTRY.pop("fake", None)
        else:
            __REGISTRY["fake"] = previous
        if previous_dofollow is None:
            __DOFOLLOW_BY_PLATFORM.pop("fake", None)
        else:
            __DOFOLLOW_BY_PLATFORM["fake"] = previous_dofollow
        if previous_rationale is None:
            __RATIONALE_BY_PLATFORM.pop("fake", None)
        else:
            __RATIONALE_BY_PLATFORM["fake"] = previous_rationale
