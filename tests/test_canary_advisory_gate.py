"""Tests for the canary advisory surfacing + opt-in hard-skip gate.

Plan: docs/plans/2026-05-27-001-feat-adapter-contract-canary-plan.md (Unit 4).

Covers:
  - Store quarantine/re-arm machinery (consecutive_oks, quarantined, N/M
    thresholds) + backward-compat with old minimal records.
  - is_degraded / is_quarantined query helpers (fail-open on unknown).
  - The publish-loop ``_canary_gate``: advisory WARNING by default (row NOT
    skipped), dedup within an invocation, opt-in hard-skip filters the row,
    fail-open for unknown platforms.
  - Security: WARNING / dashboard payload carries no secret substring.

The session-autouse ``_isolate_user_dirs`` fixture points
``BACKLINK_PUBLISHER_CONFIG_DIR`` at a tmp dir; per-test overrides use
``monkeypatch.setenv`` (never ``del os.environ``; see
feedback_del_os_environ_poisons_later_tests).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backlink_publisher.canary import store
from backlink_publisher.cli._publish_helpers import _canary_gate


@pytest.fixture(autouse=True)
def _isolated_canary_dir(tmp_path, monkeypatch):
    """Each test gets its OWN config dir so health writes don't leak; resets the
    cached _LazyStore so the path re-resolves."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    store.canary_health_store.reset()
    yield
    store.canary_health_store.reset()


def _write_config(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(body, encoding="utf-8")
    return p


# ── Store: quarantine after N drifts ─────────────────────────────────────


def test_quarantine_after_n_consecutive_drifts():
    assert store.QUARANTINE_AFTER_N == 2
    r1 = store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    assert r1["consecutive_failures"] == 1
    assert r1["quarantined"] is False  # below threshold
    r2 = store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    assert r2["consecutive_failures"] == 2
    assert r2["quarantined"] is True  # crossed N


def test_consecutive_oks_increments_on_link_alive():
    store.record_verdict("velog", store.STATUS_LINK_ALIVE)
    r2 = store.record_verdict("velog", store.STATUS_LINK_ALIVE)
    assert r2["consecutive_oks"] == 2
    assert r2["consecutive_failures"] == 0


# ── Store: re-arm after M consecutive link-alive while quarantined ────────


def test_rearm_clears_quarantine_after_m_oks():
    assert store.REARM_AFTER_M == 2
    # Quarantine first.
    store.record_verdict("telegraph", store.STATUS_DRIFT_CONFIRMED)
    store.record_verdict("telegraph", store.STATUS_DRIFT_CONFIRMED)
    assert store.is_quarantined("telegraph") is True

    # One green is NOT enough to re-arm (anti-flap).
    r1 = store.record_verdict("telegraph", store.STATUS_LINK_ALIVE)
    assert r1["consecutive_oks"] == 1
    assert r1["quarantined"] is True

    # Second green crosses M → re-arm.
    r2 = store.record_verdict("telegraph", store.STATUS_LINK_ALIVE)
    assert r2["consecutive_oks"] == 2
    assert r2["quarantined"] is False


def test_single_drift_does_not_quarantine_then_ok_resets_failures():
    store.record_verdict("ghpages", store.STATUS_DRIFT_CONFIRMED)
    assert store.is_quarantined("ghpages") is False
    r = store.record_verdict("ghpages", store.STATUS_LINK_ALIVE)
    assert r["consecutive_failures"] == 0
    assert r["consecutive_oks"] == 1


def test_advisory_does_not_touch_quarantine_or_counters():
    store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    before = store.get_health("blogger")
    after = store.record_verdict("blogger", store.STATUS_ADVISORY)
    # advisory is neither OK nor confirmed drift → quarantine + counters frozen.
    assert after["quarantined"] == before["quarantined"] is True
    assert after["consecutive_failures"] == before["consecutive_failures"]
    assert after["consecutive_oks"] == before["consecutive_oks"]


# ── Store: backward-compat with old minimal records ──────────────────────


def test_backward_compat_old_minimal_record_treated_not_quarantined(tmp_path):
    """A record written before Unit 4 has no quarantined / consecutive_oks key."""
    path = tmp_path / "canary-health.json"
    path.write_text(
        json.dumps(
            {
                "blogger": {
                    "status": "drift-confirmed",
                    "consecutive_failures": 5,
                    "last_ok_at": None,
                    "last_drift_at": "2026-05-26T00:00:00+00:00",
                }
            }
        ),
        encoding="utf-8",
    )
    store.canary_health_store.reset()
    assert store.is_quarantined("blogger") is False  # missing key → not quarantined
    # But drift-confirmed status still marks it degraded.
    assert store.is_degraded("blogger") is True
    # A subsequent drift seeds the new fields cleanly (failures continues).
    r = store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    assert r["consecutive_failures"] == 6
    assert r["quarantined"] is True
    assert r["consecutive_oks"] == 0


# ── Query helpers: fail-open + semantics ─────────────────────────────────


def test_is_degraded_and_quarantined_fail_open_for_unknown():
    assert store.is_degraded("never-seen") is False
    assert store.is_quarantined("never-seen") is False


def test_link_alive_and_advisory_are_not_degraded():
    store.record_verdict("velog", store.STATUS_LINK_ALIVE)
    assert store.is_degraded("velog") is False
    store.record_verdict("medium", store.STATUS_ADVISORY)
    assert store.is_degraded("medium") is False


def test_quarantined_platform_is_degraded_even_if_last_status_advisory():
    store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    store.record_verdict("blogger", store.STATUS_ADVISORY)  # status flips advisory
    assert store.get_health("blogger")["status"] == store.STATUS_ADVISORY
    assert store.is_quarantined("blogger") is True
    assert store.is_degraded("blogger") is True  # quarantine keeps it degraded


# ── Publish-loop gate: advisory default (NOT skipped) ─────────────────────


def test_gate_advisory_default_does_not_skip(capsys):
    store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)  # degraded, not quarantined
    warned: set[str] = set()
    skip, reason = _canary_gate("blogger", warned=warned)
    assert skip is False
    assert reason is None
    assert "blogger" in warned
    err = capsys.readouterr().err
    assert "canary" in err.lower()


def test_gate_dedups_warning_within_invocation(capsys):
    store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    warned: set[str] = set()
    _canary_gate("blogger", warned=warned)
    _canary_gate("blogger", warned=warned)
    _canary_gate("blogger", warned=warned)
    err = capsys.readouterr().err
    # Deduped: the per-platform advisory WARNING line appears exactly once.
    n_canary_lines = sum(
        1 for line in err.splitlines() if "canary" in line.lower() and "blogger" in line
    )
    assert n_canary_lines == 1


# ── Publish-loop gate: opt-in hard-skip ──────────────────────────────────


def test_gate_hard_skip_filters_quarantined_opted_in(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        "\n".join(
            [
                "[canary.blogger]",
                'post_url = "https://canary.blogspot.com/p.html"',
                'expected_target = "https://example.com/"',
                "hard_skip = true",
            ]
        )
        + "\n",
    )
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    store.canary_health_store.reset()
    # Quarantine the platform.
    store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    assert store.is_quarantined("blogger") is True

    skip, reason = _canary_gate("blogger", warned=set())
    assert skip is True
    assert reason is not None
    assert "blogger" in reason
    assert "hard_skip" in reason


def test_gate_no_hard_skip_quarantined_still_advisory_not_skipped(tmp_path, monkeypatch):
    """Quarantined but config opts OUT (default hard_skip=false) → advisory, not skip."""
    _write_config(
        tmp_path,
        "[canary.blogger]\n"
        'post_url = "https://x"\nexpected_target = "https://y"\n',
    )
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    store.canary_health_store.reset()
    store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    skip, reason = _canary_gate("blogger", warned=set())
    assert skip is False  # not opted in → advisory only


def test_gate_quarantined_but_no_config_entry_not_skipped(tmp_path, monkeypatch):
    """Quarantined platform with no [canary.<platform>] entry → cannot hard-skip."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    store.canary_health_store.reset()
    store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    skip, _reason = _canary_gate("blogger", warned=set())
    assert skip is False


# ── Publish-loop gate: fail-open ─────────────────────────────────────────


def test_gate_fail_open_for_unknown_platform(capsys):
    warned: set[str] = set()
    skip, reason = _canary_gate("never-run", warned=warned)
    assert skip is False
    assert reason is None
    assert warned == set()  # no spurious warning
    err = capsys.readouterr().err
    assert "canary" not in err.lower()


def test_gate_empty_platform_is_noop():
    skip, reason = _canary_gate("", warned=set())
    assert skip is False
    assert reason is None


# ── Security: no secret substrings in surfaced payloads ───────────────────


def test_gate_reason_carries_no_secret_substring(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        "\n".join(
            [
                "[canary.blogger]",
                'post_url = "https://canary.blogspot.com/p.html?token=SECRET123"',
                'expected_target = "https://example.com/"',
                "hard_skip = true",
            ]
        )
        + "\n",
    )
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    store.canary_health_store.reset()
    store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    _skip, reason = _canary_gate("blogger", warned=set())
    assert reason is not None
    for secret in ("token", "SECRET123", "cookie", "Authorization"):
        assert secret not in reason


def test_health_store_payload_has_only_nonsensitive_fields():
    rec = store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    assert set(rec) == {
        "status",
        "consecutive_failures",
        "last_ok_at",
        "last_drift_at",
        "consecutive_oks",
        "quarantined",
        "consecutive_advisory",
    }
    blob = json.dumps(store.list_all())
    for secret in ("token", "cookie", "password", "Authorization", "api_key"):
        assert secret not in blob
