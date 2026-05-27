"""Tests for the ``preflight-targets`` CLI verb.

Plan: docs/plans/2026-05-26-008-feat-preflight-targets-verb-plan.md (Unit 2).

``fetch_target`` is patched at the verb's consumer reference so no network is
touched; these tests exercise the R5b verdict ladder, dedupe/fan-out, the
always-exit-0 contract, and the receipt serializer.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.cli import preflight_targets as pt
from backlink_publisher.content._preflight_fetch import PreflightFacts


def _healthy(**over) -> PreflightFacts:
    base = dict(status=200, final_url="https://example.com/p", has_title=True, has_h1=True)
    base.update(over)
    return PreflightFacts(**base)


def _write(tmp_path, rows) -> str:
    p = tmp_path / "plan.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return str(p)


def _run(tmp_path, capsys, rows, fetch_return=None, fetch_side_effect=None, argv_extra=None):
    """Run main() with patched fetch_target; return (receipts, stderr)."""
    path = _write(tmp_path, rows)
    argv = ["-i", path] + (argv_extra or [])
    kw = {}
    if fetch_side_effect is not None:
        kw["side_effect"] = fetch_side_effect
    else:
        kw["return_value"] = fetch_return if fetch_return is not None else _healthy()
    with patch.object(pt, "fetch_target", **kw):
        pt.main(argv)
    out = capsys.readouterr()
    receipts = [json.loads(line) for line in out.out.splitlines() if line.strip()]
    return receipts, out.err


# --------------------------------------------------------------------------
# Happy path + always-exit-0
# --------------------------------------------------------------------------

def test_healthy_target(tmp_path, capsys):
    receipts, _ = _run(tmp_path, capsys, [{"target_url": "https://example.com/p"}])
    assert len(receipts) == 1
    assert receipts[0]["verdict"] == "healthy"
    assert "fetched_at" in receipts[0]


def test_main_returns_none_exit_zero(tmp_path, capsys):
    # main() must NOT raise SystemExit on the verdict path (always exit 0).
    path = _write(tmp_path, [{"target_url": "https://example.com/p"}])
    with patch.object(pt, "fetch_target", return_value=_healthy()):
        assert pt.main(["-i", path]) is None


# --------------------------------------------------------------------------
# Verdict ladder (behavioral, positive assertions)
# --------------------------------------------------------------------------

def test_http_404_is_not_healthy(tmp_path, capsys):
    receipts, _ = _run(tmp_path, capsys, [{"target_url": "https://example.com/x"}],
                       fetch_return=PreflightFacts(status=404, final_url="https://example.com/x", reason="http_404"))
    assert receipts[0]["verdict"] == "not-healthy"
    assert receipts[0]["status"] == 404


def test_noindex_same_host_is_not_healthy(tmp_path, capsys):
    receipts, _ = _run(tmp_path, capsys, [{"target_url": "https://example.com/p"}],
                       fetch_return=_healthy(noindex=True))
    assert receipts[0]["verdict"] == "not-healthy"
    assert "noindex" in receipts[0]["failed_checks"]


def test_soft_404_is_not_healthy(tmp_path, capsys):
    receipts, _ = _run(tmp_path, capsys, [{"target_url": "https://example.com/p"}],
                       fetch_return=_healthy(soft404=True))
    assert receipts[0]["verdict"] == "not-healthy"
    assert "soft_404" in receipts[0]["failed_checks"]


def test_cross_host_redirect_is_redirected_offsite(tmp_path, capsys):
    receipts, _ = _run(tmp_path, capsys, [{"target_url": "https://example.com/p"}],
                       fetch_return=_healthy(final_url="https://other.net/p", redirected=True, host_diff=True))
    assert receipts[0]["verdict"] == "redirected-offsite"
    assert receipts[0]["host_diff"] is True


def test_404_and_cross_host_redirect_differ(tmp_path, capsys):
    """Success Criterion: a 404 and a cross-host redirect are DIFFERENT verdicts
    the operator acts on differently — not just different fields."""
    r404, _ = _run(tmp_path, capsys, [{"target_url": "https://example.com/a"}],
                   fetch_return=PreflightFacts(status=404, final_url="https://example.com/a", reason="http_404"))
    rredir, _ = _run(tmp_path, capsys, [{"target_url": "https://example.com/b"}],
                     fetch_return=_healthy(final_url="https://other.net/b", redirected=True, host_diff=True))
    assert r404[0]["verdict"] != rredir[0]["verdict"]
    assert r404[0]["verdict"] == "not-healthy"
    assert rredir[0]["verdict"] == "redirected-offsite"


def test_unreachable_exits_zero(tmp_path, capsys):
    receipts, _ = _run(tmp_path, capsys, [{"target_url": "https://example.com/p"}],
                       fetch_return=PreflightFacts(reason="timeout"))
    assert receipts[0]["verdict"] == "unreachable"


def test_ssrf_blocked_exits_zero(tmp_path, capsys):
    receipts, _ = _run(tmp_path, capsys, [{"target_url": "http://10.0.0.1/"}],
                       fetch_return=PreflightFacts(reason="ssrf_blocked"))
    assert receipts[0]["verdict"] == "ssrf_blocked"


def test_tls_unverified_is_not_healthy(tmp_path, capsys):
    receipts, _ = _run(tmp_path, capsys, [{"target_url": "https://bad.example.com/"}],
                       fetch_return=PreflightFacts(reason="tls_unverified", tls_unverified=True))
    assert receipts[0]["verdict"] == "not-healthy"
    assert "tls_unverified" in receipts[0]["failed_checks"]


def test_invalid_url_is_not_healthy(tmp_path, capsys):
    receipts, _ = _run(tmp_path, capsys, [{"target_url": "ftp://bad/scheme"}],
                       fetch_return=PreflightFacts(reason="invalid_url"))
    assert receipts[0]["verdict"] == "not-healthy"
    assert "invalid_url" in receipts[0]["failed_checks"]


def test_redirect_capped_is_not_healthy(tmp_path, capsys):
    receipts, _ = _run(tmp_path, capsys, [{"target_url": "https://example.com/loop"}],
                       fetch_return=PreflightFacts(status=302, reason="redirect_capped", redirect_capped=True))
    assert receipts[0]["verdict"] == "not-healthy"
    assert "redirect_capped" in receipts[0]["failed_checks"]


def test_200_missing_title_h1_is_not_healthy(tmp_path, capsys):
    receipts, _ = _run(tmp_path, capsys, [{"target_url": "https://example.com/blank"}],
                       fetch_return=PreflightFacts(status=200, final_url="https://example.com/blank",
                                                   has_title=False, has_h1=False))
    assert receipts[0]["verdict"] == "not-healthy"
    assert "no_title" in receipts[0]["failed_checks"]
    assert "no_h1" in receipts[0]["failed_checks"]


# --------------------------------------------------------------------------
# Precedence: multi-failure full failed_checks; redirect-then-404
# --------------------------------------------------------------------------

def test_multi_failure_lists_all_checks(tmp_path, capsys):
    receipts, _ = _run(tmp_path, capsys, [{"target_url": "https://example.com/p"}],
                       fetch_return=_healthy(soft404=True, noindex=True))
    assert receipts[0]["verdict"] == "not-healthy"
    assert "soft_404" in receipts[0]["failed_checks"]
    assert "noindex" in receipts[0]["failed_checks"]


def test_redirect_then_404_records_final_status(tmp_path, capsys):
    receipts, _ = _run(tmp_path, capsys, [{"target_url": "https://example.com/p"}],
                       fetch_return=PreflightFacts(status=404, final_url="https://example.com/gone",
                                                   redirected=True, reason="http_404"))
    assert receipts[0]["verdict"] == "not-healthy"
    assert receipts[0]["status"] == 404
    assert receipts[0]["redirected"] is True


# --------------------------------------------------------------------------
# Fail-closed unknown
# --------------------------------------------------------------------------

def test_unknown_verdict_emitted_not_dropped(tmp_path, capsys):
    # status=None, reason=None hits the ladder's fail-closed else.
    receipts, stderr = _run(tmp_path, capsys, [{"target_url": "https://example.com/p"}],
                            fetch_return=PreflightFacts())
    assert len(receipts) == 1  # NOT dropped
    assert receipts[0]["verdict"] == "unknown"
    assert "preflight_unknown_verdict" in stderr  # loud tripwire


def test_non_empty_plan_never_yields_empty_receipts(tmp_path, capsys):
    receipts, _ = _run(tmp_path, capsys,
                       [{"target_url": "https://a.com/"}, {"target_url": "https://b.com/"}],
                       fetch_return=_healthy())
    assert len(receipts) >= 1  # exit-0 + empty receipt for a non-empty plan would be a bug


# --------------------------------------------------------------------------
# Dedupe + fan-out; skip missing target_url
# --------------------------------------------------------------------------

def test_dedupe_canonical_one_fetch_fanout(tmp_path, capsys):
    path = _write(tmp_path, [
        {"target_url": "https://x.com/p"},
        {"target_url": "https://x.com/p/"},  # trailing slash → same canonical
    ])
    mock = MagicMock(return_value=_healthy())
    with patch.object(pt, "fetch_target", mock):
        pt.main(["-i", path])
    out = capsys.readouterr()
    receipts = [json.loads(l) for l in out.out.splitlines() if l.strip()]
    assert mock.call_count == 1  # one fetch for the two equivalent rows
    assert len(receipts) == 1
    assert receipts[0]["source_rows"] == [1, 2]  # fan-out preserved


def test_missing_target_url_skipped_others_processed(tmp_path, capsys):
    receipts, stderr = _run(tmp_path, capsys,
                            [{"platform": "medium"}, {"target_url": "https://ok.com/"}],
                            fetch_return=_healthy())
    assert len(receipts) == 1
    assert receipts[0]["target_url"] == "https://ok.com/"
    assert "skipped_no_target" in stderr


# --------------------------------------------------------------------------
# Bad args → exit 1 (UsageError), never argparse's exit 2
# --------------------------------------------------------------------------

def test_bad_log_level_exits_one_not_two(tmp_path):
    path = _write(tmp_path, [{"target_url": "https://example.com/"}])
    with patch.object(pt, "fetch_target", return_value=_healthy()):
        with pytest.raises(SystemExit) as exc:
            pt.main(["-i", path, "--log-level", "LOUD"])
    assert exc.value.code == 1  # UsageError, not argparse's 2


# --------------------------------------------------------------------------
# Serializer completeness
# --------------------------------------------------------------------------

def test_receipt_has_all_fields(tmp_path, capsys):
    receipts, _ = _run(tmp_path, capsys, [{"target_url": "https://example.com/p"}],
                       fetch_return=_healthy())
    r = receipts[0]
    for key in ("target_url", "verdict", "failed_checks", "final_url", "redirected",
                "host_diff", "redirect_capped", "noindex", "soft404", "has_title",
                "has_h1", "tls_unverified", "status", "x_robots_tag", "fetched_at", "source_rows"):
        assert key in r, key
