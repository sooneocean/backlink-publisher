"""`phase0-seal` CLI — Telegraph Phase 0 ship SHA seal operator-side tool.

Subcommands:
    init        Write seal notes for each worktree HEAD after G1 Pass.
    show        Print current seal (markdown allowlist or JSON).
    verify      Compare seal SHAs to current worktree HEADs.
    reseal      Refresh seal SHAs while preserving verdict_ref + sealed_at.
    verify-hook Hook-side validator (invoked by .git/hooks/pre-push).

Unit 2 lands the dispatcher skeleton; each subcommand handler currently
raises NotImplementedError. Subsequent units fill them in:
    Unit 3 → init (incl. --manual-verdict + post-push verify)
    Unit 4 → show, verify, reseal
    Unit 5 → verify-hook
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
from ..phase0.worktree import WorktreeEntry, discover_worktree_heads
from ._seal_init import (
    _handle_init,
    _InitError,
    _NotesPushDidNotLand,
    _build_routine_verdict_ref,
    _build_manual_verdict_ref,
    _get_main_sha,
    _post_push_verify,
    _parse_comment_url,
    _NOTES_REF,
    _NOTES_VERIFY_REF,
    EXIT_OK,
    EXIT_MISUSE,
    EXIT_WORKTREE,
    EXIT_VERDICT,
    EXIT_NOT_IMPLEMENTED,
)



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="phase0-seal",
        description="Telegraph Phase 0 ship SHA seal — operator-side CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    init_p = sub.add_parser(
        "init",
        help="Create seal notes after observing G1 Pass routine comment",
    )
    src = init_p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--verdict-comment",
        metavar="URL",
        help="Full URL of the G1 Pass PR comment posted by the routine bot",
    )
    src.add_argument(
        "--manual-verdict",
        action="store_true",
        help="Fallback for routine outage; pair with --evidence-log",
    )
    init_p.add_argument(
        "--verdict-pr",
        type=int,
        help="PR # the verdict comment belongs to (required for --verdict-comment)",
    )
    init_p.add_argument(
        "--evidence-log",
        metavar="PATH",
        help="Relative path to committed evidence file (required for --manual-verdict)",
    )
    init_p.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip the render-and-confirm prompt before writing notes",
    )
    init_p.set_defaults(handler=_handle_init)

    # show
    show_p = sub.add_parser("show", help="Print current seal block(s)")
    show_p.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (markdown applies R15b field allowlist; default markdown)",
    )
    show_p.add_argument(
        "--unit",
        metavar="UNIT",
        help="Restrict output to one unit (e.g., unit2); default = all 4",
    )
    show_p.set_defaults(handler=_handle_show)

    # verify
    verify_p = sub.add_parser("verify", help="Compare seal SHAs to current worktree HEADs")
    verify_p.add_argument(
        "--check-comment",
        action="store_true",
        help="Also re-fetch verdict comment via gh and re-validate author/marker",
    )
    verify_p.set_defaults(handler=_handle_verify)

    # reseal
    reseal_p = sub.add_parser(
        "reseal",
        help="Update seal SHAs to current worktree HEADs; preserves verdict_ref + sealed_at",
    )
    reseal_p.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip the old→new diff prompt before writing",
    )
    reseal_p.set_defaults(handler=_handle_reseal)

    # verify-hook (Unit 5)
    hook_p = sub.add_parser(
        "verify-hook",
        help="Hook-side validator; invoked by .git/hooks/pre-push (Unit 5)",
    )
    hook_p.add_argument(
        "--stdin-lines",
        action="store_true",
        help="Read all stdin lines (multi-ref push); validate each that matches Telegraph pattern",
    )
    hook_p.set_defaults(handler=_handle_verify_hook)

    return parser


# ---------------------------------------------------------------------------
# Stub handlers (raise NotImplementedError; replaced in subsequent units).
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Init helpers
# ---------------------------------------------------------------------------







def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")



# ---------------------------------------------------------------------------
# Unit 4 helpers
# ---------------------------------------------------------------------------


def _read_all_seal_notes(repo_root: Path) -> list[tuple[str, dict]]:
    """Return [(object_sha, body_dict)] for every note in the phase0-seal namespace."""
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "notes", f"--ref={_NOTES_REF}", "list"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    results = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) != 2:
            continue
        _blob_sha, obj_sha = parts
        show = subprocess.run(
            ["git", "-C", str(repo_root), "notes", f"--ref={_NOTES_REF}", "show", obj_sha],
            capture_output=True, text=True,
        )
        if show.returncode != 0:
            continue
        try:
            body = json.loads(show.stdout.strip())
        except json.JSONDecodeError:
            continue
        results.append((obj_sha, body))
    return results


def _get_nested(d: dict, dotted_key: str) -> object:
    """Get a value from a nested dict using a dotted key path.

    A trailing ``_short`` suffix on the last segment returns the first 12 chars
    of the value (used for sha256 display in markdown output).
    """
    short = dotted_key.endswith("_short")
    key = dotted_key[: -len("_short")] if short else dotted_key
    val: object = d
    for k in key.split("."):
        if not isinstance(val, dict):
            return None
        val = val.get(k)
        if val is None:
            return None
    if short and isinstance(val, str):
        return val[:12]
    return val


def _print_markdown_note(obj_sha: str, body: dict) -> None:
    unit = body.get("unit", "?")
    print(f"\n## {unit} — sealed SHA `{obj_sha[:12]}`\n")
    for field in V.MARKDOWN_FIELDS:
        val = _get_nested(body, field)
        if val is None:
            continue
        label = field.split(".")[-1].removesuffix("_short").replace("_", " ")
        print(f"- **{label}**: `{val}`")


def _get_main_sha_safe(repo_root: Path) -> str | None:
    try:
        return _get_main_sha(repo_root)
    except _InitError:
        return None


def _handle_show(args: argparse.Namespace) -> int:
    repo_root = V.find_main_worktree_root()
    notes = _read_all_seal_notes(repo_root)
    if not notes:
        print("phase0-seal show: no seal notes found; run 'phase0-seal init' first", file=sys.stderr)
        return EXIT_MISUSE
    if args.unit:
        notes = [(sha, body) for sha, body in notes if body.get("unit") == args.unit]
        if not notes:
            print(f"phase0-seal show: no seal note found for unit {args.unit!r}", file=sys.stderr)
            return EXIT_MISUSE
    notes.sort(key=lambda x: x[1].get("unit", ""))
    for obj_sha, body in notes:
        if args.format == "json":
            print(json.dumps(body, indent=2, sort_keys=True))
        else:
            _print_markdown_note(obj_sha, body)
    return EXIT_OK


def _handle_verify(args: argparse.Namespace) -> int:
    repo_root = V.find_main_worktree_root()
    notes = _read_all_seal_notes(repo_root)
    if not notes:
        print("phase0-seal verify: no seal notes found; run 'phase0-seal init' first", file=sys.stderr)
        return EXIT_MISUSE
    entries = discover_worktree_heads(V.TELEGRAPH_BRANCH_PATTERN, repo_root=repo_root)
    current_by_branch: dict[str, WorktreeEntry] = {e.branch: e for e in entries}
    current_main = _get_main_sha_safe(repo_root)
    all_ok = True
    for obj_sha, body in sorted(notes, key=lambda x: x[1].get("unit", "")):
        unit = body.get("unit", "?")
        branch = body.get("branch", "?")
        sealed_main = body.get("main_sha", "?")
        current = current_by_branch.get(branch)
        if current is None:
            sha_sym, sha_detail = "?", f"no worktree for {branch!r}"
        elif current.sha == obj_sha:
            sha_sym, sha_detail = "OK", f"{obj_sha[:12]}"
        else:
            sha_sym, sha_detail = "DRIFT", f"{obj_sha[:12]} → {current.sha[:12]}"
            all_ok = False
        if current_main is None:
            main_sym, main_detail = "?", "could not resolve origin/main"
        elif current_main == sealed_main:
            main_sym, main_detail = "OK", f"{sealed_main[:12]}"
        else:
            main_sym, main_detail = "DRIFT", f"{sealed_main[:12]} → {current_main[:12]}"
            all_ok = False
        print(f"{unit}  unit-sha={sha_sym} ({sha_detail})  main={main_sym} ({main_detail})")
        if args.check_comment and body.get("verdict_ref", {}).get("kind") == "routine_comment":
            vref = body["verdict_ref"]
            comment_url = vref.get("comment_url", "")
            print(f"  --check-comment: re-fetching {comment_url!r}")
            try:
                allowlist = V.load_allowlist(repo_root)
                owner, repo_name, pr_num, comment_id = _parse_comment_url(comment_url)
                comment = V._run_gh(f"repos/{owner}/{repo_name}/issues/comments/{comment_id}")
                V.validate_verdict_comment(comment, expected_pr=pr_num, allowlist=allowlist)
                print(f"  --check-comment: verdict comment still valid")
            except Exception as exc:
                print(f"  --check-comment: FAIL — {exc}")
                all_ok = False
    return EXIT_OK if all_ok else EXIT_MISUSE


def _handle_reseal(args: argparse.Namespace) -> int:
    repo_root = V.find_main_worktree_root()
    notes = _read_all_seal_notes(repo_root)
    if not notes:
        print("phase0-seal reseal: no seal notes found; run 'phase0-seal init' first", file=sys.stderr)
        return EXIT_MISUSE
    entries = discover_worktree_heads(V.TELEGRAPH_BRANCH_PATTERN, repo_root=repo_root)
    current_by_branch: dict[str, WorktreeEntry] = {e.branch: e for e in entries}
    new_main = _get_main_sha_safe(repo_root)
    resealed_at = _now_iso()
    migrations: list[tuple[str, str, str]] = []  # (old_sha, new_sha, new_body_json)
    for old_sha, body in notes:
        branch = body.get("branch", "")
        current = current_by_branch.get(branch)
        if current is None:
            print(f"phase0-seal reseal: no current worktree for {branch!r} — skipping", file=sys.stderr)
            continue
        new_body = {
            **body,
            "main_sha": new_main or body["main_sha"],
            "last_resealed_at": resealed_at,
            "sealed_by": "operator:reseal",
            # verdict_ref and sealed_at intentionally preserved via **body
        }
        V.validate_seal_schema(new_body)
        migrations.append((old_sha, current.sha, json.dumps(new_body, sort_keys=True, separators=(",", ":"))))
    if not migrations:
        print("phase0-seal reseal: nothing to reseal (no matching worktrees)", file=sys.stderr)
        return EXIT_OK
    if not args.yes:
        print("phase0-seal reseal: about to reseal:", file=sys.stderr)
        for old, new, _ in migrations:
            label = f"{old[:12]} → {new[:12]}" if old != new else f"{old[:12]} (same SHA)"
            print(f"  {label}", file=sys.stderr)
        try:
            resp = input("Continue? [y/N]: ")
        except EOFError:
            resp = ""
        if resp.strip().lower() not in ("y", "yes"):
            print("phase0-seal reseal: cancelled", file=sys.stderr)
            return EXIT_OK
    for old_sha, new_sha, new_body in migrations:
        if old_sha == new_sha:
            proc = subprocess.run(
                ["git", "-C", str(repo_root), "notes", f"--ref={_NOTES_REF}", "add", "-f", "-m", new_body, new_sha],
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                print(f"phase0-seal reseal: failed to overwrite note at {new_sha}: {proc.stderr.strip()}", file=sys.stderr)
                return EXIT_MISUSE
        else:
            proc = subprocess.run(
                ["git", "-C", str(repo_root), "notes", f"--ref={_NOTES_REF}", "add", "-m", new_body, new_sha],
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                print(f"phase0-seal reseal: failed to add note at {new_sha}: {proc.stderr.strip()}", file=sys.stderr)
                return EXIT_MISUSE
            rm = subprocess.run(
                ["git", "-C", str(repo_root), "notes", f"--ref={_NOTES_REF}", "remove", old_sha],
                capture_output=True, text=True,
            )
            if rm.returncode != 0:
                print(f"phase0-seal reseal: failed to remove old note at {old_sha}: {rm.stderr.strip()}", file=sys.stderr)
                return EXIT_MISUSE
    push = subprocess.run(
        ["git", "-C", str(repo_root), "push", "origin", f"{_NOTES_REF}:{_NOTES_REF}"],
        capture_output=True, text=True,
    )
    if push.returncode != 0:
        print(f"phase0-seal reseal: push failed: {push.stderr.strip()}", file=sys.stderr)
        return EXIT_MISUSE
    print(f"phase0-seal reseal: resealed {len(migrations)} unit(s); last_resealed_at={resealed_at}", file=sys.stderr)
    return EXIT_OK


# ---------------------------------------------------------------------------
# Unit 5: verify-hook
# ---------------------------------------------------------------------------


# Telegraph staged-branch ref pattern. Hook keys on remote_ref (NOT local_ref)
# per plan v3 — closes the direct-SHA-push bypass surfaced in v1 adversarial
# review #5 (`git push origin <sha>:refs/heads/local/telegraph-unitN-staged`
# with a non-staged local_ref would otherwise evade R5).
_REMOTE_REF_PATTERN = re.compile(
    r"^refs/heads/local/telegraph-unit(?P<n>\d+)-staged$"
)


def _read_seal_note_at(repo_root: Path, obj_sha: str) -> dict | None:
    """Return parsed seal note body at *obj_sha*, or None if no note / unparseable.

    Hook-side helper: doesn't raise; the calling loop reports each line's
    failure with a structured JSON record so multi-ref pushes can surface every
    failure rather than aborting on the first.
    """
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "notes", f"--ref={_NOTES_REF}", "show", obj_sha],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        return None


def _handle_verify_hook(args: argparse.Namespace) -> int:
    """Hook-side validator invoked by .git/hooks/pre-push.

    Reads `<local_ref> <local_sha> <remote_ref> <remote_sha>` lines on stdin
    (git's hook contract). For each line whose *remote_ref* matches the
    Telegraph staged-branch pattern, the seal note at *local_sha* must:
      1. Parse as JSON conforming to the seal-note schema.
      2. Carry a ``unit`` matching the unit number embedded in *remote_ref*.
      3. Carry a ``branch`` matching the remote_ref's branch portion.

    Emits one structured JSON line on stderr per processed line. Exit 0 iff
    every Telegraph staged-branch line passes (or none were present); exit
    1 if any failed. Exit codes are passed verbatim through the bash hook
    per plan v3 auto-fix v2-F6 (no remapping).
    """
    if not args.stdin_lines:
        # Defensive: if invoked without --stdin-lines, refuse rather than read
        # silently. Hook always passes --stdin-lines; misinvocation should fail
        # loud at operator time.
        print(
            json.dumps({"result": "misuse", "reason": "verify-hook requires --stdin-lines"}),
            file=sys.stderr,
        )
        return EXIT_MISUSE

    try:
        repo_root = V.find_main_worktree_root()
    except Exception as exc:
        print(
            json.dumps({"result": "fail", "reason": f"cannot resolve main worktree: {exc}"}),
            file=sys.stderr,
        )
        return EXIT_MISUSE

    failed = False
    matched_any = False
    for raw_line in sys.stdin:
        line = raw_line.rstrip("\n")
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 4:
            # Malformed git hook input — surface and skip.
            print(
                json.dumps({
                    "line": line, "result": "skip",
                    "reason": "expected 4 whitespace-separated fields",
                }),
                file=sys.stderr,
            )
            continue
        local_ref, local_sha, remote_ref, remote_sha = parts

        m = _REMOTE_REF_PATTERN.match(remote_ref)
        if m is None:
            # Not a Telegraph staged-branch push — hook falls through.
            continue

        matched_any = True
        expected_unit = f"unit{m.group('n')}"
        expected_branch_short = remote_ref.removeprefix("refs/heads/")

        # Special: pushing a deletion (local_sha = 40 zeros). Pre-push contract
        # for a delete is "remote_sha non-zero, local_sha zero". Refuse —
        # post-G1 staged branches must not be deletable from the operator side.
        if set(local_sha) == {"0"}:
            failed = True
            print(
                json.dumps({
                    "line": line, "result": "fail",
                    "reason": "refuse to delete a staged Telegraph branch post-G1",
                    "remote_ref": remote_ref,
                }),
                file=sys.stderr,
            )
            continue

        note = _read_seal_note_at(repo_root, local_sha)
        if note is None:
            failed = True
            print(
                json.dumps({
                    "line": line, "result": "fail",
                    "reason": "no-seal-note",
                    "sha": local_sha,
                    "remote_ref": remote_ref,
                }),
                file=sys.stderr,
            )
            continue

        try:
            V.validate_seal_schema(note)
        except V.SealValidationError as exc:
            failed = True
            print(
                json.dumps({
                    "line": line, "result": "fail",
                    "reason": f"seal-schema: {exc}",
                    "sha": local_sha,
                    "remote_ref": remote_ref,
                }),
                file=sys.stderr,
            )
            continue

        seal_unit = note.get("unit", "")
        if seal_unit != expected_unit:
            failed = True
            print(
                json.dumps({
                    "line": line, "result": "fail",
                    "reason": f"unit-mismatch: seal={seal_unit!r} but remote_ref expects {expected_unit!r}",
                    "sha": local_sha,
                    "remote_ref": remote_ref,
                }),
                file=sys.stderr,
            )
            continue

        seal_branch = note.get("branch", "")
        # The seal's `branch` field is written by `init` as the short ref
        # (e.g., `local/telegraph-unit2-staged`); normalize remote_ref the same
        # way and compare. Accept either short or `refs/heads/...` form in the
        # seal note for forward compatibility.
        seal_branch_short = seal_branch.removeprefix("refs/heads/")
        if seal_branch_short != expected_branch_short:
            failed = True
            print(
                json.dumps({
                    "line": line, "result": "fail",
                    "reason": f"branch-mismatch: seal={seal_branch!r} but remote_ref={remote_ref!r}",
                    "sha": local_sha,
                    "remote_ref": remote_ref,
                }),
                file=sys.stderr,
            )
            continue

        print(
            json.dumps({
                "line": line, "result": "pass",
                "unit": seal_unit,
                "sha": local_sha,
                "remote_ref": remote_ref,
            }),
            file=sys.stderr,
        )

    if not matched_any:
        # No Telegraph staged-branch refs in the push — fall through (exit 0).
        return EXIT_OK
    return EXIT_OK if not failed else EXIT_MISUSE


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Argparse dispatcher.

    Returns an exit code rather than calling sys.exit() so tests can call
    main() in-process and inspect the return value.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args) or EXIT_OK
    except NotImplementedError as exc:
        print(f"phase0-seal: {exc}", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
