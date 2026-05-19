# Phase 0 ship-seal bootstrap runbook

Plan: [`2026-05-18-009-feat-telegraph-phase0-ship-seal-plan.md`](../plans/2026-05-18-009-feat-telegraph-phase0-ship-seal-plan.md) (v3).

Operational steps to activate the `phase0-seal/verify-head` GitHub Action
(plan-009 Unit 7) as a **required check** on `main`. The Action itself is
already committed; this runbook only documents the one-time GitHub-side
registration that the workflow file alone cannot perform.

Run **once**, after PR #78 merges and the workflow file lands on `main`.

## Prerequisites

- `gh` CLI authenticated (`gh auth status` shows your account) with
  repo-admin scope on `redredchen01/backlink-publisher`.
- Local working clone synced with `origin/main` (so `git log` shows the
  workflow commit).
- ~10 minutes of uninterrupted attention — the steps below must run in
  order and one step can leave the repo in a partial state if aborted
  mid-flight (Step 3 vs Step 5).

## Step 1 — Register the check name with GitHub

GitHub only "sees" a check name after at least one workflow run has
reported it. Without a prior run, the branch-protection UI's check-name
autocomplete is empty and `gh api -X PUT .../branches/main/protection`
would have to be configured blind.

**Action**: open a throwaway PR whose head branch matches the workflow's
`if:` filter (any of `feat/telegraph-adapter-unit*`,
`test/telegraph-rel-absent*`, `feat/telegraph-webui-docs*`):

```bash
git fetch origin
git switch -c feat/telegraph-adapter-unit2-smoke origin/main
# Make a no-op commit so the PR has a HEAD distinct from main
echo "smoke" > .smoke-bootstrap
git add .smoke-bootstrap
git commit -m "smoke: bootstrap phase0-seal check name (will be reverted)"
git push -u origin feat/telegraph-adapter-unit2-smoke
gh pr create --title "smoke: bootstrap phase0-seal/verify-head" \
  --body "Bootstraps the phase0-seal/verify-head check name. Closing without merge per plan-009 Unit 8." \
  --base main --head feat/telegraph-adapter-unit2-smoke
```

Wait for the `phase0-seal/verify-head` check to appear on the PR — it
will **fail** (no seal note at HEAD), which is the expected outcome of
this step. The point is purely to make GitHub register the check name.

## Step 2 — Add the check to branch protection

UI path (simplest):

1. Repository → Settings → Branches → main → Edit
2. Under "Require status checks to pass before merging" — search for
   `phase0-seal/verify-head` and add it.
3. Save.

CLI path (when the UI is unavailable; **exact payload** in the table
below — copy/paste verbatim):

```bash
gh api -X PUT \
  /repos/redredchen01/backlink-publisher/branches/main/protection \
  --input - <<'EOF'
{
  "required_status_checks": {
    "strict": false,
    "contexts": ["phase0-seal/verify-head"]
  },
  "enforce_admins": null,
  "required_pull_request_reviews": null,
  "restrictions": null
}
EOF
```

⚠️ **Beware**: this `PUT` REPLACES the protection rule wholesale. If the
repo already has additional required checks (`Backlink Publisher CI`
matrix, etc.), include them in `contexts` to preserve them. Capture the
current state first:

```bash
gh api /repos/redredchen01/backlink-publisher/branches/main/protection \
  | jq '.required_status_checks.contexts'
```

Add `phase0-seal/verify-head` to whatever that returns and use the merged
list as the new `contexts`.

## Step 3 — Smoke-test (negative)

Verify the check **blocks merge** when no seal exists. The throwaway PR
from Step 1 should now be unmergeable — confirm in the UI: the merge
button is greyed out with "Required status check `phase0-seal/verify-head`
is expected".

If the merge button is enabled, branch protection did NOT pick up the
new context — re-check Step 2.

## Step 4 — Smoke-test (positive)

Verify the check **allows merge** with a valid seal.

Attach a manual-kind seal note to the smoke PR's head SHA:

```bash
SMOKE_SHA=$(git -C $(git rev-parse --show-toplevel) rev-parse \
  feat/telegraph-adapter-unit2-smoke)
EVIDENCE_PATH="docs/phase0/smoke-evidence.md"
echo "smoke G1 evidence" > "$EVIDENCE_PATH"
git add "$EVIDENCE_PATH"
git commit -m "smoke: G1 evidence for phase0-seal bootstrap"
EVIDENCE_SHA=$(sha256sum "$EVIDENCE_PATH" | awk '{print $1}')

cat <<EOF | git notes --ref=refs/notes/phase0-seal add -f -F - "$SMOKE_SHA"
{
  "unit": "unit2",
  "branch": "feat/telegraph-adapter-unit2-smoke",
  "main_sha": "$(git rev-parse origin/main)",
  "sealed_at": "$(date -u +%FT%TZ)",
  "sealed_by": "operator:init",
  "verdict_ref": {
    "kind": "manual",
    "evidence_path": "$EVIDENCE_PATH",
    "evidence_sha256": "$EVIDENCE_SHA"
  }
}
EOF

git push origin "refs/notes/phase0-seal:refs/notes/phase0-seal"
git push origin feat/telegraph-adapter-unit2-smoke
```

The pushed commit triggers a fresh CI run. The check should now **pass**;
the PR's merge button becomes available. Don't merge it (Step 5 cleans up).

## Step 5 — Clean up

```bash
gh pr close <PR-number> --comment "Bootstrap complete; closing per plan-009 Unit 8."
git push origin --delete feat/telegraph-adapter-unit2-smoke
# Optionally remove the local note + the evidence commit if not on main:
git notes --ref=refs/notes/phase0-seal remove "$SMOKE_SHA" || true
git push origin :refs/notes/phase0-seal-smoke || true  # if you pushed a smoke ref
git branch -D feat/telegraph-adapter-unit2-smoke
```

## Rollback procedure

If the seal check causes unforeseen problems on production PRs:

1. **Operator side**: remove the phase-marker note from origin/main so
   the pre-push hook (Unit 5) falls back to pre-G1 §194 logic:
   ```bash
   git notes --ref=refs/notes/phase0-seal remove origin/main
   git push origin refs/notes/phase0-seal
   ```
   Pre-push hook stops requiring seal validation; legacy
   `PHASE0_ALLOW_LOCAL_PUSH=1` env override applies again.

2. **CI side**: remove `phase0-seal/verify-head` from branch protection
   (UI: same path as Step 2, uncheck the context; CLI: omit it from the
   `contexts` array in a fresh PUT).

3. **Workflow side**: the workflow file itself can stay — without the
   phase marker on main, the seal note never gets enforced; without the
   branch-protection requirement, the check is informational only.

## Known limitations (carried from plan v3 deferred residuals)

- `refs/notes/phase0-seal` namespace has no GitHub push protection —
  anyone with repo write access can push to it. Defense rests on the
  comment-author allowlist (`scripts/telegraph_spike/authorized-routine-bots.yaml`)
  and the body-sha256 + marker requirement in `validate_verdict_comment`.

- The hook-removal-before-PR-open window is real: a malicious operator
  could delete their local hook and push a non-sealed SHA directly. Main
  branch protection requires PR for merge regardless, so this only
  affects staged-branch refs (which are not protected on origin).

- No `phase0-seal reinit` verb yet. If a routine legitimately re-edits
  its comment (e.g., typo fix) and the original seal_body_sha256 no
  longer matches, the server-side `verify_seal.py` emits
  `pass-with-warning` (legitimate edit) instead of failing — see
  [`verify_seal.py` `body-sha-mismatch` branch](../../scripts/telegraph_spike/verify_seal.py).
  Operator recourse for un-supportable cases: blow away the seal and
  re-init.

## Related artifacts

- Plan: `docs/plans/2026-05-18-009-feat-telegraph-phase0-ship-seal-plan.md`
- CLI: `src/backlink_publisher/cli/phase0_seal.py` (init/show/verify/reseal/verify-hook)
- Pre-push hook installer: `scripts/install-pre-push-hook.sh`
- Sidecar: `scripts/telegraph_spike/verify_seal.py`
- Workflow: `.github/workflows/phase0-seal-check.yml`
- Allowlist: `scripts/telegraph_spike/authorized-routine-bots.yaml`
