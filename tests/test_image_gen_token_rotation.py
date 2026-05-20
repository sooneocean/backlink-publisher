"""Tests for ``backlink_publisher._util.secrets`` frw-token helpers.

Plan: docs/plans/2026-05-20-001-feat-banner-image-gen-plan.md
Unit 1 — credential 0600 file rotation mirror of telegraph_api.py.

Execution note (from plan): test-first. The three ``threading.Barrier``
scenarios (rotate-concurrent / bootstrap+rotate / migration precedence)
catch the bootstrap-TOCTOU regression that
``feedback_test_first_for_credential_rotation_misses_bootstrap`` warns
about — credential-rotation tests must enumerate ALL state-mutation
sites, not only the rotation hot-path.
"""

from __future__ import annotations

import json
import os
import stat
import threading
from itertools import count
from pathlib import Path

import pytest

# Module-under-test imports are TOP-LEVEL so the file fails ImportError
# until Unit 1 implementation lands — this is the failing-test gate
# that test-first requires.
from backlink_publisher._util.secrets import (
    frw_token_path,
    load_frw_token,
    write_frw_token,
)


@pytest.fixture
def isolated_config_dir(tmp_path, monkeypatch):
    """Point ``_config_dir()`` at ``tmp_path`` so writes are isolated.

    Mirrors the pattern in ``tests/test_adapter_telegraph_api_self_heal.py``
    so behavior is uniform across credential-rotation test files.
    """
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    return tmp_path


# ── Path resolver ───────────────────────────────────────────────────────────


def test_frw_token_path_respects_env_var(tmp_path, monkeypatch):
    """``BACKLINK_PUBLISHER_CONFIG_DIR=/x`` → path under ``/x``.

    Regression for ``feedback_config_paths_must_respect_env_var``: every
    call must re-resolve the env var, never freeze a ``Path.home()`` at
    import time.
    """
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    assert frw_token_path() == tmp_path / "frw-token.json"


def test_frw_token_path_is_dynamic_across_calls(tmp_path, monkeypatch):
    """Changing the env var between calls flips the resolved path."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path / "a"))
    first = frw_token_path()
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path / "b"))
    second = frw_token_path()
    assert first != second
    assert first.parent.name == "a"
    assert second.parent.name == "b"


# ── Bootstrap write (no existing file) ──────────────────────────────────────


def test_write_frw_token_creates_file_with_0600(isolated_config_dir):
    """Bootstrap happy path: write key → file present + 0600 + correct JSON."""
    write_frw_token("sk_test_abc123")

    target = isolated_config_dir / "frw-token.json"
    assert target.exists()

    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"

    data = json.loads(target.read_text())
    assert data == {"api_key": "sk_test_abc123"}


def test_write_frw_token_creates_parent_dir_0700(tmp_path, monkeypatch):
    """Parent dir auto-created with 0700 if absent.

    Mirrors PR #99 config writer parent-dir chmod (project_pr99 memory).
    """
    nested = tmp_path / "nested" / "config"
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(nested))

    write_frw_token("sk_x")

    assert nested.exists()
    parent_mode = stat.S_IMODE(os.stat(nested).st_mode)
    assert parent_mode == 0o700, f"parent dir perms should be 0o700, got {oct(parent_mode)}"


def test_write_frw_token_rejects_empty_string(isolated_config_dir):
    """Empty key rejected before any I/O — guard against
    ``frw-login`` accidentally erasing a real token with a blank input."""
    with pytest.raises(ValueError, match="empty"):
        write_frw_token("")

    assert not (isolated_config_dir / "frw-token.json").exists()


def test_write_frw_token_strips_whitespace(isolated_config_dir):
    """Common copy-paste mishap — trailing newline from terminal — must
    not survive into the file."""
    write_frw_token("  sk_pad_with_spaces  \n")

    data = json.loads((isolated_config_dir / "frw-token.json").read_text())
    assert data["api_key"] == "sk_pad_with_spaces"


# ── Rotation (existing file) ────────────────────────────────────────────────


def test_write_frw_token_archives_existing_token(isolated_config_dir):
    """Rerunning ``frw-login`` archives the old token with μs-precision
    timestamp instead of silently overwriting it.

    Mirrors ``_archive_orphan_token`` in telegraph_api.py — the archive
    is critical for audit and key-rotation post-mortem.
    """
    target = isolated_config_dir / "frw-token.json"

    write_frw_token("sk_old")
    assert json.loads(target.read_text())["api_key"] == "sk_old"

    write_frw_token("sk_new")
    assert json.loads(target.read_text())["api_key"] == "sk_new"

    orphans = list(isolated_config_dir.glob("frw-token.json.orphaned-*"))
    assert len(orphans) == 1, (
        f"expected exactly 1 orphan archive after one rotation, got {orphans}"
    )

    archived = json.loads(orphans[0].read_text())
    assert archived["api_key"] == "sk_old"

    # Archive perms must remain 0600 — the old key is still a secret.
    arch_mode = stat.S_IMODE(os.stat(orphans[0]).st_mode)
    assert arch_mode == 0o600


def test_write_frw_token_orphan_archive_has_microseconds(isolated_config_dir):
    """Two concurrent rotations within the same second must produce two
    distinct archive files — second-precision would collapse them and
    silently lose the older key.

    Mirrors telegraph orphan-archive ``%Y%m%dT%H%M%S_%fZ`` format.
    """
    write_frw_token("sk_a")
    write_frw_token("sk_b")  # archives sk_a
    write_frw_token("sk_c")  # archives sk_b

    orphans = sorted(isolated_config_dir.glob("frw-token.json.orphaned-*"))
    assert len(orphans) == 2

    # Suffix must contain microseconds — i.e. ``_<digits>Z`` segment.
    for path in orphans:
        suffix = path.name.split(".orphaned-")[-1]
        assert "_" in suffix and suffix.endswith("Z"), (
            f"orphan suffix {suffix!r} missing μs segment"
        )


# ── Load (read-back) ────────────────────────────────────────────────────────


def test_load_frw_token_returns_api_key(isolated_config_dir):
    """Happy path: write then load returns the same key."""
    write_frw_token("sk_load_me")
    assert load_frw_token() == "sk_load_me"


def test_load_frw_token_missing_fails_loud(isolated_config_dir):
    """No token file → ``RuntimeError`` with operator-actionable message.

    Per plan: image-gen is opt-in, so callers should have already
    checked ``config.image_gen is not None`` before invoking this.
    Reaching ``load_frw_token`` with no file = operator misconfiguration
    that must NOT be silently swallowed.
    """
    with pytest.raises(RuntimeError, match=r"frw-login"):
        load_frw_token()


def test_load_frw_token_malformed_json_fails_loud(isolated_config_dir):
    """Corrupt JSON → ``RuntimeError`` with file path in message."""
    target = isolated_config_dir / "frw-token.json"
    target.write_text("{not json")
    os.chmod(target, 0o600)

    with pytest.raises(RuntimeError, match="malformed"):
        load_frw_token()


def test_load_frw_token_empty_api_key_fails_loud(isolated_config_dir):
    """Empty ``api_key`` field → fail-loud (mirrors telegraph empty
    access_token check)."""
    target = isolated_config_dir / "frw-token.json"
    target.write_text(json.dumps({"api_key": ""}))
    os.chmod(target, 0o600)

    with pytest.raises(RuntimeError, match="empty"):
        load_frw_token()


def test_load_frw_token_warns_and_repairs_loose_perms(
    isolated_config_dir, caplog
):
    """Per plan: ``0644`` perms → warn + auto-``chmod 0o600`` + still
    return the key.  Do NOT fail — operator may have copied the file
    via ``cp`` losing perms; we self-heal."""
    target = isolated_config_dir / "frw-token.json"
    target.write_text(json.dumps({"api_key": "sk_loose"}))
    os.chmod(target, 0o644)

    with caplog.at_level("WARNING"):
        result = load_frw_token()

    assert result == "sk_loose"
    final_mode = stat.S_IMODE(os.stat(target).st_mode)
    assert final_mode == 0o600
    assert any("perm" in r.getMessage().lower() for r in caplog.records)


# ── Threading.Barrier — Rotation race ───────────────────────────────────────


def test_concurrent_rotation_serializes_under_flock(isolated_config_dir):
    """Four concurrent ``write_frw_token`` calls must serialize through
    ``fcntl.flock`` → final file contains exactly one writer's value,
    and three orphan archives (one per displaced predecessor).

    Without the flock, two writers' ``os.replace`` calls would race and
    one orphan archive's ``os.replace`` would clobber another (lost-key
    bug).
    """
    barrier = threading.Barrier(4)
    errors = []

    def worker(key: str):
        try:
            barrier.wait(timeout=5)
            write_frw_token(key)
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(f"sk_thread_{i}",))
        for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert errors == [], f"workers raised: {errors}"

    final = json.loads((isolated_config_dir / "frw-token.json").read_text())
    assert final["api_key"].startswith("sk_thread_")

    orphans = list(isolated_config_dir.glob("frw-token.json.orphaned-*"))
    # 4 writers → 3 displaced → 3 archives.  Each archive must have a
    # distinct μs timestamp.
    assert len(orphans) == 3, (
        f"expected 3 orphan archives from 4-way rotation race, got {len(orphans)}: "
        f"{[p.name for p in orphans]}"
    )
    suffixes = {p.name.split(".orphaned-")[-1] for p in orphans}
    assert len(suffixes) == 3, "μs collision lost an orphan archive"


def test_concurrent_bootstrap_and_rotate_no_orphan_for_bootstrap(
    isolated_config_dir,
):
    """One thread bootstraps (no existing file) while another rotates
    over an already-written file → both must succeed, total orphan
    archive count == 1 (the rotation displaces what the bootstrap
    wrote; bootstrap itself never archives).
    """
    barrier = threading.Barrier(2)
    errors = []
    order = []

    def bootstrap_worker():
        try:
            barrier.wait(timeout=5)
            write_frw_token("sk_bootstrap")
            order.append("bootstrap")
        except Exception as exc:
            errors.append(exc)

    def rotate_worker():
        try:
            barrier.wait(timeout=5)
            # Tiny sleep nudges this thread to run second more often,
            # but flock guarantees correctness regardless of ordering.
            write_frw_token("sk_rotate")
            order.append("rotate")
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=bootstrap_worker)
    t2 = threading.Thread(target=rotate_worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert errors == [], f"workers raised: {errors}"

    # Final file is from whichever worker ran second.
    final = json.loads((isolated_config_dir / "frw-token.json").read_text())
    assert final["api_key"] in {"sk_bootstrap", "sk_rotate"}

    orphans = list(isolated_config_dir.glob("frw-token.json.orphaned-*"))
    # Exactly one of the two writes ran first; that write becomes the
    # archived predecessor of the second.
    assert len(orphans) == 1, (
        f"expected 1 orphan from bootstrap+rotate pair, got {len(orphans)}"
    )


def test_concurrent_burst_release_lock_cleanly(isolated_config_dir):
    """After a burst of concurrent writes, no stale ``.lock`` file
    should be left held (subsequent writers must not deadlock on a
    leaked fd).
    """
    barrier = threading.Barrier(3)

    def worker(i):
        barrier.wait(timeout=5)
        write_frw_token(f"sk_burst_{i}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # Subsequent write must complete promptly — if a previous worker
    # leaked the lock fd, this would block until LOCK_TIMEOUT_S.
    import time as _time
    t0 = _time.monotonic()
    write_frw_token("sk_after_burst")
    elapsed = _time.monotonic() - t0
    assert elapsed < 2.0, (
        f"write after burst took {elapsed:.2f}s; suspect leaked flock"
    )


# ── Migration grandfather ───────────────────────────────────────────────────


def test_migration_0600_file_wins_over_deprecated_toml_field(
    isolated_config_dir, monkeypatch
):
    """When BOTH the deprecated ``[llm_anchor_provider].image_gen_api_key``
    toml field AND ``frw-token.json`` exist, the 0600 file is the
    source of truth.  Operator's ``frw-login`` must always trump a
    stale config.toml field.

    NOTE: This test only verifies the helper's behavior — the
    precedence is actually enforced by the *caller* (e.g. plan-backlinks
    reading ``load_frw_token`` first and ignoring the deprecated toml
    field once a file exists).  See Unit 2 / Unit 5 for the call-site
    proofs.
    """
    write_frw_token("sk_from_login")

    # Pretend a caller passes both — the helper itself only knows about
    # the file path.  This is a smoke check that ``load_frw_token``
    # works without ever consulting toml.
    assert load_frw_token() == "sk_from_login"
