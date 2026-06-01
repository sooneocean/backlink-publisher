# Publish pipeline saga runbook

Origin: [`2026-05-28-publish-saga-contracts-requirements.md`](../brainstorms/2026-05-28-publish-saga-contracts-requirements.md).
Plan: [`2026-05-28-005-feat-publish-saga-hardening-plan.md`](../plans/2026-05-28-005-feat-publish-saga-hardening-plan.md).

This runbook documents the observable contract for every step in the `publish-backlinks` saga, the exit codes, the RECON fields that prove correctness, and the operator action for each failure mode.

---

## 1. Pipeline overview

Six CLI entrypoints chain via stdin/stdout JSONL:

```
seeds.jsonl
  → plan-backlinks     # step 1 — plan generation
  → validate-backlinks # step 2 — link/SEO validation
  → publish-backlinks  # step 3 (inner saga: 3a–3k per row)
       ↓
  report-anchors / footprint / phase0-seal  # steps 4-6
```

`publish-backlinks` is where failures matter most. Each row goes through an inner saga (3a–3k). All outer steps are non-transactional — failures in step 1-2 abort cleanly with no side effects.

---

## 2. Exit codes

| Code | Meaning | Operator action |
|------|---------|----------------|
| 0 | All rows succeeded (drafted or published) | None |
| 1 | Usage error / config / dedup-enforce precondition | Fix config or run dedup bootstrap |
| 2 | Input validation failure (bad payload / CLI arg) | Fix upstream `plan-backlinks` or `validate-backlinks` output |
| 3 | Auth expired or dependency error | Re-bind credentials via WebUI or CLI |
| 4 | All dispatched rows failed (network / service) | Check platform status; retry with `--resume` |
| 5 | Partial publish: some rows succeeded, some failed verification | Inspect `dropped.unverified` in RECON; rows are dedup-marked `done` but output has `_unverified` status |
| 6 | Reserved (phase0-seal related) | — |

**Exit 5 is not a rollback trigger.** The row was published; the link-verification check failed. The operator should inspect the live URL and decide whether to re-publish or accept.

---

## 3. RECON fields — what to monitor

Every run emits two `level=RECON` JSON lines to stderr. Parse with `jq 'select(.level=="RECON")'`.

### `dedup_reconciliation`

```json
{
  "level": "RECON",
  "msg": "dedup_reconciliation",
  "skipped_already_published": 0,
  "held_uncertain": 0,
  "dispatched": 1,
  "skipped_canary": 0
}
```

**Row accounting invariant:** `skipped_canary + skipped_already_published + held_uncertain + dispatched = total input rows`

If this invariant breaks, a row was silently dropped — investigate immediately.

| Field | Healthy signal | Investigate if... |
|-------|---------------|------------------|
| `skipped_already_published` | ≥ 0 | Unexpectedly large; suggests duplicate input |
| `held_uncertain` | 0 | > 0 with `DEDUP_ENFORCE=1` — either a concurrent run holds the key (wait ~10 min and re-run), OR a prior run crashed mid-dispatch (SIGKILL/OOM) and the key was promoted to `uncertain` (the post may already be live). For the crash case, re-running will NOT clear it — run `publish-backlinks --list-uncertain` to inspect, verify whether the post is live, then `publish-backlinks --adjudicate-uncertain <platform> <target_url> --to (succeeded\|failed) --reason <text>` and re-run `--resume`. |
| `dispatched` | = total input rows (first run) | < expected — some rows were silently filtered |
| `skipped_canary` | 0 | > 0 — at least one platform has `hard_skip=true` and is quarantined. See `canary-targets` runbook. |

### `publish_reconciliation`

```json
{
  "level": "RECON",
  "msg": "publish_reconciliation",
  "input_payloads": 3,
  "output_rows": 3,
  "delta": 0,
  "dropped": {"failed": 0, "unverified": 0},
  "dropped_ids": {"failed": [], "unverified": []}
}
```

| Field | Healthy signal | Investigate if... |
|-------|---------------|------------------|
| `delta` | 0 | > 0 — output rows < input; some rows were not dispatched (likely `held_uncertain` or unreachable) |
| `dropped.failed` | 0 | > 0 — adapter returned an error; `dropped_ids.failed` lists the row IDs |
| `dropped.unverified` | 0 | > 0 — published successfully but link-verification found the backlink absent; `dropped_ids.unverified` lists the row IDs |
| `checkpoint_disabled` | absent | `true` — checkpoint creation failed (e.g., disk full). **This run cannot be resumed.** Fix disk space before retrying. |

---

## 4. Inner saga: per-row steps 3a–3k

### Step 3a — CLI arg parse
- Success: valid args, exits normally
- Failure: exit 2
- No compensation needed

### Step 3b — Checkpoint creation
- Success: `run_id` created; run can be resumed
- Failure: `checkpoint_disabled=true` in `publish_reconciliation` RECON; run continues in degraded mode
- Compensation: none (idempotent: re-run from scratch is safe)
- **Operator:** if `checkpoint_disabled=true`, do not use `--resume` — start a fresh run

### Step 3c — Adapter setup verification
- Success: credentials present and structurally valid
- Failure: exit 3 (`DependencyError`)
- Compensation: none (no publish happened)
- **Operator:** re-bind credentials via `publish-backlinks bind-channel --channel <platform>`

### Step 3d — Publish-time URL reachability
- Success: target URL and main domain respond 2xx
- Failure: row is `skipped_unreachable` (counts as dispatch, not fail); no dedup entry written
- Compensation: none
- **Operator:** target must be live before publishing; check DNS / CDN propagation

### Step 3e — Dedup gate
- Success (observe mode): intent recorded, row dispatched
- Success (enforce mode): absent/failed/stale → claim + dispatch; done → skip; uncertain → hold
- Failure: a `done` row force-republished without explicit `--force-manifest` → exit 1
- **Operator:** use `publish-backlinks dedup-status` to inspect gate state; `--force-manifest` to unlock a held row

### Step 3f — Canary health gate
- Success (default): advisory WARNING emitted, dispatch continues
- Hard-skip path: `hard_skip=true` + quarantined → row filtered, `skipped_canary` incremented
- Compensation: none
- **Operator:** run `canary-targets check` and inspect `canary-health.json`; reset quarantine with `canary-targets reset --platform <p>` after confirming health

### Step 3g — Token drift check
- Success: no config token revocation between run start and this row
- Failure: exit 3 (AuthExpiredError logged, run aborts immediately — remaining rows not dispatched)
- Compensation: dedup store row left in `attempting` → auto-transitions to `failed` on next dedup sweep; run checkpoint updated
- **Operator:** re-bind token, then resume or re-run

### Step 3h — Adapter dispatch
- Success: `AdapterResult.status` in `{drafted, published}`; row written to stdout JSONL
- Failure modes:
  - `AuthExpiredError` → exit 3 (abort run)
  - `BannerUploadError` / `ContentRejectedError` → row marked `failed`, continue remaining rows
  - `ExternalServiceError` (5xx, timeout, rate-limit) → row marked `failed`, continue
  - `DependencyError` → exit 3 (abort)
  - Unexpected exception → row marked `failed`, continue
- Compensation for in-platform side effects: **none programmable** — the platform has no atomic rollback. If a partial post was created, operator must delete it manually from the platform dashboard.
- Retry policy: `ExternalServiceError` triggers the reliability policy (circuit-breaker + jitter retry) when `BACKLINK_PUBLISHER_RELIABILITY_POLICY=1`. All other errors are terminal for the row.

### Step 3i — Link-verification
- Success: published URL fetched, backlink found with correct anchor and dofollow attribute
- Failure: `verify_ok=False`; output row status appended with `_unverified`; dedup store still records `done`
- Compensation: none (post is live; verification is advisory)
- **Operator:** fetch the URL manually, inspect with `report-anchors`, decide whether to re-publish

### Step 3j — Dedup terminal record
- Success: row written as `done` in dedup store with `verify_ok` flag
- Failure: store write error logged as WARNING; run continues
- Compensation: row may re-dispatch on next run (dedup will miss it)

### Step 3k — Checkpoint update
- Success: run checkpoint updated with `done` + `published_url`
- Failure: WARNING logged; `run_id` set to `None` (further checkpoint updates skipped)

---

## 5. Gap registry status

| Gap | Description | Status |
|-----|-------------|--------|
| G1 | Steps 1–2 not modeled as saga (no retry/rollback) | **Open** — out of scope for v1; document-only |
| G2 | Step 3d (reachability) has no retry | **Open** — operator must fix target before re-run |
| G3 | Checkpoint failure invisible in RECON | **Closed** — `checkpoint_disabled=true` added to `publish_reconciliation` RECON |
| G4 | Canary-skipped rows not counted in `dedup_reconciliation` | **Closed** — `skipped_canary` field added |
| G5 | `BannerUploadError` / `ContentRejectedError` have no structured error class in RECON | **Open** — logged as `failed` in `dropped_ids`; error detail only in WARNING line |
| G6 | Step 3g token-drift check does not clean up `attempting` row | **Open** — dedup sweep handles it eventually |
| G7 | Unverified rows not surfaced in `publish_reconciliation` RECON | **Closed** — `dropped.unverified` already in RECON |
| G8 | Steps 4–6 not in saga model | **Open** — these are read-only reporting steps; failure is non-destructive |

---

## 6. Retry and resume procedures

### Retry after exit 3 (auth expired)

```bash
publish-backlinks bind-channel --channel <platform>
# Then re-run from scratch — run_id is invalid after auth error
publish-backlinks [original args]
```

### Retry after exit 4 (all rows failed, checkpoint exists)

```bash
publish-backlinks --resume <run_id>
# run_id is emitted on stderr as: publish-backlinks: run_id=<id>
```

If checkpoint was disabled (`checkpoint_disabled=true`):
```bash
# Cannot resume — re-run from scratch
publish-backlinks [original args]
```

### Retry after exit 5 (verification failures)

```bash
# Inspect which rows failed verification
jq 'select(.level=="RECON" and .msg=="publish_reconciliation") | .dropped_ids.unverified' <<< "$STDERR"

# Rows are dedup-marked done; to re-publish, use force manifest
publish-backlinks preview-manifest | jq 'select(.id | IN("row1","row2")) | .force=true' > force.json
publish-backlinks --force-manifest force.json [original args]
```

### Investigate a held row (dedup uncertain)

```bash
publish-backlinks dedup-status --platform <platform> --id <row_id>
# If the prior run is confirmed dead (no process, checkpoint shows failed):
publish-backlinks dedup-status --unlock <row_id>
```
