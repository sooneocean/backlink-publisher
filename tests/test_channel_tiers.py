"""Unit tests for the channel automation-tier grouping helper.

Plan 2026-05-29-003 Unit 1: pure-function bucketing of the settings overview
``dashboard_channels`` list into three independent automation tiers derived
solely from ``status['auth_type']``.
"""

from __future__ import annotations

from typing import Any

import pytest

from webui_app.helpers.channel_tiers import (
    TIER_BY_AUTH_TYPE,
    _is_ready,
    group_channels_by_tier,
)


def _status(auth_type: str | None, *, bound: bool = False) -> dict[str, Any]:
    """Minimal status dict shaped like ``binding_status.get_channel_status``."""
    return {"channel": "x", "auth_type": auth_type, "bound": bound}


# Authoritative auth_type → tier expectations (Plan R4).
_AUTH_TIER_EXPECTATIONS = {
    "anon": "tier-1",
    "token": "tier-2",
    "token_fields": "tier-2",
    "oauth": "tier-2",
    "userpass": "tier-2",
    "paste_blob": "tier-3",
    "live_browser": "tier-3",
    None: "tier-2",  # R4a
}


def _by_key(tiers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {t["key"]: t for t in tiers}


class TestTierMapping:
    def test_mapping_table_matches_authoritative_spec(self):
        """Every auth_type (incl. None) maps to the plan's tier (R4/R4a)."""
        for auth, expected_tier in _AUTH_TIER_EXPECTATIONS.items():
            assert TIER_BY_AUTH_TYPE.get(auth, "tier-2") == expected_tier

    def test_full_channel_set_buckets_per_authoritative_mapping(self):
        """All auth_types present → exactly 3 groups, members match the table (R4)."""
        channels = [
            ("telegraph", _status("anon")),
            ("devto", _status("token")),
            ("notion", _status("token_fields")),
            ("blogger", _status("oauth")),
            ("livejournal", _status("userpass")),
            ("substack", _status("paste_blob")),
            ("medium", _status("live_browser")),
        ]
        tiers = _by_key(group_channels_by_tier(channels))

        assert set(tiers) == {"tier-1", "tier-2", "tier-3"}
        assert {n for n, _, _ in tiers["tier-1"]["channels"]} == {"telegraph"}
        assert {n for n, _, _ in tiers["tier-2"]["channels"]} == {
            "devto",
            "notion",
            "blogger",
            "livejournal",
        }
        assert {n for n, _, _ in tiers["tier-3"]["channels"]} == {"substack", "medium"}


class TestCountsAndDefaults:
    def test_counts_and_default_open_state(self):
        """total/ready counts correct; anon counts as ready; tier-1 open (R3/R2)."""
        channels = [
            ("telegraph", _status("anon")),  # ready (anon)
            ("devto", _status("token", bound=True)),  # ready (bound)
            ("notion", _status("token_fields")),  # not ready
            ("substack", _status("paste_blob")),  # not ready
        ]
        tiers = _by_key(group_channels_by_tier(channels))

        assert tiers["tier-1"]["total"] == 1
        assert tiers["tier-1"]["ready"] == 1
        assert tiers["tier-1"]["open"] is True

        assert tiers["tier-2"]["total"] == 2
        assert tiers["tier-2"]["ready"] == 1  # devto bound; notion not
        assert tiers["tier-2"]["open"] is False

        assert tiers["tier-3"]["total"] == 1
        assert tiers["tier-3"]["ready"] == 0
        assert tiers["tier-3"]["open"] is False

    def test_each_group_carries_label_and_subtitle(self):
        """R11: every rendered group exposes a label and a one-line subtitle."""
        tiers = group_channels_by_tier([("telegraph", _status("anon"))])
        g = tiers[0]
        assert g["label"]
        assert g["subtitle"]


class TestReadyFirstOrdering:
    def test_ready_channels_precede_unconfigured_within_tier(self):
        """R5: bound/ready first, unconfigured after."""
        channels = [
            ("a_bound", _status("token", bound=True)),
            ("b_unbound", _status("token")),
            ("c_bound", _status("token", bound=True)),
            ("d_unbound", _status("token")),
        ]
        names = [n for n, _, _ in group_channels_by_tier(channels)[0]["channels"]]
        assert names == ["a_bound", "c_bound", "b_unbound", "d_unbound"]

    def test_stable_within_segments_for_shuffled_input(self):
        """R6: each segment preserves the caller's input order (stable)."""
        channels = [
            ("z_unbound", _status("token")),
            ("a_bound", _status("token", bound=True)),
            ("m_unbound", _status("token")),
            ("b_bound", _status("token", bound=True)),
        ]
        names = [n for n, _, _ in group_channels_by_tier(channels)[0]["channels"]]
        # ready segment keeps input order (a_bound, b_bound),
        # unready segment keeps input order (z_unbound, m_unbound)
        assert names == ["a_bound", "b_bound", "z_unbound", "m_unbound"]

    def test_ready_flag_attached_per_channel(self):
        """Each item carries an accurate ready flag for template segmentation."""
        channels = [
            ("anon_ch", _status("anon")),  # ready via anon
            ("bound_ch", _status("token", bound=True)),  # ready via bound
            ("unbound_ch", _status("token")),  # not ready
        ]
        flags = {
            n: r for tier in group_channels_by_tier(channels) for n, _, r in tier["channels"]
        }
        assert flags == {"anon_ch": True, "bound_ch": True, "unbound_ch": False}


class TestFallbackAndEdgeCases:
    def test_none_auth_type_falls_into_tier_2(self):
        """R4a: auth_type=None must land in tier-2, never vanish."""
        tiers = _by_key(group_channels_by_tier([("mystery", _status(None))]))
        assert "tier-2" in tiers
        assert {n for n, _, _ in tiers["tier-2"]["channels"]} == {"mystery"}

    def test_unknown_future_auth_type_falls_into_tier_2(self):
        """An unrecognized auth_type also defaults to tier-2 (forward-safe)."""
        tiers = _by_key(group_channels_by_tier([("future", _status("quantum_oauth"))]))
        assert {n for n, _, _ in tiers["tier-2"]["channels"]} == {"future"}

    def test_empty_tier_is_dropped(self):
        """R12: a tier with no members is omitted from the output."""
        tiers = _by_key(group_channels_by_tier([("telegraph", _status("anon"))]))
        assert set(tiers) == {"tier-1"}

    def test_zero_ready_tier_keeps_full_member_list(self):
        """R12: a tier with no bound channels still renders 0/N + all members."""
        channels = [
            ("notion", _status("token_fields")),
            ("devto", _status("token")),
        ]
        tiers = _by_key(group_channels_by_tier(channels))
        assert tiers["tier-2"]["ready"] == 0
        assert tiers["tier-2"]["total"] == 2

    def test_empty_input_returns_empty_list(self):
        assert group_channels_by_tier([]) == []


class TestIsReady:
    @pytest.mark.parametrize(
        "status,expected",
        [
            ({"auth_type": "anon", "bound": False}, True),
            ({"auth_type": "token", "bound": True}, True),
            ({"auth_type": "token", "bound": False}, False),
            ({"auth_type": None, "bound": False}, False),
            ({"auth_type": None, "bound": True}, True),
            ({}, False),
        ],
    )
    def test_is_ready(self, status, expected):
        assert _is_ready(status) is expected
