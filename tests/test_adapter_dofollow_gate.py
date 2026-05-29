"""Unit 5: CI gate asserting every registered platform has a valid
dofollow declaration and rejection-map invariants hold.

Plan 2026-05-20-009 §Unit 5.

This is the structural replacement for the institutional rule
``feedback_grep_dofollow_map_before_shipping_adapter`` — what used to
be tribal knowledge ("grep _DOFOLLOW_BY_CHANNEL before merging") is now
mechanically enforced at PR-CI time.

Mirrors ``tests/test_no_monolith_regrowth.py`` structure: per-entry
parametrize → schema test → value test, plus a synthetic red-path test
that proves the gate fires when violated.
"""

from __future__ import annotations

from typing import Any

import pytest

from backlink_publisher._util.errors import RegistryError
from backlink_publisher.publishing import adapters  # noqa: F401 — import side effect: registers all 7 production platforms
from backlink_publisher.publishing.registry import (
    Publisher,
    _REGISTRY,
    _REJECTED_PLATFORMS,
    dofollow_rationale,
    dofollow_status,
    referral_value,
    register,
    registered_platforms,
)
from backlink_publisher.publishing.adapters.base import AdapterResult


_RATIONALE_MIN_CHARS = 80


@pytest.mark.parametrize("platform", registered_platforms())
class TestEveryPlatformHasValidDofollow:
    def test_dofollow_status_is_declared(self, platform: str) -> None:
        # R9: every entry in registered_platforms() must have an
        # explicit dofollow_status — not None. Missing dofollow= now
        # raises TypeError at register() time (U5 required-flip), so
        # any platform in _REGISTRY without a status entry indicates
        # a code-author bug to investigate.
        status = dofollow_status(platform)
        assert status is not None, (
            f"platform {platform!r} is registered but has no dofollow_status. "
            f"Add dofollow=True / False / 'uncertain' to its register() call "
            f"in publishing/adapters/__init__.py."
        )

    def test_non_true_dofollow_carries_rationale(self, platform: str) -> None:
        # R3 / R10: dofollow=False or 'uncertain' must carry a rationale
        # of >= 80 chars stripped. Length-only — content is reviewer
        # concern (mirrors monolith_budget.toml rationale discipline).
        status = dofollow_status(platform)
        if status in (False, "uncertain"):
            rationale = dofollow_rationale(platform)
            assert rationale is not None, (
                f"platform {platform!r} declares dofollow={status!r} but has "
                f"no rationale. Add rationale='...' (>={_RATIONALE_MIN_CHARS} "
                f"chars stripped) to its register() call documenting why a "
                f"non-dofollow platform is shipping."
            )
            assert len(rationale.strip()) >= _RATIONALE_MIN_CHARS, (
                f"platform {platform!r} has dofollow={status!r} rationale "
                f"of {len(rationale.strip())} stripped chars, need "
                f">={_RATIONALE_MIN_CHARS}. Expand the rationale string."
            )

    def test_non_true_dofollow_carries_referral_value(self, platform: str) -> None:
        # Plan 2026-05-25-001 R1/R13: dofollow=False or 'uncertain' must
        # declare a referral_value ('high'/'low') — the ship/reject
        # decision input. The register() gate enforces this at import;
        # this parametrized test is the standing assertion across every
        # production platform.
        status = dofollow_status(platform)
        if status in (False, "uncertain"):
            value = referral_value(platform)
            assert value in ("high", "low"), (
                f"platform {platform!r} declares dofollow={status!r} but has "
                f"referral_value={value!r}. Add referral_value='high'|'low' "
                f"to its register() call — it is the ship/reject decision "
                f"input for nofollow platforms."
            )


class TestRegistryAndRejectedDisjoint:
    def test_no_overlap_between_registry_and_rejected_platforms(self) -> None:
        # R13: _REGISTRY ∩ _REJECTED_PLATFORMS is strictly disjoint.
        # The un-rejection path is by deletion from _REJECTED_PLATFORMS
        # in the same PR as the new register() call (no override kwarg),
        # so no exceptions to this invariant exist.
        overlap = set(_REGISTRY.keys()) & set(_REJECTED_PLATFORMS.keys())
        assert not overlap, (
            f"platforms appear in both _REGISTRY and _REJECTED_PLATFORMS: "
            f"{sorted(overlap)}. Un-rejection requires deleting the name "
            f"from _REJECTED_PLATFORMS in the same PR as the new register() "
            f"call (no override kwarg). If you genuinely want to re-attempt "
            f"one of these, delete its rejection entry."
        )


# ---------------------------------------------------------------------------
# Synthetic red-path tests proving the gate fires when violated. Mirrors
# tests/test_no_monolith_regrowth.py:212-228 — install a violation, assert
# the gate raises, then restore. These prove the gate is not silently
# passing (a false-negative gate is worse than no gate).
# ---------------------------------------------------------------------------


class _FakeAdapter(Publisher):
    """Minimal Publisher stub for synthetic red-path tests."""

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Any,
    ) -> AdapterResult:  # pragma: no cover - never invoked
        raise NotImplementedError


@pytest.fixture
def _isolate_registry():
    """Snapshot+restore all registry dicts so red-path violations
    don't bleed into other tests in the run.
    
    Note: Manifest fields (ui, bind, policy, visibility) and dofollow/rationale/referral_value
    are now stored as fields in RegistryEntry within _REGISTRY, so only _REGISTRY and 
    _REJECTED_PLATFORMS need to be snapshotted.
    """
    reg_snap = dict(_REGISTRY)
    rej_snap = dict(_REJECTED_PLATFORMS)
    try:
        yield
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(reg_snap)
        _REJECTED_PLATFORMS.clear()
        _REJECTED_PLATFORMS.update(rej_snap)


class TestSyntheticRedPaths:
    def test_missing_dofollow_kwarg_raises_at_register_time(self) -> None:
        # The gate's structural defense: missing dofollow= is a Python
        # TypeError, not a silent pass-through. This is the U5 required-
        # flip — what used to be optional during U2 is now required.
        with pytest.raises(TypeError, match="dofollow"):
            register("redpath_no_kwarg", _FakeAdapter)  # type: ignore[call-arg]

    def test_dofollow_false_with_no_rationale_raises(self) -> None:
        with pytest.raises(RegistryError, match="rationale"):
            register("redpath_false_no_rat", _FakeAdapter, dofollow=False)

    def test_dofollow_uncertain_with_short_rationale_raises(self) -> None:
        with pytest.raises(RegistryError, match="rationale"):
            register(
                "redpath_uncertain_short",
                _FakeAdapter,
                dofollow="uncertain",
                rationale="too short",
            )

    def test_dofollow_false_with_valid_rationale_but_no_referral_value_raises(
        self,
    ) -> None:
        # Plan 2026-05-25-001: rationale satisfied but referral_value
        # unset → the silent-gap gate fires. Mirrors the rationale gate.
        with pytest.raises(RegistryError, match="referral_value"):
            register(
                "redpath_false_no_referral",
                _FakeAdapter,
                dofollow=False,
                rationale=(
                    "Valid-length rationale that satisfies the >=80-char "
                    "rationale gate so the referral_value gate is what fires "
                    "in this red-path test, not the rationale gate."
                ),
            )

    def test_dofollow_uncertain_with_referral_value_succeeds(
        self, _isolate_registry
    ) -> None:
        # Happy path: uncertain + valid rationale + referral_value → ok.
        register(
            "redpath_uncertain_ok",
            _FakeAdapter,
            dofollow="uncertain",
            rationale=(
                "Valid-length rationale satisfying the >=80-char gate so we "
                "can exercise the referral_value happy path on an uncertain "
                "platform registration end to end."
            ),
            referral_value="low",
        )
        assert dofollow_status("redpath_uncertain_ok") == "uncertain"
        assert referral_value("redpath_uncertain_ok") == "low"

    def test_register_rejected_name_raises_with_deletion_instruction(
        self, _isolate_registry
    ) -> None:
        # ``_REJECTED_PLATFORMS`` is empty after Phase 1 (devto/mastodon
        # shipped as chrome-publish channels; wordpresscom was un-rejected
        # and re-registered — kept dofollow="uncertain" by the 2026-05-26
        # audit). Use a synthetic rejected key to exercise the gate rather
        # than depending on any live rejection entry.
        _REJECTED_PLATFORMS["_audit_reject"] = (
            "synthetic rejection for the gate test — at least eighty chars of "
            "rationale padding so this entry is shape-valid xxxxxxxxxxxxxxxxxxx"
        )
        with pytest.raises(RegistryError) as exc:
            register("_audit_reject", _FakeAdapter, dofollow=True)
        message = str(exc.value)
        assert "previously rejected" in message
        assert "delete this entry" in message

    def test_disjoint_invariant_fires_on_synthetic_overlap(
        self, _isolate_registry
    ) -> None:
        # Force the overlap by direct dict mutation (bypassing register()
        # since register() would raise RegistryError first). Then assert
        # the disjoint-keys gate test fails.
        _REJECTED_PLATFORMS["blogger"] = "synthetic violation " + "x" * 80
        overlap = set(_REGISTRY.keys()) & set(_REJECTED_PLATFORMS.keys())
        assert "blogger" in overlap, "synthetic violation failed to install"

    def test_register_succeeds_after_un_rejection_by_deletion(
        self, _isolate_registry
    ) -> None:
        # Happy-path R12: delete entry, then register normally. Uses a
        # synthetic rejected key — wordpresscom is no longer a live
        # rejection entry after Phase 1 (un-rejected + re-registered as
        # dofollow="uncertain" by the 2026-05-26 audit).
        _REJECTED_PLATFORMS["_audit_reject"] = (
            "synthetic rejection for the un-rejection-by-deletion gate test — "
            "padded to satisfy the >=80-char rationale shape xxxxxxxxxxxxxxxxxx"
        )
        _REJECTED_PLATFORMS.pop("_audit_reject")
        register(
            "_audit_reject",
            _FakeAdapter,
            dofollow=False,
            rationale=(
                "Synthetic re-attempt path for the U5 gate test — "
                "exercises the un-rejection-by-deletion contract that "
                "replaces R12's deferred-from-brainstorm override kwarg."
            ),
            referral_value="low",  # nofollow now also requires referral_value (Plan 2026-05-25-001)
        )
        assert dofollow_status("_audit_reject") is False
        assert "_audit_reject" not in _REJECTED_PLATFORMS


class TestLivejournalDofollow:
    """Pin the livejournal canary verdict so it cannot silently regress to 'uncertain'.

    Canary date: 2026-05-29. Verdict: nofollow (LJ platform-wide rel=nofollow on
    external body links). Registered dofollow=False, referral_value="high".
    """

    def test_livejournal_dofollow_is_false(self) -> None:
        assert dofollow_status("livejournal") is False, (
            "livejournal must be dofollow=False after 2026-05-29 canary; "
            "do not revert to 'uncertain' without a new pipeline canary"
        )

    def test_livejournal_not_uncertain(self) -> None:
        assert dofollow_status("livejournal") != "uncertain"

    def test_livejournal_referral_value_high(self) -> None:
        assert referral_value("livejournal") == "high"

    def test_livejournal_carries_rationale(self) -> None:
        rat = dofollow_rationale("livejournal")
        assert rat is not None and len(rat) >= 80
