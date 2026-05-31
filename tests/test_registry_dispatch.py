"""Unit tests for publishing/_registry_dispatch.py — dispatch() function.

dispatch() is the critical publish-path entry: walks the registered adapter
chain, handles dry-run mode, propagates AuthExpiredError, falls through on
DependencyError, raises on unknown platform.

Each test class registers its own stub adapter under a unique platform slug
so tests are isolated and do not rely on patching conftest.FakeAdapter.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator

import pytest

import backlink_publisher.publishing.adapters as _adapters_import  # noqa: F401
from backlink_publisher.config import Config
from backlink_publisher._util.errors import (
    AuthExpiredError,
    DependencyError,
    ExternalServiceError,
)
from backlink_publisher.publishing._registry_dispatch import dispatch
from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.publishing.registry import (
    Publisher,
    _REGISTRY,
    register,
)


# ── Adapter stub builders ────────────────────────────────────────────────────


def _make_stub(
    *,
    status: str = "drafted",
    available: bool = True,
    side_effect: BaseException | None = None,
) -> type[Publisher]:
    """Return a new Publisher subclass with the requested behaviour."""

    _avail = available
    _effect = side_effect
    _status = status

    class _Stub(Publisher):
        @classmethod
        def available(cls, config: Any) -> bool:
            return _avail

        def publish(self, payload: dict, mode: str, config: Any) -> AdapterResult:
            if _effect is not None:
                raise _effect
            return AdapterResult(
                status=_status,
                adapter="stub",
                platform=payload.get("platform", ""),
                draft_url="https://stub.example/1",
            )

    _Stub.__name__ = f"_Stub_{id(_Stub)}"
    return _Stub


@contextmanager
def _registered(slug: str, cls: type[Publisher]) -> Generator[None, None, None]:
    """Register *cls* under *slug* for the duration of the with-block."""
    prev = _REGISTRY.get(slug)
    register(slug, cls, dofollow=True)
    try:
        yield
    finally:
        if prev is None:
            _REGISTRY.pop(slug, None)
        else:
            _REGISTRY[slug] = prev


def _cfg() -> Config:
    return Config()


def _payload(platform: str, **kw: Any) -> dict:
    return {"platform": platform, "title": "T", "content_markdown": "body", **kw}


# ── dry_run mode ──────────────────────────────────────────────────────────────


class TestDryRun:
    """dry_run=True must return a sentinel AdapterResult without calling publish."""

    def test_dry_run_returns_adapter_result(self) -> None:
        with _registered("drt1", _make_stub()):
            result = dispatch(_payload("drt1"), mode="api", config=_cfg(), dry_run=True)
        assert isinstance(result, AdapterResult)

    def test_dry_run_status_is_draft(self) -> None:
        with _registered("drt2", _make_stub(side_effect=AssertionError("publish called"))):
            # Adapter raises if publish() is called — proves dry_run skips it.
            result = dispatch(_payload("drt2"), mode="api", config=_cfg(), dry_run=True)
        assert result.status == "draft"

    def test_dry_run_flag_set(self) -> None:
        with _registered("drt3", _make_stub()):
            result = dispatch(_payload("drt3"), mode="api", config=_cfg(), dry_run=True)
        assert result._dry_run is True

    def test_dry_run_platform_in_result(self) -> None:
        with _registered("drt4", _make_stub()):
            result = dispatch(_payload("drt4"), mode="api", config=_cfg(), dry_run=True)
        assert result.platform == "drt4"

    def test_dry_run_command_mentions_mode(self) -> None:
        with _registered("drt5", _make_stub()):
            result = dispatch(_payload("drt5"), mode="chrome", config=_cfg(), dry_run=True)
        assert "chrome" in (result._command or "")

    def test_dry_run_works_for_unknown_platform(self) -> None:
        # dry_run short-circuits before the registry lookup.
        result = dispatch(
            _payload("no_such_platform_xyz"), mode="api", config=_cfg(), dry_run=True
        )
        assert result._dry_run is True


# ── Unknown platform ──────────────────────────────────────────────────────────


class TestUnknownPlatform:
    def test_raises_external_service_error(self) -> None:
        with pytest.raises(ExternalServiceError, match="unsupported platform"):
            dispatch(_payload("totally_unknown_xyz"), mode="api", config=_cfg())

    def test_empty_platform_string_raises(self) -> None:
        with pytest.raises(ExternalServiceError):
            dispatch({"platform": "", "title": "T"}, mode="api", config=_cfg())

    def test_missing_platform_key_raises(self) -> None:
        with pytest.raises(ExternalServiceError):
            dispatch({"title": "T"}, mode="api", config=_cfg())


# ── Successful dispatch ───────────────────────────────────────────────────────


class TestSuccessfulDispatch:
    def test_returns_adapter_result(self) -> None:
        with _registered("ok1", _make_stub(status="drafted")):
            result = dispatch(_payload("ok1"), mode="api", config=_cfg())
        assert isinstance(result, AdapterResult)

    def test_result_status_matches_stub(self) -> None:
        with _registered("ok2", _make_stub(status="drafted")):
            result = dispatch(_payload("ok2"), mode="api", config=_cfg())
        assert result.status == "drafted"

    def test_mode_is_passed_to_publish(self) -> None:
        received_modes: list[str] = []

        class _ModeCapture(Publisher):
            @classmethod
            def available(cls, config: Any) -> bool:
                return True

            def publish(self, payload: dict, mode: str, config: Any) -> AdapterResult:
                received_modes.append(mode)
                return AdapterResult(status="drafted", adapter="x", platform="mcap")

        with _registered("mcap", _ModeCapture):
            dispatch(_payload("mcap"), mode="chrome", config=_cfg())
        assert received_modes == ["chrome"]


# ── DependencyError fall-through ──────────────────────────────────────────────


class TestDependencyErrorFallthrough:
    """A DependencyError from one chain entry falls through to the next."""

    def test_single_entry_dep_error_re_raised(self) -> None:
        stub = _make_stub(side_effect=DependencyError("no creds"))
        with _registered("dep1", stub):
            with pytest.raises(DependencyError, match="no creds"):
                dispatch(_payload("dep1"), mode="api", config=_cfg())

    def test_dep_error_falls_through_to_next_entry(self) -> None:
        """Chain: [raises DependencyError, succeeds] → overall success."""
        failing = _make_stub(side_effect=DependencyError("first fails"))
        succeeding = _make_stub(status="drafted")

        prev = _REGISTRY.get("dep2")
        register("dep2", failing, dofollow=True)
        register("dep2", succeeding, dofollow=True)
        try:
            result = dispatch(_payload("dep2"), mode="api", config=_cfg())
            assert result.status == "drafted"
        finally:
            if prev is None:
                _REGISTRY.pop("dep2", None)
            else:
                _REGISTRY["dep2"] = prev

    def test_unavailable_entry_skipped_then_dep_error(self) -> None:
        stub = _make_stub(available=False)
        with _registered("dep3", stub):
            with pytest.raises(DependencyError, match="available.*False"):
                dispatch(_payload("dep3"), mode="api", config=_cfg())


# ── AuthExpiredError propagation ──────────────────────────────────────────────


class TestAuthExpiredError:
    """AuthExpiredError must propagate immediately — not fall through."""

    def test_auth_expired_error_propagates(self) -> None:
        auth_err = AuthExpiredError(channel="velog")
        stub = _make_stub(side_effect=auth_err)
        with _registered("ae1", stub):
            with pytest.raises(AuthExpiredError):
                dispatch(_payload("ae1"), mode="api", config=_cfg())

    def test_auth_expired_not_swallowed_as_dep_error(self) -> None:
        """AuthExpiredError IS-A DependencyError; dispatch must NOT swallow it."""
        auth_err = AuthExpiredError(channel="velog")
        stub = _make_stub(side_effect=auth_err)
        with _registered("ae2", stub):
            caught_auth: list[AuthExpiredError] = []
            caught_dep: list[DependencyError] = []
            try:
                dispatch(_payload("ae2"), mode="api", config=_cfg())
            except AuthExpiredError as e:
                caught_auth.append(e)
            except DependencyError as e:
                caught_dep.append(e)
            assert caught_auth and not caught_dep, (
                "AuthExpiredError was swallowed as DependencyError"
            )


# ── ExternalServiceError propagation ─────────────────────────────────────────


class TestExternalServiceError:
    def test_propagates_without_fallthrough(self) -> None:
        stub = _make_stub(side_effect=ExternalServiceError("api down"))
        with _registered("ese1", stub):
            with pytest.raises(ExternalServiceError, match="api down"):
                dispatch(_payload("ese1"), mode="api", config=_cfg())


# ── No-available-adapter fallback ────────────────────────────────────────────


class TestNoAvailableAdapter:
    def test_all_unavailable_raises_dep_error(self) -> None:
        stub = _make_stub(available=False)
        with _registered("naa1", stub):
            with pytest.raises(DependencyError, match="available.*False"):
                dispatch(_payload("naa1"), mode="api", config=_cfg())
