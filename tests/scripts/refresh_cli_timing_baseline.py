"""Refresh ``tests/baselines/cli_timing.json`` from current measurements.

Run from the worktree root:

    python tests/scripts/refresh_cli_timing_baseline.py

The script writes a schema-valid JSON file. Review the printed measurements
before committing — a big jump from the prior baseline means a real import
slowdown, not noise.
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Mirror the constants in tests/test_cli_timing_regression.py
SAMPLE_COUNT = 5
TIMEOUT_S = 15
SCHEMA_VERSION = 1
TRACKED_MODULES = [
    "backlink_publisher",
    "backlink_publisher.cli.plan_backlinks",
    "backlink_publisher.cli.publish_backlinks",
    "backlink_publisher._util.io",
    "backlink_publisher.config",
]
BASELINE_PATH = (
    Path(__file__).parent.parent / "baselines" / "cli_timing.json"
)


def _measure_ms(module_name: str) -> dict:
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


def main() -> int:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    measurements: dict[str, dict] = {}
    for module_name in TRACKED_MODULES:
        stats = _measure_ms(module_name)
        measurements[module_name] = stats
        print(
            f"  {module_name}: median {stats['median_ms']}ms "
            f"(p95 {stats['p95_ms']}ms, n={stats['samples']})"
        )
    baseline = {
        "schema_version": SCHEMA_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "imports": measurements,
    }
    BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n")
    print(f"\nWrote {BASELINE_PATH}")
    print("Next: `git add tests/baselines/cli_timing.json && git commit`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
