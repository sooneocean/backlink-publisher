---
date: 2026-05-28
topic: round10-fresh-pass
focus: open-ended
---

# Ideation: Round 10 Fresh Pass

## Codebase Context

**Project shape**: Python 3.11/3.12 SEO backlink automation. 8 CLI entrypoints (JSONL pipeline):
`plan-backlinks → validate-backlinks → publish-backlinks` + `report-anchors`, `equity-ledger`,
`footprint`, `phase0-seal`, `generate-backlink-text`. Flask WebUI (~20 route modules + 5 services +
`webui_store/` 5-6 JSON singletons + `events.db` SQLite). ~22 publisher adapters via one-liner registry
(manifest-driven: UiMeta/BindDescriptor/Policy). ~32.7k SLOC, ~3300 tests. 20+ `bp-*/` git worktrees.
HEAD `628bed2d`.

**Excluded from this round (shipped / round-9 survivors)**:
Equity Ledger, Health Dashboard, events projector dedup, token-drift exit, Blogger OAuth,
fetch().json() guards, secret-file 0600, Dual-State Divergence Auditor (#238), registration-drift
sweep (#283), reliability policy layer (#285), thin-WebUI Phase 2 (#284), Webwright scaffold (#286),
binding-surface-coverage (#288), deep-opt RegistryEntry + medium Wave1 + WebUI polish (#289),
generate-backlink-text LLM CLI (#275). Round-9 survivors: Destination-Page Preflight; Reconcile Swallow
Recovery Log; Link-Kind×Dofollow Equity Cross-Tab; Emit-Side Footprint Self-Sabotage Audit; Anchor-Language
Ratio Calibration.

## Ranked Ideas

### 1. events.db Authoritative State Source ⭐ Synthesis
**Description:** Extend `publishing/reliability/events.py:emit_attempt` (already writes to events.db
for publish attempts) to cover the full row lifecycle: plan→validate→publish→verify. Demote the 5
`webui_store/` JSON singletons (`history_store`, `profiles_store`, `drafts_store`, `schedule_store`,
`queue_store`) to read-only projection caches rebuilt on demand from events.db — mirroring the existing
equity-ledger projector pattern. `audit_state` CLI (which diffs publish-history.json vs events.db to
detect divergence) becomes redundant.
**Rationale:** Eliminates the dual-store divergence bug class permanently. audit_state CLI's existence
is the symptom; making events.db the single canonical store is the cure. Every future feature reading
history/queue state has one authoritative source. The projector pattern already works (equity ledger,
health dashboard both use it).
**Downsides:** Large migration: all 5 singletons' write paths need rerouting. Careful projector
design required to avoid query performance regression under large events.db.
**Confidence:** 82%
**Complexity:** High
**Status:** Explored — brainstorm 2026-05-28

### 2. Manifest Full-Fidelity Initiative ⭐ Synthesis
**Description:** (a) `manifest_autogen` script reads each adapter module via AST and emits draft manifest
dicts for the 12 platforms with `_manifests.py:400-434` stub entries (explicitly commented "Phase-2
placeholder stubs — WIP"); (b) Wire the 4 declared Policy fields (`throttle_band`, `language_whitelist`,
`retry_id`, `liveness_probe_sec`) to runtime dispatch (today medium/velog hardcode jitter constants
that duplicate/ignore their manifest values); (c) Standardize `BindDescriptor.extras` into first-class
fields (`credential_shape`, `requires_additional_id`) so WebUI bind forms auto-render from declaration
instead of 5-site manual wiring.
**Rationale:** Unblocks WebUI bind cards for 21 adapters + Policy enforcement in one sprint. Manifest
becomes executable contract rather than documentation. Eliminates the "new token-paste channel requires
5-site wiring" class of bugs.
**Downsides:** Auto-generated stubs need per-adapter human validation. Policy activation changes
existing throttle behaviour — full test coverage required.
**Confidence:** 78%
**Complexity:** High
**Status:** Unexplored

### 3. Plan Claims → Code Coverage Gate
**Description:** `plan_check.py:284` validates plan frontmatter but not the converse: whether `claims.paths`
files were actually modified in the implementing PR diff. Add a CI step that cross-references the plan's
`claims.paths` against `git diff --name-only HEAD~1..HEAD` and gates/warns when a declared path was
untouched.
**Rationale:** Closes the `feedback_late_plan_revisions_skip_code` documented gap — doc-review-added
changes land in the plan doc but not in the code. Implementation cost ~50 lines; plan-check
infrastructure already exists.
**Downsides:** False-positive risk: some claims.paths entries may be reference reads, not required
modifications. Needs schema addition to distinguish "must-touch" vs "references" intent.
**Confidence:** 85%
**Complexity:** Low
**Status:** Unexplored

### 4. Unified Health JSON Contract + WebUI Console
**Description:** Introduce `bp-health` subcommand that calls all 9+ advisory sub-engines and emits a
single structured JSON envelope (platform cohorts × health dimensions × action items). WebUI `/health`
route consumes this same contract. `reset_circuit()` exposed as a manual reset button. Monitoring
cron scripts and future CI advisory gate read the same contract.
**Rationale:** Compresses "is everything healthy?" from 9 shell commands to 1. WebUI and CLI share one
computation path. Fixing a health calculation propagates everywhere automatically.
**Downsides:** Aligning 9 CLIs' schemas into one envelope is non-trivial engineering. Risk of
over-broad god-object contract.
**Confidence:** 80%
**Complexity:** High
**Status:** Unexplored

### 5. Machine-checked Worktree Turf Gate
**Description:** `scripts/claim-unit.sh <topic>` atomically creates `bp-<topic>/` worktree and writes
`.claim` lock file (timestamp + agent-id). `scripts/check-turf.sh` reads all `.claim` files and exits
non-zero if two worktrees have uncommitted changes on the same `src/` paths. Wired as pre-push hook.
**Rationale:** Converts the most expensive recurring multi-agent failure mode from tribal knowledge into
a hard gate. Invalidates `feedback_multi_agent_turf_check`, `feedback_worktree_concurrent_switching`,
`feedback_stash_message_as_concurrent_agent_handshake` simultaneously.
**Downsides:** Requires agent cooperation to use claim-unit.sh; external agents may bypass. Needs `.claim`
cleanup mechanism to avoid stale locks.
**Confidence:** 88%
**Complexity:** Medium
**Status:** Unexplored

### 6. Dry-Run Per-Adapter Payload Fidelity (Unit 6)
**Description:** `adapters/__init__.py:777-794` has an explicit `pass` body with comment "per-adapter
dry-run not yet implemented (Unit 6 deliverable)". The `dry_run_intercept()` context and `Session.send`
patching are wired — only the per-adapter payload validation logic is missing. Implement dry-run
validators for each adapter: schema shape, anchor placement, tag validation.
**Rationale:** Closes a documented deliverable debt; especially valuable after generate-backlink-text
shipped — LLM content needs a validation path that doesn't require real publish attempts.
**Downsides:** 22 adapters with different payload shapes = linear implementation work. Can be
incrementally batched.
**Confidence:** 87%
**Complexity:** Medium
**Status:** Unexplored

### 7. Config Snapshot Secret Scrub
**Description:** `config/_config_io.py:29 _snapshot_config` snapshots the full TOML (including
`medium_integration_token`, `blogger.oauth.client_secret`) into `.config-history/` (up to 20 copies)
without any redaction. Add `_scrub_secrets_for_snapshot()` that blanks credential fields before the
path is handed to `rotate_snapshots`.
**Rationale:** Closes a CLAUDE.md-documented security trap with ~30 lines of code. Eliminates
credential spread across 20 rotation snapshots without affecting live config.
**Downsides:** Needs a maintained list of "what counts as a credential field"; must stay in sync as
new adapters add credential fields.
**Confidence:** 92%
**Complexity:** Low
**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | Per-Channel Identity Surfacing | Too narrow/incremental; "Unit 6 backfill" has been deferred for multiple sprints without action — not a product improvement on its own |
| 2 | Delete per-file monolith budget | Regressive — per-file granularity is intentional; AGENTS.md 80-char rationale requirement is deliberate discipline |
| 3 | Adapter Fallback Chain Order Pin | Too theoretical; no recorded production incident from ordering issues |
| 4 | Derive \_BROWSER\_TIER from manifest | Absorbed into Manifest Full-Fidelity Initiative; standalone value too small |
| 5 | Policy.language\_whitelist validate gate | Absorbed into Manifest Full-Fidelity Initiative (Policy activation) |
| 6 | Config Round-Trip Regression Test | Superseded by Config Overlay Store (structural fix > test) |
| 7 | Pipeline Update Mode | Genuinely novel but scope too large: publish-modes enum, idempotency store, all adapters |
| 8 | Capability-at-Validate-Time Snapshot | Overlaps with canary gate resolution; canary hard-skip already handles the core problem |
| 9 | Batch Multi-Platform Selection | UX improvement but no structural debt addressed |
| 10 | Session-State Loss Recovery | Real pain; drafts_store skeleton exists — but not high enough leverage vs the 7 survivors |
| 11 | Ledger Liveness from Adapter Publish-Time | Absorbed into #1 (events.db authoritative) — naturally solved once single source exists |
| 12 | Footprint from Live Published HTML | Technically compelling but canary→footprint re-plumbing complexity/value ratio too high |
| 13 | URL Reachability TTL Cache | Pragmatic but TTL invalidation semantics are harder than they appear |
| 14 | Auto-derive card\_template from slug | Absorbed into Manifest Full-Fidelity Initiative (BindDescriptor standardisation) |
| 15 | Canary Uncertain Debt | Operational task (run probes) more than product improvement; not ideation priority |

## Session Log
- 2026-05-28: Round 10 fresh pass — 32 raw candidates (4 frames × 8 each), 22 unique after dedupe, 2 cross-frame synthesis additions, 7 survivors. #1 events.db Authoritative State Source selected for brainstorm.
