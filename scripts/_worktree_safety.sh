#!/usr/bin/env bash
# Shared safety helpers for worktree-cleanup scripts.
# Sourced by scripts/prune-stale-worktrees.sh and the post-merge hook installed
# by scripts/install-post-merge-hook.sh. Not intended to be executed directly.
#
# All helpers return 0 (success) or non-zero (failure/skip) and print diagnostic
# messages to stderr. None mutate state.

# wt_is_clean <worktree-dir>
#   Returns 0 if the worktree has no uncommitted changes (tracked or untracked).
#   Returns 1 if dirty; prints summary to stderr.
wt_is_clean() {
  local wt_dir="$1"
  local status
  status="$(git -C "$wt_dir" status --porcelain 2>/dev/null)"
  if [[ -z "$status" ]]; then
    return 0
  fi
  local lines
  lines=$(echo "$status" | wc -l | tr -d ' ')
  echo "  dirty: $wt_dir has $lines uncommitted change(s)" >&2
  return 1
}

# wt_branch_in_main <worktree-dir>
#   Returns 0 if the worktree's branch tip is reachable from origin/main, OR
#   if its branch has a merged PR on GitHub whose mergeCommit is reachable.
#   Returns 1 otherwise; prints reason to stderr.
#
#   Handles squash-merge semantics: the worktree's pre-squash branch tip will
#   NOT be an ancestor of origin/main, so a bare `git merge-base --is-ancestor`
#   alone misses squash-merged worktrees. We try both gates and accept either.
wt_branch_in_main() {
  local wt_dir="$1"
  local head branch
  head="$(git -C "$wt_dir" rev-parse HEAD 2>/dev/null)" || return 1
  branch="$(git -C "$wt_dir" branch --show-current 2>/dev/null)"

  # Gate 1: direct ancestor (catches normal merges, fast-forwards)
  if git merge-base --is-ancestor "$head" origin/main 2>/dev/null; then
    return 0
  fi

  # Gate 2: squash-merged via GitHub PR — find merged PR for this branch,
  # check whether its merge commit is on main. Degrades gracefully if gh
  # is unavailable: we just say "not merged" and the caller keeps the worktree.
  if ! command -v gh >/dev/null 2>&1; then
    echo "  unmerged: $branch tip not on origin/main (gh unavailable; squash-merged branches may be missed)" >&2
    return 1
  fi
  local merge_sha
  merge_sha="$(gh pr list --head "$branch" --state merged --json mergeCommit --jq '.[0].mergeCommit.oid' 2>/dev/null || true)"
  if [[ -n "$merge_sha" ]] && git merge-base --is-ancestor "$merge_sha" origin/main 2>/dev/null; then
    return 0
  fi

  echo "  unmerged: $branch tip ($head) not reachable from origin/main" >&2
  return 1
}

# wt_remove <worktree-dir>
#   Wrapper around `git worktree remove`. Caller is responsible for having
#   already passed wt_is_clean. Refuses to remove the worktree the script
#   is currently running in (cannot remove the directory under your feet).
wt_remove() {
  local wt_dir="$1"
  local wt_abs cwd_abs
  wt_abs="$(cd "$wt_dir" 2>/dev/null && pwd -P)" || {
    echo "  error: $wt_dir does not exist" >&2
    return 1
  }
  cwd_abs="$(pwd -P)"
  if [[ "$wt_abs" == "$cwd_abs" ]] || [[ "$cwd_abs" == "$wt_abs"/* ]]; then
    echo "  error: refusing to remove $wt_dir — you are inside it. cd elsewhere and re-run." >&2
    return 1
  fi
  git worktree remove "$wt_dir"
}

# wt_list_porcelain
#   Echoes one line per worktree as: "<abspath>|<head>|<branch>"
#   Uses substr (not $2) because worktree paths may contain spaces — awk's
#   default field-split would silently truncate everything past the first
#   space and the script would compare wrong strings everywhere.
wt_list_porcelain() {
  git worktree list --porcelain | awk '
    /^worktree / { path=substr($0, 10); next }
    /^HEAD / { head=substr($0, 6); next }
    /^branch / { branch=substr($0, 8); sub(/^refs\/heads\//, "", branch); print path "|" head "|" branch; next }
    /^detached/ { print path "|" head "|<detached>"; next }
    /^$/ { path=""; head=""; branch="" }
  '
}

# wt_main_path
#   Echoes the absolute path of the main worktree (the first entry of
#   `git worktree list --porcelain`). Space-safe per wt_list_porcelain.
wt_main_path() {
  git worktree list --porcelain | awk '/^worktree / { print substr($0, 10); exit }'
}
