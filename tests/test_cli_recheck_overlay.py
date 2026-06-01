"""Unit 3: recheck-overlay CLI verb shell — stdin→stdout contract + exit posture.

The pure logic is covered in test_recheck_overlay.py; these tests exercise the
I/O shell: pure-JSONL stdout, tally on stderr, the exit-code contract, and the
opt-in --fail-on-dead gate.
"""

from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timezone

import pytest

from backlink_publisher._util.errors import DependencyError
from backlink_publisher.cli.recheck_overlay import main
from backlink_publisher.events import EventStore
from backlink_publisher.events.kinds import LINK_RECHECKED
from backlink_publisher.recheck import verdicts

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
TARGET = "https://t.com/p"


@pytest.fixture(autouse=True)
def fresh_dirs(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cache = tmp_path / "cache"
    cfg.mkdir()
    cache.mkdir()
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(cache))


def _seed(verdict=verdicts.HOST_GONE, *, target=TARGET, platform="A", aid=1):
    """Append a link.rechecked event to the default (sandboxed) events.db."""
    EventStore().append(
        LINK_RECHECKED,
        {"verdict": verdict, "platform": platform},
        article_id=aid,
        target_url=target,
        ts_utc=NOW.isoformat(),
    )


def _ledger_line(target=TARGET, live_dofollow=2, platforms=("A", "B")):
    return json.dumps({
        "target_url": target,
        "live_dofollow": live_dofollow,
        "live_dofollow_platforms": list(platforms),
        "liveness": "live",
        "liveness_verified_at": "2026-06-01T00:00:00",
    }) + "\n"


def _run(argv, stdin=""):
    out, err = io.StringIO(), io.StringIO()
    saved = sys.stdout, sys.stderr, sys.stdin
    sys.stdout, sys.stderr, sys.stdin = out, err, io.StringIO(stdin)
    code = 0
    try:
        main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        sys.stdout, sys.stderr, sys.stdin = saved
    return out.getvalue(), err.getvalue(), code


def _rows(out):
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_happy_path_discounts_live_dofollow():
    _seed(verdicts.HOST_GONE, platform="A")
    out, err, code = _run([], stdin=_ledger_line(live_dofollow=2, platforms=("A", "B")))
    assert code == 0
    rows = _rows(out)
    assert len(rows) == 1
    assert rows[0]["live_dofollow"] == 1  # 2 → 1 after discounting the dead link
    assert rows[0]["live_dofollow_platforms"] == ["B"]  # A pruned


def test_stdout_is_pure_jsonl_no_banner_leak():
    _seed(verdicts.HOST_GONE, platform="A")
    out, err, code = _run([], stdin=_ledger_line())
    # Every stdout line must parse as JSON — the banner/tally lives on stderr only.
    for line in out.splitlines():
        if line.strip():
            json.loads(line)
    assert "recheck-overlay:" in err
    assert "dead_seen=1" in err


def test_absent_db_passes_ledger_through_unchanged():
    # No events.db seeded → nothing to discount → row passes through verbatim.
    line = _ledger_line(live_dofollow=2, platforms=("A", "B"))
    out, err, code = _run([], stdin=line)
    assert code == 0
    assert _rows(out) == [json.loads(line)]


def test_empty_stdin_is_advisory_exit_0():
    out, err, code = _run([], stdin="")
    assert code == 0
    assert out.strip() == ""
    assert "empty ledger input" in err


def test_malformed_stdin_exits_2():
    _seed()
    out, err, code = _run([], stdin="{not valid json\n")
    assert code == 2  # read_jsonl strict
    assert out.strip() == ""


def test_unreadable_db_exits_3(monkeypatch):
    def _boom(_store):
        raise DependencyError("recheck-overlay: events.db unreadable: boom")

    monkeypatch.setattr(
        "backlink_publisher.cli.recheck_overlay.build_discount_map", _boom
    )
    out, err, code = _run([], stdin=_ledger_line())
    assert code == 3
    assert out.strip() == ""  # nothing on stdout when the read fails
    assert "unreadable" in err


def test_fail_on_dead_exits_6_after_emitting():
    _seed(verdicts.HOST_GONE, platform="A")
    out, err, code = _run(["--fail-on-dead"], stdin=_ledger_line())
    assert code == 6
    # Gate fires AFTER stdout is written — the discounted ledger is still emitted.
    assert _rows(out)[0]["live_dofollow"] == 1
    assert "DeadBacklinksDetected" in err


def test_no_fail_on_dead_is_advisory_exit_0():
    _seed(verdicts.HOST_GONE, platform="A")
    out, err, code = _run([], stdin=_ledger_line())
    assert code == 0


def test_dofollow_lost_does_not_trip_fail_on_dead():
    # dofollow_lost is advisory degradation, not death → --fail-on-dead stays 0.
    _seed(verdicts.DOFOLLOW_LOST, platform="A")
    out, err, code = _run(["--fail-on-dead"], stdin=_ledger_line())
    assert code == 0
    assert _rows(out)[0]["live_dofollow"] == 1  # still discounted


def test_unmatched_discount_warns_loudly():
    # A dead verdict for a target absent from the ledger input = a masked dead
    # link still counting as live equity → must be loud (not just a tally int).
    _seed(verdicts.HOST_GONE, target="https://elsewhere.site/x", platform="A")
    out, err, code = _run([], stdin=_ledger_line(target=TARGET))
    assert code == 0
    assert _rows(out) == [json.loads(_ledger_line(target=TARGET))]  # passthrough
    assert "WARNING" in err and "unmatched_discount=1" in err
