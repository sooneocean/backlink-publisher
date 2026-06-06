"""Top-level pytest fixtures.

Plan 2026-05-14-001 Unit 5: prevents new test files from accidentally firing
real HTTP via the new ``publish_backlinks.check_url`` consumer reference.
Existing tests carry per-file autouse mocks (per
``feedback_test-autouse-verify-mock``); this conftest is additive and does
not mass-migrate them.
"""

from __future__ import annotations

import atexit
import os
import pwd
import shutil
import tempfile
from pathlib import Path

import pytest

# ── Config-sandbox-escape guardrails (Plan 2026-05-27-005) ──────────────────
# Sentinel env var that marks "we are inside a sandboxed test run".
# Set by the HOME/override redirect (Unit 3) and propagated to spawned children.
# Keyed here so the AST gate (test_no_raw_home_path_primitives.py) and the
# fail-closed resolver (loader.py Unit 4) share the exact same string without
# any module-level import dependency chain.
SANDBOX_SENTINEL = "BACKLINK_PUBLISHER_TEST_SANDBOX"

# AST gate: the one allowlisted module that may contain raw home-path primitives.
_RAW_HOME_ALLOWED_MODULE = "src/backlink_publisher/config/loader.py"

# Sites whose .expanduser() calls on operator-supplied env vars are legitimate
# (expanding BACKLINK_PUBLISHER_REAL_CHROME_* config, not constructing a raw root).
# Format: frozenset of (relative_file_path, 0-indexed_line_number) pairs.
# Shrink-only: the gate asserts discovered_grandfathered_set == this set.
# To add: audit the site carefully; add a rationale comment here.
GRANDFATHERED_EXPANDUSER_SITES: frozenset[tuple[str, int]] = frozenset(
    {
        # discover_chrome_binary(): expands BACKLINK_PUBLISHER_REAL_CHROME_BIN
        # (operator-supplied Chrome binary path, not an operator-state-root
        # construction — legitimately uses ~ to find the binary).
        (
            "src/backlink_publisher/publishing/browser_publish/_chrome_session_impl.py",
            66,
        ),
        # _resolve_chrome_profile_dir(): expands BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR
        # (same rationale — operator-supplied profile dir may contain "~"; the
        # default branch already calls _config_dir() correctly).
        (
            "src/backlink_publisher/publishing/browser_publish/_chrome_session_impl.py",
            116,
        ),
        # _ChromeSession.open() in instant_web.py: env var
        # BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR may contain "~"; the
        # default arg now uses _config_dir() (folded in Unit 1) so the raw-root
        # escape is gone — only the env-var expansion remains.
        # AST lineno 77 = start of the Path(os.environ.get(...)).expanduser()
        # multi-line expression (AST reports the opening line of the call).
        (
            "src/backlink_publisher/publishing/adapters/instant_web.py",
            77,
        ),
    }
)

# ── Layer 2: Pre-import HOME/override redirect (Plan 2026-05-27-005 Unit 3) ──
#
# MUST run as module-level code, before any backlink_publisher import.
# pytest_configure is too late — the conftest body imports
# publishing.registry/adapters.base at module load, which transitively
# can freeze a raw Path.home() constant against the real HOME.
#
# Step 1: Capture the real operator roots via the OS (not $HOME, which we're
# about to overwrite). Use pwd.getpwuid to bypass any existing $HOME mutation.
# These are stored in REAL_CONFIG_ROOT / REAL_CACHE_ROOT so the tripwire
# (Unit 7) can watch the operator's actual files — post-redirect Path.home()
# and _config_dir() both resolve to the sandbox.
_real_pw_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
# Honour a pre-existing operator-exported CONFIG/CACHE override (rare, but
# possible in CI). If set, that IS the real root.
_pre_existing_config = os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR")
_pre_existing_cache = os.environ.get("BACKLINK_PUBLISHER_CACHE_DIR")
REAL_CONFIG_ROOT: Path = (
    Path(_pre_existing_config)
    if _pre_existing_config
    else _real_pw_home / ".config" / "backlink-publisher"
)
REAL_CACHE_ROOT: Path = (
    Path(_pre_existing_cache)
    if _pre_existing_cache
    else _real_pw_home / ".cache" / "backlink-publisher"
)

# Step 2: Create a sandbox home and redirect env vars BEFORE imports.
_sandbox_home_dir = Path(tempfile.mkdtemp(prefix="bp-sandbox-home-"))

# Snapshot every env var we are about to clobber (pop-or-reassign pattern).
_prev_env_home = os.environ.get("HOME")
_prev_env_config = os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR")
_prev_env_cache = os.environ.get("BACKLINK_PUBLISHER_CACHE_DIR")
_prev_env_sentinel = os.environ.get(SANDBOX_SENTINEL)
_prev_env_xdg_cfg = os.environ.get("XDG_CONFIG_HOME")
_prev_env_xdg_cache = os.environ.get("XDG_CACHE_HOME")

# Redirect.
os.environ["HOME"] = str(_sandbox_home_dir)
# Sandbox sub-dirs for the two overrides — _isolate_user_dirs will later
# replace these with its own mktemp dirs, but the sentinel and HOME must
# be set from this earliest point so any module-level import that calls
# _config_dir() already lands in the sandbox.
_sandbox_config_dir = _sandbox_home_dir / ".config" / "backlink-publisher"
_sandbox_cache_dir = _sandbox_home_dir / ".cache" / "backlink-publisher"
_sandbox_config_dir.mkdir(parents=True, exist_ok=True)
_sandbox_cache_dir.mkdir(parents=True, exist_ok=True)
os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = str(_sandbox_config_dir)
os.environ["BACKLINK_PUBLISHER_CACHE_DIR"] = str(_sandbox_cache_dir)
os.environ[SANDBOX_SENTINEL] = "1"
# XDG dirs — redirect so platformdirs-aware libraries also land in sandbox.
os.environ["XDG_CONFIG_HOME"] = str(_sandbox_home_dir / ".config")
os.environ["XDG_CACHE_HOME"] = str(_sandbox_home_dir / ".cache")

# Step 3: Blast-radius containment (R4a) — point git/coverage caches at
# sandbox subdirs so redirecting HOME doesn't break git-subprocess tests.
_sandbox_git_config = _sandbox_home_dir / ".gitconfig"
if not _sandbox_git_config.exists():
    _sandbox_git_config.write_text(
        "[user]\n\tname = Test User\n\temail = test@example.com\n",
        encoding="utf-8",
    )

# Step 4: Register atexit cleanup — restore env vars and remove the sandbox.
def _restore_home_redirect() -> None:  # noqa: E302 (inside module body)
    """Restore HOME and related env vars; delete the sandbox tmpdir."""
    for key, prev in [
        ("HOME", _prev_env_home),
        ("BACKLINK_PUBLISHER_CONFIG_DIR", _prev_env_config),
        ("BACKLINK_PUBLISHER_CACHE_DIR", _prev_env_cache),
        (SANDBOX_SENTINEL, _prev_env_sentinel),
        ("XDG_CONFIG_HOME", _prev_env_xdg_cfg),
        ("XDG_CACHE_HOME", _prev_env_xdg_cache),
    ]:
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev
    shutil.rmtree(str(_sandbox_home_dir), ignore_errors=True)


atexit.register(_restore_home_redirect)

# Real operator state roots — populated above (before the redirect fired).
# REAL_CONFIG_ROOT and REAL_CACHE_ROOT now hold the REAL paths.
# (Declared above; re-annotated here for readability.)
# REAL_CONFIG_ROOT: Path  ← already set
# REAL_CACHE_ROOT: Path   ← already set

# ── Test global-state pollution guardrail (Plan 2026-05-27-003) ─────────────
# Single source of truth for the security-relevant keys, shared by the
# containment net (below) and the AST gate
# (tests/test_security_toggle_mutation_gate.py imports SECURITY_CONFIG_KEYS).
#
# SECURITY_CONFIG_KEYS: webui.app.config keys the AST gate bans from raw
#   subscript mutation AND the net restores. SESSION_COOKIE_SECURE / SECRET_KEY
#   are cookie-integrity toggles that can neuter effective CSRF even with
#   CSRF_ENABLED=True (a leaked SESSION_COOKIE_SECURE=True strips the cookie
#   over HTTP -> no session -> token can't round-trip).
SECURITY_CONFIG_KEYS = frozenset(
    {"CSRF_ENABLED", "WTF_CSRF_ENABLED", "SESSION_COOKIE_SECURE", "SECRET_KEY"}
)
# The net ALSO restores TESTING for cleanliness, but the gate does NOT ban it:
# ``config["TESTING"] = True`` is standard Flask test-client setup (~31 files),
# not a security downgrade, and the CSRF guard never reads it.
NET_CONFIG_RESTORE_KEYS = SECURITY_CONFIG_KEYS | {"TESTING"}
# os.environ keys the net restores (defense-in-depth; all current env mutations
# already go through monkeypatch, which auto-reverts).
SECURITY_ENV_KEYS = frozenset(
    {
        "BACKLINK_PUBLISHER_ALLOW_NETWORK",
        "OAUTHLIB_INSECURE_TRANSPORT",
        "BACKLINK_PUBLISHER_SESSION_COOKIE_SECURE",
    }
)

# Sentinel marking "key was absent" so restore can distinguish absent-vs-None.
_ABSENT = object()

# Lazily-built clean baseline of NET_CONFIG_RESTORE_KEYS, captured from a fresh
# create_app() (never the possibly-mutated module-level webui.app singleton).
# ``None`` until built — an explicit "not built" marker rather than dict
# truthiness (a populated baseline could in principle be falsy-shaped).
_CSRF_CONFIG_BASELINE: dict[str, object] | None = None


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


# ── B2 R5: function-scoped config isolation re-assert ────────────────────────
#
# The session-scoped fixture above sets env vars with bare ``os.environ``.
# Any test that ``pop``s the env var mid-session will cause subsequent tests
# to resolve ``_config_dir()`` to the operator's real ``~/.config/`` path.
# This function-scoped fixture re-asserts the isolation before every test,
# making order-dependent leaks impossible (B2 R5).
#
# Plan 2026-05-27 B2 R5 / R6 — see docs/brainstorms/2026-05-27-channel-
# binding-bug-sweep-requirements.md.

_B2_REAL_CONFIG = os.path.expanduser("~/.config/backlink-publisher")


@pytest.fixture(autouse=True)
def _reassert_config_isolation():
    """Re-assert config dir isolation lost by mid-session env pop.

    R5: If a test popped ``BACKLINK_PUBLISHER_CONFIG_DIR`` without
    restoring, create a fresh temp dir so the next test still runs
    isolated — order dependence is eliminated regardless of polluter.

    R6: After re-asserting, verify that the resolved config dir does
    NOT point to the operator's real ``~/.config/backlink-publisher``.
    If it does, loudly fail — this catches any test that touches real
    operator data and makes the test suite a hard safety net.
    """
    if "BACKLINK_PUBLISHER_CONFIG_DIR" not in os.environ:
        import tempfile
        os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = str(
            tempfile.mkdtemp(prefix="bp-config-reassert-")
        )
    if "BACKLINK_PUBLISHER_CACHE_DIR" not in os.environ:
        import tempfile
        os.environ["BACKLINK_PUBLISHER_CACHE_DIR"] = str(
            tempfile.mkdtemp(prefix="bp-cache-reassert-")
        )

    from backlink_publisher.config.loader import _resolve_config_dir
    resolved = str(_resolve_config_dir())
    if resolved == _B2_REAL_CONFIG or resolved.startswith(_B2_REAL_CONFIG + "/"):
        raise RuntimeError(
            f"B2 R6 isolation FAILED: _resolve_config_dir() returned real "
            f"operator config path {resolved!r}. A test fixture must have "
            f"failed to restore BACKLINK_PUBLISHER_CONFIG_DIR. Fix the "
            f"polluter, don't silence this check."
        )


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


@pytest.fixture(autouse=True)
def _mock_recheck_indexability_fetch(request, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default the recheck indexability axis to a zero-network ``unknown``.

    ``probe_liveness`` issues a second ``fetch_target`` call for every page it
    reads, to read ``PreflightFacts.noindex`` (the indexability axis). Without a
    default stub, every CLI/WebUI recheck test that reaches a readable page would
    drive a real ``getaddrinfo``/SSRF resolution. Patches the *consumer* reference
    (``recheck.probe.fetch_target``) only — NOT the producer module — so
    ``test_preflight_fetch.py`` and ``cli/canary_targets`` (which read the real
    ``fetch_target``/their own fetch) are unaffected. Tests that exercise the real
    indexability read inject their own ``fetch_fn`` (last-wins) or mark
    ``real_content_fetch``. Mirrors ``_mock_content_fetch``.
    """
    if request.node.get_closest_marker("real_content_fetch"):
        return
    from backlink_publisher.content._preflight_fetch import PreflightFacts

    monkeypatch.setattr(
        "backlink_publisher.recheck.probe.fetch_target",
        lambda url, *, timeout=None: PreflightFacts(reason="network_error"),
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


def _ensure_csrf_config_baseline() -> dict[str, object]:
    """Return the clean baseline for ``NET_CONFIG_RESTORE_KEYS``.

    Built once, lazily, from a *fresh* ``create_app(start_scheduler=False)`` —
    never a read of the already-imported (possibly-mutated) module-level
    ``webui.app`` singleton, which could enshrine a leaked ``False`` as the
    restore target. Lazy + cached so pure-CLI tests that never touch webui
    don't pay for a webui import. Always runs after the session-scope
    ``_isolate_user_dirs`` fixture (any function fixture does), so
    ``create_app()`` reads the isolated tmp config dir, not the operator's
    real ``~/.config``.
    """
    global _CSRF_CONFIG_BASELINE
    if _CSRF_CONFIG_BASELINE is None:
        from webui_app import create_app

        fresh = create_app(start_scheduler=False)
        baseline = {key: fresh.config.get(key, _ABSENT) for key in NET_CONFIG_RESTORE_KEYS}
        # Fail loud if the baseline itself is not CSRF-enabled — otherwise the
        # net would faithfully restore the guard to a disabled state.
        assert baseline.get("CSRF_ENABLED") is True, (
            "create_app() baseline does not have CSRF_ENABLED=True; the "
            "containment net cannot trust it as a restore target."
        )
        _CSRF_CONFIG_BASELINE = baseline
    return _CSRF_CONFIG_BASELINE


def _apply_csrf_config_baseline() -> None:
    """Reset ``webui.app.config`` security keys to baseline, if webui is loaded.

    No-op for tests that never imported ``webui`` (pure-CLI), honoring the
    "don't force a webui import on unrelated tests" requirement.
    """
    import sys

    webui = sys.modules.get("webui")
    if webui is None:
        return
    baseline = _ensure_csrf_config_baseline()
    for key, value in baseline.items():
        if value is _ABSENT:
            webui.app.config.pop(key, None)
        else:
            webui.app.config[key] = value


@pytest.fixture(autouse=True)
def _restore_global_state_net():
    """Containment net: restore security-relevant config + env around each test.

    Plan 2026-05-27-003 Unit 1. Defined *after* the three monkeypatch-based
    autouse fixtures above so its setup runs last among autouse fixtures (a
    clean baseline is established right before the test body). On teardown it
    restores the singleton config after the test's own non-autouse fixtures
    (e.g. a ``client`` fixture that left CSRF disabled) have already finished,
    so no disabled-guard state survives into the next test.

    Setup resets ``webui.app.config`` security keys to a clean baseline so a
    leak from a prior test cannot create an in-test guard-dead window. Teardown
    restores both config and the enumerated env keys (pop-or-reassign — never
    ``del os.environ``, per feedback_del_os_environ_poisons_later_tests).
    """
    # Setup: reset config to baseline (no in-test dead window) + snapshot env.
    _apply_csrf_config_baseline()
    env_prev = {key: os.environ.get(key, _ABSENT) for key in SECURITY_ENV_KEYS}
    try:
        yield
    finally:
        _apply_csrf_config_baseline()
        for key, prev in env_prev.items():
            if prev is _ABSENT:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev


@pytest.fixture
def disable_csrf():
    """Sanctioned, restoring way to disable the global CSRF guard for a test.

    Plan 2026-05-27-003 Unit 2. The AST gate exempts ``conftest.py``, so this
    is the single blessed mutation site — tests should use this instead of raw
    ``webui.app.config["CSRF_ENABLED"] = False``. Yields the app for convenience.
    """
    import webui

    prev = webui.app.config.get("CSRF_ENABLED", _ABSENT)
    webui.app.config["CSRF_ENABLED"] = False
    try:
        yield webui.app
    finally:
        if prev is _ABSENT:
            webui.app.config.pop("CSRF_ENABLED", None)
        else:
            webui.app.config["CSRF_ENABLED"] = prev


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
    _register("fake", FakeAdapter, dofollow=True)
    try:
        yield
    finally:
        if previous is None:
            __REGISTRY.pop("fake", None)
        else:
            __REGISTRY["fake"] = previous


# ── Layer 3: Credential tripwire (Plan 2026-05-27-005 Unit 7) ───────────────
#
# Session fixture that watches REAL_CONFIG_ROOT / REAL_CACHE_ROOT for unexpected
# writes to operator credential/state files during the test suite.
#
# ON CI: REAL_CONFIG_ROOT typically does not exist (sandboxed HOME), so the
# snapshot is empty and the fixture is a cheap no-op.  Phase 2 is dev-only.
#
# Security contract (see plan Risks & Dependencies):
#  - Failure message: relative filename only, boolean "changed" — no SHA-256,
#    no file contents, no absolute real-home path.
#  - Setup reads wrap exceptions so a mid-setup read failure cannot propagate
#    secret bytes into a visible traceback frame.
#  - events.db snapshot: tmpdir cleaned in finally; copies 0o600; not a fixed path.
#  - Failure uses pytest.fail(), not bare assert (avoids assertion-rewrite
#    leaking digest bytes into pytest output).
#
# NOTE: helper functions (snapshot_protected_files, check_protected_files) are
# intentionally importable from conftest so meta-tests can call them with a
# controlled fake-root without touching real operator files.

import fnmatch as _fnmatch
import hashlib as _hashlib
import sqlite3 as _sqlite3
import subprocess as _subprocess


# ---- Exclusion helpers -------------------------------------------------------

_CHROME_PROFILE_EXCLUDES: tuple[str, ...] = (
    "real-chrome-profile",
    "browser-profile",
)
_WAL_EXCLUDE_SUFFIXES: tuple[str, ...] = ("-shm", "-journal")


def _tw_relpath(path: Path, root: Path) -> str:
    """Return POSIX relpath of path relative to root, falling back to basename."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _tw_is_excluded(path: Path, root: Path) -> bool:
    """True if path should be excluded from byte-hash (chrome profiles, WAL sidecars)."""
    rel = _tw_relpath(path, root)
    for prefix in _CHROME_PROFILE_EXCLUDES:
        if rel == prefix or rel.startswith(prefix + "/"):
            return True
    for suffix in _WAL_EXCLUDE_SUFFIXES:
        if path.name.endswith(suffix):
            return True
    return False


def _tw_is_protected(filename: str) -> bool:
    """True if filename matches any glob from PROTECTED_GLOBS (Unit 6)."""
    # Import at call time — test-time evaluation (invert-drift lesson).
    from test_protected_set_coverage import PROTECTED_GLOBS  # noqa: PLC0415
    return any(_fnmatch.fnmatch(filename, g) for g in PROTECTED_GLOBS)


# ---- Digest helpers ----------------------------------------------------------

def _tw_sha256_file(path: Path) -> "str | None":
    """SHA-256 hex digest of path bytes, or None on any read failure."""
    try:
        data = path.read_bytes()
        return _hashlib.sha256(data).hexdigest()
    except OSError:
        return None


def _tw_events_db_fingerprint(db_path: Path) -> "str | None":
    """Logical fingerprint for events.db via WAL snapshot-copy.

    Returns sorted-rows SHA-256 across all user tables, or None if the db is
    absent or cannot be read.  WAL-safe: copies db + -wal before opening
    (mirrors audit/readers.py:_read_articles_from_snapshot).

    Security: tmpdir is cleaned in finally; never a fixed path; copies 0o600.
    """
    if not db_path.exists():
        return None
    wal_path = db_path.with_name(db_path.name + "-wal")
    tmp_dir = Path(tempfile.mkdtemp(prefix="bp-tripwire-"))
    try:
        copy_db = tmp_dir / "events.db"
        try:
            shutil.copy2(db_path, copy_db)
            copy_db.chmod(0o600)
            if wal_path.exists():
                copy_wal = tmp_dir / "events.db-wal"
                shutil.copy2(wal_path, copy_wal)
                copy_wal.chmod(0o600)
            conn = _sqlite3.connect(f"file:{copy_db}?mode=ro", uri=True)
            try:
                tables = sorted(
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                    if not row[0].startswith("sqlite_")
                )
                rows_repr: list[str] = []
                for tbl in tables:
                    try:
                        rows = conn.execute(f"SELECT * FROM {tbl}").fetchall()
                        rows_repr.extend(sorted(
                            "(" + ",".join(
                                v.hex() if isinstance(v, bytes) else repr(v)
                                for v in r
                            ) + ")"
                            for r in rows
                        ))
                    except _sqlite3.Error:
                        pass
                combined = "\n".join(rows_repr)
                return _hashlib.sha256(combined.encode()).hexdigest()
            finally:
                conn.close()
        except (OSError, _sqlite3.Error):
            return None
    finally:
        shutil.rmtree(str(tmp_dir), ignore_errors=True)


def _tw_chrome_profile_size(config_root: Path) -> int:
    """Total byte-size of the real-chrome-profile subtree, or 0 if absent."""
    profile_dir = config_root / "real-chrome-profile"
    if not profile_dir.exists():
        return 0
    total = 0
    try:
        for p in profile_dir.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


# ---- Public API (importable by meta-tests) -----------------------------------

def snapshot_protected_files(
    config_root: Path,
    cache_root: Path,
) -> "dict[str, str | None]":
    """Snapshot all protected credential files under config_root and cache_root.

    Returns ``{relative_name: digest_or_None}`` for every file matching
    PROTECTED_GLOBS.  A None digest means the file existed but could not be
    read.  The special key ``"__chrome_profile_size__"`` tracks the coarse
    presence/size tripwire over the real-chrome-profile subtree.

    Importable by tests/test_credential_tripwire.py so meta-tests can drive
    the logic with a controlled fake-root.
    """
    state: dict[str, str | None] = {}
    for root in (config_root, cache_root):
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if _tw_is_excluded(p, root):
                continue
            rel = _tw_relpath(p, root)
            if not _tw_is_protected(str(p.relative_to(root))):
                continue
            if p.name == "events.db":
                state[rel] = _tw_events_db_fingerprint(p)
            else:
                state[rel] = _tw_sha256_file(p)
    # Coarse presence/size tripwire for real-chrome-profile.
    state["__chrome_profile_size__"] = str(_tw_chrome_profile_size(config_root))
    return state


def check_protected_files(
    initial: "dict[str, str | None]",
    config_root: Path,
    cache_root: Path,
) -> list[str]:
    """Return relative names of protected files that changed since initial snapshot.

    Detects: modifications, new protected files added, files that disappeared.
    Also detects real-chrome-profile subtree growth (coarse presence/size check).

    Importable by tests/test_credential_tripwire.py.
    """
    current = snapshot_protected_files(config_root, cache_root)
    changed: list[str] = []
    for key in sorted(set(initial) | set(current)):
        if key == "__chrome_profile_size__":
            prev_size = int(initial.get(key) or "0")
            curr_size = int(current.get(key) or "0")
            if curr_size > prev_size:
                changed.append("real-chrome-profile/ (grew during suite)")
        elif initial.get(key) != current.get(key):
            changed.append(key)
    return changed


def _tw_is_operator_live() -> bool:
    """True if a running WebUI or Chrome operator process is detected.

    Best-effort: uses pgrep if available; fails silently (returns False).
    Result is advisory — a True result means benign writes may be in flight;
    a False result means the suite is the most likely writer of any change.
    """
    for pattern in ("webui.py", "backlink-publisher webui"):
        try:
            result = _subprocess.run(
                ["pgrep", "-f", pattern],
                capture_output=True,
                timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                return True
        except (OSError, _subprocess.TimeoutExpired):
            pass
    return False


# ---- Fixture -----------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _credential_tripwire():
    """Layer 3: session tripwire over real operator credential files.

    Plan 2026-05-27-005 Unit 7 (Phase 2 — dev-only backstop).

    Harmless on CI where REAL_CONFIG_ROOT does not exist (sandboxed HOME
    means no real credentials present — the snapshot is empty and teardown
    is a no-op).

    On a developer machine, hashes protected credential files at session
    start and re-checks at session end.  If any file changed while no
    operator process was live, fails with a redacted message naming only the
    relative filenames (no SHA-256, no contents, no absolute home path).
    """
    # Guard: REAL_CONFIG_ROOT must not equal the sandbox config dir.
    # If they match, the tripwire would silently watch the sandbox — a bug.
    try:
        from backlink_publisher.config.loader import _config_dir as _get_sandbox
        _sandbox_cfg = _get_sandbox()
    except (ImportError, AttributeError, RuntimeError):
        # ImportError: loader not importable; AttributeError: _config_dir renamed;
        # RuntimeError: fail-closed resolver fired (means sandbox is active — safe).
        _sandbox_cfg = None

    if _sandbox_cfg is not None and REAL_CONFIG_ROOT == _sandbox_cfg:
        pytest.fail(
            "Credential tripwire bug: REAL_CONFIG_ROOT equals the sandbox "
            "config dir — the HOME redirect did not capture real roots before "
            "running. Check conftest.py ordering."
        )

    initial = snapshot_protected_files(REAL_CONFIG_ROOT, REAL_CACHE_ROOT)

    yield

    changed = check_protected_files(initial, REAL_CONFIG_ROOT, REAL_CACHE_ROOT)
    if not changed:
        return

    operator_live = _tw_is_operator_live()
    # Redaction: emit relative filenames only — no SHA-256, no absolute paths,
    # no file contents.
    changed_names = ", ".join(changed)

    if operator_live:
        import warnings as _warnings
        _warnings.warn(
            f"[tripwire] {len(changed)} protected file(s) changed during the "
            f"test suite, but an operator process is live — likely benign. "
            f"Relative names: {changed_names}",
            UserWarning,
            stacklevel=1,
        )
    else:
        pytest.fail(
            f"[tripwire] {len(changed)} protected credential file(s) changed "
            f"during the test suite with no live operator process detected. "
            f"The test suite is the likely writer.\n"
            f"Changed (relative names only): {changed_names}\n"
            f"Fix: ensure the offending test uses the sandboxed _config_dir() "
            f"(via BACKLINK_PUBLISHER_CONFIG_DIR) rather than a hardcoded or "
            f"Path.home()-derived path."
        )
