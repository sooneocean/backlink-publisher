---
date: 2026-05-29
topic: deficit-driven-replan
---

# Deficit-Driven Re-Plan Verb (`plan-gap`) — close the plan → publish → blind loop

## Problem Frame

The pipeline runs `plan → validate → publish`, and then the operator goes **blind**: to start the next
batch they hand-author `seeds.jsonl` again, with zero feedback from what already landed. The
`equity-ledger` already knows which targets are under-linked — it emits one row per `target_url` with a
`live_dofollow` count and **already sorts targets weakest-first** (`ledger/aggregate.py:150`) — but
**nothing turns that deficit into the next plan**. Verified by integration sweep (2026-05-29): no
ledger→plan-seed bridge exists under any name; `plan-backlinks` seeds come only from hand-authored
stdin / `--from-csv` / `--from-sitemap`.

This is the single biggest missing verb in the operator's actual job loop. It is **distinct from the
rejected yield-weighted plan _selection_** (Round-11 D5, blocked on unobserved yield data): this is
deficit-driven plan _generation_ from **already-observed liveness**, with no yield model.

Affected: any operator running an ongoing/repeated backlink campaign across many targets.

```
 equity-ledger (exists)          plan-gap (NEW, pure transform + registry read)        plan-backlinks
 ─────────────────────   ──►   for each target with liveness ∈ {live} (stale          (pure engine,
 per-target:                   suppressed by default):                                 reads stdin)
   target_url                    deficit = max(0, D[target] − live_dofollow)      ──►  validate → publish
   live_dofollow                 candidates = active_dofollow_platforms − platforms
   platforms[] (already-live)    emit min(deficit, |candidates|) seeds,
   liveness / liveness_verified_at   one per distinct candidate platform;
   (sorted weakest-first)        stamp liveness as-of; warn if stale
```

## Requirements

**Verb & composition**
- R1. A standalone, read-only CLI verb (`plan-gap`) reads `equity-ledger` JSONL on stdin and emits
  `plan-backlinks`-compatible seed JSONL on stdout. It does NO network and NO store writes. It MAY read
  the static publisher registry (`active_platforms()` / `dofollow_status()`) — read-only, not a store.
  `plan-backlinks` stays a pure stdin-reading engine; no `--from-ledger` flag is added to it. Composes as
  `equity-ledger | plan-gap | plan-backlinks`.
- R2. Each emitted seed is a complete `plan-backlinks` input row carrying the 6 required
  `INPUT_SCHEMA_FIELDS` (`schema.py:10`): `target_url` (from the ledger), `platform` (chosen by the
  fan-out, R4), and `main_domain` / `language` / `url_mode` / `publish_mode` (from `plan-gap` config
  defaults / flags — the operator's campaign defaults; the ledger carries none of these). A bare
  `target_url` is NOT a valid seed and would be dropped by `plan-backlinks` validation.

**Deficit policy**
- R3. The operator supplies a desired per-target dofollow count `D`, as a **global default** with an
  optional **per-target override map** (`--desired-map` / config) so high-value money pages can be set
  higher than incidental pages. For each target, `deficit = max(0, D[target] − live_dofollow)`. Targets
  whose deficit is 0 are omitted.
- R4. **Channel-aware fan-out.** For each deficient target, the candidate platforms are the registry's
  active dofollow platforms minus the platforms the target already has links on (`LedgerRow.platforms`,
  `model.py:75`). `plan-gap` emits one seed per distinct candidate platform, capped at
  `min(deficit, |candidates|)` — so the N emitted seeds map to N **distinct** dedup keys
  `(platform, account, target_url)` and actually become N new backlinks (not collapsed to one). When a
  target's candidate set is empty (already live on every active dofollow platform), it is omitted with a
  per-target advisory. Weakest targets are emitted first (ledger input is pre-sorted).

**Staleness & trust** (the verb must not introduce a new silent-failure or thrash loop)
- R5. Each run stamps the **liveness as-of basis** on its output (`liveness_verified_at` and/or the
  `liveness` enum from the ledger row), so the operator and downstream tools see how fresh the deficit is.
- R6. By default, `plan-gap` **omits targets whose `liveness` is `stale` or `unverified`** (and skips
  `failed` targets entirely — a dead target should be retired, not re-linked). `--emit-stale` /
  `--include-failed` override this. Rationale: publishing does not refresh `live_dofollow` (only the
  in-flight recheck loop does), so emitting un-refreshed targets would re-propose the same targets every
  run (thrash) and act on untrusted counts. When suppression drops targets, `plan-gap` emits a stderr
  summary naming how many and why. Exits 0 (advisory-by-default, diagnostic-verb convention).
- R7 (shadow paths). Zero-deficit / empty input must not read as failure. When every eligible target is
  already at `D`, all targets are suppressed, or the ledger is empty, `plan-gap` emits a stderr advisory
  ("0 targets to re-plan: <reason breakdown>") and exits 0; it documents that the downstream
  `plan-backlinks` will itself exit-2 on an empty stream (strict `read_jsonl`), so the operator can
  distinguish "campaign satisfied" from a real failure. A ledger row with missing/`None` `live_dofollow`
  is treated as full deficit (or skipped with a warning) — never a crash on `max(0, D − None)`.

## Success Criteria
- An operator running an ongoing campaign can run `equity-ledger | plan-gap --desired N | plan-backlinks
  | …` and get a next batch targeted at the weakest **live** targets, across distinct dofollow platforms,
  without hand-authoring seeds.
- **Deficit closure, not just emission:** across repeated runs (with recheck refreshing liveness between
  them), the sum of open deficits strictly decreases until each target is channel-limited — i.e. the loop
  actually converges, rather than re-emitting the same targets forever.
- A run on stale/unverified liveness never silently drives re-planning: such targets are suppressed by
  default, and the operator sees the as-of stamp + the suppression summary.
- `plan-backlinks` gains no new store dependency; the plan/validate engine purity is preserved.

## Scope Boundaries
- NOT yield-weighted plan **selection** (Round-11 D5, rejected — needs unobserved yield data). This is
  deficit-driven **generation** from observed liveness only.
- NOT a recheck/monitor. It consumes whatever liveness the ledger reports; refreshing liveness is the
  in-flight recheck survival loop's job (`docs/plans/2026-05-29-004-...`). `plan-gap` only reads,
  stamps, and suppresses on staleness.
- NOT writing to any store; NOT touching plan/validate engine internals; NOT adding a `--from-ledger`
  flag to `plan-backlinks`.
- NOT choosing **anchors** or per-seed creative metadata — that stays `plan-backlinks`' job. (`plan-gap`
  DOES choose `platform` via channel-aware fan-out — this is required for the deficit to close, since
  `plan-backlinks` does not rotate platforms and the dedup gate collapses same-platform/same-target
  seeds.)

## Key Decisions
- **Standalone pure verb, not a `--from-ledger` flag** — keeps `plan-backlinks` pure (no events.db/ledger
  dependency in the planner), per the deterministic-planning-principle. [constraint-driven]
- **Channel-aware fan-out (one seed per distinct active dofollow platform not already live, capped at the
  candidate count)** — resolves the document-review P0: `plan-backlinks` does not rotate platforms and the
  dedup key is `(platform, account, target_url)`, so N identical seeds collapse to ≤1 backlink. Reading
  the registry + the ledger's `platforms` list is the only way the deficit actually closes; it also
  auto-handles saturated/retired-channel targets (empty candidate set ⇒ omitted). [document-review]
- **Target-value deficit `max(0, D[target] − live_dofollow)`, global `D` with per-target override** —
  clearest "fill to D" semantics; the override prevents over-linking low-value pages and under-serving
  money pages (the ledger deliberately has no composite equity index, so value tiering lives here). [user]
- **Build now; suppress stale/unverified (and skip failed) targets by default** — ships value now while
  preventing the thrash loop and acting on untrusted counts; converges once the recheck loop refreshes
  liveness. `--emit-stale` / `--include-failed` for the operator who wants the raw view. [user]
- **Reuse the existing 6-field seed shape** (what `urls_to_seed_rows` produces) — no new schema; `plan-gap`
  supplies the non-URL fields from config. [grounded]

## Dependencies / Assumptions
- **CONFIRMED (document-review):** `equity-ledger` output already carries per-target `live_dofollow`
  (`model.py:73`), `platforms` (already-live platforms, `model.py:75`), `liveness` enum (`model.py:80`:
  live/stale/failed/unverified), and `liveness_verified_at` (`model.py:83`, `None` when never verified),
  all serialized to stdout via `to_jsonl_dict` (asdict). So R4/R5/R6 need **no ledger-output change**.
- `active_platforms()` (`_registry_manifest.py:77`) and `dofollow_status()` (`registry.py:417`) provide
  the active-dofollow candidate set for the fan-out (read-only, static registry).
- `live_dofollow` accuracy sharpens once the in-flight recheck survival loop lands; until then,
  stale-suppression (R6) is what keeps `plan-gap` from acting on un-refreshed counts.

## Outstanding Questions

### Deferred to Planning
- [Affects R2/R3/R6][Technical] Config home + flags: global `D`, `--desired-map` (per-target override),
  `X` (staleness threshold), `--emit-stale`, `--include-failed`, and the campaign defaults `plan-gap`
  must supply per seed (`main_domain` / `language` / `url_mode` / `publish_mode`). The ledger has no
  `language` signal — decide a default vs a required `--language` flag.
- [Affects R4][Technical] Confirm the canonical-`target_url` form `plan-gap` emits matches what the dedup
  key canonicalizes to, so the N distinct-platform seeds key correctly (and don't accidentally collapse
  on a normalization mismatch).
- [Affects R4][Technical] Account dimension: the dedup key includes `account` (defaults to `default`). If
  a target is already live on platform P under account A, does a new seed for P need a different account,
  or is `(P, default, target)` genuinely new? Confirm the fan-out's distinctness assumption per platform.

## Next Steps
→ `/ce:plan` for structured implementation planning (no blocking product questions remain; the deferred
items are technical confirmations for the planner).
