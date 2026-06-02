"""Cross-process (two-OS-process) safety tests for the publish circuit breaker.

Plan 2026-05-28-001 Unit 3 persists circuit state in
``publish-circuit-state.json`` guarded by an ``fcntl.LOCK_EX`` flock so that
simultaneous ``publish-backlinks`` runs in *separate OS processes* — two
terminals, or a WebUI-triggered run racing a CLI run — cannot lose updates.

The sibling ``test_reliability_circuit.py::test_concurrent_trip_barrier`` only
spawns two *threads* (a single OS process). A plain ``threading.Lock`` would
pass that test, so it cannot distinguish a real cross-process flock from a
process-local lock. These tests spawn genuine subprocesses and would FAIL if
the critical section were guarded by anything that does not cross the
OS-process boundary.

``subprocess`` (fork+exec+pipes) is used rather than ``multiprocessing`` so the
children are unambiguously independent interpreters and never touch the
socket layer that the test suite disables.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from backlink_publisher.publishing.reliability import circuit

# src/ root of the package under test, forwarded to the child interpreters so
# they import the *same* source tree as this test regardless of their cwd
# (mirrors the editable-install / ``PYTHONPATH=src`` invocation).
_SRC_DIR = str(Path(circuit.__file__).resolve().parents[3])

# Child program: drive the circuit's real RMW critical section from a fresh
# interpreter. Two modes — a lost-update counter and the public trip() API.
_CHILD = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from backlink_publisher.publishing.reliability import circuit

    mode = sys.argv[1]
    config_dir = Path(sys.argv[2])
    state_path = config_dir / circuit._STATE_FILE
    lock_path = config_dir / circuit._LOCK_FILE

    class _Cfg:
        pass
    cfg = _Cfg()
    cfg.config_dir = config_dir

    if mode == "counter":
        iters = int(sys.argv[3])
        for _ in range(iters):
            fd = circuit._acquire_lock(lock_path)
            try:
                try:
                    state = circuit._read_state_unsafe(state_path)
                except Exception:
                    state = {}
                state["counter"] = state.get("counter", 0) + 1
                circuit._write_state_unsafe(state_path, state)
            finally:
                circuit._release_lock(fd)
    elif mode == "trip":
        platform = sys.argv[3]
        iters = int(sys.argv[4])
        for _ in range(iters):
            circuit.trip(platform, cfg)
    """
)


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = _SRC_DIR + (os.pathsep + existing if existing else "")
    return env


def _spawn(*args: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", _CHILD, *args],
        env=_child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_all(procs: list[subprocess.Popen], timeout: float = 120.0) -> None:
    for proc in procs:
        out, err = proc.communicate(timeout=timeout)
        assert proc.returncode == 0, (
            f"child exited {proc.returncode}\n--- stdout ---\n{out}\n--- stderr ---\n{err}"
        )


def test_crossproc_flock_serializes_rmw(tmp_path):
    """N separate OS processes each perform ITERS lock-guarded increments of a
    shared counter. flock-across-RMW => every increment survives
    (counter == N * ITERS). A process-local lock loses updates and the counter
    falls short — that is precisely the failure this test exists to catch.
    """
    n_proc, iters = 5, 50
    procs = [_spawn("counter", str(tmp_path), str(iters)) for _ in range(n_proc)]
    _wait_all(procs)

    state = json.loads((tmp_path / circuit._STATE_FILE).read_text(encoding="utf-8"))
    assert state["counter"] == n_proc * iters


def test_crossproc_trip_distinct_platforms_both_survive(tmp_path):
    """Two OS processes trip two *different* platforms concurrently through the
    public ``trip()`` API. Without cross-process locking the two read-modify-write
    cycles race and one platform's trip is clobbered by the other's blind write;
    with flock both survive in the final state.
    """
    procs = [
        _spawn("trip", str(tmp_path), "medium", "60"),
        _spawn("trip", str(tmp_path), "velog", "60"),
    ]
    _wait_all(procs)

    state = json.loads((tmp_path / circuit._STATE_FILE).read_text(encoding="utf-8"))
    assert state.get("medium", {}).get("tripped") is True
    assert state.get("velog", {}).get("tripped") is True
