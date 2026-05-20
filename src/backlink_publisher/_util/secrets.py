"""0600 token-file helpers for the FRW image-gen API key.

Plan: docs/plans/2026-05-20-001-feat-banner-image-gen-plan.md
Unit 1 — mirrors the 6-component credential-rotation pattern in
``publishing/adapters/telegraph_api.py`` (see
``reference_telegraph_adapter_credential_rotation_pattern``).

The 6 components, in order:
  1. Path resolver — re-resolves ``BACKLINK_PUBLISHER_CONFIG_DIR`` on
     every call (no frozen ``Path.home()``)
  2. Fail-loud load — missing / malformed / empty key raises
     ``RuntimeError`` with operator-actionable message
  3. Atomic write — tmp → chmod 0600 → ``os.replace`` (POSIX-atomic)
  4. Flock with jitter — ``fcntl.LOCK_EX | LOCK_NB`` poll +
     ``random.uniform(0.05, 0.15)`` jitter + 10 s deadline
  5. Orphan archive μs — ``%Y%m%dT%H%M%S_%fZ`` timestamp so two
     concurrent rotations cannot collide
  6. Rotate-under-lock — archive happens INSIDE the flock so two
     archives never collide on the same destination

Deliberately NOT abstracted with telegraph_api.py's equivalents — the
two are similar but diverge in fail-mode taxonomy (DependencyError vs
RuntimeError), legacy migration (telegraph migrates the spike-era
filename; frw has none), and recovery semantics (telegraph self-heals
on 401; frw expects operator to rerun ``frw-login``).  Lift to a
shared abstraction only after a third token file appears (see plan's
"Patterns to follow" note).
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import random
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

#: Lock acquisition timeout for the rotate-write sequence.  Above this,
#: assume a stuck peer and abort rather than block forever.
_LOCK_TIMEOUT_S = 10


# ── 1. Path resolver ────────────────────────────────────────────────────────


def frw_token_path() -> Path:
    """Resolve the FRW token file path, re-reading the env var every call.

    ``BACKLINK_PUBLISHER_CONFIG_DIR`` is honored so tests, CI, and
    sandboxed operator runs don't pollute the real ``~/.config``.
    Per ``feedback_config_paths_must_respect_env_var`` this MUST be a
    function — module-level ``Path.home() / ...`` would freeze at import
    time and the conftest fixture's monkeypatch would never reach it.
    """
    # Lazy import: avoid an import cycle between ``_util`` (low-level)
    # and ``config`` (high-level) at module load time.
    from backlink_publisher import config as _cfg
    return _cfg._config_dir() / "frw-token.json"


def _lock_path(token_path: Path) -> Path:
    """``<token-path>.lock`` sibling used by the advisory flock."""
    return token_path.with_suffix(token_path.suffix + ".lock")


# ── 2. Fail-loud load ───────────────────────────────────────────────────────


def load_frw_token() -> str:
    """Return the FRW api_key from the 0600 token file.

    Raises:
        RuntimeError: file missing / malformed / empty key.  The
            message names ``frw-login`` so the operator knows the fix.

    Loose perms (anything other than 0o600) emit a WARN and the file
    is re-chmod-ed to 0o600 — this matches the plan's "warn don't
    fail" decision for the perms-only case, since a ``cp``-induced
    0o644 is more common than malicious tampering.
    """
    path = frw_token_path()

    if not path.exists():
        raise RuntimeError(
            f"FRW token not found at {path}\n"
            "Run `frw-login` to create one (the API key lives in a 0600 "
            "JSON file, never in config.toml)."
        )

    mode = os.stat(path).st_mode & 0o777
    if mode != 0o600:
        log.warning(
            "frw_token_loose_perms path=%s mode=%s — auto-chmod to 0o600",
            path,
            oct(mode),
        )
        os.chmod(path, 0o600)

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(
            f"frw-token.json malformed at {path}: {exc}\n"
            "Run `frw-login` to rewrite it."
        ) from None

    key = data.get("api_key") if isinstance(data, dict) else None
    if not key:
        raise RuntimeError(
            f"frw-token.json missing or empty 'api_key' field at {path}\n"
            "Run `frw-login` to rewrite it."
        )
    return key


# ── 3 + 4 + 5 + 6. Write under flock with μs-precise orphan archive ─────────


def write_frw_token(api_key: str) -> None:
    """Atomically write ``api_key`` to the 0600 token file.

    Handles bootstrap (no existing file) and rotation (file present —
    old content is archived under a μs-precise suffix) uniformly under
    a single ``fcntl.flock`` so concurrent ``frw-login`` invocations
    serialize cleanly with no lost keys.

    Raises:
        ValueError: ``api_key`` is empty or whitespace-only.

    Args:
        api_key: The operator's FRW api_key. Leading / trailing
            whitespace is stripped (common copy-paste artifact).
    """
    stripped = api_key.strip()
    if not stripped:
        raise ValueError(
            "api_key is empty (or whitespace-only); refusing to write."
        )

    target = frw_token_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Parent dir 0o700: per plan + PR #99 chmod pattern.  Other writers
    # may have left it more permissive — tighten it.
    try:
        current_parent_mode = os.stat(target.parent).st_mode & 0o777
        if current_parent_mode != 0o700:
            os.chmod(target.parent, 0o700)
    except OSError:
        # Best-effort — don't die if we can't tighten perms on someone
        # else's umbrella dir.
        log.warning(
            "frw_token_parent_chmod_failed path=%s", target.parent,
        )

    with _token_lock(target):
        # Inside the flock: archive any existing file first, then
        # write the new one atomically.  Doing archive-before-write
        # under the lock means two concurrent rotations cannot collide
        # on the same archive path.
        if target.exists():
            _archive_orphan_token(target)

        _write_token_atomic(target, {"api_key": stripped})


def _write_token_atomic(path: Path, data: dict[str, str]) -> None:
    """Write ``data`` to ``path`` atomically with 0o600 perms.

    tmp file → chmod 0o600 → ``os.replace`` (POSIX-atomic).  Crash at
    any step never leaves the destination in a half-written state.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    # Belt-and-suspenders: confirm perms survived rename (POSIX
    # ``os.replace`` preserves source mode, but a future cross-FS move
    # could lose it).
    mode = os.stat(path).st_mode & 0o777
    if mode != 0o600:
        os.chmod(path, 0o600)


@contextmanager
def _token_lock(token_path: Path) -> Iterator[None]:
    """Advisory file lock around the rotate-write sequence.

    Two concurrent ``frw-login`` invocations would otherwise race the
    orphan archive's ``os.replace``, silently losing one previous
    api_key with no audit trail.  The flock serializes both at the
    OS layer with no cooperation needed between the processes.

    Lock fd is created if absent and released cleanly even on
    exception.  Polling uses jittered sleep to avoid thundering-herd
    wakeups when multiple peers hit the deadline simultaneously.
    """
    lock_path = _lock_path(token_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + _LOCK_TIMEOUT_S
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"could not acquire frw-token lock after "
                        f"{_LOCK_TIMEOUT_S}s: {lock_path}\n"
                        "Another frw-login may be stuck. Inspect with "
                        "`lsof <path>` and retry."
                    )
                time.sleep(random.uniform(0.05, 0.15))
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _archive_orphan_token(token_path: Path) -> Path | None:
    """Move ``token_path`` to ``<path>.orphaned-<UTC iso μs>``.

    Microsecond precision in the suffix is load-bearing: two concurrent
    rotations within the same second would otherwise pick identical
    archive paths, and the second ``os.replace`` would silently
    overwrite the first archive — losing the orphaned api_key.
    """
    if not token_path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    archive = token_path.with_suffix(token_path.suffix + f".orphaned-{stamp}")
    os.replace(token_path, archive)
    os.chmod(archive, 0o600)
    return archive
