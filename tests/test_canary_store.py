"""Tests for the canary health store + ``[canary.<platform>]`` config reader.

Plan: docs/plans/2026-05-27-001-feat-adapter-contract-canary-plan.md (Unit 1).

Covers the v1-minimal health record round-trip, drift/link-alive debounce
counters, ``BACKLINK_PUBLISHER_CONFIG_DIR`` re-resolution, 0o600 atomic
writes, and the ``[canary.<platform>]`` config parse round-trip.

The session-autouse ``_isolate_user_dirs`` fixture (tests/conftest.py)
already points ``BACKLINK_PUBLISHER_CONFIG_DIR`` at a tmp dir; per-test
overrides use ``monkeypatch.setenv`` (never ``del os.environ`` — that
poisons later tests; see feedback_del_os_environ_poisons_later_tests).
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from backlink_publisher.canary import store


@pytest.fixture(autouse=True)
def _isolated_canary_dir(tmp_path, monkeypatch):
    """Point each test at its OWN config dir so health-file writes from one
    test don't leak onto disk into the next (the session-autouse
    ``_isolate_user_dirs`` fixture shares a single dir across the suite).
    Uses ``monkeypatch.setenv`` — never ``del os.environ``. Resets the
    cached _LazyStore so the path re-resolves."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    store.canary_health_store.reset()
    yield
    store.canary_health_store.reset()


def _health_path() -> Path:
    return Path(store.canary_health_store.path)


# ── Happy: first write round-trips ───────────────────────────────────────


def test_first_write_link_alive_round_trips():
    rec = store.record_verdict("blogger", store.STATUS_LINK_ALIVE)
    assert rec["status"] == store.STATUS_LINK_ALIVE
    assert rec["consecutive_failures"] == 0
    assert rec["last_ok_at"] is not None
    assert rec["last_drift_at"] is None

    reloaded = store.get_health("blogger")
    assert reloaded == rec
    # Disk truly persists identical content.
    on_disk = json.loads(_health_path().read_text(encoding="utf-8"))
    assert on_disk["blogger"] == rec


def test_get_health_unknown_returns_minimal_default():
    rec = store.get_health("never-seen")
    assert rec == {
        "status": store.STATUS_NOT_CONFIGURED,
        "consecutive_failures": 0,
        "last_ok_at": None,
        "last_drift_at": None,
        "consecutive_oks": 0,
        "quarantined": False,
        "consecutive_advisory": 0,
    }
    # Read default must not have written a file.
    assert not _health_path().exists()


def test_health_record_has_quarantine_fields():
    rec = store.record_verdict("velog", store.STATUS_DRIFT_CONFIRMED)
    # Unit 4 adds quarantined + consecutive_oks + consecutive_advisory
    # alongside the Unit 1 minimal set.
    assert set(rec) == {
        "status",
        "consecutive_failures",
        "last_ok_at",
        "last_drift_at",
        "consecutive_oks",
        "quarantined",
        "consecutive_advisory",
    }
    # A single drift is below QUARANTINE_AFTER_N → not yet quarantined.
    assert rec["consecutive_failures"] == 1
    assert rec["quarantined"] is False


# ── Edge: debounce counters ──────────────────────────────────────────────


def test_consecutive_drift_increments_then_link_alive_resets():
    r1 = store.record_verdict("telegraph", store.STATUS_DRIFT_CONFIRMED)
    r2 = store.record_verdict("telegraph", store.STATUS_DRIFT_CONFIRMED)
    assert r1["consecutive_failures"] == 1
    assert r2["consecutive_failures"] == 2
    assert r2["last_drift_at"] is not None

    r3 = store.record_verdict("telegraph", store.STATUS_LINK_ALIVE)
    assert r3["consecutive_failures"] == 0
    assert r3["last_ok_at"] is not None
    # Prior drift timestamp is preserved (link-alive only touches last_ok_at).
    assert r3["last_drift_at"] == r2["last_drift_at"]


def test_advisory_preserves_counters_and_timestamps():
    store.record_verdict("ghpages", store.STATUS_DRIFT_CONFIRMED)
    before = store.get_health("ghpages")
    after = store.record_verdict("ghpages", store.STATUS_ADVISORY)
    # advisory is neither OK nor confirmed drift → counters untouched.
    assert after["consecutive_failures"] == before["consecutive_failures"]
    assert after["last_ok_at"] == before["last_ok_at"]
    assert after["last_drift_at"] == before["last_drift_at"]
    assert after["status"] == store.STATUS_ADVISORY


# ── Unit 4: consecutive-advisory streak (canary-stale detection) ──────────


def test_advisory_streak_increments_while_quarantine_counters_frozen():
    # A drift seeds consecutive_failures=1; subsequent advisory runs must
    # increment the advisory streak WITHOUT moving the quarantine counter.
    store.record_verdict("ghpages", store.STATUS_DRIFT_CONFIRMED)
    a1 = store.record_verdict("ghpages", store.STATUS_ADVISORY)
    a2 = store.record_verdict("ghpages", store.STATUS_ADVISORY)
    assert a1["consecutive_advisory"] == 1
    assert a2["consecutive_advisory"] == 2
    # Quarantine counter stays frozen across the advisory streak (by design).
    assert a2["consecutive_failures"] == 1


def test_advisory_streak_resets_on_link_alive_drift_and_not_configured():
    for resetter in (
        store.STATUS_LINK_ALIVE,
        store.STATUS_DRIFT_CONFIRMED,
        store.STATUS_NOT_CONFIGURED,
    ):
        store.record_verdict("velog", store.STATUS_ADVISORY)
        streaked = store.record_verdict("velog", store.STATUS_ADVISORY)
        assert streaked["consecutive_advisory"] == 2
        after = store.record_verdict("velog", resetter)
        assert after["consecutive_advisory"] == 0, resetter


def test_multiple_platforms_keyed_independently():
    store.record_verdict("blogger", store.STATUS_LINK_ALIVE)
    store.record_verdict("velog", store.STATUS_DRIFT_CONFIRMED)
    allrecs = store.list_all()
    assert allrecs["blogger"]["status"] == store.STATUS_LINK_ALIVE
    assert allrecs["velog"]["status"] == store.STATUS_DRIFT_CONFIRMED
    assert allrecs["velog"]["consecutive_failures"] == 1


# ── Edge: env re-resolution ──────────────────────────────────────────────


def test_config_dir_change_reresolves_store_path(tmp_path, monkeypatch):
    first = tmp_path / "cfg-a"
    second = tmp_path / "cfg-b"
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(first))
    store.canary_health_store.reset()
    store.record_verdict("blogger", store.STATUS_LINK_ALIVE)
    assert (first / "canary-health.json").exists()

    # Flip env (monkeypatch.setenv, NOT del) → path must follow.
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(second))
    store.record_verdict("velog", store.STATUS_DRIFT_CONFIRMED)
    assert (second / "canary-health.json").exists()
    # The blogger write stayed in the first dir; second dir only has velog.
    second_data = json.loads(
        (second / "canary-health.json").read_text(encoding="utf-8")
    )
    assert set(second_data) == {"velog"}


# ── Error: permissions + atomicity ───────────────────────────────────────


def test_health_file_is_0600():
    store.record_verdict("blogger", store.STATUS_LINK_ALIVE)
    mode = stat.S_IMODE(_health_path().stat().st_mode)
    assert mode == 0o600


def test_failed_write_leaves_no_half_file(monkeypatch):
    # Seed a valid record first.
    store.record_verdict("blogger", store.STATUS_LINK_ALIVE)
    good = _health_path().read_text(encoding="utf-8")

    # Simulate an interruption mid-write inside atomic_write's fdopen body.
    import backlink_publisher.persistence.safe_write as sw

    real_fdopen = sw.os.fdopen

    class _Boom:
        def __enter__(self):
            raise OSError("simulated interruption")

        def __exit__(self, *a):
            return False

    def _boom_fdopen(*a, **k):
        # Close the real fd to avoid a leak, then blow up on context enter.
        fd = a[0]
        try:
            import os as _os

            _os.close(fd)
        except OSError:
            pass
        return _Boom()

    monkeypatch.setattr(sw.os, "fdopen", _boom_fdopen)
    with pytest.raises(OSError):
        store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    monkeypatch.setattr(sw.os, "fdopen", real_fdopen)

    # Original file is intact; no leftover temp sibling.
    assert _health_path().read_text(encoding="utf-8") == good
    leftovers = [
        p
        for p in _health_path().parent.iterdir()
        if p.name.startswith("canary-health.json.") and p != _health_path()
    ]
    assert leftovers == []


# ── Edge: [canary.<platform>] config round-trip ──────────────────────────


def _write_config(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_read_canary_config_round_trips(tmp_path):
    cfg = _write_config(
        tmp_path,
        "\n".join(
            [
                '[canary.blogger]',
                'post_url = "https://canary.blogspot.com/p.html"',
                'expected_target = "https://example.com/"',
                'marker = "cnry-7f3a9c2e"',
                'hard_skip = true',
            ]
        )
        + "\n",
    )
    entry = store.read_canary_config("blogger", config_path=cfg)
    assert entry == {
        "post_url": "https://canary.blogspot.com/p.html",
        "expected_target": "https://example.com/",
        "marker": "cnry-7f3a9c2e",
        "hard_skip": True,
    }


def test_read_canary_config_marker_defaults_none(tmp_path):
    cfg = _write_config(
        tmp_path,
        "\n".join(
            [
                '[canary.blogger]',
                'post_url = "https://canary.blogspot.com/p.html"',
                'expected_target = "https://example.com/"',
            ]
        )
        + "\n",
    )
    entry = store.read_canary_config("blogger", config_path=cfg)
    assert entry is not None
    assert entry["marker"] is None  # no marker → drift can never be confirmed


def test_read_canary_config_hard_skip_defaults_false(tmp_path):
    cfg = _write_config(
        tmp_path,
        "\n".join(
            [
                '[canary.velog]',
                'post_url = "https://velog.io/@x/p"',
                'expected_target = "https://example.com/"',
            ]
        )
        + "\n",
    )
    entry = store.read_canary_config("velog", config_path=cfg)
    assert entry is not None
    assert entry["hard_skip"] is False


def test_read_canary_config_missing_platform_returns_none(tmp_path):
    cfg = _write_config(
        tmp_path,
        '[canary.blogger]\npost_url = "https://x"\nexpected_target = "https://y"\n',
    )
    assert store.read_canary_config("telegraph", config_path=cfg) is None


def test_read_canary_config_no_file_returns_none(tmp_path):
    assert (
        store.read_canary_config("blogger", config_path=tmp_path / "nope.toml")
        is None
    )


def test_read_canary_config_honors_env_config_dir(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    _write_config(
        cfg_dir,
        '[canary.ghpages]\npost_url = "https://gh.io/p"\n'
        'expected_target = "https://example.com/"\n',
    )
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(cfg_dir))
    entry = store.read_canary_config("ghpages")
    assert entry is not None
    assert entry["post_url"] == "https://gh.io/p"


# ── Unit 2: sibling forward-path (publish-time) drift stream ──────────────


def test_publish_path_first_drift_increments():
    rec = store.record_publish_path_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    assert rec["status"] == store.STATUS_DRIFT_CONFIRMED
    assert rec["consecutive_failures"] == 1
    assert rec["last_drift_at"] is not None
    # A single drift is below the debounce threshold → not yet degraded.
    assert rec["degraded"] is False
    assert store.is_publish_path_degraded("blogger") is False
    # Stored under the sibling key, not as a top-level platform record.
    on_disk = json.loads(_health_path().read_text(encoding="utf-8"))
    assert set(on_disk) == {"_publish_path"}
    assert on_disk["_publish_path"]["blogger"] == rec


def test_publish_path_unknown_returns_minimal_default():
    rec = store.get_publish_path_health("never-seen")
    assert rec == {
        "status": store.STATUS_NOT_CONFIGURED,
        "consecutive_failures": 0,
        "last_ok_at": None,
        "last_drift_at": None,
        "consecutive_oks": 0,
        "degraded": False,
    }
    assert not _health_path().exists()


def test_publish_path_degrades_at_n_then_rearms():
    store.record_publish_path_verdict("velog", store.STATUS_DRIFT_CONFIRMED)
    r2 = store.record_publish_path_verdict("velog", store.STATUS_DRIFT_CONFIRMED)
    # Two consecutive confirmed drifts cross QUARANTINE_AFTER_N=2 → degraded.
    assert r2["consecutive_failures"] == 2
    assert r2["degraded"] is True
    assert store.is_publish_path_degraded("velog") is True

    # One green resets the failure counter but anti-flap keeps degraded set
    # until REARM_AFTER_M consecutive OKs.
    r3 = store.record_publish_path_verdict("velog", store.STATUS_LINK_ALIVE)
    assert r3["consecutive_failures"] == 0
    assert r3["consecutive_oks"] == 1
    assert r3["degraded"] is True
    r4 = store.record_publish_path_verdict("velog", store.STATUS_LINK_ALIVE)
    assert r4["consecutive_oks"] == 2
    assert r4["degraded"] is False  # re-armed
    assert store.is_publish_path_degraded("velog") is False


def test_publish_path_unmapped_status_is_noop():
    store.record_publish_path_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    before = store.get_publish_path_health("blogger")
    after = store.record_publish_path_verdict("blogger", store.STATUS_ADVISORY)
    # advisory/not-configured never mutate the forward-path record.
    assert after == before


def test_publish_path_disjoint_from_evergreen_record():
    """P0 regression: the evergreen ``record_verdict`` REPLACES ``data[blogger]``
    wholesale; the forward-path stream must survive that and vice versa."""
    # Seed forward-path drift, then write an evergreen verdict for the SAME
    # platform — the forward-path record must be untouched.
    store.record_publish_path_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    store.record_publish_path_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    store.record_verdict("blogger", store.STATUS_LINK_ALIVE)

    fp = store.get_publish_path_health("blogger")
    assert fp["consecutive_failures"] == 2
    assert fp["degraded"] is True  # evergreen write did not wipe it

    # And the evergreen record is independent of the forward-path stream.
    ev = store.get_health("blogger")
    assert ev["status"] == store.STATUS_LINK_ALIVE
    assert ev["consecutive_failures"] == 0
    assert "degraded" not in ev  # evergreen has quarantined, not degraded

    # A subsequent forward-path green still re-arms independently.
    store.record_publish_path_verdict("blogger", store.STATUS_LINK_ALIVE)
    store.record_publish_path_verdict("blogger", store.STATUS_LINK_ALIVE)
    assert store.is_publish_path_degraded("blogger") is False
    # Evergreen untouched by the forward-path writes.
    assert store.get_health("blogger")["status"] == store.STATUS_LINK_ALIVE


def test_list_all_excludes_publish_path_sentinel():
    """``list_all`` (consumed by the /ce:health evergreen canary card) must not
    surface the sibling stream as a bogus platform."""
    store.record_verdict("velog", store.STATUS_LINK_ALIVE)
    store.record_publish_path_verdict("velog", store.STATUS_DRIFT_CONFIRMED)
    allrecs = store.list_all()
    assert "velog" in allrecs
    assert "_publish_path" not in allrecs
    # The sentinel is still on disk (only the read API filters it).
    on_disk = json.loads(_health_path().read_text(encoding="utf-8"))
    assert "_publish_path" in on_disk


def test_publish_path_missing_key_loads_defaults_no_keyerror():
    # Pre-seed a health file with only an evergreen record (no _publish_path).
    store.record_verdict("blogger", store.STATUS_LINK_ALIVE)
    assert "_publish_path" not in json.loads(
        _health_path().read_text(encoding="utf-8")
    )
    # Reading the forward-path stream must not KeyError on the absent key.
    assert store.get_publish_path_health("blogger") == dict(
        store._PUBLISH_PATH_DEFAULT
    )
    assert store.is_publish_path_degraded("blogger") is False


def test_publish_path_file_is_0600():
    store.record_publish_path_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    mode = stat.S_IMODE(_health_path().stat().st_mode)
    assert mode == 0o600


def test_get_publish_path_health_corrupted_stream_returns_default():
    """Non-dict _publish_path value → isinstance guard → returns default (P1 fix)."""
    # Directly write a corrupted JSON to canary-health.json.
    hp = _health_path()
    hp.write_text(json.dumps({"_publish_path": "corrupted-string"}), encoding="utf-8")
    hp.chmod(0o600)
    store.canary_health_store.reset()

    rec = store.get_publish_path_health("medium")
    assert rec == dict(store._PUBLISH_PATH_DEFAULT)
    # And the predicate must not raise.
    assert store.is_publish_path_degraded("medium") is False
    store.canary_health_store.reset()


def test_list_publish_path_all_absent_key_returns_empty():
    """Store with only evergreen records (no _publish_path key) → {}."""
    store.record_verdict("velog", store.STATUS_LINK_ALIVE)
    assert "_publish_path" not in json.loads(
        _health_path().read_text(encoding="utf-8")
    )
    assert store.list_publish_path_all() == {}


def test_list_publish_path_all_non_dict_value_returns_empty():
    """_publish_path exists but is not a dict → isinstance guard → {}."""
    hp = _health_path()
    hp.write_text(json.dumps({"_publish_path": 99}), encoding="utf-8")
    hp.chmod(0o600)
    store.canary_health_store.reset()

    assert store.list_publish_path_all() == {}
    store.canary_health_store.reset()


def test_list_publish_path_all_fills_missing_defaults():
    """On-disk record with only 'status' key → missing fields filled from _PUBLISH_PATH_DEFAULT."""
    hp = _health_path()
    hp.write_text(
        json.dumps({"_publish_path": {"medium": {"status": "drift-confirmed"}}}),
        encoding="utf-8",
    )
    hp.chmod(0o600)
    store.canary_health_store.reset()

    result = store.list_publish_path_all()
    assert "medium" in result
    rec = result["medium"]
    # The stored "status" overrides the default
    assert rec["status"] == "drift-confirmed"
    # But other fields are filled from _PUBLISH_PATH_DEFAULT
    assert "degraded" in rec
    assert rec["degraded"] is False
    assert "consecutive_failures" in rec
    store.canary_health_store.reset()


def test_publish_path_noop_for_not_configured_and_unknown():
    """STATUS_NOT_CONFIGURED and an arbitrary unknown string are both no-ops."""
    store.record_publish_path_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    before = store.get_publish_path_health("blogger")

    store.record_publish_path_verdict("blogger", store.STATUS_NOT_CONFIGURED)
    after_not_configured = store.get_publish_path_health("blogger")
    assert after_not_configured == before

    store.record_publish_path_verdict("blogger", "totally-unknown-status")
    after_unknown = store.get_publish_path_health("blogger")
    assert after_unknown == before
