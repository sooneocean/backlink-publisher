"""Regression guard: every CLI dispatched by webui_app/helpers.py through
``python -m <module>`` must actually produce output. Without this guard the
WebUI silently surfaces hardcoded fallback errors (e.g. "生成失败，没有输出",
"验证失败，请检查链接数量是否在 6-8 个之间") when a CLI module is missing its
``if __name__ == "__main__":`` guard or, in the case of a package, an
executable ``__main__.py``.

The bug class showed up twice in the same session (2026-05-20):
- ``plan_backlinks`` decomposed into a package with an intentionally empty
  ``__main__.py`` -> webui /ce:generate silently failed.
- ``validate_backlinks`` / ``publish_backlinks`` / ``report_anchors`` had
  ``def main()`` but no module-level guard -> webui /ce:validate and
  /ce:publish silently failed.

This test pins the contract so future refactors can't reintroduce the same
silent failure.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Mirrors webui_app/helpers.py:_CLI_MODULES. Kept in sync manually rather
# than imported because importing webui_app pulls in Flask + a bunch of
# state-mutating module-level singletons we don't want in this unit test.
_CLI_MODULES = {
    "publish-backlinks": "backlink_publisher.cli.publish_backlinks",
    "plan-backlinks": "backlink_publisher.cli.plan_backlinks",
    "validate-backlinks": "backlink_publisher.cli.validate_backlinks",
    "footprint": "backlink_publisher.cli.footprint",
    "report-anchors": "backlink_publisher.cli.report_anchors",
    "equity-ledger": "backlink_publisher.cli.equity_ledger",
}

# CLI/``python -m``-only verbs intentionally NOT wired into the WebUI
# ``_CLI_MODULES`` dispatch map, but which still need the ``__main__`` guard so
# the console-script and ``python -m`` entrypoints work. ``audit-state`` is
# read-only CLI-only in v1 (Plan 2026-05-26-001).
_CLI_ONLY_MODULES = {
    "audit-state": "backlink_publisher.cli.audit_state",
    "preflight-targets": "backlink_publisher.cli.preflight_targets",
}

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _REPO_ROOT / "src"


@pytest.mark.parametrize(
    "cli_name,module_path",
    sorted(_CLI_MODULES.items()),
    ids=sorted(_CLI_MODULES.keys()),
)
def test_python_m_help_produces_output(cli_name: str, module_path: str) -> None:
    """``python -m <module_path> --help`` must print a usage banner.

    Catches:
    - empty ``__main__.py`` in a package (silent exit 0, no output)
    - module defining ``def main()`` without a ``if __name__ == "__main__":``
      guard (import-only execution, no output)
    - typo in ``_CLI_MODULES`` pointing at a non-existent module
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_SRC_DIR) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    result = subprocess.run(
        [sys.executable, "-m", module_path, "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_REPO_ROOT),
        timeout=30,
    )
    combined = result.stdout + result.stderr
    assert combined.strip(), (
        f"`python -m {module_path} --help` produced no output (exit "
        f"{result.returncode}). This means the CLI entry-point is broken — "
        f"webui's run_pipe() will silently fail. Add a `if __name__ == "
        f'"__main__": main()` guard, or for packages, populate __main__.py.'
    )
    assert "usage:" in combined.lower() or "options:" in combined.lower(), (
        f"`python -m {module_path} --help` output didn't look like an "
        f"argparse banner; got: {combined[:200]!r}"
    )


@pytest.mark.parametrize(
    "cli_name,module_path",
    sorted(_CLI_ONLY_MODULES.items()),
    ids=sorted(_CLI_ONLY_MODULES.keys()),
)
def test_cli_only_python_m_help_produces_output(
    cli_name: str, module_path: str
) -> None:
    """Same ``__main__``-guard contract for CLI-only verbs (e.g. audit-state)
    that are not WebUI-dispatched. The console-script and ``python -m`` paths
    still rely on the module-level guard producing a usage banner.
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_SRC_DIR) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    result = subprocess.run(
        [sys.executable, "-m", module_path, "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_REPO_ROOT),
        timeout=30,
    )
    combined = result.stdout + result.stderr
    assert combined.strip(), (
        f"`python -m {module_path} --help` produced no output (exit "
        f"{result.returncode}). Add a `if __name__ == \"__main__\": main()` "
        f"guard."
    )
    assert "usage:" in combined.lower() or "options:" in combined.lower(), (
        f"`python -m {module_path} --help` output didn't look like an "
        f"argparse banner; got: {combined[:200]!r}"
    )
