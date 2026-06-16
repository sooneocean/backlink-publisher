"""Test scripts/prune-stale-worktrees.sh against a fixture git repo.

The helper is shell, so we exercise it end-to-end by:
  1. Creating a throw-away git repo with real `git worktree add` calls.
  2. Constructing the exact ancestor / dirty / clean states the helper checks.
  3. Running the helper with --dry-run (no destructive flags in tests).
  4. Parsing its stdout for the "would remove:" lines.

We do NOT mock git — the value of this test is that the bash dispatch logic
actually agrees with real `git merge-base --is-ancestor` and real `git status
--porcelain` semantics. `gh` is intentionally unavailable in the test harness
so we exercise the graceful-degradation path (squash-merged detection skipped).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PRUNE = REPO_ROOT / "scripts" / "prune-stale-worktrees.sh"
SAFETY = REPO_ROOT / "scripts" / "_worktree_safety.sh"


def _run(cmd: list[str], cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=full_env, check=False)


def _git(cwd: Path, *args: str) -> str:
    res = _run(["git", *args], cwd)
    assert res.returncode == 0, f"git {args} failed: {res.stderr}"
    return res.stdout.strip()


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """Build a fixture repo with: main, one merged feature branch, one unmerged."""
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "config", "user.email", "t@t")
    _git(main, "config", "user.name", "t")
    (main / "README.md").write_text("init\n")
    _git(main, "add", "README.md")
    _git(main, "commit", "-q", "-m", "init")

    # Simulate an `origin` remote so the helper's `origin/main` reference resolves.
    bare = tmp_path / "origin.git"
    _git(main, "clone", "--bare", "-q", str(main), str(bare))
    _git(main, "remote", "add", "origin", str(bare))
    _git(main, "fetch", "-q", "origin")
    _git(main, "branch", "--set-upstream-to=origin/main", "main")

    # Merged feature branch: create in a worktree, commit, ff-merge into main.
    merged_wt = tmp_path / "wt-merged"
    _git(main, "worktree", "add", "-b", "feat/merged", str(merged_wt))
    (merged_wt / "f.txt").write_text("a\n")
    _git(merged_wt, "add", "f.txt")
    _git(merged_wt, "commit", "-q", "-m", "merged work")
    _git(main, "merge", "--ff-only", "-q", "feat/merged")
    _git(main, "push", "-q", "origin", "main")
    _git(main, "fetch", "-q", "origin")

    # Unmerged feature branch: separate worktree, commits NOT on main.
    unmerged_wt = tmp_path / "wt-unmerged"
    _git(main, "worktree", "add", "-b", "feat/unmerged", str(unmerged_wt))
    (unmerged_wt / "g.txt").write_text("b\n")
    _git(unmerged_wt, "add", "g.txt")
    _git(unmerged_wt, "commit", "-q", "-m", "unmerged work")

    # Dirty merged worktree: as merged but with an uncommitted change layered on.
    dirty_wt = tmp_path / "wt-dirty-merged"
    _git(main, "worktree", "add", "-b", "feat/dirty-merged", str(dirty_wt))
    (dirty_wt / "f.txt").write_text("local override\n")  # uncommitted change
    # branch tip already on main (just-created branch == main HEAD)

    # Copy the helper scripts into the fixture so PRUNE's `$SCRIPT_DIR/_worktree_safety.sh`
    # resolves correctly when invoked from this tmp tree's main worktree.
    scripts_dst = main / "scripts"
    scripts_dst.mkdir()
    shutil.copy(PRUNE, scripts_dst / PRUNE.name)
    shutil.copy(SAFETY, scripts_dst / SAFETY.name)
    (scripts_dst / PRUNE.name).chmod(0o755)

    return main


def test_dry_run_lists_only_merged_clean_worktrees(fixture_repo: Path) -> None:
    """The merged+clean worktree is the only candidate; unmerged + dirty are skipped."""
    res = _run(["bash", "scripts/prune-stale-worktrees.sh", "--dry-run"], fixture_repo)
    assert res.returncode == 0, f"stderr: {res.stderr}"
    assert "would remove:" in res.stdout
    assert "wt-merged" in res.stdout
    assert "wt-unmerged" not in res.stdout, "unmerged branch should not be a candidate"
    assert "wt-dirty-merged" not in res.stdout, "dirty worktree should not be a candidate"


def test_dry_run_summary_counts(fixture_repo: Path) -> None:
    """stderr summary reports the expected per-category counts."""
    res = _run(["bash", "scripts/prune-stale-worktrees.sh", "--dry-run"], fixture_repo)
    assert "candidates for removal: 1" in res.stderr
    assert "skipped (unmerged):     1" in res.stderr
    assert "skipped (dirty):        1" in res.stderr


def test_no_candidates_exits_zero_with_message(tmp_path: Path) -> None:
    """An empty repo with only the main worktree exits 0 and prints 'no stale worktrees'."""
    repo = tmp_path / "empty"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "x").write_text("x")
    _git(repo, "add", "x")
    _git(repo, "commit", "-q", "-m", "x")
    bare = tmp_path / "empty-origin.git"
    _git(repo, "clone", "--bare", "-q", str(repo), str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "fetch", "-q", "origin")
    _git(repo, "branch", "--set-upstream-to=origin/main", "main")
    scripts_dst = repo / "scripts"
    scripts_dst.mkdir()
    shutil.copy(PRUNE, scripts_dst / PRUNE.name)
    shutil.copy(SAFETY, scripts_dst / SAFETY.name)
    res = _run(["bash", "scripts/prune-stale-worktrees.sh", "--dry-run"], repo)
    assert res.returncode == 0
    assert "no stale worktrees" in res.stdout


def test_help_flag(fixture_repo: Path) -> None:
    """--help exits 0 and prints usage."""
    res = _run(["bash", "scripts/prune-stale-worktrees.sh", "--help"], fixture_repo)
    assert res.returncode == 0
    assert "Usage:" in res.stdout


def test_unknown_flag_exits_one(fixture_repo: Path) -> None:
    """Unknown flag is a usage error."""
    res = _run(["bash", "scripts/prune-stale-worktrees.sh", "--bogus"], fixture_repo)
    assert res.returncode == 1
    assert "unknown argument" in res.stderr


def test_handles_worktree_paths_with_spaces(tmp_path: Path) -> None:
    """Workspace paths containing spaces must not break the helper.

    Regression: an earlier implementation used `awk $2` to parse the
    porcelain `worktree <path>` line, which silently truncated everything
    past the first space. Both the main-worktree-skip logic and the
    per-worktree path comparisons then matched the wrong string and the
    script silently skipped every entry.     Caught by dogfooding against a real workspace whose path
    contained two consecutive spaces. (Project renamed 2026-06-01
    to `backlink-publisher/`, no spaces; regression test still
    applies to installations with spaces in their workspace path.)
    """
    # Build the same fixture as fixture_repo but rooted at a path that has spaces.
    spaced_root = tmp_path / "has spaces in name"
    spaced_root.mkdir()
    main = spaced_root / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "config", "user.email", "t@t")
    _git(main, "config", "user.name", "t")
    (main / "x").write_text("x")
    _git(main, "add", "x")
    _git(main, "commit", "-q", "-m", "x")
    bare = spaced_root / "origin.git"
    _git(main, "clone", "--bare", "-q", str(main), str(bare))
    _git(main, "remote", "add", "origin", str(bare))
    _git(main, "fetch", "-q", "origin")
    _git(main, "branch", "--set-upstream-to=origin/main", "main")

    # One merged+clean candidate worktree so we expect exactly 1 "would remove" line.
    merged_wt = spaced_root / "wt-merged"
    _git(main, "worktree", "add", "-b", "feat/merged", str(merged_wt))
    (merged_wt / "f.txt").write_text("a")
    _git(merged_wt, "add", "f.txt")
    _git(merged_wt, "commit", "-q", "-m", "merged")
    _git(main, "merge", "--ff-only", "-q", "feat/merged")
    _git(main, "push", "-q", "origin", "main")
    _git(main, "fetch", "-q", "origin")

    scripts_dst = main / "scripts"
    scripts_dst.mkdir()
    shutil.copy(PRUNE, scripts_dst / PRUNE.name)
    shutil.copy(SAFETY, scripts_dst / SAFETY.name)

    res = _run(["bash", "scripts/prune-stale-worktrees.sh", "--dry-run"], main)
    assert res.returncode == 0, f"stderr: {res.stderr}"
    assert "would remove:" in res.stdout, (
        "main worktree compare with wrong path string would skip every entry; "
        f"stdout={res.stdout!r} stderr={res.stderr!r}"
    )
    assert "wt-merged" in res.stdout
    assert "candidates for removal: 1" in res.stderr
