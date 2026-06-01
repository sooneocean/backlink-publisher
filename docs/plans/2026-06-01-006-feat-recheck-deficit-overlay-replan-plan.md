---
title: "feat: Recheck-verdict deficit-overlay — close the recheck→re-plan loop (read-only)"
type: feat
status: completed
shipped: aa0464b9 (#357)
date: 2026-06-01
deepened: 2026-06-01
origin: docs/brainstorms/2026-06-01-recheck-ledger-liveness-writeback-requirements.md
claims: {}
---

# feat: Recheck-verdict Deficit-Overlay (close the #310 → #313 loop)

## Overview

`recheck-backlinks` (#310, cron/CLI) writes authoritative `link.rechecked` verdicts
(`host_gone` / `link_stripped` / `dofollow_lost` / `alive` / `probe_error`) into `events.db`, but
`plan-gap` (#313) computes its deficit from the equity-ledger's `live_dofollow`, whose liveness is
**publish-time-clock only** and never reads recheck verdicts. So a cron-detected dead link still
counts as live equity → `deficit = max(0, desired − live_dofollow)` reads 0 → **zero replacement
seeds**. The two big investments are wired in parallel but never in series.

This plan adds a **read-only `recheck-overlay` verb** that sits in the pipe between `equity-ledger`
and `plan-gap`:

```
equity-ledger | recheck-overlay | plan-gap --emit-stale --desired N --language L | plan-backlinks → validate → publish
```

(`--emit-stale` is required: a discounted-to-zero aged target is often `liveness=stale`/`unverified`,
which `plan-gap` suppresses by default — see Key Decisions → "Re-plan stale aged targets".)

It reads the latest `link.rechecked` verdict per link from `events.db` (read-only), discounts
deterministically-dead and `dofollow_lost` links from each target's `live_dofollow` count (and prunes
the dead platform from `live_dofollow_platforms`), and re-emits **ledger-shaped JSONL**. `plan-gap`'s
existing deficit math and platform fan-out then re-count and propose replacements **avoiding the dead
platform**, with no change to `gap/engine.py` or `cli/plan_gap.py`. The overlay mutates nothing.

This is the **interim** path. The "proper" fix (R6 — write recheck verdicts into the ledger's own
liveness via plan-007's `articles` columns) carries two P0 correctness gaps (failed-target suppression;
cross-kind `publish.verified` revival of a dead marker) and a deep plan-007 U5 dependency. The overlay
sidesteps both by construction (a dead link is a *discounted count*, never a `failed` target) and is
**throwaway by design** — retired when R6-proper lands (see origin: deferred consolidation).

## Problem Frame

The operator's cron loop *sees* the death (`derive_decay_counts` banner) and cannot *act* on it: only
the manual WebUI `recheck_one` path drives re-planning today. A headless/cron operator who runs
`recheck-backlinks` then `plan-gap` gets zero replacement seeds for a confirmed-dead link — the
project's signature **false-success at the integration layer**: a dead link counts as live equity, so
the deficit reads 0 and no replacement is planned (see origin: Problem Frame).

## Requirements Trace

(Origin: `docs/brainstorms/2026-06-01-recheck-ledger-liveness-writeback-requirements.md`)

- **R1.** Overlay reads the **latest** `link.rechecked` verdict per link; `host_gone` / `link_stripped`
  → link is **discounted from its target's `live_dofollow`** count.
- **R2.** `dofollow_lost` verdict discounts the link from `live_dofollow`. Note `live_dofollow` is a
  single dofollow-and-live integer (there is **no** separate dofollow sub-counter), so this decrement is
  arithmetically the same as a dead discount; the verdict is read directly and does not re-derive the
  ledger's static-manifest dofollow classification. *(Resolved in review: `dofollow_lost` keeps a full
  `live_dofollow` discount — a nofollow'd link passes zero equity — but stays advisory for the
  `--fail-on-dead` gate. See Open Questions → Resolved in Review.)*
- **R3.** `probe_error` is ignored (no discount, re-probed); `alive` confirms the link live.
- **R4.** Recency wins: the latest verdict per link governs; an `alive` newer than a prior dead verdict
  restores the link to the count.
- **R5.** The adjusted `live_dofollow` (and pruned `live_dofollow_platforms`) feeds `plan-gap`'s
  **existing** deficit math + fan-out unchanged; replacements avoid the dead platform.
- **R6.** Overlay is **read-only and pure**: mutates nothing (events.db, dedup.db, ledger,
  history_store, canary-health.json); no schema change, no projector change, no new event kind.
- **R7.** With the documented recipe (`plan-gap --emit-stale`, no `--include-failed`), a
  deterministic-dead verdict newer than the link's last live signal increases the target's deficit by one
  and the re-plan produces a replacement seed for that target — **including** when the target's ledger
  `liveness` is `stale`/`unverified` (the aged-link case). A no-op overlay is theater. *(Verified by the
  positive-assertion + characterization tests in Unit 4.)*
- **R8.** Recency test: a later `alive` verdict restores the link (deficit returns to baseline); the
  overlay is deterministic given the same event set (re-running converges).

## Scope Boundaries

- **Ledger liveness writeback (R6-proper)** — DEFERRED. The overlay does not change the ledger's stored
  liveness, projector, or schema. Recorded as the eventual consolidation that retires the overlay.
- **Replacement provenance / `decay_origin`** (which dead link a replacement replaces) — OUT.
- **`suspected_dead` derivation** (K-consecutive `probe_error`) — OUT (separate trigger).
- **Recheck coverage-deficit warning** (`M·(N/P) ≥ C`) — OUT.
- **Remediation / ack / snooze queue** — OUT (origin non-goal).
- **`liveness_source` operator dimension** — OUT of this interim path. (`liveness_source` = a future
  ledger dimension recording *which* signal produced each liveness assertion — publish-time-clock vs
  recheck-confirmed — so the operator can see how much equity rests on never-re-probed evidence. It is a
  ledger-display feature that belongs with R6-proper.)
- **Equity-ledger *display* integration** — OUT. The overlay's own stdout **is** the discounted-ledger
  view (pipe `equity-ledger | recheck-overlay` to see discounted counts); no change to the
  `equity-ledger` verb. (Resolves the origin's deferred display question.)
- No change to `recheck-backlinks` verdict taxonomy/probe; no change to `plan-gap`'s fan-out logic; no
  auto-publish (operator still gates `plan → validate → publish`).

## Context & Research

### Relevant Code and Patterns

- **plan-gap engine (the injection contract):** `gap/engine.py:92` `plan_gap(rows, opts, *, active_dofollow=None)`
  consumes **plain dict rows** read from stdin (`cli/plan_gap.py:107-123`), reading exactly
  `target_url` (`:116`), `live_dofollow` via `_coerce_live_dofollow` (`:128`), `liveness` (`:127`),
  `liveness_verified_at` (`:121`), `live_dofollow_platforms` (`:164`); `deficit = max(0, desired − live_dofollow)`
  (`:158`). Missing keys are fail-safe (`_coerce_live_dofollow`, `:72-76`). **A row-rewriting overlay is
  a drop-in — no engine or `cli/plan_gap.py` change.**
- **Ledger row shape:** `ledger/model.py:59` `LedgerRow` with `target_url`, `live_dofollow`,
  `live_dofollow_platforms` (`:80`), serialized via `to_jsonl_dict()` (`:95`). Built by
  `ledger.build_ledger(...)` (`ledger/aggregate.py:68`); `live_dofollow` computed publish-time-clock
  only in `_link_liveness` (`aggregate.py:50`, `:117-122`) from `history_store` rows
  (`ledger/sources.py:130-153`) — **never reads `LINK_RECHECKED`** (confirms the bug).
- **Recheck event shape:** emitted by `recheck/events_io.py:33` `emit_recheck`. Payload: `verdict`,
  `reason`, `live_url`, `platform`, `expected_nofollow`, `anchor_drift`, `source`. First-class
  **columns**: `target_url`, `host`, `article_id`, `ts_utc`, `id` — note `target_url`/`article_id` can be
  NULL on stdin-sourced rechecks. Verdict taxonomy + the
  `DETERMINISTIC_DEAD = {host_gone, link_stripped}` set live in `recheck/verdicts.py` (cite symbols, not
  line numbers; `dofollow_lost` is advisory, never trips `--fail-on-dead`) — note
  `dofollow_lost ∉ DETERMINISTIC_DEAD` (advisory by design), so the overlay defines its **own** discount
  set.
- **Latest-verdict-per-link pattern to adapt:** `derive_decay_counts` (`events_io.py:73-100`) and
  `_recheck_cursors` (`selection.py:126-148`) key by **`article_id`**, order by **`ts_utc`** via
  `_parse_ts` (`selection.py:56-64`, normalizes to aware UTC), resolving ties by first-seen (strict
  `ts >`, no `id` tiebreak). `article_id` is 1:1 with `live_url` (`articles.live_url UNIQUE`,
  `schema.py:58`) **when present**, but is NULL on stdin rechecks — so the overlay keys on canonical
  `live_url` (article_id fast path) and adds an `events.id` same-`ts` tiebreaker (see Key Decisions).
- **Read API:** `EventStore.query(sql, params)` (`events/store.py:320`) — SELECT-only, enforced at
  runtime (`:332`). Filter `WHERE kind=?` covered by `idx_events_kind_ts` (`schema.py:47`).
- **Read-only verb exemplar:** `cli/equity_ledger.py` (closest — `build_ledger` + `write_jsonl`,
  `config_echo.emit_banner` to stderr `:57-58`, import `publishing.adapters` first `:15`). Exit posture
  from `cli/audit_state.py:135-152` (absent store → exit 0; unreadable → `DependencyError` exit 3).
  Opt-in exit-6 gate pattern: `cli/recheck_backlinks.py:36-39, 133-139`
  (`emit_envelope_and_exit("DeadBacklinksDetected", 6, msg)`).
- **Canonicalization:** `_util/url.py:180` `canonicalize_url` — the event's `target_url` column is **not**
  pre-canonicalized; the ledger's `LedgerRow.target_url` **is** (`ledger/sources.py:70` `_canon`). The
  overlay must canonicalize the event target before matching.
- **Console-script registration:** `pyproject.toml [project.scripts]` (line 38+) — one new line.

### Institutional Learnings

- **`docs/solutions/logic-errors/projector-silent-drop-status-vocabulary-drift-2026-05-26.md`** — never
  let an authoritative-source value (a verdict string) hit a silent `else`. The verdict→liveness mapping
  is the load-bearing seam; an unknown/future verdict must be **loudly quarantined**, never default-to-alive.
- **`docs/solutions/integration-issues/dofollow-canary-verdict-dropped-at-publish-output-seam-2026-05-25.md`**
  — the canonical "recorded but not load-bearing" precedent; enumerate ALL consume paths and inspect the
  *specific backlink's* verdict, not a page-wide aggregate.
- **`docs/solutions/test-failures/negative-assertion-locks-in-bug-2026-05-15.md`** +
  **`logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md`** — ship a **positive flip
  test** (host_gone ⇒ live_dofollow drops ⇒ deficit rises ⇒ replacement seed emitted), and
  **characterize the current no-op first** (prove the un-overlaid pipe emits zero seeds for a known-dead
  link). Avoid shape-only assertions.
- **`docs/solutions/logic-errors/argparse-choices-vs-usage-error-exit-clash-2026-05-20.md`** — do NOT use
  argparse `choices=`; validate post-parse via `UsageError` (exit 1, not argparse's 2).
- **`docs/solutions/best-practices/typed-error-envelope-over-stderr-truncation-2026-05-27.md`** — route
  any nonzero exit through the canonical error emitter (a static AST guard flags bare `raise SystemExit`).
- **`docs/solutions/workflow-issues/grep-dofollow-map-before-shipping-adapter-2026-05-20.md`** — dofollow
  is the unit of value; the overlay reads `dofollow_lost` from the verdict directly and does not
  re-derive dofollow classification (consistent with R2).

### External References

None — internal-only seam (events.db / ledger / plan-gap). The codebase has strong local patterns
(multiple read-only diagnostic verbs); external research was skipped.

## Key Technical Decisions

- **Standalone row-transform verb (seam a), not a `plan-gap` flag or an in-process wrapper.** The
  engine already treats `live_dofollow` / `live_dofollow_platforms` / `liveness` as the only deficit
  inputs and tolerates missing keys fail-safe, so a JSONL row rewrite is a drop-in with **zero**
  `gap/engine.py` / `cli/plan_gap.py` change and keeps the engine's pure no-I/O contract intact. A flag
  would force an engine signature change and still pre-resolve the feed in the shell anyway; a wrapper
  re-implements the ledger→stdin contract and couples both engines. (Resolves origin deferred-question R5.)
- **Key latest-verdict on canonical `live_url` (with `article_id` as a fast path), NOT on `article_id`
  alone.** *Preserves the origin's R1 keying decision.* A `link.rechecked` event's `article_id` column
  is **NULL on stdin-sourced rechecks** (the normal headless/cron case this plan targets — a piped URL
  list omits it), so filtering `WHERE article_id IS NOT NULL` or keying solely on `article_id` would
  silently drop exactly the verdicts the overlay exists to act on. Read **all** `link.rechecked` rows;
  resolve latest per canonical `live_url` (from the payload), using `article_id` as a fast-path key when
  present. *(This corrects an earlier draft that keyed on `article_id` only and re-introduced the
  false-success for stdin rechecks — flagged by review.)*
- **Order by `ts_utc`, with `events.id` as a same-`ts_utc` tiebreaker — NOT rowid as primary.** For
  `link.rechecked` specifically, `ts_utc` is uniformly tz-aware UTC (written via `store.append`→
  `_now_iso_utc`), so the naive/tz mixing the origin feared is a *history_store / ledger `verified_at`*
  problem, not a recheck-event problem. The existing consumers `derive_decay_counts` / `_recheck_cursors`
  resolve latest by strict `ts >` (first-seen wins on an exact tie, no `id` tiebreak); this overlay
  **adds** an `events.id` tiebreaker for deterministic same-`ts_utc` resolution (R8) — an intentional
  determinism improvement, not a mirror of those consumers. Using `id` as the *primary* key (append
  order) is still rejected.
- **Represent a dead link as a discounted count, never as `failed`.** This is what dissolves the P0
  failed-target-suppression interaction — `plan-gap` sees a normal deficit, not a suppressed failed
  target. There is no shared `articles` column for a later `publish.verified` to revive, so the second
  P0 is dissolved too.
- **Discount AND prune the platform.** A dead link on platform X both decrements `live_dofollow` and
  removes X from `live_dofollow_platforms` (matching `gap/engine.py:164` semantics) so the fan-out frees
  X for re-fan-out rather than re-publishing to the platform that just died.
- **Own discount set, explicitly defined.** `{host_gone, link_stripped}` discount `live_dofollow`;
  `dofollow_lost` also discounts `live_dofollow` (the same single counter — there is no separate dofollow
  sub-count); `alive` restores; `probe_error` ignored; **any unrecognized verdict is loudly quarantined**
  (counted + stderr), never silently treated as alive.
- **Silent-drop guard / positive-assertion.** Every dropped or unmatched record is a counted, stderr
  signal: links discounted, dead verdicts seen, events with NULL/unmatched `target_url`, targets whose
  `live_dofollow` was reduced. No silent `continue`.
- **Exit posture mirrors the read-only verbs:** absent `events.db` → exit 0 ("nothing to discount");
  unreadable → `DependencyError` exit 3; usage error → `UsageError` exit 1; default **advisory exit 0**.
  An opt-in `--fail-on-dead` gate exits 6 via `emit_envelope_and_exit` only when the dead count > 0.
- **Guard store existence BEFORE opening it.** `EventStore.query()` routes through `connect()` →
  `_connect_raw()`, which **creates `events.db`** (dir `0o700`, WAL/SHM sidecars, a schema-upgrade
  commit) when the path is absent. So the overlay must `path.exists()`-check the default store path
  *before* constructing/querying `EventStore`; on a missing store it passes the stdin ledger through
  unchanged and exits 0 **without ever instantiating the store** — otherwise the absent-db path violates
  R6's "mutates nothing (events.db)" by creating the file. (Mirror `audit-state`'s absent-store guard.)
- **Re-plan stale aged targets via `plan-gap --emit-stale` in the recipe — overlay does NOT rewrite
  `liveness`.** `plan-gap` suppresses `liveness ∈ {stale, unverified}` rows with `live_dofollow == 0`
  unless `--emit-stale`; since recheck targets aged links, a discounted-to-zero target is usually in that
  state, so the documented recipe pipes `--emit-stale`. The overlay stays a pure `live_dofollow` /
  `live_dofollow_platforms` transform and leaves `liveness` untouched — rewriting to `failed` would trip
  plan-gap's failed-target suppression (the P0 this interim path avoids), and rewriting to `live` would be
  dishonest. Accepted side effect: `--emit-stale` also re-plans zero-coverage stale targets with no dead
  evidence, which is reasonable (zero live dofollow links warrants re-planning).
- **Single-observation demotion accepted** (origin): the overlay feeds a *reviewable*
  `plan → validate → publish`, so a transient `host_gone` blip yields at most one reviewable extra seed,
  never an auto-wasted publish. Revisit if the loop is ever cron-auto-published.

## Open Questions

### Resolved in Review (were P1 — now decided)

- **[Affects R1, R5, R7][Liveness suppression] RESOLVED → operator recipe uses `plan-gap --emit-stale`.**
  The overlay discounts `live_dofollow` but leaves the row's `liveness` field untouched; `plan-gap` gates
  on `liveness` **before** the deficit (`gap/engine.py` — a row with `liveness ∈ {stale, unverified}` AND
  `live_dofollow == 0` is suppressed with no seed unless `--emit-stale`). Since recheck targets the
  aged-link population (often `stale`/`unverified`), discounting their last live link to 0 would otherwise
  emit zero seeds — re-opening the false-success. **Decision:** the documented re-plan recipe always pipes
  through `plan-gap --emit-stale`, so a discounted-to-zero stale target is re-planned. The overlay stays
  a pure row transform and does NOT rewrite `liveness` (rewriting to `failed` would trip plan-gap's
  failed-target suppression — the very P0 this interim path avoids; rewriting to `live` would be
  dishonest). **Accepted side effect:** `--emit-stale` also re-plans stale/unverified targets that have
  zero live coverage but no dead-link evidence — acceptable, since a target with zero live dofollow links
  genuinely warrants re-planning. **Unit 4 must test the stale/unverified aged-link case, not only the
  fresh-publish (`liveness=live`) fixture.**
- **[Affects R2][Discount semantics] RESOLVED → `dofollow_lost` keeps a full `live_dofollow` discount.**
  A link that lost `rel=dofollow` passes zero link equity, so for re-planning purposes it is equivalent
  to a dead link and a full decrement (→ replacement seed) is correct. It remains classed as advisory for
  the `--fail-on-dead` gate (a nofollow drift is not "dead"), so `dofollow_lost` discounts the deficit
  but does **not** trip exit 6 — only `{host_gone, link_stripped}` do.

### Resolved During Planning

- **Exact plan-gap seam (origin R5):** standalone row-transform verb between `equity-ledger` and
  `plan-gap` (plan-gap reads stdin JSONL, not the ledger directly — confirmed `cli/plan_gap.py:107-123`).
- **Latest-verdict resolution (origin R1/R4):** order by `ts_utc` with an `events.id` same-`ts`
  tiebreaker (a determinism addition over the existing first-seen-on-tie consumers; `id` is **not** the
  primary key).
- **Keying / target join (origin R1):** key on canonical `live_url` (article_id fast path), **not**
  `article_id` alone — `article_id` is NULL on stdin rechecks. Recover the target from the event's
  `target_url` column, falling back to the `articles` row when it is NULL; canonicalize with
  `canonicalize_url` to match `LedgerRow.target_url`.
- **Display surfacing (origin R6 deferred):** the overlay's own stdout is the discounted view; no
  separate `equity-ledger` display change.
- **No `events/kinds.py` change:** `LINK_RECHECKED` is already registered (`kinds.py:57,78,132`); reading
  it needs no registration change.

### Deferred to Implementation

- Exact helper/module names (`recheck/overlay.py` reader + a small pure transform vs a function beside
  `derive_decay_counts`) — settle once the code is in front of you; keep the engine/shell split.
- Whether the same-`ts_utc` tiebreaker needs `ORDER BY ts_utc, id` in SQL or a Python-side stable sort —
  decide against the actual `query()` result ordering.
- Final console-script **verb name**. Plan uses `recheck-overlay` (sits beside `recheck-backlinks`,
  reads recheck verdicts); origin calls the concept "deficit-overlay". Trivial; confirm at registration.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation
> specification. The implementing agent should treat it as context, not code to reproduce.*

Pipe position and data flow:

```
events.db ──(read-only EventStore.query: latest link.rechecked per canonical live_url)──┐
                                                                                 ▼
equity-ledger ──(LedgerRow JSONL on stdin)──► recheck-overlay ──(discounted LedgerRow JSONL)──► plan-gap --emit-stale
                                                     │
                                                     └─(stderr tally: discounted / dead-seen / NULL-target / targets-reduced)
```

Directional shape of the discount the overlay computes (NOT an implementation spec):

```
# latest verdict per link, keyed on canonical live_url (article_id fast path), ordered by ts_utc then id
latest[canonical_live_url] = (verdict, canonical_target_url, platform)   # read ALL rows; do NOT filter article_id IS NOT NULL

# discount set semantics  (live_dofollow is ONE dofollow-and-live integer; no separate sub-count)
host_gone | link_stripped  -> dead:           target.live_dofollow -= 1; drop platform from live_dofollow_platforms
dofollow_lost              -> dofollow_lost:   target.live_dofollow -= 1; drop platform   # same counter as dead
alive                      -> live:            no change (restores if it was the latest)
probe_error                -> ignored:         no change
<unrecognized>             -> QUARANTINE:      no change + loud stderr count   # never default-to-alive

# row transform (per LedgerRow on stdin), live_dofollow floored at 0
row.live_dofollow            = max(0, row.live_dofollow - discounts_for[canon(row.target_url)])
row.live_dofollow_platforms  = row.live_dofollow_platforms - dead_platforms_for[canon(row.target_url)]
```

## Implementation Units

- [ ] **Unit 1: Pure latest-verdict reader + discount classifier**

**Goal:** Read the latest `link.rechecked` verdict per `article_id` from `events.db` (read-only) and
produce a per-target discount map keyed by **canonical** `target_url`, plus the set of dead platforms
per target. Loudly tally unknown verdicts and NULL/blank targets.

**Requirements:** R1, R2, R3, R4, R6

**Dependencies:** None

**Files:**
- Create: `src/backlink_publisher/recheck/overlay.py` (pure reader + classifier)
- Test: `tests/test_recheck_overlay.py`

**Approach:**
- **Guard first:** if the default `events.db` path does not exist, return an empty discount map without
  constructing `EventStore` (opening it would *create* the file — see Key Decisions). The caller treats
  an empty map as "nothing to discount".
- `EventStore.query("SELECT article_id, target_url, payload_json, ts_utc, id FROM events WHERE kind=?", (LINK_RECHECKED,))`
  — **do NOT** filter `article_id IS NOT NULL` (that drops stdin-sourced rechecks whose `article_id` is
  NULL — the headless case this plan targets).
- Resolve latest-per-link keyed on **canonical `live_url`** (from `payload["live_url"]`), `article_id`
  as a fast path when present; order by `_parse_ts(ts_utc)` then `id` as a same-`ts` tiebreaker (an
  intentional determinism addition over `derive_decay_counts`'s first-seen tie behavior — R8).
- Classify `payload["verdict"]` against an explicit overlay discount set (`{host_gone, link_stripped}` →
  dead; `dofollow_lost` → discount (same `live_dofollow` counter); `alive`/`probe_error` → no discount;
  else → quarantine).
- Recover the target: prefer the event's `target_url` column; when it is NULL but `article_id` is
  present, fall back to the `articles` row (`articles.target_urls_json[0]`) so a confirmed-dead link is
  not silently un-discounted. Canonicalize via `canonicalize_url` before bucketing; carry
  `payload["platform"]` into the dead-platform set so Unit 2 can prune it.
- Return a small dataclass/dict: per-canonical-target `{dead_count, dofollow_lost_count, dead_platforms}`
  plus tallies (`discounted`, `dead_seen`, `null_or_blank_target`, `unrecoverable_target`, `unknown_verdict`).
- Pure: no writes, no network, no stdout/stderr (the CLI shell prints the tally).

**Patterns to follow:** `recheck/events_io.py:73` `derive_decay_counts`; `recheck/selection.py:56` `_parse_ts`,
`:126` `_recheck_cursors`; `events/store.py:320` `query`.

**Test scenarios:**
- Happy path: one `host_gone` verdict for an article → discount map has `dead_count=1` for that
  canonical target and the platform in `dead_platforms`.
- Happy path: `dofollow_lost` verdict → `dofollow_lost_count=1`, platform recorded.
- Edge — recency (R4): two verdicts for the same link, `host_gone` then a newer `alive` → no discount
  (latest wins); reverse order → discounted.
- Edge — same `ts_utc` tiebreaker: two verdicts identical `ts_utc`, differing `id` → higher `id` wins;
  result is deterministic across re-runs (R8).
- **Edge — NULL `article_id` (stdin recheck): a `host_gone` verdict with `article_id=NULL` but a valid
  `live_url` is STILL discounted** (keyed on canonical `live_url`); it must not be dropped by an
  `article_id IS NOT NULL` filter. This is the headless regression the plan exists to prevent.
- Edge — `probe_error` only → no discount.
- Edge — unknown verdict string → quarantined: no discount **and** `unknown_verdict` tally incremented
  (never counted as alive).
- Edge — NULL `target_url` column **with** a valid `article_id` → target recovered via the `articles`
  row and the discount IS applied; only when neither yields a target is it counted in
  `unrecoverable_target` (loud, not silent).
- Edge — canonicalization: event target `http://Ex.com/p?utm=1` and ledger target `https://ex.com/p`
  resolve to the same canonical key (whatever `canonicalize_url` yields — assert they match).
- Edge — absent `events.db`: empty discount map returned and **the store file is never created**
  (assert path still does not exist after the call).
- Edge — empty (present-but-no-recheck-events) store → empty discount map, zero tallies (no error).

**Verification:** Given a fixture event set, the returned discount map and tallies match expected
counts; the function performs zero writes (assert store unchanged) and never raises on unknown verdicts.

---

- [ ] **Unit 2: Ledger-row discount transform**

**Goal:** Apply the discount map to ledger JSONL rows — decrement `live_dofollow` (floored at 0), prune
dead platforms from `live_dofollow_platforms`, pass unaffected rows through unchanged, and surface
discounts that match no ledger row.

**Requirements:** R1, R2, R5, R6

**Dependencies:** Unit 1

**Files:**
- Modify: `src/backlink_publisher/recheck/overlay.py` (add the pure transform alongside the reader)
- Test: `tests/test_recheck_overlay.py`

**Approach:**
- Pure function `(ledger_rows: list[dict], discount_map) -> (rewritten_rows, transform_tally)`.
- Match each row by `canonicalize_url(row["target_url"])`; subtract `dead_count + dofollow_lost_count`
  from `live_dofollow` (floor 0); remove `dead_platforms` from `live_dofollow_platforms`.
- Preserve all other row keys verbatim (re-emit full ledger-shaped dict so `plan-gap` sees an unchanged
  contract) — including `liveness` (the overlay does **not** rewrite it; the discounted-to-zero aged-row
  suppression is handled by the recipe's `plan-gap --emit-stale`, per Key Decisions / Open Questions).
- Tally `targets_reduced` and `unmatched_discount` (a discount whose canonical target is absent from the
  ledger rows — loud, not silent).

**Patterns to follow:** `gap/engine.py:128,164` (the exact keys plan-gap reads); `ledger/model.py:95`
`to_jsonl_dict` (row shape); `gap/engine.py:45` `SuppressionCounts` (per-reason stderr tally style).

**Test scenarios:**
- Happy path: a ledger row with `live_dofollow=3, live_dofollow_platforms=[A,B,C]` + a dead link on A →
  output `live_dofollow=2, live_dofollow_platforms=[B,C]`.
- Happy path: `dofollow_lost` on platform B → `live_dofollow` reduced by 1, B pruned.
- Edge: two dead links on the same target → `live_dofollow` reduced by 2, both platforms pruned.
- Edge: floor — `live_dofollow=1` with 2 discounts → result 0 (never negative).
- Edge: a row with no matching discount passes through byte-for-byte identical (all keys preserved).
- Edge: a discount whose canonical target matches **no** ledger row → row set unchanged +
  `unmatched_discount` tally incremented (surfaced, not silent).
- Integration: the rewritten rows still parse as valid `plan-gap` input (feed them to
  `gap.engine.plan_gap` and confirm the deficit reflects the reduced `live_dofollow`).

**Verification:** Transformed rows carry reduced counts and pruned platforms; unaffected rows are
unchanged; unmatched discounts are tallied; output is accepted unchanged by `gap.engine.plan_gap`.

---

- [ ] **Unit 3: `recheck-overlay` CLI verb shell**

**Goal:** Wire Units 1+2 into a read-only console verb that reads ledger JSONL on stdin, emits
discounted ledger JSONL on stdout, prints the tally banner on stderr, honors the exit-code contract, and
offers an opt-in `--fail-on-dead` gate.

**Requirements:** R5, R6, R7 (enables), R8

**Dependencies:** Unit 1, Unit 2

**Files:**
- Create: `src/backlink_publisher/cli/recheck_overlay.py`
- Modify: `pyproject.toml` (`[project.scripts]`: `recheck-overlay = "backlink_publisher.cli.recheck_overlay:main"`)
- Test: `tests/test_cli_recheck_overlay.py`

**Approach:**
- Import `backlink_publisher.publishing.adapters` first (populate registry, like `equity_ledger.py:15`).
- argparse with **no `choices=`**; post-parse validation via `emit_error(..., exit_code=1)`.
- Read ledger rows from stdin via `read_jsonl`; call Unit 1 reader (which guards `events.db` existence
  before opening the store — never creates it) → Unit 2 transform; `write_jsonl(rows, sys.stdout)`.
- Config banner + discount tally to **stderr** via `config_echo.emit_banner(cfg, "recheck-overlay")`.
- Exit posture: absent `events.db` → exit 0 (no discount, pass ledger through unchanged); unreadable
  store → `DependencyError` (exit 3) via `handle_error`; usage → exit 1.
- `--fail-on-dead`: after emitting JSONL, if dead count > 0, `emit_envelope_and_exit("DeadBacklinksDetected", 6, msg)`;
  default exit 0 (advisory).

**Execution note:** Start with a failing end-to-end test for the stdin-ledger → stdout-discounted-ledger
contract (the I/O shape is the risk, not the pure logic).

**Patterns to follow:** `cli/equity_ledger.py` (shell shape, stdout/stderr split, adapter import);
`cli/audit_state.py:135-152` (absent-vs-unreadable exit posture); `cli/recheck_backlinks.py:36-39,133-139`
(`--fail-on-dead` exit-6).

**Test scenarios:**
- Happy path: stdin ledger with a target whose link is `host_gone` in events.db → stdout row shows
  reduced `live_dofollow`; stdout is **pure JSONL** (no banner/diagnostic leak on stdout).
- Happy path: stderr carries the discount tally (discounted / dead-seen / targets-reduced).
- Edge: absent `events.db` → exit 0, ledger passed through unchanged.
- Error path: unreadable `events.db` → exit 3 (`DependencyError`), nothing on stdout.
- Error path: malformed stdin row → loud failure routed through the error emitter, not a silent skip.
- Gate: `--fail-on-dead` with a dead link → exit 6 + typed envelope; without `--fail-on-dead` → exit 0.
- Contract: a bad flag value exits 1 (UsageError), not argparse's 2.

**Verification:** Piping `equity-ledger | recheck-overlay` yields a discounted ledger on stdout with the
tally on stderr; exit codes match the contract; no writes to any store.

---

- [ ] **Unit 4: End-to-end positive-assertion loop test (R7/R8) — the no-theater gate**

**Goal:** Prove the overlay actually closes the loop: a deterministic-dead verdict produces a
replacement seed through `plan-gap` on default flags, and a later `alive` restores baseline.

**Requirements:** R7, R8

**Dependencies:** Unit 3

**Files:**
- Create: `tests/test_recheck_overlay_replan_loop.py`

**Approach:**
- Build an `events.db` fixture: a `publish.confirmed` for a target on a dofollow platform, then a
  `link.rechecked` `host_gone` verdict newer than the publish; an `equity-ledger` JSONL fixture where
  that target's `live_dofollow` still counts the now-dead link.
- **Characterization first:** run `equity-ledger`-fixture → `plan-gap` *without* the overlay and assert
  **zero** replacement seeds (proves the current parallel-wired no-op).
- Then run fixture → `recheck-overlay` → `gap.engine.plan_gap` (or the CLI) and assert: deficit for that
  target increased by exactly 1; a replacement seed is emitted for that target; the seed avoids the dead
  platform; all on **default flags (no `--include-failed`)**.
- **Run the loop for BOTH a `liveness=live` row and a `liveness=stale`/`unverified` row** (per Open
  Questions → "Liveness suppression"): the aged-link case is the realistic recheck population and must
  also produce a replacement seed, not get suppressed. A fresh-publish-only fixture would mask the bug.

**Patterns to follow:** existing `plan-gap` tests; the autouse conftest isolation fixtures (sandboxed
config dir, blocked sockets); `recheck/events_io.py` for constructing `link.rechecked` events.

**Test scenarios:**
- Positive flip (R7): host_gone verdict newer than publish ⇒ deficit +1 ⇒ replacement seed emitted on
  default flags, avoiding the dead platform.
- **Aged-link (R7): same flip on a `liveness=stale`/`unverified` target ⇒ replacement seed still emitted
  (not suppressed). This is the realistic recheck population — see Open Questions → Liveness suppression.**
- Stdin recheck (R1): the dead `link.rechecked` event carries `article_id=NULL` ⇒ still discounted and
  still produces a replacement seed (the headless regression guard, end-to-end).
- Characterization: identical fixture **without** the overlay ⇒ zero replacement seeds (the difference is
  the fix, not pre-existing behavior).
- Recency (R8): a later `alive` verdict for the same link ⇒ deficit returns to baseline ⇒ no replacement
  seed.
- Determinism (R8): re-running the overlay on the same event set yields identical output.
- Negative: a `probe_error`-only link ⇒ no discount ⇒ no spurious seed.

**Verification:** The positive and characterization tests both pass, demonstrating the overlay flips a
real behavior rather than asserting a shape.

---

- [ ] **Unit 5: Runbook + deferred-consolidation note**

**Goal:** Point the recheck runbook's dual-source note at the overlay for re-planning, and record
R6-proper (ledger writeback via plan-007) as the deferred consolidation that retires the overlay.

**Requirements:** Success Criteria (operator no longer hand-clicks WebUI recheck per dead link)

**Dependencies:** Unit 3

**Files:**
- Modify: the recheck runbook under `docs/runbooks/` (the doc that records the R6 deferral)

**Approach:**
- Add the `equity-ledger | recheck-overlay | plan-gap --emit-stale` recipe for the cron/headless re-plan
  loop (call out that `--emit-stale` is required for aged-target re-planning).
- Note the ledger's stored liveness stays publish-time-clock until R6-proper lands; the overlay is the
  interim re-plan path and is throwaway by design.

**Test expectation:** none — documentation only.

**Verification:** The runbook describes the overlay recipe and the R6-proper retirement condition.

## System-Wide Impact

- **Interaction graph:** New verb is a **leaf consumer** of `events.db` (read-only `query`) and a pure
  stdin→stdout transform between `equity-ledger` and `plan-gap`. No callback, projector, reducer, or
  emitter is touched. `gap/engine.py` and `cli/plan_gap.py` are unchanged.
- **Error propagation:** Unreadable store → exit 3; usage → exit 1; opt-in dead-gate → exit 6; default
  advisory exit 0. All nonzero exits route through the canonical error emitter (AST guard).
- **State lifecycle risks:** Zero content writes by design — BUT `EventStore.connect()` *creates*
  `events.db` on a missing path, so the absent-store path MUST guard existence before opening (Key
  Decisions). With that guard, no partial-write/cache/cleanup surface; the overlay is idempotent and
  deterministic given a fixed event set (R8).
- **Double-liveness divergence (bounded):** The overlay introduces a second liveness signal
  (recheck-derived `live_dofollow` discount) alongside the ledger's untouched publish-time-clock
  `liveness` field. They can disagree at the `plan-gap` seam; the `--emit-stale` recipe decision bounds
  the one harmful case (discounted-to-zero aged target getting suppressed). R6-proper ultimately
  collapses the two signals into one ledger-native liveness, retiring the divergence.
- **API surface parity:** WebUI `recheck_one` already drives re-planning interactively; this gives the
  **headless/CLI** path parity. No WebUI change in scope.
- **Integration coverage:** Unit 4 is the cross-layer proof (events.db → overlay → plan-gap → seed) that
  unit tests of the pure functions cannot establish alone.
- **Unchanged invariants:** `plan-gap`'s deficit math and fan-out, the ledger's stored liveness, the
  recheck verdict taxonomy, the events.db schema, and `events/kinds.py` are all **unchanged**. The
  overlay only rewrites in-flight JSONL between two verbs.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| **Stdin rechecks (NULL `article_id`) dropped → headless false-success re-opened** | Read all rows (no `article_id IS NOT NULL` filter); key latest on canonical `live_url`. End-to-end test in Unit 4 (NULL-article_id ⇒ seed). |
| **Aged-link suppression: discounting `live_dofollow`→0 on a stale/unverified row makes `plan-gap` suppress it → zero seeds** | RESOLVED: documented recipe uses `plan-gap --emit-stale`; overlay does not rewrite `liveness`. Unit 4 tests the stale/unverified case. |
| Ordering-key divergence from the dashboard producing a different "latest" verdict | Order by `ts_utc` with an `events.id` same-`ts` tiebreaker (determinism addition over the first-seen consumers); `id` never primary. Determinism asserted in Unit 1 + Unit 4. |
| Canonicalization mismatch / NULL target silently drops a discount | Canonicalize both sides with `canonicalize_url`; recover NULL target via the `articles` row; surface `unmatched_discount` / `unrecoverable_target` loudly on stderr (Unit 1/2). |
| Unknown/future verdict silently treated as alive (re-creates the silent-drop class) | Explicit discount set with a loud quarantine path; `unknown_verdict` tally; test asserts no default-to-alive. |
| Transient `host_gone` blip (5xx/anti-bot) over-discounts | Accepted: feeds a *reviewable* plan→validate→publish, so at most one reviewable extra seed; revisit only if cron-auto-published. |
| New verb leaks diagnostics onto stdout, breaking the JSONL pipe into `plan-gap` | Stdout = `write_jsonl` only; all banner/tally to stderr; test asserts pure-JSONL stdout. |
| Overlay becomes permanent and masks the proper fix | Recorded as throwaway; Unit 5 documents R6-proper as the retirement condition. |

**Dependencies:** Reuses `link.rechecked` events (#310), `plan-gap` (#313), and the existing
`build_ledger` / `live_dofollow` source. **No plan-007 dependency** (that is the deferred R6-proper path).

## Documentation / Operational Notes

- Operator recipe (cron/headless): `equity-ledger | recheck-overlay | plan-gap --emit-stale --desired N --language L | plan-backlinks`.
  `--emit-stale` is load-bearing (re-plans discounted-to-zero aged targets that plan-gap would otherwise suppress).
- The overlay is advisory by default (exit 0); `--fail-on-dead` is opt-in for CI/cron alarms.
- Promote a `docs/solutions/` note on the equity-ledger liveness model after implementation (the
  learnings search found it undocumented).

## Sources & References

- **Origin document:** `docs/brainstorms/2026-06-01-recheck-ledger-liveness-writeback-requirements.md`
- Related code: `gap/engine.py` (`plan_gap`), `cli/plan_gap.py`, `ledger/aggregate.py` (`build_ledger`,
  `_link_liveness`), `ledger/model.py` (`LedgerRow`), `recheck/events_io.py` (`emit_recheck`,
  `derive_decay_counts`), `recheck/selection.py` (`_recheck_cursors`, `_parse_ts`),
  `recheck/verdicts.py`, `events/store.py` (`query`), `cli/equity_ledger.py`, `cli/audit_state.py`,
  `cli/recheck_backlinks.py`, `_util/url.py` (`canonicalize_url`).
- Related work: #310 (recheck survival loop), #313 (`plan-gap` deficit re-plan). R6-proper = deferred
  ledger writeback via plan-007.
