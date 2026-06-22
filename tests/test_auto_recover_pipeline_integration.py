"""Offline integration guard for the auto-recover Phase-4 data bridge.

auto-recover's Phase 4 composes three CLIs as subprocesses:

    replan seed  →  plan-backlinks  →  quality-gate

Each link was independently broken when the closed loop shipped under broken CI
(quality_gate.py crashed on import-time-undefined name; replan seeds omitted the
``main_domain`` plan-backlinks requires), and ``_pipe_through_cli`` swallows a
non-zero subprocess exit — so a dead stage degraded silently. This test exercises
the real CLIs end-to-end, deterministically and offline:

* plan-backlinks' network target-reachability probe is skipped with
  ``--no-fetch-verify`` (the silent-dead-phase bugs live in the data contract,
  not the network), so generation is hermetic.
* It asserts the load-bearing contract: a replan-dead seed is accepted by
  plan-backlinks, which generates a non-empty article body, and quality-gate
  then runs on that real generated article without crashing.

It deliberately does NOT assert whether quality-gate passes or blocks the
article — the default-threshold calibration between plan-backlinks' generated
anchor density and quality-gate's ``--max-density`` is a separate product
decision, and pinning the current outcome here would re-encode a calibration
choice as a contract (the mistake that hid the missing-main_domain bug).
"""

from __future__ import annotations

import json
import subprocess
import sys


def _pipe(rows, module, extra=None):
    """Run ``python -m <module>`` as a subprocess, JSONL in → JSONL out."""
    proc = subprocess.run(
        [sys.executable, "-m", module] + (extra or []),
        input="\n".join(json.dumps(r) for r in rows),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert (
        proc.returncode == 0
    ), f"{module} exited {proc.returncode}: {proc.stderr[-500:]}"
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def test_replan_to_plan_to_quality_gate_bridge(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    from backlink_publisher.cli.replan_dead import _build_seed

    seed = _build_seed(
        live_url="https://medium.com/dead",
        target_url="https://example.com/article/one",
        host=None,
        platform="medium",
        language="en",
        url_mode="A",
        publish_mode="draft",
    )

    # Phase 4a: plan-backlinks must ACCEPT the replan seed and generate content.
    # (Regression: a seed without main_domain was rejected here and silently
    # dropped; a crashing quality-gate downstream was likewise swallowed.)
    planned = _pipe(
        [seed],
        "backlink_publisher.cli.plan_backlinks",
        ["--no-fetch-verify"],
    )
    assert len(planned) == 1, "replan seed must produce exactly one planned article"
    body = planned[0].get("content_markdown") or planned[0].get(
        "article_content_markdown"
    )
    assert body, "plan-backlinks must generate a non-empty article body"

    # Phase 4b: the generated article must SURVIVE the quality gate, so the
    # closed loop actually yields a publishable row. Regression: with the
    # default 5% gate counting every markdown link (incl. outbound citations)
    # and a whitespace word count, plan-backlinks' own output (~5.6% all-link
    # density, far higher for CJK) was blocked 100% — the loop produced zero
    # output. quality-gate now counts only self-site links over a CJK-aware
    # word count, so the planner's standard article passes.
    gated = _pipe(planned, "backlink_publisher.cli.quality_gate")
    assert len(gated) == 1, "the generated article must survive the quality gate"
