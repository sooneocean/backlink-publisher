"""Anti-redrift guard decision-matrix tests (plan 2026-06-02-*).

Drives ``detect_redrift`` with injected resolvers so the status × claims ×
resolution matrix is covered without a real git repo. The resolvers themselves
(``_path_exists_on_main`` / ``_sha_reachable_from_main``) are covered by
``test_cli_plan_check.py`` — here we only test the redrift DECISION.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "check_plan_redrift", _REPO / "scripts" / "check_plan_redrift.py"
)
redrift = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(redrift)


def _all_exist(_p):
    return (True, "exists")


def _all_missing(_p):
    return (False, "missing")


def _write_plan(tmp_path: Path, *, status: str, claims: str, date: str = "2026-06-01") -> Path:
    """Write a minimal post-cutoff plan-doc with the given status + claims block."""
    p = tmp_path / "plan.md"
    p.write_text(
        f'---\ntitle: "t"\ntype: feat\nstatus: {status}\ndate: {date}\n{claims}---\n\n# body\n',
        encoding="utf-8",
    )
    return p


_CLAIMS_ONE_PATH = "claims:\n  paths:\n    - src/foo.py\n"
_CLAIMS_TWO_PATHS = "claims:\n  paths:\n    - src/foo.py\n    - src/bar.py\n"
_CLAIMS_EMPTY = "claims: {}\n"
_CLAIMS_SHA = "claims:\n  shas:\n    - 0123456789abcdef0123456789abcdef01234567\n"


# ── REDRIFT detected ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["active", "ready"])
def test_in_progress_status_with_all_claims_resolved_is_redrift(tmp_path, status):
    plan = _write_plan(tmp_path, status=status, claims=_CLAIMS_TWO_PATHS)
    out = redrift.detect_redrift(plan, path_resolver=_all_exist, sha_resolver=_all_exist)
    assert out is not None
    assert status in out
    assert "2" in out  # both declared paths counted


def test_redrift_on_sha_claim(tmp_path):
    plan = _write_plan(tmp_path, status="active", claims=_CLAIMS_SHA)
    out = redrift.detect_redrift(plan, path_resolver=_all_exist, sha_resolver=_all_exist)
    assert out is not None


# ── NO redrift (genuinely in progress) ───────────────────────────────────────

def test_in_progress_with_a_missing_path_is_not_redrift(tmp_path):
    plan = _write_plan(tmp_path, status="active", claims=_CLAIMS_TWO_PATHS)
    # one path still missing on main → work genuinely unfinished
    def _one_missing(p):
        return (False, "missing") if p == "src/bar.py" else (True, "exists")
    assert redrift.detect_redrift(plan, path_resolver=_one_missing, sha_resolver=_all_exist) is None


def test_in_progress_with_unreachable_sha_is_not_redrift(tmp_path):
    plan = _write_plan(tmp_path, status="ready", claims=_CLAIMS_SHA)
    assert redrift.detect_redrift(plan, path_resolver=_all_exist, sha_resolver=_all_missing) is None


# ── NO redrift (status is honest) ────────────────────────────────────────────

@pytest.mark.parametrize("status", ["completed", "shipped", "done", "complete", "archived"])
def test_terminal_status_is_never_flagged(tmp_path, status):
    plan = _write_plan(tmp_path, status=status, claims=_CLAIMS_TWO_PATHS)
    assert redrift.detect_redrift(plan, path_resolver=_all_exist, sha_resolver=_all_exist) is None


@pytest.mark.parametrize("status", ["partial", "phase1-complete"])
def test_honest_partial_status_is_never_flagged(tmp_path, status):
    # These already tell the truth about being incomplete; flagging them would
    # be a false positive even when some artifacts exist.
    plan = _write_plan(tmp_path, status=status, claims=_CLAIMS_TWO_PATHS)
    assert redrift.detect_redrift(plan, path_resolver=_all_exist, sha_resolver=_all_exist) is None


# ── NO redrift (blind spots / out of scope) ──────────────────────────────────

def test_empty_claims_optout_is_blind_spot(tmp_path):
    plan = _write_plan(tmp_path, status="active", claims=_CLAIMS_EMPTY)
    assert redrift.detect_redrift(plan, path_resolver=_all_exist, sha_resolver=_all_exist) is None


def test_pre_cutoff_grandfathered_plan_is_skipped(tmp_path):
    plan = _write_plan(tmp_path, status="active", claims=_CLAIMS_ONE_PATH, date="2026-05-13")
    assert redrift.detect_redrift(plan, path_resolver=_all_exist, sha_resolver=_all_exist) is None


def test_status_with_inline_comment_is_parsed(tmp_path):
    # YAML treats `# ...` as a comment, so status resolves to the bare token.
    plan = _write_plan(tmp_path, status="active  # mid-flight", claims=_CLAIMS_ONE_PATH)
    assert redrift.detect_redrift(plan, path_resolver=_all_exist, sha_resolver=_all_exist) is not None


def test_malformed_frontmatter_is_skipped_not_crashed(tmp_path):
    # A plan-doc with invalid YAML (unquoted colon in title) must be skipped —
    # the forward plan-check owns schema errors; a full scan must not crash.
    p = tmp_path / "bad.md"
    p.write_text(
        "---\ntitle: feat: unquoted colon breaks yaml\nstatus: active\n"
        "date: 2026-06-01\nclaims:\n  paths:\n    - src/foo.py\n---\n# body\n",
        encoding="utf-8",
    )
    assert redrift.detect_redrift(p, path_resolver=_all_exist, sha_resolver=_all_exist) is None
