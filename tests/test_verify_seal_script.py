"""Tests for `scripts/telegraph_spike/verify_seal.py` — Plan 009 Unit 6.

Server-side sidecar invoked from the GitHub Action workflow (Unit 7).

Each negative scenario is paired with a positive (PAIRED-positive rule). The
two integration tests at the bottom exercise the actual `python <script>`
subprocess invocation; the rest are in-process via direct `main()` calls.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import textwrap
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from backlink_publisher.phase0 import validation as V

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "telegraph_spike" / "verify_seal.py"


def _load_script() -> object:
    """Import verify_seal as a fresh module (so tests don't share state)."""
    spec = importlib.util.spec_from_file_location("verify_seal_script", SCRIPT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=check,
    )


_ALLOWLIST_YAML = textwrap.dedent("""\
    schema_version: 1
    authorized_authors:
      - login: telegraph-routine-bot[bot]
        routine_id: trig_01U8
        captured_at: "2026-05-25T10:00:00Z"
        captured_by: operator
        run_id_observed: trig-01-fire-1
""")


def _valid_comment_body() -> str:
    return "G1 Pass!\n<!-- phase0-verdict: result=pass run_id=trig-01-fire-42 -->\n"


def _routine_seal_body(*, comment_body: str | None = None, comment_sha_override: str | None = None) -> dict:
    body = comment_body if comment_body is not None else _valid_comment_body()
    sha = comment_sha_override if comment_sha_override is not None else V.sha256_hex(body)
    return {
        "unit": "unit2",
        "branch": "local/telegraph-unit2-staged",
        "main_sha": "0" * 40,
        "sealed_at": "2026-06-01T10:00:00Z",
        "sealed_by": "operator:init",
        "verdict_ref": {
            "kind": "routine_comment",
            "pr": 36,
            "comment_url": "https://github.com/x/y/pull/36#issuecomment-12345",
            "comment_id": 12345,
            "comment_author": "telegraph-routine-bot[bot]",
            "comment_created_at": "2026-05-25T10:00:00Z",
            "comment_updated_at": "2026-05-25T10:00:00Z",
            "comment_body_sha256": sha,
        },
    }


def _manual_seal_body(*, evidence_path: str, evidence_sha256: str) -> dict:
    return {
        "unit": "unit2",
        "branch": "local/telegraph-unit2-staged",
        "main_sha": "0" * 40,
        "sealed_at": "2026-06-01T10:00:00Z",
        "sealed_by": "operator:init",
        "verdict_ref": {
            "kind": "manual",
            "evidence_path": evidence_path,
            "evidence_sha256": evidence_sha256,
        },
    }


def _comment_payload(*, body: str | None = None, login: str = "telegraph-routine-bot[bot]",
                     pr: int = 36, comment_id: int = 12345,
                     updated_at: str = "2026-05-25T10:00:00Z") -> dict:
    if body is None:
        body = _valid_comment_body()
    return {
        "id": comment_id,
        "url": f"https://api.github.com/repos/x/y/issues/comments/{comment_id}",
        "html_url": f"https://github.com/x/y/pull/{pr}#issuecomment-{comment_id}",
        "issue_url": f"https://api.github.com/repos/x/y/pulls/{pr}",
        "user": {"login": login},
        "body": body,
        "created_at": "2026-05-25T10:00:00Z",
        "updated_at": updated_at,
    }


@pytest.fixture
def seal_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Build a tmp repo with allowlist + a single commit. Tests then attach
    seal notes per-scenario at that commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "--initial-branch=main")
    _run(repo, "config", "user.email", "test@example.com")
    _run(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("init\n")
    (repo / "scripts" / "telegraph_spike").mkdir(parents=True)
    (repo / "scripts" / "telegraph_spike" / "authorized-routine-bots.yaml").write_text(_ALLOWLIST_YAML)
    _run(repo, "add", ".")
    _run(repo, "commit", "-m", "init")
    sha = _run(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.chdir(repo)
    return {"tmp_path": tmp_path, "repo": repo, "sha": sha}


def _attach_note(repo: Path, sha: str, body: dict) -> None:
    _run(repo, "notes", "--ref=refs/notes/phase0-seal", "add",
         "-m", json.dumps(body), sha)


def _invoke(mod, *argv: str) -> tuple[int, dict]:
    """Run verify_seal.main(...) and capture the single JSON stdout line."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod.main(list(argv))
    line = buf.getvalue().strip()
    parsed = json.loads(line) if line else {}
    return rc, parsed


# ============================================================================
# Routine verdict — happy + negatives
# ============================================================================


class TestRoutineVerdict:
    def test_happy_pass(self, seal_repo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        body = _routine_seal_body()
        _attach_note(seal_repo["repo"], seal_repo["sha"], body)
        mod = _load_script()
        monkeypatch.setattr(V, "_run_gh", lambda *a, **kw: _comment_payload())
        rc, out = _invoke(mod, seal_repo["sha"])
        assert rc == 0
        assert out["result"] == "pass"

    def test_note_absent_fails(self, seal_repo: dict) -> None:
        # No note attached
        mod = _load_script()
        rc, out = _invoke(mod, seal_repo["sha"])
        assert rc == 1
        assert out["result"] == "fail"
        assert out["reason"] == "no-seal-note"

    def test_schema_malformed_fails(self, seal_repo: dict) -> None:
        body = _routine_seal_body()
        del body["unit"]  # required field
        _attach_note(seal_repo["repo"], seal_repo["sha"], body)
        mod = _load_script()
        rc, out = _invoke(mod, seal_repo["sha"])
        assert rc == 1
        assert "seal-schema" in out["reason"]
        assert "unit" in out["reason"]

    def test_author_not_in_allowlist_fails(
        self, seal_repo: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = _routine_seal_body()
        _attach_note(seal_repo["repo"], seal_repo["sha"], body)
        mod = _load_script()
        monkeypatch.setattr(V, "_run_gh",
            lambda *a, **kw: _comment_payload(login="attacker[bot]"))
        rc, out = _invoke(mod, seal_repo["sha"])
        assert rc == 1
        assert "verdict-comment-invalid" in out["reason"]
        assert "attacker[bot]" in out["reason"]

    def test_body_missing_marker_fails(
        self, seal_repo: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = _routine_seal_body()
        _attach_note(seal_repo["repo"], seal_repo["sha"], body)
        mod = _load_script()
        monkeypatch.setattr(V, "_run_gh",
            lambda *a, **kw: _comment_payload(body="just some text, no marker"))
        rc, out = _invoke(mod, seal_repo["sha"])
        assert rc == 1
        assert "verdict-comment-invalid" in out["reason"]
        assert "marker" in out["reason"].lower()

    def test_body_sha_mismatch_with_advanced_updated_at_is_warning(
        self, seal_repo: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Legitimate operator-permitted comment edit: sha mismatch but
        comment_updated_at advanced → pass-with-warning, exit 0."""
        original_body = _valid_comment_body()
        body = _routine_seal_body(comment_body=original_body)
        _attach_note(seal_repo["repo"], seal_repo["sha"], body)
        # Comment was edited — body differs, updated_at later
        edited_body = original_body + "(edited typo)\n"
        edited_comment = _comment_payload(
            body=edited_body, updated_at="2026-05-25T11:00:00Z",
        )
        mod = _load_script()
        monkeypatch.setattr(V, "_run_gh", lambda *a, **kw: edited_comment)
        rc, out = _invoke(mod, seal_repo["sha"])
        assert rc == 0
        assert out["result"] == "pass-with-warning"
        assert out["warning"] == "seal-comment-edited"

    def test_body_sha_mismatch_without_advanced_updated_at_fails(
        self, seal_repo: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """sha mismatch + updated_at NOT advanced — could be a forged or
        replaced comment. Refuse."""
        original_body = _valid_comment_body()
        body = _routine_seal_body(comment_body=original_body)
        _attach_note(seal_repo["repo"], seal_repo["sha"], body)
        forged_comment = _comment_payload(
            body=original_body + " forged",
            updated_at="2026-05-25T10:00:00Z",  # same as seal
        )
        mod = _load_script()
        monkeypatch.setattr(V, "_run_gh", lambda *a, **kw: forged_comment)
        rc, out = _invoke(mod, seal_repo["sha"])
        assert rc == 1
        assert out["reason"] == "body-sha-mismatch-no-edit-marker"


# ============================================================================
# Manual verdict — happy + negatives
# ============================================================================


class TestManualVerdict:
    def test_happy_manual(self, seal_repo: dict) -> None:
        """Manual evidence file + sha matches → pass-manual, exit 0."""
        evidence_rel = "docs/phase0/evidence.md"
        target = seal_repo["repo"] / evidence_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("captured G1 evidence")
        sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
        body = _manual_seal_body(evidence_path=evidence_rel, evidence_sha256=sha256)
        _attach_note(seal_repo["repo"], seal_repo["sha"], body)
        mod = _load_script()
        rc, out = _invoke(mod, seal_repo["sha"])
        assert rc == 0
        assert out["result"] == "pass-manual"
        assert out["details"]["evidence_path"] == evidence_rel

    def test_evidence_file_missing_fails(self, seal_repo: dict) -> None:
        body = _manual_seal_body(
            evidence_path="docs/phase0/never-created.md",
            evidence_sha256="0" * 64,
        )
        _attach_note(seal_repo["repo"], seal_repo["sha"], body)
        mod = _load_script()
        rc, out = _invoke(mod, seal_repo["sha"])
        assert rc == 1
        assert out["reason"] == "evidence-file-missing"

    def test_evidence_sha_mismatch_fails(self, seal_repo: dict) -> None:
        evidence_rel = "docs/phase0/evidence.md"
        target = seal_repo["repo"] / evidence_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("captured G1 evidence")
        body = _manual_seal_body(
            evidence_path=evidence_rel,
            evidence_sha256="f" * 64,  # wrong
        )
        _attach_note(seal_repo["repo"], seal_repo["sha"], body)
        mod = _load_script()
        rc, out = _invoke(mod, seal_repo["sha"])
        assert rc == 1
        assert out["reason"] == "evidence-sha-mismatch"


# ============================================================================
# Integration — real subprocess invocation
# ============================================================================


class TestSubprocessInvocation:
    def test_script_is_executable_and_emits_json_on_no_note(
        self, seal_repo: dict
    ) -> None:
        """No note → exit 1 with reason=no-seal-note. Proves the script's
        shebang resolves to a working python and the import-side-effects
        don't crash."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), seal_repo["sha"]],
            cwd=seal_repo["repo"], capture_output=True, text=True,
        )
        assert result.returncode == 1
        parsed = json.loads(result.stdout.strip())
        assert parsed["reason"] == "no-seal-note"

    def test_script_argparse_rejects_missing_positional(
        self, seal_repo: dict
    ) -> None:
        """The script must require <commit_sha> — argparse rejects empty
        invocation with exit 2."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            cwd=seal_repo["repo"], capture_output=True, text=True,
        )
        assert result.returncode == 2  # argparse usage error
