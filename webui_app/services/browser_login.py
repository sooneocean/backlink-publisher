"""Spawn headed-browser login CLIs for credential capture.

Reusable template for binding social platforms (velog today; medium / future
adapters next) whose credential flow lives in a dedicated CLI module under
``backlink_publisher.cli.<platform>_login``.

The helper:

- Spawns ``python -m <module>`` in a new session so the WebUI request thread
  does not block on the headed browser window.
- Tees subprocess stdout+stderr to a log file under the cache dir. Without
  this, ``subprocess.PIPE`` deadlocks the child once Playwright fills the
  ~64KB pipe buffer mid-login.
- Probes briefly to detect immediate crashes (missing Playwright, import
  errors, logger API drift) and returns the last log lines as the error.

To add a new platform binding endpoint:

    from ..services.browser_login import spawn_browser_login
    result = spawn_browser_login("backlink_publisher.cli.<platform>_login")
    if not result.ok:
        return jsonify({"ok": False, "error": result.error,
                        "log_path": str(result.log_path)}), 500
    return jsonify({"ok": True, "log_path": str(result.log_path)})
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from backlink_publisher.config.loader import _cache_dir

# Absolute path so the spawned subprocess finds ``backlink_publisher`` regardless
# of the CWD the caller used to launch the WebUI.  Mirrors the pattern in
# bind_job.py which uses the same derivation.
_SRC_DIR = str(Path(__file__).parent.parent.parent / "src")


def _log_dir() -> Path:
    out = _cache_dir() / "browser-login-logs"
    out.mkdir(parents=True, exist_ok=True)
    return out


@dataclass(frozen=True)
class SpawnResult:
    ok: bool
    error: str | None
    log_path: Path


def spawn_browser_login(module: str, *, probe_seconds: float = 1.5) -> SpawnResult:
    """Spawn ``python -m <module>`` headed; report early-failure status.

    ``ok=True`` means the process survived ``probe_seconds`` — likely now
    showing a headed Chromium window. ``ok=False`` means the process exited
    before that, and ``error`` carries the last ~5 lines of its log so the
    UI can surface the real cause instead of falsely claiming success.

    The subprocess is detached via ``start_new_session=True`` so it outlives
    the Flask request; its output keeps writing to ``log_path`` for the
    operator to ``tail -f``.
    """
    short = module.rsplit(".", 1)[-1]
    log_path = _log_dir() / f"{short}.log"
    log_path.write_bytes(b"")  # truncate prior crash output

    existing_pp = os.environ.get("PYTHONPATH", "")
    pythonpath = _SRC_DIR + (os.pathsep + existing_pp if existing_pp else "")

    fh = log_path.open("ab")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", module],
            env={**os.environ, "PYTHONPATH": pythonpath},
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        fh.close()  # child kept its own dup'd fd via Popen

    try:
        rc = proc.wait(timeout=probe_seconds)
    except subprocess.TimeoutExpired:
        return SpawnResult(ok=True, error=None, log_path=log_path)

    tail = log_path.read_text("utf-8", errors="replace").strip().splitlines()[-5:]
    err = "\n".join(tail) or f"{module} exited rc={rc} with no output"
    return SpawnResult(ok=False, error=err, log_path=log_path)
