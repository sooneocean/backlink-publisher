#!/usr/bin/env bash
# Prune git worktrees whose branch tip is already merged into origin/main.
#
# Usage:
#   bash scripts/prune-stale-worktrees.sh           # interactive: prompt per candidate
#   bash scripts/prune-stale-worktrees.sh --dry-run # list candidates, no removal
#   bash scripts/prune-stale-worktrees.sh --force   # remove without prompting (cron-safe)
#   bash scripts/prune-stale-worktrees.sh --help    # this message
#
# Safety:
#   - Skips the main worktree.
#   - Skips any worktree with uncommitted changes (tracked OR untracked).
#   - Refuses to remove the worktree the script is running in.
#   - Detects squash-merged branches via `gh pr list` when available; falls back
#     to `git merge-base --is-ancestor` (catches normal merges only) when gh is
#     not installed or not authenticated.
#
# Exit codes:
#   0 — success (no candidates, or all candidates removed)
#   1 — usage error
#   2 — one or more candidate removals failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=./_worktree_safety.sh
source "$SCRIPT_DIR/_worktree_safety.sh"

MODE="interactive"  # interactive | dry-run | force

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) MODE="dry-run"; shift ;;
    --force)   MODE="force"; shift ;;
    --help|-h)
      sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *)
      echo "error: unknown argument: $1" >&2
      echo "run with --help for usage" >&2
      exit 1 ;;
  esac
done

# Ensure origin/main is current. If we cannot fetch (offline), continue with
# what we have but warn — staleness only causes false-negatives (keeping a
# worktree that could be removed), never false-positives (removing live work).
if ! git fetch --quiet origin main 2>/dev/null; then
  echo "warn: git fetch origin main failed; using local origin/main ref (may be stale)" >&2
fi

main_wt="$(wt_main_path)"

candidates=()
skipped_dirty=()
skipped_unmerged=()

while IFS='|' read -r wt_path wt_head wt_branch; do
  [[ -z "$wt_path" ]] && continue
  # Skip the main worktree
  [[ "$wt_path" == "$main_wt" ]] && continue

  echo "checking: $wt_path  ($wt_branch @ $wt_head)" >&2

  if ! wt_branch_in_main "$wt_path"; then
    skipped_unmerged+=("$wt_path")
    continue
  fi
  if ! wt_is_clean "$wt_path"; then
    skipped_dirty+=("$wt_path")
    continue
  fi
  candidates+=("$wt_path")
done < <(wt_list_porcelain)

echo "" >&2
echo "summary:" >&2
echo "  candidates for removal: ${#candidates[@]}" >&2
echo "  skipped (unmerged):     ${#skipped_unmerged[@]}" >&2
echo "  skipped (dirty):        ${#skipped_dirty[@]}" >&2
echo "" >&2

if [[ ${#candidates[@]} -eq 0 ]]; then
  echo "no stale worktrees"
  exit 0
fi

failures=0
for wt in "${candidates[@]}"; do
  case "$MODE" in
    dry-run)
      echo "would remove: $wt"
      ;;
    force)
      if wt_remove "$wt"; then
        echo "removed: $wt"
      else
        echo "failed: $wt" >&2
        ((failures+=1))
      fi
      ;;
    interactive)
      read -r -p "remove $wt? [y/N/q] " ans </dev/tty
      case "$ans" in
        y|Y)
          if wt_remove "$wt"; then
            echo "removed: $wt"
          else
            echo "failed: $wt" >&2
            ((failures+=1))
          fi
          ;;
        q|Q)
          echo "quit (remaining ${#candidates[@]} candidates not processed)" >&2
          break
          ;;
        *) echo "skipped: $wt" >&2 ;;
      esac
      ;;
  esac
done

[[ $failures -gt 0 ]] && exit 2
exit 0
