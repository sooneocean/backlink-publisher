#!/usr/bin/env bash
# Install a per-clone git pre-push hook that gates Telegraph staged-branch
# pushes via the Phase 0 ship seal (plan 009 Unit 5). Pre-G1 (no phase marker),
# the legacy §194 fallback applies (PHASE0_ALLOW_LOCAL_PUSH=1 env override
# allowed). Post-G1, the env override does NOT bypass; the seal note at the
# pushed SHA must validate against the operator's local clone via
# `python -m backlink_publisher.cli.phase0_seal verify-hook --stdin-lines`.
#
# Run once per clone:  bash scripts/install-pre-push-hook.sh
# Re-run after fresh clone (git hooks are not committed).
#
# Companion of scripts/install-post-merge-hook.sh; same scope-warning shape
# for non-default core.hooksPath.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
HOOK_DIR="$(git -C "$REPO_ROOT" rev-parse --git-path hooks)"
HOOK_PATH="$HOOK_DIR/pre-push"

COMMON_DIR="$(git -C "$REPO_ROOT" rev-parse --git-common-dir)"
COMMON_DIR_ABS="$(cd "$COMMON_DIR" && pwd -P)"
HOOK_DIR_ABS="$(mkdir -p "$HOOK_DIR" && cd "$HOOK_DIR" && pwd -P)"
EXPECTED_HOOK_DIR_ABS="$COMMON_DIR_ABS/hooks"
if [[ "$HOOK_DIR_ABS" != "$EXPECTED_HOOK_DIR_ABS" ]]; then
  echo "warn: git core.hooksPath is overridden — hook will be written to a shared location" >&2
  echo "      installing to: $HOOK_DIR_ABS" >&2
  echo "      (this repo's own .git/hooks would normally be: $EXPECTED_HOOK_DIR_ABS)" >&2
  for scope in local global system; do
    val=$(git -C "$REPO_ROOT" config --$scope --get core.hooksPath 2>/dev/null || true)
    [[ -n "$val" ]] && echo "      core.hooksPath ($scope): $val" >&2
  done
  echo "" >&2
fi

if [[ -f "$HOOK_PATH" ]]; then
  # Idempotency: if the existing hook already contains the phase0-seal Unit 5
  # marker line, treat it as already installed and reinstall (safe — script is
  # source of truth). Otherwise warn that we're overwriting custom content.
  if grep -q "phase0_seal verify-hook --stdin-lines" "$HOOK_PATH"; then
    : # already managed by us; overwrite safely below
  else
    echo "warn: existing pre-push hook at $HOOK_PATH will be replaced" >&2
    echo "      backed up to: ${HOOK_PATH}.bak" >&2
    cp -p "$HOOK_PATH" "${HOOK_PATH}.bak"
  fi
fi

cat >"$HOOK_PATH" <<'HOOK_EOF'
#!/bin/sh
# Telegraph Phase 0 §194 + plan-009 Unit 5 pre-push gate.
#
# Pre-G1 (no phase-marker note on origin/main): legacy §194 fallback applies.
#   - Pushes to refs/heads/local/telegraph-unit*-staged are refused unless
#     PHASE0_ALLOW_LOCAL_PUSH=1 is set in the environment.
#
# Post-G1 (phase-marker note present on origin/main): seal enforcement.
#   - PHASE0_ALLOW_LOCAL_PUSH=1 no longer bypasses.
#   - Each Telegraph staged-branch ref in the push is validated by
#     `python -m backlink_publisher.cli.phase0_seal verify-hook --stdin-lines`.
#   - The Python exit code passes through verbatim (plan v3 auto-fix v2-F6).
#
# Closes the direct-SHA-push bypass (v1 adversarial probe #5): the hook keys
# on the remote_ref of each pushed line, not on the local_ref — so
# `git push origin <sha>:refs/heads/local/telegraph-unitN-staged` is gated
# the same as `git push origin local/telegraph-unitN-staged`.
#
# Managed by scripts/install-pre-push-hook.sh — DO NOT edit in place; rerun
# the installer after changes there.

set -e

remote="$1"

# Buffer stdin so we can scan + iterate it multiple times.
STDIN_BUFFER=$(cat)

# Quick exit: if no Telegraph staged-branch refs in this push, return 0
# immediately (the hook stays out of the way of regular feature branches).
TELEGRAPH_PRESENT=0
if printf '%s\n' "$STDIN_BUFFER" | grep -qE "refs/heads/local/telegraph-unit[0-9]+-staged"; then
    TELEGRAPH_PRESENT=1
fi
if [ "$TELEGRAPH_PRESENT" = "0" ]; then
    exit 0
fi

# Detached HEAD: refuse staged-branch push from detached HEAD (plan v3 §408).
if ! git symbolic-ref --quiet HEAD >/dev/null 2>&1; then
    echo "ERROR: phase0-seal: cannot push Telegraph staged branch with detached HEAD" >&2
    exit 1
fi

# Best-effort fetch the phase-marker note into a TEMP ref. NO `+` prefix —
# fetches into a distinct ref name so local notes the operator just wrote are
# preserved (plan v3 auto-fix v2-F4).
git fetch -q "$remote" "refs/notes/phase0-seal:refs/notes/phase0-seal-origin" >/dev/null 2>&1 || true

PHASE_STARTED=0
if git notes --ref=phase0-seal-origin show origin/main >/dev/null 2>&1; then
    PHASE_STARTED=1
elif git notes --ref=phase0-seal show origin/main >/dev/null 2>&1; then
    # Local cache: operator already fetched the marker in a prior network op.
    PHASE_STARTED=1
fi

if [ "$PHASE_STARTED" = "1" ]; then
    # Post-G1: seal enforcement. Pipe ALL stdin lines (not just Telegraph ones)
    # so verify-hook's structured stderr output covers every line uniformly.
    printf '%s\n' "$STDIN_BUFFER" \
        | "${PYTHON:-python3}" -m backlink_publisher.cli.phase0_seal verify-hook --stdin-lines
    exit $?
fi

# Pre-G1 fallback: legacy §194 logic — PHASE0_ALLOW_LOCAL_PUSH=1 env override
# applies. Iterate each ref in the buffer. Using a heredoc keeps the loop in
# the current shell so `exit 1` inside it terminates the hook (vs. a pipe
# which would put the loop in a subshell).
while read local_ref local_sha remote_ref remote_sha; do
    case "$remote_ref" in
        refs/heads/local/telegraph-unit*-staged)
            if [ "${PHASE0_ALLOW_LOCAL_PUSH:-0}" != "1" ]; then
                echo "ERROR: refusing to push '$remote_ref' to '$remote'" >&2
                echo "       Telegraph Phase 0 §194 blocks Unit 2/4/5/6 push until 6/01 G1 Pass." >&2
                echo "       See: docs/plans/2026-05-18-002-refactor-phase0-unblock-actions-plan.md" >&2
                echo "       Override (use after G1 Pass): PHASE0_ALLOW_LOCAL_PUSH=1 git push ..." >&2
                exit 1
            fi
            echo "WARNING: PHASE0_ALLOW_LOCAL_PUSH=1 set — allowing $remote_ref push" >&2
            ;;
    esac
done <<INNER_EOF
$STDIN_BUFFER
INNER_EOF

exit 0
HOOK_EOF

chmod +x "$HOOK_PATH"
echo "installed pre-push hook → $HOOK_PATH"
