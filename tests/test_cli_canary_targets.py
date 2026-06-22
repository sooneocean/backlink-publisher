"""Tests for the ``canary-targets`` CLI verb (Plan 2026-05-27-001 Unit 3).

``fetch_target`` and ``inspect_target_anchor`` are patched at the verb's module
reference (per ``feedback_mock_patch_paths_after_extraction``) so no network is
touched; ``_sleep`` is patched to a no-op so the inter-platform jitter never
blocks. A tmp config dir is injected via ``monkeypatch.setenv`` (never ``del``;
see ``feedback_del_os_environ_poisons_later_tests``) so the canary health store
and ``[canary.<platform>]`` config resolve into a sandbox. conftest autouse
fixtures apply.

TEST-FIRST cases (★):
  (a) marker present + anchor gone  → drift-confirmed (NOT advisory)
  (b) marker present + anchor nofollow → drift-confirmed (NOT advisory)
  (c) marker absent / soft-404      → advisory (NOT drift)
  (d) empty cohort                  → fail-loud (UsageError / exit 1)
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from backlink_publisher.cli import canary_targets as ct
from backlink_publisher.content._preflight_fetch import PreflightFacts


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------

def _facts(**over) -> PreflightFacts:
    base = dict(status=200, final_url="https://platform.example/post")
    base.update(over)
    return PreflightFacts(**base)


def _anchor(
    *,
    page_readable=True,
    marker_present=True,
    target_anchor_found=True,
    target_rel=None,
    target_is_nofollow=False,
    reason=None,
) -> dict:
    return {
        "page_readable": page_readable,
        "marker_present": marker_present,
        "target_anchor_found": target_anchor_found,
        "target_rel": target_rel,
        "target_is_nofollow": target_is_nofollow,
        "reason": reason,
    }


def _seed_config(tmp_path, monkeypatch, platforms: dict[str, dict]) -> None:
    """Write a config.toml with ``[canary.<platform>]`` sections and point the
    config dir env var at tmp_path."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    lines = []
    for plat, entry in platforms.items():
        lines.append(f"[canary.{plat}]")
        lines.append(f'post_url = "{entry["post_url"]}"')
        lines.append(f'expected_target = "{entry["expected_target"]}"')
        if "hard_skip" in entry:
            lines.append(f"hard_skip = {str(entry['hard_skip']).lower()}")
        lines.append("")
    (tmp_path / "config.toml").write_text("\n".join(lines), encoding="utf-8")


def _run(
    cohort,
    fetch_return=None,
    anchor_return=None,
    fetch_side_effect=None,
    anchor_side_effect=None,
    argv=None,
):
    """Run main() with patched cohort/fetch/anchor/sleep. Returns (rc, raised)
    via the caller capturing stdout. Cohort is forced via ``_build_cohort``."""
    fkw = {"side_effect": fetch_side_effect} if fetch_side_effect else {
        "return_value": fetch_return if fetch_return is not None else _facts()
    }
    akw = {"side_effect": anchor_side_effect} if anchor_side_effect else {
        "return_value": anchor_return if anchor_return is not None else _anchor()
    }
    with patch.object(ct, "_build_cohort", return_value=list(cohort)), \
         patch.object(ct, "fetch_target", **fkw), \
         patch.object(ct, "inspect_target_anchor", **akw), \
         patch.object(ct, "_sleep", lambda *_a, **_k: None):
        return ct.main(argv if argv is not None else [])


def _receipts(capsys):
    out = capsys.readouterr()
    return [json.loads(line) for line in out.out.splitlines() if line.strip()], out.err


# --------------------------------------------------------------------------
# ★ Drift true-positives (must NOT be downgraded to advisory)
# --------------------------------------------------------------------------

def test_marker_present_anchor_nofollow_is_drift_confirmed(tmp_path, monkeypatch, capsys):
    _seed_config(tmp_path, monkeypatch,
                 {"blogger": {"post_url": "https://b.example/p", "expected_target": "https://t.example/"}})
    rv = _run(["blogger"],
              fetch_return=_facts(status=200),
              anchor_return=_anchor(marker_present=True, target_anchor_found=True, target_is_nofollow=True))
    receipts, _ = _receipts(capsys)
    assert rv is None  # exit 0
    assert receipts[0]["verdict"] == "drift-confirmed"
    assert "target_nofollow" in receipts[0]["failed_checks"]
    # health store reflects the drift: consecutive_failures incremented.
    from backlink_publisher.canary.store import get_health
    assert get_health("blogger")["status"] == "drift-confirmed"
    assert get_health("blogger")["consecutive_failures"] == 1


def test_marker_present_anchor_gone_is_drift_confirmed_not_advisory(tmp_path, monkeypatch, capsys):
    _seed_config(tmp_path, monkeypatch,
                 {"blogger": {"post_url": "https://b.example/p", "expected_target": "https://t.example/"}})
    rv = _run(["blogger"],
              fetch_return=_facts(status=200),
              anchor_return=_anchor(marker_present=True, target_anchor_found=False))
    receipts, _ = _receipts(capsys)
    assert rv is None
    assert receipts[0]["verdict"] == "drift-confirmed"  # NOT advisory
    assert "target_anchor_missing" in receipts[0]["failed_checks"]


# --------------------------------------------------------------------------
# ★ Advisory false-positive guards (must NOT be drift)
# --------------------------------------------------------------------------

def test_marker_absent_is_advisory_not_drift(tmp_path, monkeypatch, capsys):
    _seed_config(tmp_path, monkeypatch,
                 {"blogger": {"post_url": "https://b.example/p", "expected_target": "https://t.example/"}})
    # anchor gone AND marker absent: cannot prove this is the canary page.
    _run(["blogger"],
         fetch_return=_facts(status=200),
         anchor_return=_anchor(marker_present=False, target_anchor_found=False))
    receipts, _ = _receipts(capsys)
    assert receipts[0]["verdict"] == "advisory"  # NOT drift
    assert "marker_absent" in receipts[0]["failed_checks"]
    from backlink_publisher.canary.store import get_health
    assert get_health("blogger")["status"] == "advisory"
    assert get_health("blogger")["consecutive_failures"] == 0  # not a confirmed drift


def test_soft_404_is_advisory(tmp_path, monkeypatch, capsys):
    _seed_config(tmp_path, monkeypatch,
                 {"blogger": {"post_url": "https://b.example/p", "expected_target": "https://t.example/"}})
    _run(["blogger"],
         fetch_return=_facts(status=200, soft404=True),
         anchor_return=_anchor(marker_present=False, target_anchor_found=False))
    receipts, _ = _receipts(capsys)
    assert receipts[0]["verdict"] == "advisory"
    assert "soft_404" in receipts[0]["failed_checks"]


def test_non_200_is_advisory(tmp_path, monkeypatch, capsys):
    _seed_config(tmp_path, monkeypatch,
                 {"blogger": {"post_url": "https://b.example/p", "expected_target": "https://t.example/"}})
    _run(["blogger"],
         fetch_return=PreflightFacts(status=404, reason="http_404"),
         anchor_return=_anchor(page_readable=False, marker_present=None, target_anchor_found=False, reason="http_404"))
    receipts, _ = _receipts(capsys)
    assert receipts[0]["verdict"] == "advisory"
    assert "http_404" in receipts[0]["failed_checks"]


# --------------------------------------------------------------------------
# Ambiguous → advisory (never quarantine)
# --------------------------------------------------------------------------

def test_ssrf_blocked_is_advisory(tmp_path, monkeypatch, capsys):
    _seed_config(tmp_path, monkeypatch,
                 {"blogger": {"post_url": "https://b.example/p", "expected_target": "https://t.example/"}})
    _run(["blogger"],
         fetch_return=PreflightFacts(reason="ssrf_blocked"),
         anchor_return=_anchor(page_readable=False, marker_present=None, target_anchor_found=False, reason="ssrf_blocked"))
    receipts, _ = _receipts(capsys)
    assert receipts[0]["verdict"] == "advisory"
    assert "ssrf_blocked" in receipts[0]["failed_checks"]


def test_page_not_readable_is_advisory(tmp_path, monkeypatch, capsys):
    _seed_config(tmp_path, monkeypatch,
                 {"blogger": {"post_url": "https://b.example/p", "expected_target": "https://t.example/"}})
    _run(["blogger"],
         fetch_return=PreflightFacts(reason="network_error"),
         anchor_return=_anchor(page_readable=False, marker_present=None, target_anchor_found=False, reason="network_error"))
    receipts, _ = _receipts(capsys)
    assert receipts[0]["verdict"] == "advisory"
    assert "page_not_readable" in receipts[0]["failed_checks"]


# --------------------------------------------------------------------------
# ★ Unit 4: canary-stale detection after a consecutive-advisory streak
# --------------------------------------------------------------------------

def _run_advisory(cohort):
    """Run a guaranteed-advisory cycle (page unreadable → cannot prove canary)."""
    return _run(
        cohort,
        fetch_return=PreflightFacts(reason="network_error"),
        anchor_return=_anchor(
            page_readable=False, marker_present=None,
            target_anchor_found=False, reason="network_error",
        ),
    )


def test_advisory_streak_flags_stale_only_at_threshold(tmp_path, monkeypatch, capsys):
    _seed_config(tmp_path, monkeypatch,
                 {"blogger": {"post_url": "https://b.example/p", "expected_target": "https://t.example/"}})
    # _STALE_ADVISORY_RUNS == 3: runs 1-2 advisory-but-not-stale, run 3 stale.
    for run_idx in range(1, ct._STALE_ADVISORY_RUNS):
        _run_advisory(["blogger"])
        receipts, stderr = _receipts(capsys)
        assert receipts[0]["verdict"] == "advisory"
        assert "note" not in receipts[0], f"run {run_idx} flagged stale early"
        assert "canary_stale_needs_reseed" not in stderr

    # Threshold run: now flagged stale + surfaced loudly on the RECON summary.
    _run_advisory(["blogger"])
    receipts, stderr = _receipts(capsys)
    assert receipts[0]["verdict"] == "advisory"
    assert receipts[0]["note"] == "canary-stale/needs-reseed"
    assert "canary_stale_needs_reseed" in stderr
    assert "blogger" in stderr


def test_link_alive_breaks_advisory_streak_resets_stale(tmp_path, monkeypatch, capsys):
    _seed_config(tmp_path, monkeypatch,
                 {"blogger": {"post_url": "https://b.example/p", "expected_target": "https://t.example/"}})
    # Two advisory runs, then a healthy run resets the streak …
    _run_advisory(["blogger"])
    _run_advisory(["blogger"])
    _run(["blogger"], fetch_return=_facts(status=200), anchor_return=_anchor())
    capsys.readouterr()  # discard
    from backlink_publisher.canary.store import get_health
    assert get_health("blogger")["consecutive_advisory"] == 0

    # … so a fresh advisory does NOT immediately re-trip the stale note.
    _run_advisory(["blogger"])
    receipts, _ = _receipts(capsys)
    assert "note" not in receipts[0]


# --------------------------------------------------------------------------
# not-configured (coverage gap, first-class verdict)
# --------------------------------------------------------------------------

def test_not_configured_platform_is_first_class_not_drift(tmp_path, monkeypatch, capsys):
    # blogger configured, ghpages NOT → ghpages must be not-configured.
    _seed_config(tmp_path, monkeypatch,
                 {"blogger": {"post_url": "https://b.example/p", "expected_target": "https://t.example/"}})
    _run(["blogger", "ghpages"],
         fetch_return=_facts(status=200),
         anchor_return=_anchor())
    receipts, stderr = _receipts(capsys)
    by_plat = {r["platform"]: r for r in receipts}
    assert by_plat["ghpages"]["verdict"] == "not-configured"
    assert "not_configured" in by_plat["ghpages"]["failed_checks"]
    assert by_plat["blogger"]["verdict"] == "link-alive"
    # Loudly listed as a coverage gap on stderr.
    assert "canary_coverage_gap" in stderr
    assert "ghpages" in stderr


# --------------------------------------------------------------------------
# link-alive happy path
# --------------------------------------------------------------------------

def test_link_alive_happy_path(tmp_path, monkeypatch, capsys):
    _seed_config(tmp_path, monkeypatch,
                 {"blogger": {"post_url": "https://b.example/p", "expected_target": "https://t.example/"}})
    rv = _run(["blogger"],
              fetch_return=_facts(status=200, noindex=False),
              anchor_return=_anchor(marker_present=True, target_anchor_found=True, target_is_nofollow=False))
    receipts, stderr = _receipts(capsys)
    assert rv is None
    assert receipts[0]["verdict"] == "link-alive"
    assert receipts[0]["mode"] == "evergreen"  # never "healthy"
    assert receipts[0]["failed_checks"] == []
    from backlink_publisher.canary.store import get_health
    assert get_health("blogger")["status"] == "link-alive"
    assert get_health("blogger")["last_ok_at"] is not None


def test_link_alive_with_noindex_downgrades_to_advisory(tmp_path, monkeypatch, capsys):
    # marker present + anchor present + dofollow but noindex → NOT link-alive,
    # and NOT drift (anchor is fine) → advisory.
    _seed_config(tmp_path, monkeypatch,
                 {"blogger": {"post_url": "https://b.example/p", "expected_target": "https://t.example/"}})
    _run(["blogger"],
         fetch_return=_facts(status=200, noindex=True),
         anchor_return=_anchor(marker_present=True, target_anchor_found=True, target_is_nofollow=False))
    receipts, _ = _receipts(capsys)
    assert receipts[0]["verdict"] == "advisory"
    assert "noindex" in receipts[0]["failed_checks"]


# --------------------------------------------------------------------------
# ★ Empty cohort → fail-loud
# --------------------------------------------------------------------------

def test_empty_cohort_fail_loud(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    with patch.object(ct, "_build_cohort", return_value=[]):
        with pytest.raises(SystemExit) as exc:
            ct.main([])
    assert exc.value.code == 1  # UsageError → exit 1, not argparse's 2


def test_unknown_platform_flag_fail_loud(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    with patch.object(ct, "_build_cohort", return_value=["blogger"]):
        with pytest.raises(SystemExit) as exc:
            ct.main(["--platform", "nonsuch"])
    assert exc.value.code == 1


# --------------------------------------------------------------------------
# Bad args → exit 1, never argparse's exit 2
# --------------------------------------------------------------------------

def test_bad_log_level_exits_one_not_two(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    with patch.object(ct, "_build_cohort", return_value=["blogger"]):
        with pytest.raises(SystemExit) as exc:
            ct.main(["--log-level", "LOUD"])
    assert exc.value.code == 1


# --------------------------------------------------------------------------
# Security: receipt allowlist — no credentials/tokens/cookies
# --------------------------------------------------------------------------

def test_receipt_contains_no_secret_substrings(tmp_path, monkeypatch, capsys):
    secret = "SECRET-TOKEN-abc123"
    # Bury a secret-looking value in the post_url query string + the fetched
    # facts; the receipt allowlist must not leak it.
    _seed_config(tmp_path, monkeypatch,
                 {"blogger": {"post_url": f"https://b.example/p?token={secret}",
                              "expected_target": "https://t.example/"}})
    _run(["blogger"],
         fetch_return=_facts(status=200, final_url=f"https://b.example/p?token={secret}",
                             x_robots_tag=secret),
         anchor_return=_anchor(target_rel=secret))
    receipts, stderr = _receipts(capsys)
    blob = json.dumps(receipts)
    assert secret not in blob, "receipt leaked a secret-bearing value"
    assert "token" not in blob.lower() or "not_configured" in blob  # no query-string keys either
    assert "cookie" not in blob.lower()
    # Allowlist: only the expected keys.
    assert set(receipts[0].keys()) <= {"platform", "verdict", "mode", "failed_checks", "checked_at", "note", "backlink_outcome"}


# --------------------------------------------------------------------------
# Integration: health store actually reflects verdicts after a run
# --------------------------------------------------------------------------

def test_integration_health_store_updated_for_whole_cohort(tmp_path, monkeypatch, capsys):
    _seed_config(tmp_path, monkeypatch, {
        "blogger": {"post_url": "https://b.example/p", "expected_target": "https://t.example/"},
        "velog": {"post_url": "https://v.example/p", "expected_target": "https://t.example/"},
    })

    def fetch_side(url):
        return _facts(status=200)

    def anchor_side(url, target, **kw):
        if "v.example" in url:  # velog drifts
            return _anchor(marker_present=True, target_anchor_found=True, target_is_nofollow=True)
        return _anchor(marker_present=True, target_anchor_found=True, target_is_nofollow=False)

    _run(["blogger", "velog"], fetch_side_effect=fetch_side, anchor_side_effect=anchor_side)
    receipts, _ = _receipts(capsys)
    from backlink_publisher.canary.store import get_health, list_all
    assert get_health("blogger")["status"] == "link-alive"
    assert get_health("velog")["status"] == "drift-confirmed"
    assert set(list_all().keys()) == {"blogger", "velog"}


# --------------------------------------------------------------------------
# ★ Canary Blitz: --include-uncertain flag (Plan 2026-06-06 R2/R5)
# --------------------------------------------------------------------------


def test__build_cohort_default_excludes_uncertain():
    """Default _build_cohort() returns only dofollow=True platforms."""
    from backlink_publisher.publishing import registry

    with patch.object(registry, "registered_platforms",
                      return_value=["plat_yes", "plat_no", "plat_maybe"]), \
         patch.object(registry, "dofollow_status") as mock_status:
        mock_status.side_effect = {"plat_yes": True, "plat_no": False, "plat_maybe": "uncertain"}.get
        cohort = ct._build_cohort(include_uncertain=False)
    assert cohort == ["plat_yes"]


def test__build_cohort_include_uncertain_includes_both():
    """With include_uncertain=True, dofollow=True AND 'uncertain' are included."""
    from backlink_publisher.publishing import registry

    with patch.object(registry, "registered_platforms",
                      return_value=["plat_yes", "plat_no", "plat_maybe"]), \
         patch.object(registry, "dofollow_status") as mock_status:
        mock_status.side_effect = {"plat_yes": True, "plat_no": False, "plat_maybe": "uncertain"}.get
        cohort = ct._build_cohort(include_uncertain=True)
    assert sorted(cohort) == ["plat_maybe", "plat_yes"]


def test__build_cohort_include_uncertain_empty_when_all_nofollow():
    """When no True or uncertain platforms exist, cohort is empty regardless."""
    from backlink_publisher.publishing import registry

    with patch.object(registry, "registered_platforms",
                      return_value=["plat_no"]), \
         patch.object(registry, "dofollow_status") as mock_status:
        mock_status.side_effect = {"plat_no": False}.get
        cohort = ct._build_cohort(include_uncertain=True)
    assert cohort == []


def test_cli_include_uncertain_flag_argv_accepted(tmp_path, monkeypatch, capsys):
    """--include-uncertain is accepted as a CLI flag and flows through to
    _build_cohort (verified by patching the function and asserting argv)."""
    from backlink_publisher.publishing import registry
    # The _run helper patches _build_cohort so we can't verify the flag's
    # cohort effect through it. Instead verify argparse-acceptance + that
    # _build_cohort is called with include_uncertain=True.
    _orig = ct._build_cohort
    call_kwargs = {}

    def _patched_build_cohort(**kw):
        call_kwargs.update(kw)
        return _orig(**kw)

    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    with patch.object(registry, "registered_platforms", return_value=["blogger"]), \
         patch.object(registry, "dofollow_status", return_value=True), \
         patch.object(ct, "_build_cohort", _patched_build_cohort), \
         patch.object(ct, "fetch_target", return_value=_facts()), \
         patch.object(ct, "inspect_target_anchor", return_value=_anchor()), \
         patch.object(ct, "_sleep", lambda *a, **k: None):
        ct.main(["--include-uncertain"])
    assert call_kwargs.get("include_uncertain") is True


# --------------------------------------------------------------------------
# Contract: stdout pure JSONL, stderr is recon, main() returns None (exit 0)
# --------------------------------------------------------------------------

def test_contract_stdout_pure_jsonl_stderr_recon(tmp_path, monkeypatch, capsys):
    _seed_config(tmp_path, monkeypatch,
                 {"blogger": {"post_url": "https://b.example/p", "expected_target": "https://t.example/"}})
    rv = _run(["blogger"], fetch_return=_facts(status=200), anchor_return=_anchor())
    out = capsys.readouterr()
    assert rv is None
    # Every stdout line is valid JSON (pure JSONL).
    for line in out.out.splitlines():
        if line.strip():
            json.loads(line)
    # recon summary lives on stderr.
    assert "canary_summary" in out.err
