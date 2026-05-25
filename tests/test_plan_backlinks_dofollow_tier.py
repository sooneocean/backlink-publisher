"""Plan-backlinks dofollow-tier observability marking (Plan 2026-05-25-001 Unit 2).

The enrichment loop in ``cli/plan_backlinks/core.py`` injects
``dofollow_tier`` / ``referral_value`` / ``tier_pending`` into every
payload's ``metadata`` via ``dofollow_tier_metadata(platform)``. These
tests pin the mapping (the behavior-bearing part) against the registry's
single source of truth, plus the observability-only invariant that the
mark adds metadata without changing platform allocation.
"""

from __future__ import annotations

import pytest

from backlink_publisher.cli.plan_backlinks._payload import dofollow_tier_metadata
from backlink_publisher.publishing import adapters  # noqa: F401 — registers production platforms
from backlink_publisher.publishing.registry import (
    _DOFOLLOW_BY_PLATFORM,
    _RATIONALE_BY_PLATFORM,
    _REFERRAL_VALUE_BY_PLATFORM,
    _REGISTRY,
    register,
)
from backlink_publisher.publishing.registry import Publisher
from backlink_publisher.publishing.adapters.base import AdapterResult


class _FakeAdapter(Publisher):
    def publish(self, payload, mode, config) -> AdapterResult:  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def _isolate_registry():
    reg = {k: list(v) for k, v in _REGISTRY.items()}
    df = dict(_DOFOLLOW_BY_PLATFORM)
    rat = dict(_RATIONALE_BY_PLATFORM)
    ref = dict(_REFERRAL_VALUE_BY_PLATFORM)
    try:
        yield
    finally:
        for d, snap in (
            (_REGISTRY, reg),
            (_DOFOLLOW_BY_PLATFORM, df),
            (_RATIONALE_BY_PLATFORM, rat),
            (_REFERRAL_VALUE_BY_PLATFORM, ref),
        ):
            d.clear()
            d.update(snap)


def test_dofollow_platform_marked_dofollow() -> None:
    # blogger ships dofollow=True in production.
    meta = dofollow_tier_metadata("blogger")
    assert meta["dofollow_tier"] == "dofollow"
    assert meta["referral_value"] is None
    assert "tier_pending" not in meta


def test_nofollow_high_platform_marked_nofollow_signal() -> None:
    # devto ships dofollow=False, referral_value="high" in production.
    meta = dofollow_tier_metadata("devto")
    assert meta["dofollow_tier"] == "nofollow-signal"
    assert meta["referral_value"] == "high"
    assert "tier_pending" not in meta


def test_uncertain_platform_flagged_tier_pending(_isolate_registry) -> None:
    register(
        "tier_probe",
        _FakeAdapter,
        dofollow="uncertain",
        rationale=(
            "Uncertain platform registered to exercise the tier_pending "
            "marking path for a not-yet-measured dofollow status end to end."
        ),
        referral_value="low",
    )
    meta = dofollow_tier_metadata("tier_probe")
    assert meta["dofollow_tier"] == "nofollow-signal"
    assert meta["referral_value"] == "low"
    assert meta["tier_pending"] is True


def test_unregistered_platform_defaults_to_nofollow_signal() -> None:
    # Unknown platform → status None → nofollow-signal, no referral grade.
    meta = dofollow_tier_metadata("does_not_exist")
    assert meta["dofollow_tier"] == "nofollow-signal"
    assert meta["referral_value"] is None


def test_marking_is_pure_does_not_mutate_registry() -> None:
    # Observability-only: calling the mapper must not register or alter
    # any platform state.
    before = dict(_DOFOLLOW_BY_PLATFORM)
    dofollow_tier_metadata("blogger")
    dofollow_tier_metadata("devto")
    assert dict(_DOFOLLOW_BY_PLATFORM) == before
