"""Tests for [anchor_alarm] TOML parsing + precedence resolution."""

from __future__ import annotations

import pytest

from backlink_publisher.anchor.metrics import (
    resolve_thresholds,
)
from backlink_publisher.config import (
    AnchorAlarmConfig,
    AnchorAlarmOverride,
    _parse_anchor_alarm,
)
from backlink_publisher._util.errors import InputValidationError


# ── parsing ─────────────────────────────────────────────────────────────────


def test_missing_section_returns_defaults():
    cfg = _parse_anchor_alarm(None)
    assert cfg.entropy_floor is None
    assert cfg.exact_ratio_ceiling is None
    assert cfg.top3_concentration_ceiling is None
    assert cfg.overrides == []


def test_empty_section_returns_defaults():
    cfg = _parse_anchor_alarm({})
    assert cfg.entropy_floor is None
    assert cfg.exact_ratio_ceiling is None
    assert cfg.top3_concentration_ceiling is None
    assert cfg.overrides == []


def test_globals_only():
    cfg = _parse_anchor_alarm(
        {
            "entropy_floor": 1.8,
            "exact_ratio_ceiling": 0.05,
            "top3_concentration_ceiling": 0.20,
        }
    )
    assert cfg.entropy_floor == 1.8
    assert cfg.exact_ratio_ceiling == 0.05
    assert cfg.top3_concentration_ceiling == 0.20
    assert cfg.overrides == []


def test_partial_globals():
    """Only some fields set → others remain None and fall through to defaults."""
    cfg = _parse_anchor_alarm({"exact_ratio_ceiling": 0.05})
    assert cfg.entropy_floor is None
    assert cfg.exact_ratio_ceiling == 0.05
    assert cfg.top3_concentration_ceiling is None


def test_override_url_scope():
    cfg = _parse_anchor_alarm(
        {
            "override": [
                {
                    "match": "https://example.com/page",
                    "scope": "url",
                    "entropy_floor": 2.0,
                }
            ]
        }
    )
    assert len(cfg.overrides) == 1
    o = cfg.overrides[0]
    assert o.match == "https://example.com/page"
    assert o.scope == "url"
    assert o.entropy_floor == 2.0
    assert o.exact_ratio_ceiling is None


def test_override_domain_scope():
    cfg = _parse_anchor_alarm(
        {
            "override": [
                {
                    "match": "example.com",
                    "scope": "domain",
                    "exact_ratio_ceiling": 0.05,
                    "top3_concentration_ceiling": 0.20,
                }
            ]
        }
    )
    assert len(cfg.overrides) == 1
    o = cfg.overrides[0]
    assert o.scope == "domain"
    assert o.entropy_floor is None
    assert o.exact_ratio_ceiling == 0.05


# ── parsing errors ──────────────────────────────────────────────────────────


def test_unknown_top_level_key_raises():
    with pytest.raises(InputValidationError, match="not a known threshold"):
        _parse_anchor_alarm({"entropy_flor": 1.5})  # typo


def test_non_numeric_threshold_raises():
    with pytest.raises(InputValidationError, match="must be a number"):
        _parse_anchor_alarm({"entropy_floor": "high"})


def test_bool_threshold_raises():
    """Python bool is a subclass of int — must be caught explicitly."""
    with pytest.raises(InputValidationError, match="must be a number, got bool"):
        _parse_anchor_alarm({"exact_ratio_ceiling": True})


def test_negative_entropy_floor_raises():
    with pytest.raises(InputValidationError, match="must be ≥ 0"):
        _parse_anchor_alarm({"entropy_floor": -1.0})


def test_ratio_above_one_raises():
    with pytest.raises(InputValidationError, match=r"must be in \[0.0, 1.0\]"):
        _parse_anchor_alarm({"exact_ratio_ceiling": 1.5})


def test_ratio_below_zero_raises():
    with pytest.raises(InputValidationError, match=r"must be in \[0.0, 1.0\]"):
        _parse_anchor_alarm({"top3_concentration_ceiling": -0.1})


def test_infinite_value_raises():
    with pytest.raises(InputValidationError, match="must be finite"):
        _parse_anchor_alarm({"entropy_floor": float("inf")})


def test_override_unknown_scope_raises():
    with pytest.raises(InputValidationError, match="scope"):
        _parse_anchor_alarm(
            {"override": [{"match": "x.com", "scope": "regex", "entropy_floor": 1.5}]}
        )


def test_override_missing_match_raises():
    with pytest.raises(InputValidationError, match="'match' is required"):
        _parse_anchor_alarm(
            {"override": [{"scope": "url", "entropy_floor": 1.5}]}
        )


def test_override_empty_match_raises():
    with pytest.raises(InputValidationError, match="'match' is required"):
        _parse_anchor_alarm(
            {"override": [{"match": "", "scope": "url", "entropy_floor": 1.5}]}
        )


def test_override_with_no_threshold_fields_raises():
    """A row with match+scope but no thresholds would have zero effect — typo."""
    with pytest.raises(InputValidationError, match="no threshold fields"):
        _parse_anchor_alarm(
            {"override": [{"match": "x.com", "scope": "domain"}]}
        )


def test_override_not_a_list_raises():
    with pytest.raises(InputValidationError, match="array of tables"):
        _parse_anchor_alarm({"override": "not-a-list"})


def test_override_unknown_key_raises():
    """Override rows mirror the global raise-loud posture: an unknown key is a
    typo, not a no-op. Without this guard a misspelled threshold field is
    silently dropped whenever the row also carries a valid field."""
    with pytest.raises(InputValidationError, match="not a known field"):
        _parse_anchor_alarm(
            {
                "override": [
                    {
                        "match": "x.com",
                        "scope": "domain",
                        "entropy_floor": 1.8,  # valid
                        "exact_ratio_ceil": 0.05,  # typo — would be silently lost
                    }
                ]
            }
        )


def test_override_arbitrary_extra_key_raises():
    with pytest.raises(InputValidationError, match="not a known field"):
        _parse_anchor_alarm(
            {
                "override": [
                    {"match": "x.com", "scope": "url", "entropy_floor": 1.5, "note": "hi"}
                ]
            }
        )


def test_top_level_not_a_table_raises():
    with pytest.raises(InputValidationError, match="must be a table"):
        _parse_anchor_alarm("scalar")


# ── precedence resolver ─────────────────────────────────────────────────────


def test_resolve_uses_hardcoded_defaults_when_unset():
    cfg = AnchorAlarmConfig()
    th = resolve_thresholds(cfg, "https://x.com/page", "https://x.com")
    # Hardcoded defaults from anchor_metrics
    assert th.entropy_floor == 1.5
    assert th.exact_ratio_ceiling == 0.10
    assert th.top3_concentration_ceiling == 0.25


def test_resolve_uses_globals():
    cfg = AnchorAlarmConfig(
        entropy_floor=1.8,
        exact_ratio_ceiling=0.05,
        top3_concentration_ceiling=0.20,
    )
    th = resolve_thresholds(cfg, "https://x.com/p", "https://x.com")
    assert th.entropy_floor == 1.8
    assert th.exact_ratio_ceiling == 0.05
    assert th.top3_concentration_ceiling == 0.20


def test_resolve_partial_global_falls_through():
    """Unset global field → falls back to hardcoded default."""
    cfg = AnchorAlarmConfig(entropy_floor=1.8)
    th = resolve_thresholds(cfg, "https://x.com/p", "https://x.com")
    assert th.entropy_floor == 1.8
    # Others stay at hardcoded defaults
    assert th.exact_ratio_ceiling == 0.10
    assert th.top3_concentration_ceiling == 0.25


def test_resolve_per_domain_override():
    cfg = AnchorAlarmConfig(
        overrides=[
            AnchorAlarmOverride(
                match="example.com",
                scope="domain",
                exact_ratio_ceiling=0.05,
            )
        ]
    )
    th = resolve_thresholds(cfg, "https://example.com/p", "https://example.com")
    assert th.exact_ratio_ceiling == 0.05
    # Other fields stay at hardcoded defaults
    assert th.entropy_floor == 1.5


def test_resolve_per_url_overrides_per_domain():
    cfg = AnchorAlarmConfig(
        overrides=[
            AnchorAlarmOverride(
                match="example.com",
                scope="domain",
                entropy_floor=1.6,  # domain-level
            ),
            AnchorAlarmOverride(
                match="https://example.com/a",
                scope="url",
                entropy_floor=2.2,  # per-URL, wins for this URL
            ),
        ]
    )
    # Matched URL gets per-URL value
    th_a = resolve_thresholds(cfg, "https://example.com/a", "https://example.com")
    assert th_a.entropy_floor == 2.2

    # Different URL on same domain falls back to per-domain
    th_b = resolve_thresholds(cfg, "https://example.com/b", "https://example.com")
    assert th_b.entropy_floor == 1.6


def test_resolve_partial_overrides_fall_through():
    """Per-URL override sets one field; others come from per-domain → global → default."""
    cfg = AnchorAlarmConfig(
        entropy_floor=1.7,  # global
        overrides=[
            AnchorAlarmOverride(
                match="example.com",
                scope="domain",
                exact_ratio_ceiling=0.08,  # domain
            ),
            AnchorAlarmOverride(
                match="https://example.com/p",
                scope="url",
                top3_concentration_ceiling=0.30,  # per-URL only
            ),
        ]
    )
    th = resolve_thresholds(cfg, "https://example.com/p", "https://example.com")
    # per-URL set top3
    assert th.top3_concentration_ceiling == 0.30
    # per-domain set exact_ratio (per-URL didn't override)
    assert th.exact_ratio_ceiling == 0.08
    # global set entropy (neither override touched)
    assert th.entropy_floor == 1.7


def test_resolve_unrelated_domain_uses_defaults():
    cfg = AnchorAlarmConfig(
        overrides=[
            AnchorAlarmOverride(
                match="example.com", scope="domain", entropy_floor=2.0,
            )
        ]
    )
    th = resolve_thresholds(cfg, "https://other.com/p", "https://other.com")
    assert th.entropy_floor == 1.5  # hardcoded default


def test_resolve_domain_match_handles_trailing_slash():
    """main_domain rstrip('/') equality — trailing slash should not matter."""
    cfg = AnchorAlarmConfig(
        overrides=[
            AnchorAlarmOverride(
                match="example.com/", scope="domain", entropy_floor=2.0,
            )
        ]
    )
    # Caller passes main_domain with or without slash; both must match.
    th = resolve_thresholds(cfg, "https://example.com/p", "example.com")
    assert th.entropy_floor == 2.0
