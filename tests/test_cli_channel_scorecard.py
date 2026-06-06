"""channel-scorecard CLI verb. Fresh config/cache dir per test so the
session-scoped sandbox doesn't bleed seeded state across tests."""

import io
import json
import sys

import pytest

from backlink_publisher.cli.channel_scorecard import main
from backlink_publisher.events import EventStore


@pytest.fixture(autouse=True)
def fresh_dirs(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cache = tmp_path / "cache"
    cfg.mkdir()
    cache.mkdir()
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(cache))


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    code = 0
    try:
        main(argv)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            code = exc.code
        elif exc.code is None:
            code = 0
        else:
            err.write(str(exc.code))
            code = 1
    finally:
        sys.stdout, sys.stderr = saved
    return out.getvalue(), err.getvalue(), code


def _seed():
    EventStore().add_article({
        "target_urls_json": json.dumps(["https://site.com/p"]),
        "live_url": "https://medium.com/post1",
    })
    from webui_store import history_store
    history_store.save([{
        "id": "h1", "platform": "medium", "target_url": "https://site.com/p",
        "article_urls": ["https://medium.com/post1"], "status": "published",
    }])


def _rows(out):
    return [json.loads(l) for l in out.splitlines() if l.strip()]


def test_happy_path_emits_jsonl_exit_0():
    _seed()
    out, err, code = _run([])
    assert code == 0
    rows = _rows(out)  # stdout is pure JSONL — parses cleanly
    medium = next(r for r in rows if r["channel"] == "medium")
    assert medium["total_links"] == 1
    assert medium["declared_dofollow"] == "dofollow"
    assert medium["referral_traffic"] == "inert:not-landed"


def test_empty_stores_lists_registered_channels_exit_0():
    # Unlike the per-target equity-ledger, the per-channel scorecard lists every
    # registered channel (declared half) even with no data — all 0/0.
    out, _, code = _run([])
    assert code == 0
    rows = _rows(out)
    assert rows  # registered channels present
    assert all(r["total_links"] == 0 for r in rows)
    assert any(r["channel"] == "medium" for r in rows)


def test_stdout_is_pure_jsonl_stderr_has_banner():
    _seed()
    out, err, code = _run([])
    assert code == 0
    for line in out.splitlines():
        if line.strip():
            json.loads(line)  # no banner/diagnostics leaked onto stdout
    assert err.strip()  # config-echo banner went to stderr


def test_bad_stale_days_exits_1():
    _, err, code = _run(["--stale-days", "-5"])
    assert code == 1  # UsageError-style, not argparse's exit 2
    assert "stale-days" in err


def test_stale_days_zero_exits_1():
    # Boundary: 0 is invalid (must be positive).
    _, err, code = _run(["--stale-days", "0"])
    assert code == 1
    assert "stale-days" in err


def test_negative_small_sample_max_exits_1():
    _, err, code = _run(["--small-sample-max", "-1"])
    assert code == 1
    assert "small-sample-max" in err


def test_small_sample_flag_accepted():
    _seed()
    _, _, code = _run(["--small-sample-max", "0"])
    assert code == 0
