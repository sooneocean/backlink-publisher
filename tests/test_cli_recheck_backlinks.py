"""Unit 5: recheck-backlinks CLI verb contract.

--probe gating (default = zero-network dry preview), exit-0-default, --fail-on-dead
opt-in exit 6 (only on deterministic dead), stdin vs events.db selection, never-
raises batch, and usage-error validation. Network is patched at the shared
inspect_target_anchor engine; the autouse conftest also blocks real sockets.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from backlink_publisher.cli import recheck_backlinks as cli
from backlink_publisher.events import EventStore
from backlink_publisher.events.kinds import LINK_RECHECKED, PUBLISH_CONFIRMED

NOW = datetime.now(timezone.utc)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(tmp_path / "cache"))
    # CLI reads sys.stdin; default to empty so it uses events.db selection.
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    return tmp_path


def _seed_confirmed(aid, url, *, tgt="https://my.site/", days_ago=30,
                    host="medium.com", platform="medium"):
    ts = (NOW - timedelta(days=days_ago)).isoformat()
    EventStore().append(
        PUBLISH_CONFIRMED,
        {"live_url": url, "target_url": tgt, "platform": platform},
        target_url=tgt, host=host, article_id=aid, ts_utc=ts,
    )


def _inspect(**overrides):
    base = {
        "page_readable": True, "marker_present": None, "target_anchor_found": True,
        "target_rel": None, "target_is_nofollow": False, "target_anchor_text": None,
        "reason": None,
    }
    base.update(overrides)
    return lambda url, target, **kw: dict(base)


def _link_rechecked_rows():
    return EventStore().query(
        "SELECT payload_json FROM events WHERE kind = ?", (LINK_RECHECKED,)
    )


# ── dry preview (default, zero network) ──────────────────────────────────────

def test_dry_preview_lists_candidates_zero_network(isolated, capsys):
    _seed_confirmed(1, "https://medium.com/a")
    _seed_confirmed(2, "https://medium.com/b")
    spy = []

    def _spy(url, target, **kw):
        spy.append(url)
        return _inspect()(url, target)

    with patch(
        "backlink_publisher.publishing.adapters.link_attr_verifier.inspect_target_anchor",
        side_effect=_spy,
    ):
        assert cli.main([]) is None  # exit 0 (no SystemExit)
    out = capsys.readouterr().out
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert {r["live_url"] for r in rows} == {"https://medium.com/a", "https://medium.com/b"}
    assert all(r["will_probe"] for r in rows)
    assert spy == []  # zero network on dry preview
    assert _link_rechecked_rows() == []  # nothing emitted


# ── probe path ───────────────────────────────────────────────────────────────

def test_probe_emits_events(isolated, capsys):
    _seed_confirmed(1, "https://medium.com/a")
    with patch(
        "backlink_publisher.publishing.adapters.link_attr_verifier.inspect_target_anchor",
        _inspect(),
    ):
        assert cli.main(["--probe"]) is None
    rows = _link_rechecked_rows()
    assert len(rows) == 1
    assert json.loads(rows[0]["payload_json"])["verdict"] == "alive"


def test_default_exit_zero_even_with_dead_links(isolated):
    _seed_confirmed(1, "https://medium.com/a")
    with patch(
        "backlink_publisher.publishing.adapters.link_attr_verifier.inspect_target_anchor",
        _inspect(page_readable=False, reason="http_404"),
    ):
        # No --fail-on-dead → exit 0 despite a dead backlink (advisory diagnostic).
        assert cli.main(["--probe"]) is None


def test_fail_on_dead_exits_6_on_deterministic_dead(isolated):
    _seed_confirmed(1, "https://medium.com/a")
    with patch(
        "backlink_publisher.publishing.adapters.link_attr_verifier.inspect_target_anchor",
        _inspect(page_readable=False, reason="http_404"),
    ):
        with pytest.raises(SystemExit) as exc:
            cli.main(["--probe", "--fail-on-dead"])
    assert exc.value.code == cli.FAIL_ON_DEAD_EXIT_CODE


def test_fail_on_dead_exit_zero_when_only_probe_error(isolated):
    _seed_confirmed(1, "https://medium.com/a")
    with patch(
        "backlink_publisher.publishing.adapters.link_attr_verifier.inspect_target_anchor",
        _inspect(page_readable=False, reason="http_503"),  # transient → probe_error
    ):
        # probe_error is not deterministic dead → --fail-on-dead does NOT trip.
        assert cli.main(["--probe", "--fail-on-dead"]) is None


def test_never_raises_one_bad_link_does_not_abort_batch(isolated):
    _seed_confirmed(1, "https://medium.com/a")
    _seed_confirmed(2, "https://medium.com/b")

    def _inspect_one_raises(url, target, **kw):
        if url == "https://medium.com/a":
            raise RuntimeError("boom")
        return _inspect()(url, target)

    with patch(
        "backlink_publisher.publishing.adapters.link_attr_verifier.inspect_target_anchor",
        side_effect=_inspect_one_raises,
    ):
        assert cli.main(["--probe"]) is None
    verdicts_seen = {json.loads(r["payload_json"])["verdict"] for r in _link_rechecked_rows()}
    assert verdicts_seen == {"probe_error", "alive"}  # both rows processed


# ── stdin selection (R11) ────────────────────────────────────────────────────

def test_stdin_jsonl_overrides_events_selection(isolated, monkeypatch):
    _seed_confirmed(1, "https://medium.com/from-events")  # should be ignored
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO('{"live_url": "https://medium.com/from-stdin", "target_url": "https://t/", "platform": "medium"}\n'),
    )
    captured = []
    with patch(
        "backlink_publisher.publishing.adapters.link_attr_verifier.inspect_target_anchor",
        side_effect=lambda url, target, **kw: captured.append(url) or _inspect()(url, target),
    ):
        cli.main(["--probe"])
    assert captured == ["https://medium.com/from-stdin"]


# ── SEC1: concurrency guard + batch budget ───────────────────────────────────

def test_concurrent_run_skipped_when_lock_held(isolated):
    _seed_confirmed(1, "https://medium.com/a")
    import fcntl

    def _flock(fd, op):
        if op & fcntl.LOCK_EX:
            raise BlockingIOError("locked by another run")

    with patch("fcntl.flock", side_effect=_flock):
        with patch(
            "backlink_publisher.publishing.adapters.link_attr_verifier.inspect_target_anchor",
            _inspect(),
        ):
            assert cli.main(["--probe"]) is None  # skipped, exit 0
    assert _link_rechecked_rows() == []  # nothing probed/emitted while locked


def test_batch_budget_exhaustion_defers_remaining(isolated, monkeypatch, capsys):
    _seed_confirmed(1, "https://medium.com/a")
    _seed_confirmed(2, "https://medium.com/b")
    monkeypatch.setattr(cli, "_BATCH_BUDGET_S", 0.0)  # deadline already passed
    with patch(
        "backlink_publisher.publishing.adapters.link_attr_verifier.inspect_target_anchor",
        _inspect(),
    ):
        assert cli.main(["--probe"]) is None
    err = capsys.readouterr().err
    assert "budget" in err.lower()
    assert _link_rechecked_rows() == []  # all candidates deferred to next run


# ── usage validation (UsageError exit 1, not argparse exit 2) ────────────────

def test_limit_must_be_positive(isolated):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--limit", "0"])
    assert exc.value.code == 1


def test_since_must_be_iso(isolated):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--since", "not-a-date"])
    assert exc.value.code == 1
