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
- **Per-verdict remediation:**
  - `host_gone` / `link_stripped` → the page or backlink is gone. Re-publish the
    backlink elsewhere or remove the dead target from active tracking.
  - `dofollow_lost` → **advisory, confirm before acting.** The verifier UA may be
    cloaked (the platform can serve dofollow to real visitors and nofollow to the
    canary). Manually open the live page; if genuinely nofollow, decide whether
    to accept it or escalate to the platform.
  - `probe_error` → transient/anti-bot/unreachable. No action; it stays selectable
    and will be re-probed. (A persistent cluster signals a host or anti-bot issue
    worth investigating manually.)

### Open-loop honesty

Until the R6 fast-follow lands there is **no remediation-status store**: after you
fix a dead link, events.db has nowhere to record "handled," so the `/ce:health`
banner keeps showing the same decay count until the next probe re-verifies the
link. Do **not** read a steady banner count as "nobody acted." A lightweight
ack/snooze is intentionally out of scope this release (the origin made a
remediation queue a non-goal).

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
