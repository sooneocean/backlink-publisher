"""phase0-seal init subcommand implementation.

Extracted from phase0_seal.py to keep the main CLI module within the
monolith_budget.toml SLOC ceiling.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from ..phase0 import validation as V
from ..phase0.worktree import discover_worktree_heads


def _now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


_NOTES_REF = "refs/notes/phase0-seal"
_NOTES_VERIFY_REF = "refs/notes/phase0-seal-verify-init"

# Exit-code namespace
EXIT_OK = 0
EXIT_MISUSE = 1
EXIT_WORKTREE = 2
EXIT_VERDICT = 3
EXIT_NOT_IMPLEMENTED = 99

_COMMENT_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?:pull|issues)/(?P<pr>\d+)"
    r"#issuecomment-(?P<id>\d+)"
)


def _parse_comment_url(url: str) -> tuple[str, str, int, int]:
    """Return (owner, repo, pr_number, comment_id) parsed from the PR comment URL."""
    if not isinstance(url, str):
        raise _InitError(f"--verdict-comment must be a URL string, got {type(url).__name__}", EXIT_VERDICT)
    m = _COMMENT_URL_RE.search(url)
    if not m:
        raise _InitError(
            f"--verdict-comment URL not recognized; expected "
            f"https://github.com/<owner>/<repo>/pull/<n>#issuecomment-<id>, got {url}",
            EXIT_VERDICT,
        )
    return m["owner"], m["repo"], int(m["pr"]), int(m["id"])


def _handle_init(args: argparse.Namespace) -> int:
    """Create seal notes for each staged worktree HEAD.

    Routine path (`--verdict-comment <url>`): fetch comment via `gh api`,
    validate author/PR/marker/body_sha256 against the allowlist, then write
    per-worktree notes + push to origin + **post-push verify** (v3 BLOCKER
    fix: ensure the push actually landed by fetching back into a temp ref
    and round-tripping each note before exiting 0).

    Manual path (`--manual-verdict --evidence-log <path>`): same flow but
    skips comment fetch; reads the evidence file (must be inside repo and
    committed at HEAD on each unit's branch) and records its sha256.
    """
    repo_root = V.find_main_worktree_root()

    try:
        allowlist = V.load_allowlist(repo_root)
    except (V.AllowlistFileMissingError, V.EmptyAllowlistError, V.AllowlistSchemaError) as exc:
        print(f"phase0-seal init: {exc}", file=sys.stderr)
        return EXIT_VERDICT

    # Build verdict_ref (routine or manual)
    try:
        if args.manual_verdict:
            verdict_ref = _build_manual_verdict_ref(args.evidence_log, repo_root)
        else:
            verdict_ref = _build_routine_verdict_ref(
                comment_url=args.verdict_comment,
                expected_pr=args.verdict_pr,
                allowlist=allowlist,
                repo_root=repo_root,
            )
    except _InitError as exc:
        print(f"phase0-seal init: {exc}", file=sys.stderr)
        return exc.exit_code

    # Discover staged worktrees
    entries = discover_worktree_heads(V.TELEGRAPH_BRANCH_PATTERN, repo_root=repo_root)
    if not entries:
        print(
            f"phase0-seal init: no staged worktrees matching {V.TELEGRAPH_BRANCH_PATTERN!r}; "
            f"clone the worktrees first via 'git worktree add ../bp-local-unit{{N}} {V.TELEGRAPH_BRANCH_PATTERN.replace('*', '<N>')}'",
            file=sys.stderr,
        )
        return EXIT_WORKTREE

    # Refuse if any worktree is missing / dirty / detached / mid-rebase (R20a)
    for e in entries:
        if e.path is None:
            print(
                f"phase0-seal init: unit {e.unit} worktree missing at expected path; "
                f"run 'git worktree add ../bp-local-{e.unit} {e.branch}' first",
                file=sys.stderr,
            )
            return EXIT_WORKTREE
        if e.is_clean is False:
            print(
                f"phase0-seal init: unit {e.unit} worktree dirty (uncommitted changes); "
                f"commit, stash, or revert first",
                file=sys.stderr,
            )
            return EXIT_WORKTREE
        if e.is_detached:
            print(
                f"phase0-seal init: unit {e.unit} HEAD detached; "
                f"check out the staged branch by name first",
                file=sys.stderr,
            )
            return EXIT_WORKTREE
        if e.has_rebase_in_progress:
            print(
                f"phase0-seal init: unit {e.unit} mid-rebase; "
                f"complete or abort the rebase before sealing",
                file=sys.stderr,
            )
            return EXIT_WORKTREE

    # For manual-verdict, also require evidence file committed on EACH unit's branch.
    if args.manual_verdict:
        rel = verdict_ref["evidence_path"]
        for e in entries:
            check = subprocess.run(
                ["git", "-C", str(e.path), "ls-files", "--error-unmatch", rel],
                capture_output=True, text=True,
            )
            if check.returncode != 0:
                print(
                    f"phase0-seal init: --manual-verdict evidence file {rel!r} "
                    f"is NOT committed at HEAD on unit {e.unit}'s branch "
                    f"(R7a reads it at PR head — file must exist there); "
                    f"commit it on each unit branch first",
                    file=sys.stderr,
                )
                return EXIT_WORKTREE

    main_sha = _get_main_sha(repo_root)
    sealed_at = _now_iso()

    # Build per-SHA seal bodies
    bodies: dict[str, str] = {}
    for e in entries:
        body = {
            "unit": e.unit,
            "branch": e.branch,
            "main_sha": main_sha,
            "sealed_at": sealed_at,
            "last_resealed_at": None,
            "sealed_by": "operator:init",
            "verdict_ref": verdict_ref,
        }
        # Validate before writing — strict-positive (catches our own construction bugs)
        V.validate_seal_schema(body)
        bodies[e.sha] = json.dumps(body, sort_keys=True, separators=(",", ":"))

    # Confirmation prompt (skip with -y)
    if not args.yes:
        print("phase0-seal init: about to write seal notes:", file=sys.stderr)
        for sha, body in bodies.items():
            print(f"  {sha}: {body[:160]}{'...' if len(body) > 160 else ''}", file=sys.stderr)
        try:
            resp = input("Continue? [y/N]: ")
        except EOFError:
            resp = ""
        if resp.strip().lower() not in ("y", "yes"):
            print("phase0-seal init: cancelled by operator", file=sys.stderr)
            return EXIT_OK

    # Write notes (no -f; refuses if a note already exists at the SHA)
    for sha, body in bodies.items():
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "notes", f"--ref={_NOTES_REF}", "add", "-m", body, sha],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            stderr = (proc.stderr or "").lower()
            if "cannot add" in stderr or "exists" in stderr or "already" in stderr:
                print(
                    f"phase0-seal init: seal note already exists at {sha}; "
                    f"use 'phase0-seal reseal' to update",
                    file=sys.stderr,
                )
                return EXIT_MISUSE
            print(
                f"phase0-seal init: git notes add failed at {sha}: {proc.stderr.strip()}",
                file=sys.stderr,
            )
            return EXIT_MISUSE

    # Push notes ref to origin
    push_proc = subprocess.run(
        ["git", "-C", str(repo_root), "push", "origin", f"{_NOTES_REF}:{_NOTES_REF}"],
        capture_output=True, text=True,
    )
    if push_proc.returncode != 0:
        print(
            f"phase0-seal init: pushing notes ref to origin failed: {push_proc.stderr.strip()}; "
            f"retry init after fixing the cause (network, refs/notes/ branch protection)",
            file=sys.stderr,
        )
        return EXIT_MISUSE

    # Post-push verify (v3 BLOCKER fix #1 — closes v2-review adversarial F1)
    try:
        _post_push_verify(repo_root, bodies)
    except _NotesPushDidNotLand as exc:
        print(f"phase0-seal init: {exc}", file=sys.stderr)
        return EXIT_MISUSE

    print(
        f"phase0-seal init: wrote and verified seal notes for {len(bodies)} unit(s); "
        f"sealed_at={sealed_at}",
        file=sys.stderr,
    )
    return EXIT_OK

class _InitError(Exception):
    """Init pre-flight validation failure with a specific exit_code."""

    def __init__(self, msg: str, exit_code: int) -> None:
        super().__init__(msg)
        self.exit_code = exit_code

class _NotesPushDidNotLand(Exception):
    """Notes-push to origin returned 0 but verify-fetch shows it did not land."""

def _build_routine_verdict_ref(
    *,
    comment_url: str,
    expected_pr: int | None,
    allowlist: dict,
    repo_root: Path,
) -> dict:
    owner, repo, pr_from_url, comment_id = _parse_comment_url(comment_url)
    if expected_pr is None:
        expected_pr = pr_from_url
    elif expected_pr != pr_from_url:
        raise _InitError(
            f"--verdict-pr={expected_pr} does not match PR # parsed from --verdict-comment URL ({pr_from_url})",
            EXIT_VERDICT,
        )

    try:
        comment = V._run_gh(f"repos/{owner}/{repo}/issues/comments/{comment_id}")
    except V.GhNotInstalledError as exc:
        raise _InitError(str(exc), EXIT_VERDICT) from exc
    except V.GhAuthError as exc:
        raise _InitError(str(exc), EXIT_VERDICT) from exc
    except RuntimeError as exc:
        raise _InitError(f"gh api failed: {exc}", EXIT_VERDICT) from exc

    try:
        validated = V.validate_verdict_comment(
            comment, expected_pr=expected_pr, allowlist=allowlist,
        )
    except V.SealValidationError as exc:
        raise _InitError(f"verdict comment validation failed: {exc}", EXIT_VERDICT) from exc

    return {
        "kind": "routine_comment",
        "pr": expected_pr,
        "comment_url": validated["comment_url"] or comment_url,
        "comment_id": validated["comment_id"],
        "comment_author": validated["user_login"],
        "comment_created_at": validated["comment_created_at"],
        "comment_updated_at": validated["comment_updated_at"],
        "comment_body_sha256": validated["body_sha256"],
    }

def _build_manual_verdict_ref(evidence_log: str | None, repo_root: Path) -> dict:
    if not evidence_log:
        raise _InitError(
            "--manual-verdict requires --evidence-log <path>",
            EXIT_VERDICT,
        )
    repo_resolved = repo_root.resolve()
    # Resolve evidence path relative to repo root if not absolute
    rel = Path(evidence_log)
    if rel.is_absolute():
        try:
            rel = rel.resolve().relative_to(repo_resolved)
        except ValueError as exc:
            raise _InitError(
                f"--evidence-log {evidence_log!r} is outside the repo at {repo_resolved}",
                EXIT_WORKTREE,
            ) from exc
    full = (repo_resolved / rel).resolve()
    if not str(full).startswith(str(repo_resolved)):
        raise _InitError(
            f"--evidence-log {evidence_log!r} resolves outside the repo ({full})",
            EXIT_WORKTREE,
        )
    if not full.exists():
        raise _InitError(
            f"--evidence-log {full} does not exist",
            EXIT_WORKTREE,
        )

    # Verify it is committed in the repo (at main repo HEAD).
    check = subprocess.run(
        ["git", "-C", str(repo_resolved), "ls-files", "--error-unmatch", str(rel)],
        capture_output=True, text=True,
    )
    if check.returncode != 0:
        raise _InitError(
            f"--evidence-log {rel} is NOT tracked by git; "
            f"commit it (and to each unit branch) before init; "
            f"R7a reads it at PR head — uncommitted files do not exist on the PR side",
            EXIT_WORKTREE,
        )

    content = full.read_bytes()
    return {
        "kind": "manual",
        "evidence_path": str(rel),
        "evidence_sha256": hashlib.sha256(content).hexdigest(),
    }

def _get_main_sha(repo_root: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "origin/main"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        # Fallback to local main
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "main"],
            capture_output=True, text=True,
        )
    sha = (proc.stdout or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise _InitError(
            f"could not resolve main_sha (origin/main or main); got {sha!r}",
            EXIT_MISUSE,
        )
    return sha

def _post_push_verify(repo_root: Path, expected_bodies: dict[str, str]) -> None:
    """Fetch origin notes ref into a TEMP local ref; assert each note round-trips.

    Closes v2-review adversarial F1: a silent push failure (push exits 0
    but origin's ref did not advance) would otherwise produce
    "init succeeded → push succeeded → R7a rejected" surprise at PR open.
    """
    try:
        # Fetch into temp ref (does NOT touch the regular phase0-seal ref).
        fetch = subprocess.run(
            ["git", "-C", str(repo_root), "fetch", "origin",
             f"{_NOTES_REF}:{_NOTES_VERIFY_REF}"],
            capture_output=True, text=True,
        )
        if fetch.returncode != 0:
            raise _NotesPushDidNotLand(
                f"notes push to origin returned 0 but verify-fetch failed: "
                f"{fetch.stderr.strip() or '(empty stderr)'}; "
                f"the ref may not have actually advanced on origin"
            )

        for sha, expected in expected_bodies.items():
            show = subprocess.run(
                ["git", "-C", str(repo_root), "notes", f"--ref={_NOTES_VERIFY_REF}", "show", sha],
                capture_output=True, text=True,
            )
            if show.returncode != 0:
                raise _NotesPushDidNotLand(
                    f"note for {sha} is NOT present on origin after push "
                    f"(push exited 0 but verify-fetch can't read it); "
                    f"check ref-protection rules on refs/notes/phase0-seal and retry"
                )
            actual = (show.stdout or "").strip()
            if actual != expected:
                raise _NotesPushDidNotLand(
                    f"note body on origin for {sha} differs from what was written: "
                    f"actual={actual[:120]!r} expected={expected[:120]!r}"
                )
    finally:
        # Always clean up the temp ref, even on failure (best-effort).
        subprocess.run(
            ["git", "-C", str(repo_root), "update-ref", "-d", _NOTES_VERIFY_REF],
            capture_output=True, text=True,
        )
