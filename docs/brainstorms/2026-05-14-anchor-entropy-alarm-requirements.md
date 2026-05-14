---
date: 2026-05-14
topic: anchor-entropy-alarm
---

# Anchor History + Entropy Alarm (per-target rolling window)

## Problem Frame

`report-anchors` currently counts anchor *types* (branded / partial / exact / lsi) per `main_domain` but never measures *distributional shape over time*. Penguin-era anchor over-optimization — the classic Google manual-action trigger for backlink networks — operates per destination URL with thresholds on anchor diversity, exact-match ratio, and concentration. The project today is structurally blind to its own SEO threat model: an operator can ship 30 articles where 80% of anchors to one money URL are the same exact-match phrase, and the report shows "30 articles, well-distributed across 4 types" with no alarm.

This brainstorm scopes a reporting-layer extension that closes that gap without changing the publish path.

## Requirements

**Metrics**
- R1. Compute three rolling-window metrics per target URL: Shannon entropy of anchor-text distribution, exact-match ratio (`anchor_type == "exact"` / total), top-3 anchor-text concentration.
- R2. Use absolute time windows (30d and 90d) gated against entry `ts`. Surface both windows side-by-side in the report.
- R3. Suppress alarm emission when the target's observed sample count is below the existing `_RELIABLE_SAMPLE_MIN = 50` floor. The metrics still compute and display; the alarm verdict, stderr warning (R11), breach entries in the JSON `alarm` key (R10), and the exit-code-2 path (R12) are all gated by this floor. Low-N targets produce metrics-only output, no warnings, and exit 0.
- R4. Normalize anchor text via `casefold()` + `strip()` before all distribution math (entropy, ratio, top-3). Penguin classification is case-insensitive; raw-text math would let `iPhone repair` / `iphone repair` / `iPhone Repair` inflate diversity artificially.

**Granularity and data model**
- R5. Aggregate per target URL (the actual destination link), not per `main_domain`. Add a `target_url: str = ""` field to `ProfileEntry` and bump `_PROFILE_SCHEMA_VERSION`. Populate from each link's URL at `record_article` time.
- R6. Backwards-compat: pre-bump entries (where `target_url == ""`) are rolled up into a domain-level bucket. The report shows URL-level metrics where data exists and falls back to domain-level rollup otherwise, transparently labeling each row's granularity.
- R7. No migration script required. New entries gain the field naturally; old entries remain readable.

**Thresholds**
- R8. Default thresholds hardcoded as module-level constants (mirroring the existing `_DEGRADATION_ALARM_PCT = 10.0` precedent):
  - `_ENTROPY_FLOOR = 1.5`
  - `_EXACT_RATIO_CEILING = 0.10`
  - `_TOP3_CONCENTRATION_CEILING = 0.25`
- R9. All three thresholds overridable via a new `[anchor_alarm]` TOML section in `config.toml` with global defaults plus optional per-target-URL or per-`main_domain` overrides. Resolution precedence: **per-target-URL > per-`main_domain` > global defaults from `[anchor_alarm]` > hardcoded constants from R8**.

**Output and exit semantics**
- R10. Extend the existing JSON stdout report with a new `alarm` key per target containing `{ metrics: {...}, breaches: [...], sample_size, window }`. Breach list is empty when below threshold or below sample minimum.
- R11. Emit a one-line stderr warning per breaching target in human-readable form (target URL + which threshold + observed value + window).
- R12. Set process exit code to `2` when any target breaches in the 90d window. Exit `0` when no breaches. Reserve `1` for the existing error path.

**Scope (within requirements)**
- R13. `report-anchors` is the only entry point. The publish path (`publish-backlinks`) does **not** consult the alarm. Operator is the deciding agent. (See **Scope Boundaries** below for full non-goals.)

## Success Criteria

- An operator running `report-anchors` against a real anchor profile sees URL-level entropy, exact-ratio, and top-3 concentration over both 30d and 90d in the JSON output.
- A profile with synthetic over-optimization (e.g. 60% exact-match anchors to one URL) produces a non-empty `breaches` list, a stderr warning, and exit code 2.
- A profile with normal distribution produces an empty `breaches` list and exit code 0.
- A profile with < 50 entries per target produces metrics but no breaches and exit code 0 — low-N false alarms are suppressed.
- Existing callers of `report-anchors` JSON consumers can ignore the new `alarm` key without breaking (additive schema change).

## Scope Boundaries

- No publish-time gate. Velocity Governor (Round-2 survivor) and anchor profile scheduler remain authoritative for publish-flow decisions.
- No webui surface. Stays a CLI report; if a UI is desired later it must be a sibling page, not a `webui.py` retrofit.
- No runtime LLM. All math is deterministic — entropy, ratio, count.
- No new sidecar storage. Reads from existing `ProfileState` only.
- No migration script. Backwards-compat fallback handles pre-bump entries.
- No retroactive `target_url` reconstruction for old entries — pre-bump entries stay domain-level.
- No alarm dispatch beyond stdout/stderr/exit-code (no email, no webhook, no Slack). Future integration with the proposed Round-2 Append-Only Event Log survivor is a separate piece of work.

## Key Decisions

- **Per target URL, not per domain**: Penguin classification operates per destination. Domain-level rollup masks single-URL over-optimization when a domain hosts multiple targets. Cost: one `ProfileEntry` schema bump.
- **Backwards-compat via empty-string fallback**: cheaper than a migration script; preserves all historical data; semantically clean ("no target URL recorded → roll into domain bucket").
- **Absolute time windows over last-N**: SEO risk is "what shipped in the last 90 days", not "the last 100 articles you happened to publish". The existing `recent_*` helpers (last-N) coexist; they serve different consumers.
- **Hardcoded defaults + TOML override**: mirrors the established `_DEGRADATION_ALARM_PCT` pattern; ships immediately; per-target tuning available when operators accumulate ground truth.
- **case-fold + trim normalization**: matches Google's case-insensitive evaluation; minimal-risk normalization; explicitly NOT stemming or fuzzy-matching to avoid merging distinct brand variants.
- **Three-channel emission (stdout JSON + stderr line + exit 2)**: serves human readers, log scrapers, and cron exit-code monitors simultaneously without any consumer needing to opt in.
- **Sample-size floor reuses existing `_RELIABLE_SAMPLE_MIN = 50`**: keeps one floor convention across the file; planning may revisit this if real data shows the floor needs to differ per metric.

## Dependencies / Assumptions

- The full target URL is available at `record_article` time from the article's `links` array. (Brief inspection of `anchor_profile.py:183` `record_article` signature suggests this — planning must verify.)
- Existing `ProfileEntry` schema bump path (`_PROFILE_SCHEMA_VERSION`) handles backwards-compat for unknown fields on read.
- JSON consumers of `report-anchors` stdout treat unknown top-level keys as ignorable (additive change is safe).
- No live SERP data, no Google API integration, no LLM. Pure local profile-data analysis.

## Outstanding Questions

### Resolve Before Planning

*(none — all product decisions resolved during brainstorm)*

### Deferred to Planning

**Surfaced by document-review on 2026-05-14 (P0/P1; planning must resolve before any code is written):**

- [Affects R5, R7][P0 — Data destruction risk][Needs research] `anchor_profile.py:139` returns an EMPTY ProfileState on `_PROFILE_SCHEMA_VERSION` mismatch. R5's schema bump as written will **wipe all existing on-disk anchor history** on first run after upgrade — R6's domain-rollup fallback never gets a chance to fire because the entries are discarded before reach the fallback. Planning must choose between: (a) drop the version bump entirely, add `target_url: str = ""` as a tolerant additive read in `load_profile` using `entry.get("target_url", "")` — preserves history; or (b) keep the bump and add explicit upgrade logic that synthesizes `target_url=""` for v1 entries on read. Verdict in brainstorm leans (a) — simpler, no migration.
- [Affects R5, R6][P0 — Data path unverified][Needs research] `record_article(main_domain, new_entries)` does NOT see URLs — entries are pre-built by `_build_profile_entries` at `cli/plan_backlinks.py:569` which receives `sec_records` as `list[tuple[url_category, anchor_type, anchor_text]]` (URL already stripped). Additionally, two paths bypass the builder entirely: degrade path at `plan_backlinks.py:849` and work-themed at `plan_backlinks.py:953`. Planning must thread `target_url` through three call sites and disambiguate: does one article-with-N-links produce N ProfileEntries (one per link, per-destination math) or 1 ProfileEntry with a list field (per-article math)? Brainstorm assumed per-link/per-destination semantics — planning to verify with the SEO threat-model lens.
- [Affects Problem Frame, R13][P1 — Framing vs mechanism][User decision] Three reviewers flagged the same gap: Problem Frame claims Penguin **defense**; mechanism is post-hoc **report-only**. Reviewer recommended either (a) rename/reframe as "anchor distribution visibility" and drop Penguin-defense language, OR (b) add a publish-time soft warning (cheaper than full gate) before `record_article` finalizes when next anchor would breach. Brainstorm chose (a)-leaning: keep report-only. Planning to make the rename pass through Problem Frame, success criteria, and any user-facing copy before merge.
- [Affects R12][P1 — Exit-code collision][Technical] `plan_backlinks.py:1246`, `validate_backlinks.py:227`, `publish_backlinks.py:542` all use `SystemExit(2)` for generic errors. R12's "exit 2 on breach" collides with the project's existing convention; cron wrappers that do `report-anchors || true` will silently swallow the alarm. Planning to pick a non-conflicting code (suggest **exit 3** for alarm) or replace exit signaling with a sentinel file / structured stdout marker, then update R12 to match.
- [Affects R2, R3][P1 — Storage cannot hold the metric][Technical] `_MAX_ENTRIES = 100` (`anchor_profile.py:43`) trims ProfileState on every write. An active 5-target site at 1 article/day fills the window in 20 days — 90d metrics will silently degrade to "whatever the 100-entry window happens to cover" and per-target samples never reach the 50-floor. Planning to choose: (a) bump `_MAX_ENTRIES` (breaks the "no storage change" scope claim — needs explicit scope edit), or (b) replace the global cap with per-target buckets so per-target retention is independent.
- [Affects R3][P1 — Sample floor wrong granularity][Technical] `_RELIABLE_SAMPLE_MIN = 50` was set for domain-level aggregation. At per-target granularity the floor is rarely reached, and the Problem Frame's "30 articles to one URL with 80% exact match" scenario is **precisely the case R3 silently suppresses**. Planning to lower per-target floor (suggest 10-20) given asymmetric costs: a false-positive triggers a 5-min anchor strategy review; a suppressed true-positive triggers a Penguin penalty.

**Original deferred questions (still valid):**

- [Affects R10][Technical] Exact JSON schema for the `alarm` key — per-target keys (`alarm.<url>`) vs flat array — depends on the existing JSON shape conventions. Planning to inspect `_build_report` return shape and choose the least-invasive extension. Also: scope `--from-profile` vs JSONL-stdin entry paths — only `--from-profile` has `anchor_type` available, so JSONL path may need to be out-of-scope for v1.
- [Affects R8, R9][Needs research] Validate the default threshold values against any anchor profile data the operator has accumulated. If real data shows defaults trigger constantly or never, planning should adjust before merging. Run a one-off dump as the planning kickoff.
- [Affects R2, R3][Technical] When a target has data in the 30d window but not the 90d window (e.g., new target launched 20 days ago), how does the report present the 90d row — omit / show "insufficient data" / show partial-window metrics? Planning to choose a consistent rendering. (Related to AR8: consider whether 30d window earns its keep at all — it drives no breach verdict.)
- [Affects R1, R8][Design][P2] Top-3 concentration metric will alarm on healthy brand-anchor dominance (any well-optimized home page target trips 25% naturally). Planning to either (a) compute top-3 over non-branded anchors only, or (b) demote top-3 from breach trigger to informational metric (matching SEO-tool conventions).
- [Affects R4][Verification] "case-fold + trim matches Google's case-insensitive evaluation" is asserted without source citation. Planning should at minimum collapse internal whitespace and strip ASCII punctuation (`re.sub(r'\s+', ' ', text.casefold().strip())`) — cheap wins that move closer to a realistic SpamBrain normalization. Branded-variant preservation is still the limit; no stemming.
- [Affects R9][P2 — YAGNI] Per-target-URL override in R9 is a third config tier with zero operator demand. Planning may choose to ship only global + per-domain, leaving per-URL for a future PR.

## Next Steps

→ `/ce:plan` for structured implementation planning.
