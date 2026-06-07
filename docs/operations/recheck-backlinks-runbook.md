# recheck-backlinks — operations runbook

Plan: `docs/plans/2026-05-29-004-feat-recheck-backlinks-survival-loop-plan.md`

`recheck-backlinks` re-verifies previously-published backlinks for **liveness**,
**dofollow drift**, and **link/anchor tampering**, emits a `link.rechecked`
lifecycle time series to events.db, and surfaces decay counts on the `/ce:health`
dashboard. This version is an **observability slice** — it makes decay *visible*;
it does not auto-remediate, and (this release) it does not refresh the equity
ledger's liveness column (see Fast-follows).

## Invocation

```bash
recheck-backlinks                 # zero-network dry preview: which links WOULD be probed
recheck-backlinks --probe         # re-verify (network); emits link.rechecked events; exit 0
recheck-backlinks --probe --fail-on-dead   # exit 6 if any deterministic dead link is found
recheck-backlinks --probe --host medium.com --limit 25   # scoped run
cat live_urls.jsonl | recheck-backlinks --probe          # probe a piped live_url list (R11)
```

- **`--probe` is off by default.** No-probe is a zero-network preview so an
  operator sees the scope before any HTTP is made.
- **Exit 0 by default** (advisory diagnostic). `--fail-on-dead` exits **6** (the
  domain-alarm code) only when a *deterministic* dead link (`host_gone` /
  `link_stripped`) is found — `dofollow_lost` and `probe_error` never trip it.
- Stateless and **externally scheduled** (cron / remote-trigger); there is no
  in-repo scheduler. A flock guards against overlapping runs.

## Action loop (who watches, what cadence, what to do)

R10's purpose: *detected-but-unactioned decay = zero recovery value.* The loop is
only closed by a human, so name the owner and cadence:

- **Owner:** the external-link operator on rotation (the person who owns the
  publish pipeline). 
- **Cadence:** review the `/ce:health` decay banner once per working day (and on
  any `--fail-on-dead` cron alert).
- **Per-verdict remediation (close the loop):**
  - `host_gone` / `link_stripped` → the page or backlink is gone. Re-publish the
    backlink elsewhere or remove the dead target from active tracking. After
    acting, resolve via `remediation-queue --resolve <live_url>` or the
    `/ce:health` remediation panel Resolve button.
  - `dofollow_lost` → **advisory, confirm before acting.** The verifier UA may be
    cloaked (the platform can serve dofollow to real visitors and nofollow to the
    canary). Manually open the live page; if genuinely nofollow, decide whether
    to accept it or escalate to the platform. Escalate the operator decision
    with `remediation-queue --ack <live_url> --note "confirmed nofollow"`.
  - `probe_error` → transient/anti-bot/unreachable. No action; it stays selectable
    and will be re-probed. Optionally snooze noisy links:
    `remediation-queue --snooze <live_url> --days 7`.

### Closed-loop with remediation queue

Phase A (Plan 2026-06-07-001) delivered the **remediation queue**: events.db now
records `remediation.event` rows carrying an `action` field (`ack` / `resolve` /
`snooze`) per `live_url`. The `/ce:health` decay banner shows **unresolved decay
count** — resolved links are excluded — so a steady banner count now *does* mean
unaddressed decay.

See [Remediation Queue](#remediation-queue) below for CLI and WebUI workflows.

## Baseline metric (is the loop yielding?)

Commit a falsifiable bar **before** trusting the loop, then validate it on the
first full `--probe` sweep:

1. Record the current corpus size **C** = number of `publish.confirmed` live_urls
   (`recheck-backlinks` dry-preview count over the full corpus is a proxy).
2. State an **estimated dead-link rate hypothesis** (e.g. "we expect <2% of
   published backlinks to be dead within 90 days").
3. **Threshold:** the loop is yielding if the first full sweep surfaces a
   deterministic-dead rate *materially above* the build-time `stale`
   (age-based) heuristic the equity ledger already shows, **or** detects
   ≥X dofollow regressions/month. Do **not** use "detected any dead link" as the
   bar — almost any sweep clears it and it cannot distinguish signal from noise.

The first `--probe` full run validates or refutes the hypothesis; if the measured
dead rate is negligible and stable, reconsider the cron cadence rather than
declaring success by tautology.

## Coverage math (avoid starving the oldest links)

With corpus **C** placements, age threshold **N** days (default 14), per-run cap
**M** (default 50), and cron period **P** days, steady state needs ~C placements
re-probed every N days while each N-day window can probe `M × (N / P)`. Keep:

```
M × (N / P) ≥ C
```

Defaults N=14 / M=50 / P=1 (daily) cover ~700 placements per 14-day window. If the
live corpus exceeds that, **raise M or shorten the cron period** — N and M are
built-in (not externally configurable) this release by design. On a cold start
(no prior rechecks) the whole corpus is eligible; oldest-published-first ordering
drains the backlog deterministically over successive runs.

## Dual-source liveness authority (temporary, until plan-007)

This release writes decay to events.db only; it does **not** refresh the equity
ledger's liveness column (R6 deferred). So during the plan-007 window the two
surfaces can disagree: `/ce:health` may show a link `host_gone` while the equity
ledger still shows it `live`.

> **For dead-link triage, `/ce:health` decay counts are authoritative for
> liveness. The equity ledger's liveness column is publish-time-only until the R6
> fast-follow — do not act on it for decay.**

This is a deliberate, temporary divergence (the project is otherwise consolidating
on a single authoritative store via plan-007).

## Operational safety

- **Probe identity isolation:** the preflight probe UA is distinct from publish.
  Keep recheck off the publish host's IP/cookies so anti-bot reputation from
  automated probing does not bleed into real publishes (Cloudflare-fronted
  channels apply windowed anti-bot budgets).
- **Batch budget:** a probe batch has a wall-clock ceiling; a tarpitting host
  cannot stall the cron run indefinitely — remaining candidates defer to the next
  run (logged on stderr).
- **Concurrency:** a flock in the config dir skips a run if another holds it.

## Remediation Queue

Plan: `docs/plans/2026-06-07-001-feat-backlink-remediation-queue-plan.md`

`remediation-queue` is the operator interface for tracking decay remediation
status. It reads/writes `remediation.event` rows in events.db — the same store
that backs `link.rechecked` — so there is no separate persistence layer.

### Concept

Each `live_url` with decay has a **remediation record** tracking the latest
operator action:

| Action | Meaning | Dashboard effect |
|--------|---------|------------------|
| `ack` | Operator knows about it but hasn't fixed it yet | Counted as unresolved |
| `resolve` | Operator fixed it (re-published / removed target) | Excluded from decay banner |
| `snooze` | Temporary ack for N days | Unresolved until `snooze_until_utc` expires |

A link with no remediation record is implicitly **unresolved** (same as
un-actioned decay).

### CLI invocation

```bash
remediation-queue --list                              # human table (default)
remediation-queue --list --json                       # JSONL for pipe
remediation-queue --ack <live_url>                    # acknowledge
remediation-queue --ack <live_url> --note "checking"  # ack with optional note
remediation-queue --resolve <live_url>                # mark fixed
remediation-queue --resolve <live_url> --note "republished"
remediation-queue --snooze <live_url> --days 7        # snooze 7 days
remediation-queue --snooze <live_url> --days 14 --note "waiting on platform"
remediation-queue --list --fail-on-unresolved         # exit 6 if any unresolved
```

- **Exit 0** default (advisory). `--fail-on-unresolved` exits **6**.
- URL scheme validation: `UsageError` on malformed URLs.
- All commands emit RECON-style logs to stderr; `--list --json` emits
  unresolved-links JSONL on stdout.

### WebUI remediation panel

`/ce:health` shows a **Remediation** card below the decay banner:

1. **Card header badge** — unresolved count (may differ from total decay count).
2. **Unresolved table** — columns: Live URL, Latest Action, Note (if any).
3. **Per-row action buttons:**
   - **Ack** — `POST /ce:health/remediation` with `{"action":"ack","live_url":"..."}`
   - **Resolve** — same endpoint, `action: "resolve"`
   - **Snooze (7d)** — same endpoint, `action: "snooze", "days": 7`
4. **Empty state** — "No unresolved backlink decay" when all clear.
5. **Page reload** on success; `alert()` on error.

All operations are CSRF-protected (global guard). Fail-open: if events.db is
unreachable, the panel shows the empty state (non-blocking).

### Unresolved decay count

The `/ce:health` decay banner now displays **unresolved decay count**, which
excludes links whose latest remediation event is `resolve`. The total (including
resolved) is available via a "show all" toggle or `remediation-queue --list --json`.

### Design invariants

- **Latest action wins.** For each `live_url`, only the most recent
  `remediation.event` determines state. Ack does not override a previous resolve.
- **Snooze expiry is advisory.** The dashboard checks `snooze_until_utc` at render
  time; expired snoozes revert to unresolved. There is no scheduled timer.
- **Never-raises.** All remediation IO is fail-open: read failure falls back to
  raw decay counts (`--list` shows empty); write failure logs a warning
  and continues. The dashboard never crashes due to remediation store issues.

## Fast-follows (deferred, with triggers)

1. **R6 — ledger liveness writeback.** Refresh the equity ledger's liveness column
   from recheck verdicts. **Trigger:** after `plan-007` (history_store→events.db
   migration) U1+U3 merge — its events.db `articles` columns + projector are the
   sanctioned sink. If plan-007 slips >~6 months, open an independent follow-up to
   project deterministic-dead `link.rechecked` verdicts onto a minimal liveness
   read path so the ledger stops being stale.
2. **`suspected_dead` derivation.** Surface links with K consecutive `probe_error`
   over D days. **Trigger:** after the first full `--probe` sweep calibrates the
   real probe_error rate (to set K/D), and after adding a dedicated selection lane
   for probe_error-only links so the M-cap can't starve them out of re-selection.
