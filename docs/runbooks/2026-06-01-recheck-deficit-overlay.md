# Recheck deficit-overlay operations runbook

Plan: [`2026-06-01-006-feat-recheck-deficit-overlay-replan-plan.md`](../plans/2026-06-01-006-feat-recheck-deficit-overlay-replan-plan.md).
Origin: [`2026-06-01-recheck-ledger-liveness-writeback-requirements.md`](../brainstorms/2026-06-01-recheck-ledger-liveness-writeback-requirements.md).

`recheck-overlay` is a **read-only** CLI verb that bridges the recheck survival loop (#310) to deficit-driven re-planning (#313). It reads the *latest* `link.rechecked` verdict per link from `events.db`, discounts dead / `dofollow_lost` links from each target's `live_dofollow`, prunes the dead platform from `live_dofollow_platforms`, and re-emits the discounted equity-ledger JSONL. It **mutates nothing**.

It exists because the equity-ledger's `live_dofollow` is **publish-time-clock only** and never reads recheck verdicts: a cron-detected dead link still counts as live equity, so `plan-gap`'s `deficit = max(0, desired − live_dofollow)` reads 0 and proposes **zero** replacement seeds. The overlay closes that loop without touching the ledger, the projector, or the events schema.

---

## 1. The re-plan recipe (cron / headless)

```
equity-ledger | recheck-overlay | plan-gap --desired N --language L | plan-backlinks → validate → publish
```

`recheck-overlay` sits between `equity-ledger` and `plan-gap`. stdout is the discounted ledger JSONL (pure data — the banner and discount tally go to **stderr**). The operator still gates `plan → validate → publish`; the overlay only changes what `plan-gap` *counts*, never auto-publishes.

Run it without `plan-gap` to **see** the discounted ledger directly:

```
equity-ledger | recheck-overlay   # discounted live_dofollow on stdout; tally on stderr
```

---

## 2. What it discounts (the verdict taxonomy)

| Latest verdict | Effect on `live_dofollow` | Platform pruned? | Trips `--fail-on-dead`? |
|---|---|---|---|
| `host_gone` / `link_stripped` | −1 (dead) | yes | **yes** |
| `dofollow_lost` | −1 (dofollow weight gone) | yes | no — advisory degradation |
| `alive` | no change (restores if it is the latest verdict) | no | no |
| `probe_error` | no change (indeterminate; re-probed later) | no | no |
| *unrecognized* | no change — **quarantined + counted on stderr** | no | no |

**Recency wins.** Only the latest verdict per link governs (ordered by `ts_utc`, `events.id` as a same-timestamp tiebreaker). An `alive` newer than a prior `host_gone` restores the link to the count. Re-running on a fixed event set is deterministic.

**Pruning frees the platform for re-fan-out.** Removing the dead platform from `live_dofollow_platforms` lets `plan-gap`'s fan-out re-propose it (the old link there is dead, so a replacement there is valid) — it does **not** force the replacement elsewhere.

---

## 3. Exit codes

| Code | Meaning |
|---|---|
| 0 | Advisory success (default). Also: absent `events.db` → the ledger passes through unchanged; empty stdin → nothing to discount. |
| 1 | Usage error. |
| 2 | Malformed stdin JSONL (strict). |
| 3 | `events.db` exists but is unreadable (`DependencyError`). |
| 6 | `--fail-on-dead` opt-in: a deterministic-dead verdict was discounted. Emitted **after** the discounted ledger is written, so the pipe data is intact. `dofollow_lost` never trips this. |

---

## 4. Retirement condition (R6-proper)

The overlay is **interim and throwaway by design.** The ledger's stored liveness stays publish-time-clock until the proper fix lands: **R6-proper** writes recheck verdicts into the ledger's own liveness (via the plan-007 `articles` columns). That path carries two P0 correctness gaps the overlay sidesteps by construction (a dead link is a *discounted count*, never a `failed` target; there is no shared `articles` column for a later `publish.verified` to revive). When R6-proper ships, retire `recheck-overlay` from the recipe and drop this runbook.
