"""Cross-process regression test for ``atomic_write_json``'s per-PID temp name.

Before the per-PID temp suffix, two processes writing the same ``path`` via
``atomic_write_json`` collided on a shared ``<path>.tmp``: one process's
``replace()`` renamed the temp away and the other's ``replace()`` then raised
``FileNotFoundError``. This test spawns real subprocesses writing the same
target concurrently and asserts none crash, the final file is valid JSON, and
no orphan temp is left behind. It fails on the pre-fix fixed-``.tmp`` naming.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from backlink_publisher._util import io as _io

# src/ root, forwarded to children so they import this same source tree.
_SRC_DIR = str(Path(_io.__file__).resolve().parents[2])

_CHILD = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from backlink_publisher._util.io import atomic_write_json

    target = Path(sys.argv[1])
    writer_id = sys.argv[2]
    iters = int(sys.argv[3])
    for i in range(iters):
        atomic_write_json(target, {"writer": writer_id, "i": i})
    """
)


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = _SRC_DIR + (os.pathsep + existing if existing else "")
    return env


def test_atomic_write_json_concurrent_writers_no_crash(tmp_path):
    target = tmp_path / "shared.json"
    n_proc, iters = 6, 60
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _CHILD, str(target), str(w), str(iters)],
            env=_child_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for w in range(n_proc)
    ]
    for proc in procs:
        out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, (
            f"writer crashed (returncode={proc.returncode})\n--- stderr ---\n{err}"
        )

    # Final file is a complete, valid JSON object from exactly one writer —
    # no torn write, no interleaved bytes.
    data = json.loads(target.read_text(encoding="utf-8"))
    assert set(data) == {"writer", "i"}
    # Every per-PID temp was renamed away (or cleaned on error): no orphans.
    assert list(tmp_path.glob("*.tmp")) == []
