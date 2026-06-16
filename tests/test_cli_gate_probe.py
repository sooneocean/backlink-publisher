"""Unit 2 — gate-probe dispatcher CLI (plan 2026-06-01-005).

Exercises the dispatcher routing + exit posture. The autouse conftest fixtures
sandbox the config dir (so no money pages are configured) and block sockets, so
``--gate g2`` here resolves to an empty money-page set → INCONCLUSIVE, exit 0,
without any network.
"""

from __future__ import annotations

import json

import pytest

from backlink_publisher.cli import gate_probe
from backlink_publisher.gates import verdict as gv


def _run(argv, capsys):
    gate_probe.main(argv)
    out = capsys.readouterr()
    return out


def test_g2_empty_config_is_inconclusive_exit_zero(capsys):
    # No SystemExit → exit 0 (read-only verb completes even with nothing to probe).
    gate_probe.main(["--gate", "g2"])
    out = capsys.readouterr()
    rows = [json.loads(line) for line in out.out.splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["gate"] == "g2"
    assert rows[0]["verdict"] == gv.INCONCLUSIVE
    assert rows[0]["sample_n"] == 0
    assert "no money pages configured" in out.err


def test_missing_gate_is_usage_error_exit_1():
    with pytest.raises(SystemExit) as exc:
        gate_probe.main([])
    assert exc.value.code == 1


def test_unknown_gate_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        gate_probe.main(["--gate", "g9"])
    assert exc.value.code == 1


# --- G5 routing (empty events.db in the isolated sandbox → INCONCLUSIVE) -------
def test_g5_no_published_links_is_inconclusive_exit_zero(capsys):
    gate_probe.main(["--gate", "g5"])
    rows = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert rows[0]["gate"] == "g5"
    assert rows[0]["verdict"] == gv.INCONCLUSIVE
    assert rows[0]["sample_n"] == 0


def test_g5_invalid_saturation_floor_rejected():
    with pytest.raises(SystemExit) as exc:
        gate_probe.main(["--gate", "g5", "--saturation-floor", "2.0"])
    assert exc.value.code == 1


def test_g5_invalid_sample_size_rejected():
    with pytest.raises(SystemExit) as exc:
        gate_probe.main(["--gate", "g5", "--sample-size", "0"])
    assert exc.value.code == 1


# --- G3 routing (no network; the static audit is deterministic) ---------------
def test_g3_calibration_inconclusive_exit_zero(capsys):
    gate_probe.main(["--gate", "g3"])  # no threshold, creds available, no referral
    rows = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert rows[0]["gate"] == "g3"
    assert rows[0]["tier"] == 2
    assert rows[0]["verdict"] == gv.INCONCLUSIVE


def test_g3_majority_strip_kills(capsys):
    gate_probe.main(["--gate", "g3", "--strip-threshold", "0.5"])
    rows = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert rows[0]["verdict"] == gv.KILL


def test_g3_credentials_unavailable_blocks(capsys):
    gate_probe.main(["--gate", "g3", "--strip-threshold", "0.9", "--credentials-unavailable"])
    rows = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert rows[0]["verdict"] == gv.BLOCKED


def test_g3_positive_referral_is_go(capsys):
    gate_probe.main([
        "--gate", "g3", "--strip-threshold", "0.9",
        "--referral-sessions", "12", "--referral-window", "2026-05",
    ])
    rows = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert rows[0]["verdict"] == gv.GO


def test_g3_referral_sessions_without_window_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        gate_probe.main(["--gate", "g3", "--referral-sessions", "5"])
    assert exc.value.code == 1


def test_out_of_range_decay_threshold_rejected():
    with pytest.raises(SystemExit) as exc:
        gate_probe.main(["--gate", "g2", "--decay-threshold", "1.5"])
    assert exc.value.code == 1


def test_stdout_is_pure_jsonl(capsys):
    gate_probe.main(["--gate", "g2"])
    out = capsys.readouterr()
    # Every stdout line must be valid JSON (the pipeline contract: stdout = data).
    for line in out.out.splitlines():
        if line.strip():
            json.loads(line)
