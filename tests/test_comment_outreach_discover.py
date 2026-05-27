"""Tests for ``comment discover`` (plan Unit 5).

``discover`` fetches each operator-supplied **exact** URL (no link following), detects the
comment region, and emits one ``CommentTarget`` per seed. Fetch failures degrade to
``comment_open=null``; the process always exits 0. The fetcher is stubbed — no network.
"""

from __future__ import annotations

import io
import json

import pytest

from backlink_publisher.comment_outreach import discover as disc
from backlink_publisher.comment_outreach import schema
from backlink_publisher.comment_outreach.fetch import FetchResult


def _seed(source_url: str, **overrides) -> dict:
    row = {
        "source_url": source_url,
        "topic": "python testing",
        "target_url": "https://my.example.org/landing",
    }
    row.update(overrides)
    return row


def _jsonl(*rows: dict) -> str:
    return "".join(json.dumps(r) + "\n" for r in rows)


def _run(seeds_text: str):
    dest = io.StringIO()
    counts = disc.discover_targets(io.StringIO(seeds_text), dest)
    out = [json.loads(line) for line in dest.getvalue().splitlines() if line]
    return out, counts


_WITH_REGION = b"<html><head><title>Post</title></head><body><h1>Post</h1><div id='disqus_thread'></div></body></html>"


def test_cli_discover_writes_to_output_file(monkeypatch, tmp_path, capsys):
    # Regression / parity: discover honors --output like import does (agent-native parity).
    from backlink_publisher.cli import comment

    monkeypatch.setattr(disc, "fetch_comment_page", lambda url, **k: FetchResult(_WITH_REGION, "ok"))
    monkeypatch.setattr("sys.stdin", io.StringIO(_jsonl(_seed("https://a.example/p1"))))
    out_path = tmp_path / "targets.jsonl"
    rc = comment.main(["discover", "--output", str(out_path)])
    assert rc == 0
    assert capsys.readouterr().out == ""  # data went to the file, not stdout
    lines = [json.loads(l) for l in out_path.read_text().splitlines() if l]
    assert len(lines) == 1 and lines[0]["comment_open"] is True
_NO_REGION = b"<html><body><h1>Press release</h1><p>Comments are closed.</p></body></html>"


# --- Happy path: regions detected -> comment_open=true ---------------------
def test_two_seeds_with_regions_detected_true(monkeypatch):
    monkeypatch.setattr(disc, "fetch_comment_page", lambda url, **k: FetchResult(_WITH_REGION, "ok"))
    out, counts = _run(_jsonl(_seed("https://a.example/p1"), _seed("https://b.example/p2")))
    assert counts == {"discovered": 2, "rejected": 0, "fetched": 2}
    assert all(t["comment_open"] is True for t in out)
    assert all(t["discovered_by"] == "discover" for t in out)
    assert all(t.get("page_title") == "Post" for t in out)


# --- Fetched but no region -> comment_open=false ---------------------------
def test_seed_without_region_detected_false(monkeypatch):
    monkeypatch.setattr(disc, "fetch_comment_page", lambda url, **k: FetchResult(_NO_REGION, "ok"))
    out, _ = _run(_jsonl(_seed("https://a.example/closed")))
    assert out[0]["comment_open"] is False


# --- Fetch failure -> comment_open=null, exit 0, note carries reason -------
@pytest.mark.parametrize("reason", ["ssrf_blocked", "timeout", "non_200"])
def test_fetch_failure_degrades_to_null(monkeypatch, reason):
    monkeypatch.setattr(disc, "fetch_comment_page", lambda url, **k: FetchResult(None, reason))
    out, counts = _run(_jsonl(_seed("https://a.example/x")))
    assert counts["discovered"] == 1  # still emitted, not crashed
    assert out[0]["comment_open"] is None
    assert reason in out[0]["notes"]


# --- Integration: emitted target survives Unit 2 validation ----------------
def test_discovered_target_passes_schema(monkeypatch):
    monkeypatch.setattr(disc, "fetch_comment_page", lambda url, **k: FetchResult(_WITH_REGION, "ok"))
    out, _ = _run(_jsonl(_seed("https://a.example/p1")))
    assert schema.validate_comment_target(out[0]) == []


# --- No link following: a link-heavy page yields exactly one target --------
def test_no_link_following_one_seed_one_target(monkeypatch):
    links = b"<html><body>" + b"".join(b"<a href='/l%d'>x</a>" % i for i in range(50)) + b"<div id='respond'></div></body></html>"
    calls = []

    def _fetch(url, **k):
        calls.append(url)
        return FetchResult(links, "ok")

    monkeypatch.setattr(disc, "fetch_comment_page", _fetch)
    out, counts = _run(_jsonl(_seed("https://a.example/hub")))
    assert len(out) == 1
    assert counts["fetched"] == 1
    assert calls == ["https://a.example/hub"]  # only the exact URL, no followed links


# --- Malformed seed (missing required field) is rejected, not emitted ------
def test_seed_missing_target_url_rejected(monkeypatch, capsys):
    monkeypatch.setattr(disc, "fetch_comment_page", lambda url, **k: FetchResult(_WITH_REGION, "ok"))
    bad = {"source_url": "https://a.example/p", "topic": "x"}  # no target_url
    out, counts = _run(_jsonl(bad))
    assert out == []
    assert counts["rejected"] == 1
    assert "target_url" in capsys.readouterr().err


# --- Security: seed count is capped at the documented constant -------------
def test_seed_cap_bounds_fetch_attempts(monkeypatch):
    monkeypatch.setattr(disc, "MAX_SEEDS", 3)
    calls = []

    def _fetch(url, **k):
        calls.append(url)
        return FetchResult(_WITH_REGION, "ok")

    monkeypatch.setattr(disc, "fetch_comment_page", _fetch)
    seeds = _jsonl(*[_seed(f"https://a.example/p{i}") for i in range(10)])
    out, counts = _run(seeds)
    assert counts["fetched"] == 3  # exactly the cap, not 10
    assert len(calls) == 3
    assert len(out) == 3


def test_documented_cap_is_a_concrete_constant():
    assert isinstance(disc.MAX_SEEDS, int) and disc.MAX_SEEDS > 0


# --- title / summary are length-bounded ------------------------------------
def test_title_and_summary_are_length_bounded(monkeypatch):
    big_title = b"T" * 5000
    big_body = b"<p>" + b"word " * 5000 + b"</p><div id='respond'></div>"
    html = b"<html><head><title>" + big_title + b"</title></head><body>" + big_body + b"</body></html>"
    monkeypatch.setattr(disc, "fetch_comment_page", lambda url, **k: FetchResult(html, "ok"))
    out, _ = _run(_jsonl(_seed("https://a.example/big")))
    assert len(out[0]["page_title"]) <= disc.PAGE_TITLE_MAX
    assert len(out[0]["thread_summary"]) <= disc.THREAD_SUMMARY_MAX
