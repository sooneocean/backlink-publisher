"""Tests for channel_discovery.decided — decided-store query API (R8).

Verifies:
- is_decided returns True for known NO-GO / removed / deferred / hold platforms
- is_decided returns False for genuinely undecided platforms
- get_verdict returns the full record with verdict + reason
- Registry-registered platforms are decided (verdict="registered")
- undecided_only filters correctly
- all_decided_platforms includes both registry + store entries
"""

from __future__ import annotations

import pytest
from backlink_publisher.channel_discovery.decided import (
    all_decided_platforms,
    get_verdict,
    is_decided,
    undecided_only,
)


class TestIsDecided:
    """is_decided() covers registry + decided-store."""

    def test_nogo_platform_is_decided(self):
        assert is_decided("bloglovin") is True

    def test_removed_platform_is_decided(self):
        assert is_decided("jianshu") is True
        assert is_decided("csdn") is True
        assert is_decided("juejin") is True
        assert is_decided("note") is True

    def test_hold_is_decided(self):
        assert is_decided("jkforum") is True

    def test_conditional_deferred_is_decided(self):
        assert is_decided("justpaste.it") is True
        assert is_decided("teletype.in") is True

    def test_deferred_is_decided(self):
        assert is_decided("readthedocs") is True
        assert is_decided("notesio") is True

    def test_registered_platform_is_decided(self):
        # Registered platforms (from registry) are always decided.
        assert is_decided("blogger") is True
        assert is_decided("medium") is True
        assert is_decided("hackmd") is True
        assert is_decided("mataroa") is True
        assert is_decided("gitlabpages") is True

    def test_unknown_platform_is_not_decided(self):
        assert is_decided("totally_new_platform_xyz_2026") is False
        assert is_decided("") is False


class TestGetVerdict:
    """get_verdict() returns full record or None."""

    def test_nogo_returns_record(self):
        rec = get_verdict("bloglovin")
        assert rec is not None
        assert rec["verdict"] == "no-go"
        assert "reason" in rec
        assert "date" in rec

    def test_registered_returns_synthetic_record(self):
        rec = get_verdict("blogger")
        assert rec is not None
        assert rec["verdict"] == "registered"
        assert rec["source"] == "registry"

    def test_removed_returns_record(self):
        rec = get_verdict("jianshu")
        assert rec is not None
        assert rec["verdict"] == "removed"

    def test_hold_returns_record(self):
        rec = get_verdict("jkforum")
        assert rec is not None
        assert rec["verdict"] == "hold"

    def test_unknown_returns_none(self):
        assert get_verdict("totally_new_platform_xyz_2026") is None

    def test_discovery_batch_nogo_covered(self):
        for platform in ("gitbook", "bearblog", "svbtle", "scrapbox", "paste.ee", "weebly"):
            rec = get_verdict(platform)
            assert rec is not None, f"{platform} not in decided-store"
            assert rec["verdict"] == "no-go"


class TestUndecidedOnly:
    """undecided_only() filters out already-decided platforms."""

    def test_filters_known_nogo(self):
        result = undecided_only(["bloglovin", "new_platform_abc"])
        assert "bloglovin" not in result
        assert "new_platform_abc" in result

    def test_filters_registered(self):
        result = undecided_only(["blogger", "new_platform_abc"])
        assert "blogger" not in result
        assert "new_platform_abc" in result

    def test_empty_input(self):
        assert undecided_only([]) == []

    def test_all_undecided(self):
        inputs = ["brand_new_1", "brand_new_2"]
        assert undecided_only(inputs) == inputs

    def test_all_decided(self):
        inputs = ["bloglovin", "jianshu", "blogger"]
        assert undecided_only(inputs) == []


class TestAllDecidedPlatforms:
    """all_decided_platforms() includes both registry + store."""

    def test_includes_registry_platforms(self):
        all_p = all_decided_platforms()
        assert "blogger" in all_p
        assert "medium" in all_p
        assert "hackmd" in all_p

    def test_includes_store_entries(self):
        all_p = all_decided_platforms()
        assert "bloglovin" in all_p
        assert "jianshu" in all_p
        assert "jkforum" in all_p

    def test_returns_set(self):
        assert isinstance(all_decided_platforms(), set)

    def test_minimum_coverage(self):
        # At least registry + 20 store entries
        all_p = all_decided_platforms()
        assert len(all_p) >= 30
