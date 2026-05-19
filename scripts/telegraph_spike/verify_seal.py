#!/usr/bin/env python3
"""verify_seal.py — server-side phase0 seal validation (plan-009 Unit 6).

Standalone sidecar invoked from the GitHub Action workflow (Unit 7) for every
staged-branch PR head SHA. Mirrors the R7a contract: reads the seal note at
the given commit SHA, validates schema + verdict source, and emits a single
JSON line on stdout summarizing the result.

Usage::

    python scripts/telegraph_spike/verify_seal.py <commit_sha>
    python scripts/telegraph_spike/verify_seal.py <commit_sha> --allowlist-path <p>

Output (one JSON line on stdout)::

    {"result": "pass" | "pass-with-warning" | "pass-manual" | "fail",
     "reason": "<short string, only on fail>",
     "details": {...},  # optional
     "warning": "seal-comment-edited"}  # only on pass-with-warning

Exit codes:
    0 — pass, pass-with-warning, or pass-manual
    1 — fail

Reuses ``backlink_publisher.phase0.validation`` via package import, so the
caller must have ``pip install -e ".[dev]"`` active.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

# Make src/ importable when called directly from a fresh checkout in CI
# (the GitHub Action runs `pip install -e ".[dev]"` first, but a developer
# running the script from a sibling worktree without an editable install
# still gets the right import path).
_REPO_ROOT_GUESS = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT_GUESS / "src"))

from backlink_publisher.phase0 import validation as V  # noqa: E402


_RESULT_PASS = "pass"
_RESULT_PASS_WARN = "pass-with-warning"
_RESULT_PASS_MANUAL = "pass-manual"
_RESULT_FAIL = "fail"


def _emit(payload: dict) -> None:
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def _fail(reason: str, **details) -> int:
    out: dict = {"result": _RESULT_FAIL, "reason": reason}
    if details:
        out["details"] = details
    _emit(out)
    return 1


def _read_seal_note(repo_root: Path, sha: str) -> dict | None:
    """Return the seal-note JSON body at *sha*, or None if no note."""
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "notes",
         f"--ref={V.NOTES_REF}" if hasattr(V, "NOTES_REF") else "--ref=refs/notes/phase0-seal",
         "show", sha],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        return None


def _show_toplevel() -> Path:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return Path(proc.stdout.strip())


def _validate_manual(repo_root: Path, body: dict) -> int:
    """Validate kind=manual verdict source. Resolves evidence_path relative
    to *repo_root*; verifies sha256 matches."""
    vref = body["verdict_ref"]
    evidence_path = vref.get("evidence_path", "")
    expected_sha = vref.get("evidence_sha256", "")
    target = (repo_root / evidence_path).resolve()
    try:
        target.relative_to(repo_root.resolve())
    except ValueError:
        return _fail("evidence-path-out-of-repo", evidence_path=evidence_path)
    if not target.exists() or not target.is_file():
        return _fail("evidence-file-missing", evidence_path=evidence_path)
    try:
        data = target.read_bytes()
    except OSError as exc:
        return _fail(f"evidence-read-error: {exc}", evidence_path=evidence_path)
    actual_sha = hashlib.sha256(data).hexdigest()
    if actual_sha != expected_sha:
        return _fail(
            "evidence-sha-mismatch",
            evidence_path=evidence_path,
            expected=expected_sha,
            actual=actual_sha,
        )
    _emit({"result": _RESULT_PASS_MANUAL, "details": {"evidence_path": evidence_path}})
    return 0


def _validate_routine(repo_root: Path, body: dict, allowlist_path: Path | None) -> int:
    """Validate kind=routine_comment verdict source. Re-fetches the GH comment
    and re-runs the marker/author/PR checks, then compares body_sha256."""
    vref = body["verdict_ref"]
    try:
        allowlist = V.load_allowlist(repo_root=repo_root) if allowlist_path is None \
                    else V.load_allowlist_from_path(allowlist_path) \
                    if hasattr(V, "load_allowlist_from_path") \
                    else V.load_allowlist(repo_root=repo_root)
    except V.AllowlistFileMissingError as exc:
        return _fail(f"allowlist-missing: {exc}")
    except V.EmptyAllowlistError as exc:
        return _fail(f"allowlist-empty: {exc}")
    except V.AllowlistSchemaError as exc:
        return _fail(f"allowlist-schema: {exc}")

    comment_url = vref.get("comment_url", "")
    expected_pr = vref.get("pr")
    if not isinstance(expected_pr, int):
        return _fail("verdict-ref-pr-missing-or-not-int")

    # Pull the comment ID out of the URL and fetch via gh.
    try:
        owner, repo, _pr, comment_id = _parse_comment_url(comment_url)
    except ValueError as exc:
        return _fail(f"comment-url-malformed: {exc}", comment_url=comment_url)

    try:
        comment = V._run_gh(f"repos/{owner}/{repo}/issues/comments/{comment_id}")
    except V.GhNotInstalledError:
        return _fail("gh-not-installed")
    except V.GhAuthError as exc:
        return _fail(f"gh-auth: {exc}")
    except subprocess.CalledProcessError as exc:
        # gh emits "404" on a missing comment via stderr; surface specifically.
        stderr = (exc.stderr or "").lower() if hasattr(exc, "stderr") else ""
        if "404" in stderr or "not found" in stderr:
            return _fail("comment-not-found", comment_id=comment_id)
        return _fail(f"gh-error: {exc}")

    try:
        V.validate_verdict_comment(comment, expected_pr=expected_pr, allowlist=allowlist)
    except V.SealValidationError as exc:
        return _fail(f"verdict-comment-invalid: {exc}")

    # body_sha256 comparison — handle legitimate operator-allowed comment edit
    # by checking comment_updated_at advanced past the seal's recorded
    # updated_at. Per plan v3 §462 + R15a: pass-with-warning, not fail.
    sealed_sha = vref.get("comment_body_sha256", "")
    sealed_updated_at = vref.get("comment_updated_at", "")
    body_norm = V.normalize_body(comment.get("body", ""))
    actual_sha = V.sha256_hex(comment.get("body", ""))
    current_updated_at = comment.get("updated_at", "")

    if sealed_sha and actual_sha != sealed_sha:
        if current_updated_at and current_updated_at > sealed_updated_at:
            _emit({
                "result": _RESULT_PASS_WARN,
                "warning": "seal-comment-edited",
                "details": {
                    "sealed_body_sha256": sealed_sha,
                    "current_body_sha256": actual_sha,
                    "sealed_updated_at": sealed_updated_at,
                    "current_updated_at": current_updated_at,
                },
            })
            return 0
        return _fail(
            "body-sha-mismatch-no-edit-marker",
            sealed_body_sha256=sealed_sha,
            current_body_sha256=actual_sha,
        )

    _emit({"result": _RESULT_PASS, "details": {"comment_id": comment_id}})
    return 0


def _parse_comment_url(url: str) -> tuple[str, str, int, int]:
    """Parse a GitHub PR-comment URL into (owner, repo, pr, comment_id).

    Accepts both `html_url` form (https://github.com/owner/repo/pull/N#issuecomment-XYZ)
    and `api_url` form (https://api.github.com/repos/owner/repo/issues/comments/XYZ).
    For api_url form, returns pr=0 (caller must source pr from verdict_ref.pr).
    """
    import re
    m = re.match(
        r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)#issuecomment-(\d+)", url
    )
    if m:
        return m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
    m = re.match(
        r"https://api\.github\.com/repos/([^/]+)/([^/]+)/issues/comments/(\d+)", url
    )
    if m:
        return m.group(1), m.group(2), 0, int(m.group(3))
    raise ValueError(f"unrecognized comment URL shape: {url}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify_seal",
        description="Validate the phase0-seal note at a commit SHA.",
    )
    parser.add_argument("commit_sha", help="40-char hex SHA whose seal note to validate")
    parser.add_argument(
        "--allowlist-path", default=None,
        help="Override the allowlist file location (default: scripts/telegraph_spike/authorized-routine-bots.yaml)",
    )
    parser.add_argument(
        "--repo-root", default=None,
        help="Repo root (default: git rev-parse --show-toplevel)",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root) if args.repo_root else _show_toplevel()
    allowlist_path = Path(args.allowlist_path) if args.allowlist_path else None

    body = _read_seal_note(repo_root, args.commit_sha)
    if body is None:
        return _fail("no-seal-note", sha=args.commit_sha)

    try:
        V.validate_seal_schema(body)
    except V.SealValidationError as exc:
        return _fail(f"seal-schema: {exc}")

    kind = body["verdict_ref"]["kind"]
    if kind == "manual":
        return _validate_manual(repo_root, body)
    if kind == "routine_comment":
        return _validate_routine(repo_root, body, allowlist_path)
    return _fail(f"verdict-ref-kind-unknown: {kind!r}")


if __name__ == "__main__":
    sys.exit(main())
