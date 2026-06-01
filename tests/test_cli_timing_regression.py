"""Wave 1 — CLI cold-import timing regression gate (T1.1 refocus).

This test fails if any tracked module's ``import`` time grows beyond
``REGRESSION_MULTIPLIER`` times the stored baseline median. The baseline
lives at ``tests/baselines/cli_timing.json`` (schema version 1).

Why this exists: the existing ``test_performance_profiling.py`` covers the
``--profile`` cProfile hook (writes ``.prof`` files). It does NOT cover
import-time regressions, which is the more common operator-visible
slowdown (every CLI invocation pays cold-import cost). Plan 2026-05-14-008
shipped the content-fetch gate's perf observability, not a regression gate.

The fresh-subprocess measurement is intentionally simple — no per-module
mocking, no fixture scaffolding. A regression here is one a developer can
reproduce with ``python -X importtime -c 'import X'``.

Refresh the baseline (rare — only after an intentional import-time change):

    python tests/scripts/refresh_cli_timing_baseline.py
    git add tests/baselines/cli_timing.json && git commit
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
from pathlib import Path

import pytest

BASELINE_PATH = Path(__file__).parent / "baselines" / "cli_timing.json"
SCHEMA_VERSION = 1
REGRESSION_MULTIPLIER = 2.0  # 100% regression → fail (loose, absorbs CI noise)
SAMPLE_COUNT = 3  # outer subprocess runs per module; total = SAMPLE_COUNT per measurement
TIMEOUT_S = 15

# Modules whose cold-import time we want to track. Add a new entry when a new
# entrypoint is added (or when an existing entry's import cost grows enough
# to matter for CLI startup UX).
TRACKED_MODULES = [
    "backlink_publisher",
    "backlink_publisher.cli.plan_backlinks",
    "backlink_publisher.cli.publish_backlinks",
    "backlink_publisher._util.io",
    "backlink_publisher.config",
]


def _measure_ms(module_name: str) -> dict:
    """Time ``import <module_name>`` in SAMPLE_COUNT fresh subprocesses.

    Returns ``{median_ms, p95_ms, samples}``. A fresh subprocess is the
    only way to measure cold-import cost reliably; in-process measurement
    conflates it with the existing module's sys.modules cache.
    """
    code = (
        "import time, json\n"
        f"t0 = time.perf_counter()\n"
        f"__import__({module_name!r})\n"
        "t1 = time.perf_counter()\n"
        "print(json.dumps((t1 - t0) * 1000))\n"
    )
    times: list[float] = []
    for _ in range(SAMPLE_COUNT):
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, check=True, timeout=TIMEOUT_S,
        )
        times.append(float(json.loads(result.stdout.strip())))
    times.sort()
    return {
        "median_ms": round(times[len(times) // 2], 2),
        "p95_ms": round(times[-1], 2),
        "samples": len(times),
    }


def _load_baseline() -> dict:
    if not BASELINE_PATH.exists():
        pytest.fail(
            f"Timing baseline missing at {BASELINE_PATH}. "
            f"Run `python tests/scripts/refresh_cli_timing_baseline.py` "
            f"to capture current values, then commit the JSON."
        )
    data = json.loads(BASELINE_PATH.read_text())
    if data.get("schema_version") != SCHEMA_VERSION:
        pytest.fail(
            f"Timing baseline schema_version mismatch: "
            f"file has {data.get('schema_version')}, test expects {SCHEMA_VERSION}. "
            f"Either bump the test or refresh the baseline."
        )
    return data


def test_import_times_within_regression_threshold(capsys):
    """Every tracked module imports within REGRESSION_MULTIPLIER× of its baseline median."""
    baseline = _load_baseline()
    failures: list[str] = []
    measured: dict[str, dict] = {}
    for module_name in TRACKED_MODULES:
        stats = _measure_ms(module_name)
        measured[module_name] = stats
        baseline_entry = baseline.get("imports", {}).get(module_name, {})
        baseline_median = baseline_entry.get("median_ms")
        if baseline_median is None:
            failures.append(
                f"{module_name}: no baseline median_ms (refresh the baseline)"
            )
            continue
        threshold = baseline_median * REGRESSION_MULTIPLIER
        if stats["median_ms"] > threshold:
            failures.append(
                f"{module_name}: median {stats['median_ms']}ms "
                f"exceeds {REGRESSION_MULTIPLIER}× baseline {baseline_median}ms "
                f"(threshold {threshold:.2f}ms)"
            )
    if failures:
        # Print schema-ready baseline for refresh convenience.
        # capsys is used so this prints even with default pytest capture.
        with capsys.disabled():
            print("\n=== Suggested new baseline (refresh) ===")
            print(json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "imports": measured,
                },
                indent=2,
            ))
            print("=== Failures ===")
            for f in failures:
                print(f"  - {f}")
        pytest.fail("\n".join(failures))
