---
title: "feat: Blast-radius Phase 1 — channel cull + money-site cell containment"
type: feat
status: completed
shipped: 87fbbcfa (#267)
date: 2026-05-27
origin: docs/brainstorms/2026-05-27-cross-channel-blast-radius-requirements.md
claims: {}
---

# feat: Blast-radius Phase 1 — channel cull + money-site cell containment

## Overview

Phase 1 of cross-channel blast-radius containment. Two structural, low-cost
risk reductions that shrink the SEO connected-penalty exposure surface *before*
any measurement tooling is built:

1. **R9 — channel cull**: a new read-only CLI verb that surfaces channels which
   add footprint but little equity (nofollow + `referral_value="low"`) as
   **cull candidates**, reusing the registry's existing grading. (Informational
   diagnostic — surfacing a candidate is not a cull; it reduces no blast radius
   by itself until a later, out-of-scope retirement acts on it.)
2. **R7-minimal — money-site cell containment**: a new operator-authored
   `[cells.*]` config section plus an **opt-in admission gate** in
   `plan-backlinks` so each money site that declares a cell only emits payloads
   for its declared channel subset — breaking the full mesh for enrolled sites.

The blast-radius **scorer** (origin R1–R6) is **Phase 2** (validation +
guardrail) and the **de-correlation** work (R8) is **Phase 3** — both out of
scope here.

**What Phase 1 actually delivers (honest framing):** the *mechanism* for
structural containment, not an automatic blast-radius reduction. The reduction
is realized only for money sites the operator **enrolls** in a cell (R7 is
opt-in) — unenrolled sites keep full-mesh exposure. R9 (cull) is purely
informational in Phase 1. So Phase 1 = "containment is now possible and
one-site-at-a-time safe to adopt", contingent on operator enrollment; the
scorer (Phase 2) later quantifies the realized reduction.

## Problem Frame

The operator publishes in a *full mesh*: every money site ends up linked from
all ~21 channels. A single algorithmic detection on any one correlation axis can
connect every money site to the same footprint and devalue them together
(see origin: `docs/brainstorms/2026-05-27-cross-channel-blast-radius-requirements.md`).

Review of the initial brainstorm reversed an earlier "scorer-first" plan: under
full mesh the scorer's worst-case answer is trivially "all money sites", so the
cheap structural levers (cull + cells) must lead and measurement validates later
(see origin: Key Decisions / Sequencing decision 2026-05-27).

**Critical grounding correction from repo research:** there is *no* money-site ×
channel cross-product in `cli/plan_backlinks/`. The package is strictly
**one-platform-per-row** (`_dispatch_row` emits for the single `row["platform"]`).
"Full mesh" is operator behavior — running the pipeline once per channel, or
authoring `seeds.jsonl` with one row per (money-site, channel) pair. Therefore
R7-minimal is implemented as a **per-row admission gate** keyed on
`(row["main_domain"], row["platform"])`, *not* as a fan-out-loop split.

## Requirements Trace

- **R9** (Phase 1): Surface nofollow + low-`referral_value` channels as cull
  candidates; read-only; reuse registry grading. → Unit 1.
- **R7** (Phase 1, minimal): Each enrolled money site touches a *disjoint*
  channel subset; containment is a config-declared cell, enforced at the
  `plan-backlinks` admission boundary; the scorer does not generate cells. → Units 2, 3.
- **Origin success criterion (Phase 1):** exposure is measurably smaller than
  full mesh and checkable *without* the scorer — count the channels each enrolled
  money site touches, confirm declared cells are disjoint. → Units 2, 3.

## Scope Boundaries

- **Not** the blast-radius scorer (R1–R6) — that is Phase 2.
- **Not** de-correlation / anchor-pool rotation / timing jitter (R8) — Phase 3.
- **Not** an actual `register()` removal of any channel. Unit 1 only *surfaces*
  cull candidates; retiring a channel later carries the full sync cost
  (`register()` + manifest + `_AUTH_TYPE_BY_PLATFORM` + 3 sample tests) and is
  out of scope.
- **Opt-in only:** money sites without a `[cells.*]` entry keep current behavior
  (unrestricted). No default-deny.
- **No live network / no `rel` re-verification.** The cull verdict is derived
  from static registry grading, not from fetching pages.

## Context & Research

### Relevant Code and Patterns

- **New read-only verb template:** `src/backlink_publisher/cli/preflight_targets.py`
  — single module, `def main(argv=None)` + `if __name__ == "__main__": main()`
  guard, `argparse` with `--input/-i` + `--log-level`, `PipelineLogger` with an
  always-on `.recon(...)` stderr summary, `try/except PipelineError → handle_error`,
  exit 0 for a pure diagnostic.
- **Markdown-default + `--json` verb:** `src/backlink_publisher/cli/report_anchors.py`
  + formatter `cli/_report_format.py` (tier-segmented output).
- **Channel grading API (`publishing/registry.py`):** `registered_platforms()`
  (:401), `active_platforms()` (:508), `dofollow_status(name)` (:406 → `True | False
  | "uncertain" | None`), `referral_value(name)` (:427 → `"high" | "low" | None`),
  `dofollow_rationale(name)` (:438). `register()` (:247) *gates* `referral_value`
  as required whenever `dofollow != True`, so it is always non-None for nofollow
  channels — no missing-data branch needed.
- **R7 gate point:** `cli/plan_backlinks/core.py:main()` row loop (~:301), right
  after `validate_input_payload(row, line_num)` and before `_dispatch_row`.
  Catches both the JSONL and bulk-input paths at one chokepoint.
- **Planned payload shape:** `cli/plan_backlinks/_payload.py:_generate_payload()`
  (return ~:262) — carries `main_domain`, `target_url`, `platform`,
  `links[].anchor`, and `metadata{dofollow_tier, referral_value}` via
  `dofollow_tier_metadata()` (`_payload.py:29`).
- **Config section parser:** mirror `config/parsers/target.py`; wire in
  `config/loader.py` (~:131); add a field in `config/types.py`.

### Institutional Learnings (`docs/solutions/`)

- `logic-errors/save-config-write-paths-bypass-preservation-2026-05-15.md` —
  `save_config` owns only `_SAVE_CONFIG_KNOWN_ROOTS`; everything else is
  preserved verbatim via `_preserve_unknown_sections`. **Put cells under a new
  top-level `[cells.*]` root** (preserved-by-default); do **not** add it to the
  owned roots, and do **not** nest under `[targets.*]` (round-trip trap).
- `test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md`
  — write **positive + semantic** round-trip assertions
  (`assert cfg2.cell_assignments == cfg.cell_assignments`), never
  `assert "[cells." not in rewritten`.
- `logic-errors/argparse-choices-vs-usage-error-exit-clash-2026-05-20.md` —
  closed-set flags (`--format`) must **not** use argparse `choices=` (exits 2);
  validate post-parse and raise `UsageError` (exit 1). Put the valid set in `help=`.
- `workflow-issues/grep-dofollow-map-before-shipping-adapter-2026-05-20.md` —
  treat `dofollow_status == None` as *unverifiable* (do **not** auto-cull);
  `False`/`"uncertain"` are the cull-candidate signals. A cull recommendation is
  not a cull.
- `best-practices/plan-time-url-validation-prevents-publish-404-2026-05-15.md` —
  when reducing a money site's channel set, grep for any link-density/quota/count
  code that assumed "publishes to all channels" and keep it in sync; emit a
  `recon`-level signal when a site's fan-out is downscoped.
- `test-failures/tests-coupled-to-operator-config-state-2026-05-18.md` — gate
  tests must isolate `BACKLINK_PUBLISHER_CONFIG_DIR` (autouse fixture does this)
  and use RFC-2606 domains (`example.com`); `code == 0` + empty stdout is a
  routing-divergence smell, not success.
- `logic-errors/python-m-needs-main-module-after-package-split-2026-05-19.md` —
  keep the cull verb a **single module** (`cli/cull_channels.py`) so no
  `__main__.py` is needed; add it to the `python -m` smoke loop.

### External References

None — strong local patterns exist for every piece; external research skipped.

## Key Technical Decisions

- **R7-minimal is a per-row admission gate, not a fan-out split.** Repo research
  confirmed there is no channel cross-product to split; the one chokepoint that
  covers JSONL + bulk-input is the `core.py:main()` row loop after
  `validate_input_payload`.
- **Cell enforcement = opt-in + drop-with-warning** (user decision 2026-05-27).
  Only money sites with a `[cells.*]` entry are gated. A row whose `platform`
  is not in its site's cell is **dropped** with an always-on `recon` warning;
  exit stays 0. Sites without a cell entry are unrestricted. Rationale:
  progressive, zero-disruption rollout — the operator enrolls sites one at a
  time, and a misconfigured cell never halts an entire batch.
- **Cells live in a new `[cells.*]` top-level config root**, parsed into
  `Config.cell_assignments: dict[str, list[str]]`, preserved verbatim by
  `save_config` (unmanaged root). Avoids the `[targets.*]` round-trip trap.
- **Unknown channel name in a cell is a fail-loud `InputValidationError` at
  parse time** (the repo's config-validation exception — `_util/errors.py:22`;
  there is no `ConfigError` class). A typo (`"telegrph"`) would otherwise
  silently drop a channel the operator believes is enrolled — a footgun in a
  *safety* feature. Validate every cell channel against `registered_platforms()`.
- **Cell overlap (non-disjoint cells) is a fail-loud `InputValidationError` at
  parse time** (tightened after review). Disjointness *is* the point of
  containment, and the check runs at config load — before any publishing — so a
  hard-fail has near-zero disruption (no mid-batch halt), and it is consistent
  with the fail-loud unknown-channel decision. This keeps Phase 1's "confirm
  declared cells are disjoint" success criterion self-certifiable. The Phase 2
  scorer still quantifies the actual connected-set across enrolled + unenrolled
  sites; Phase 1 just guarantees declared cells don't overlap.
- **Missing-cell is opt-in by design, but made visible.** A site with no
  `[cells.*]` entry is unrestricted (the point of opt-in). To prevent the
  "operator thinks it's contained but isn't" footgun, `plan-backlinks` emits an
  always-on `recon` summary of which money sites in the run *are* enrolled vs
  unrestricted, so an accidentally-unenrolled site is visible.
- **Cull verdict from static registry grading only.** No page fetch, no
  page-wide `nofollow_detected` flag (noisy — counts nav/footer `rel`). The verb
  outputs evidence/recommendation, never an authoritative deletion.

## Open Questions

### Resolved During Planning

- *Where is the full-mesh fan-out?* → There is none in code; full mesh is
  operator behavior. R7 gates seed-row admission. (repo research)
- *Should the full-mesh default be fixed upstream in `plan-backlinks`?* (origin
  deferred Q) → The admission gate *is* the upstream fix; no separate default to
  change.
- *Where do cells live in config?* → New `[cells.*]` unmanaged root. (repo
  research + save-config learning)
- *Enforcement posture?* → Opt-in + drop-with-recon-warning. (user decision)
- *How to treat `dofollow_status == None` in the cull?* → Unverifiable; list
  separately, never auto-cull. (dofollow-map learning)

### Deferred to Implementation

- Exact markdown table columns / `_report_format` helper reuse for the cull
  output — settle when mirroring `report_anchors`.
- Whether the cull verb enumerates `registered_platforms()` or `active_platforms()`
  — confirm which set the operator wants (likely active; decide against real
  registry contents at implementation).
- ~~Whether any link-density/quota path assumes full-mesh~~ — **resolved during
  review**: density is per-payload, no cross-row aggregation; dropping a row is
  safe (see Unit 3 approach).

### Deferred to Later Phases (out of scope)

- **Containment granularity — `main_domain` vs `target_url`.** Cells key on
  `main_domain`, but payloads also carry `target_url`; one money domain may serve
  multiple target pages. If the real correlation risk is at the target-URL level,
  `main_domain` cells are coarser than the threat model. v1 uses `main_domain`
  (the available gate key and the natural operator-config unit); revisit when the
  Phase 2 scorer reveals whether target-level granularity matters.
- Phase 1 sizing — how many money sites / cells / how aggressive the cull — is an
  operator config decision, not a code decision; the mechanism is count-agnostic.
- Gate hardness + threshold for the *scorer* (origin R6), content-template
  similarity (origin R3), penalty-propagation validation (origin Outstanding Q) —
  all Phase 2/3.

## Implementation Units

Units 1 and 2 are independent; Unit 3 depends on Unit 2.

```
Unit 1 (cull verb)         ── independent ──┐
Unit 2 (cells config) ──► Unit 3 (gate)     │
```

- [x] **Unit 1: `cull-channels` read-only CLI verb (R9)**

**Goal:** Surface nofollow + `referral_value="low"` channels as retirement
candidates; list `dofollow_status == None` channels separately as unverifiable.

**Requirements:** R9

**Dependencies:** None

**Files:**
- Create: `src/backlink_publisher/cli/cull_channels.py`
- Modify: `pyproject.toml` (`[project.scripts]`: `cull-channels = "backlink_publisher.cli.cull_channels:main"`)
- Modify: `tests/test_cli_python_m_entrypoints.py` (add verb to smoke loop)
- Test: `tests/test_cli_cull_channels.py`

**Approach:**
- Single module mirroring `cli/preflight_targets.py`: `main(argv=None)`, argparse
  with `--format` (default `markdown`, also `json`) and `--log-level`,
  `if __name__ == "__main__": main()`.
- Validate `--format` **post-parse** → `UsageError` on invalid (exit 1); put the
  valid set in `help=`. No `choices=`.
- Enumerate channels via registry; for each read `dofollow_status` +
  `referral_value` + `dofollow_rationale`. Classify into: **cull candidate**
  (`dofollow_status is False` AND `referral_value == "low"`),
  **unverifiable** (`dofollow_status == "uncertain"`), **keep** (everything else).
  Note: `"uncertain"` maps to `"unverifiable"` — never auto-culled; and
  `dofollow_status is None` means *unregistered* platform, not a live signal.
- Markdown table to stdout by default; `--json` emits machine-clean JSONL.
- Always-on `.recon(...)` summary line to stderr (candidate count); exit 0.

**Patterns to follow:** `cli/preflight_targets.py` (verb skeleton **and** the
`--format` closed-set + post-parse `UsageError` pattern), `cli/_report_format.py`
+ `cli/report_anchors.py` (markdown-table rendering only — note `report_anchors`
exposes a boolean `--json` flag, not `--format`, so mirror it for rendering, not
for flag shape), `cli/plan_backlinks/_payload.py:29` `dofollow_tier_metadata`
(reading both grades).

**Test scenarios:**
- Happy path: a registered nofollow + `referral_value="low"` channel appears
  under cull candidates; a dofollow channel does not.
- Edge: nofollow + `referral_value="high"` channel → NOT a candidate (carries
  equity).
- Edge: `dofollow_status is True` + `referral_value="low"` → NOT a candidate
  (dofollow carries equity regardless of referral grade); classifier must
  tolerate `referral_value is None` for dofollow channels without erroring.
- Edge: `dofollow_status is None` channel → listed under "unverifiable", never
  under candidates.
- Edge: `--format json` → stdout parses as valid JSON/JSONL and is free of
  markdown / human prose.
- Error path: `--format xml` → `UsageError`, exit 1 (not argparse exit 2).
- Integration: `python -m backlink_publisher.cli.cull_channels` runs and exits 0
  (smoke).

**Verification:** Running the verb against the live registry prints a candidate
list whose membership matches a hand-traced application of the classification
rule; `--json` output is machine-parseable; exit code 0.

- [x] **Unit 2: `[cells.*]` config section + round-trip preservation (R7-minimal)**

**Goal:** Parse operator-authored money-site→channel-subset cells into
`Config.cell_assignments`; fail loud on unknown channels; preserve the section
across `save_config`; fail loud on cross-cell overlap.

**Requirements:** R7

**Dependencies:** None

**Files:**
- Create: `src/backlink_publisher/config/parsers/cells.py`
- Modify: `src/backlink_publisher/config/loader.py` (wire parser ~:131)
- Modify: `src/backlink_publisher/config/types.py` (add `cell_assignments` field)
- Modify: `config.example.toml` (document the `[cells.*]` section, operator-edit-only)
- Test: `tests/test_config_cells.py`

**Approach:**
- Parser mirrors the **table-iteration shape** of `config/parsers/target.py`
  (`[cells."<main_domain>"]` blocks), but a cell is a **dict-of-list**
  (`channels = ["telegraph", "rentry", ...]`), not the dict-of-dict shape of
  `[targets.*]` — re-read the shape before assuming parallel structure.
- For **fail-loud** validation behavior, mirror `config/parsers/alarm.py`
  (raises `InputValidationError`), NOT `target.py`'s tolerant skip-with-warning
  posture. Validate each channel name against `registered_platforms()`; raise
  `InputValidationError` on any unknown name.
- Add `Config.cell_assignments: dict[str, list[str]]` (empty default).
- Do **not** add `cells` to `_SAVE_CONFIG_KNOWN_ROOTS` — it stays an unmanaged
  root preserved verbatim by `_preserve_unknown_sections`.
- Disjointness: at parse, if a channel appears in more than one site's cell,
  raise `InputValidationError` naming the overlapping channel and the sites that
  share it (fail-loud — overlap defeats containment).

**Patterns to follow:** `config/parsers/alarm.py` (fail-loud
`InputValidationError` validation), `config/parsers/target.py` (table-iteration
shape only), `config/loader.py:131-137`, `config/types.py` (`target_three_url` /
`site_url_categories` field style), `config/_toml_utils.py`
(`_SAVE_CONFIG_KNOWN_ROOTS`, preservation passthrough).

**Test scenarios:**
- Happy path: `[cells."example.com"] channels=["telegraph","rentry"]` parses to
  `Config.cell_assignments == {"example.com": ["telegraph", "rentry"]}`.
- Round-trip (positive/semantic): `save_config` then reload preserves the
  section — `cfg2.cell_assignments == cfg.cell_assignments`; a sibling unmanaged
  section (e.g. `[anchor_alarm]`) also survives the same save.
- Error path: a cell listing an unknown channel (`"telegrph"`) →
  `InputValidationError` at parse, naming the bad value.
- Error path: the same channel in two different sites' cells →
  `InputValidationError` at parse, naming the overlapping channel and both sites.
- Edge: empty `[cells.*]` / absent section → `cell_assignments == {}`, no error.

**Verification:** A config with cells loads into `cell_assignments`; a typo'd
channel fails loud; the section survives a `save→load→save→load` cycle
semantically intact; overlapping cells fail loud at parse.

- [x] **Unit 3: opt-in cell admission gate in `plan-backlinks` (R7-minimal)**

**Goal:** At the seed-row admission boundary, drop rows whose `(main_domain,
platform)` violates the site's declared cell — opt-in (only sites with a cell),
drop-with-`recon`-warning, exit 0.

**Requirements:** R7

**Dependencies:** Unit 2 (`Config.cell_assignments`)

**Files:**
- Modify: `src/backlink_publisher/cli/plan_backlinks/core.py` (row loop ~:301,
  after `validate_input_payload`, before `_dispatch_row`)
- Test: `tests/test_cli_plan_backlinks_cell_gate.py`

**Approach:**
- Load cells from `Config.cell_assignments` once before the row loop.
- Per row: if `row["main_domain"]` has a cell entry **and** `row["platform"]`
  not in that cell → skip the row, emit an always-on `.recon(...)` warning
  (`dropped <platform> for <main_domain>: not in cell`), and continue. If the
  site has no cell entry → pass through unchanged (opt-in).
- Route the decision through a single small helper (one chokepoint) so the
  bulk-input path and JSONL path share identical logic.
- **Enrolled-vs-unrestricted summary:** at run start (or end), emit an always-on
  `.recon(...)` line listing which money sites in the run are cell-enrolled vs
  unrestricted, so an accidentally-unenrolled site (silent full mesh) is visible.
- **End-of-run drop tally:** emit an always-on `.recon(...)` summary
  (`cell gate dropped N rows across M sites`) after the loop, so a run that
  produces fewer payloads than seeded is distinguishable from a routing bug.
  Document that exit 0 with fewer-than-seeded (or empty) stdout is *expected*
  gate behavior, not failure — per the `code==0 + empty stdout = routing smell`
  learning, the tally is the disambiguating signal.
- **Density coupling — resolved, no reconciliation needed:** grep confirmed link
  density is per-payload (`_TARGET_PADDED_LINK_COUNT`/`_build_link_density_paragraph`
  in `_links.py`, applied inside `_generate_payload`); there is no cross-row /
  per-site aggregation, so dropping a row simply yields one fewer independent
  payload. No quota path to sync.
- **Gate runs after the fetch-verify prefetch** (`core.py:282-299`, before the
  row loop). Out-of-cell rows are still prefetched (wasted network for dropped
  rows). Acceptable for v1; moving the gate ahead of prefetch is a possible
  optimization, deferred.

**Execution note:** Start with a failing integration test that seeds one allowed
and one violating row through `core.main()` and asserts only the allowed payload
reaches stdout.

**Patterns to follow:** `cli/plan_backlinks/core.py` row loop + `_dispatch_row`,
`validate_input_payload` call site, `PipelineLogger.recon` usage in
`cli/preflight_targets.py`.

**Test scenarios:**
- Happy path: site WITH a cell, row `platform` in the cell → payload emitted
  normally to stdout.
- Happy path (opt-in): site WITHOUT a cell entry → row passes regardless of
  `platform`.
- Edge: site WITH a cell, row `platform` NOT in cell → no payload on stdout, a
  `recon` warning on stderr, exit 0.
- Integration: isolated `BACKLINK_PUBLISHER_CONFIG_DIR` + RFC-2606 domain; seed
  two rows (one allowed, one violating) via `core.main()` → stdout contains only
  the allowed payload (assert count + identity, not just exit 0).
- Integration: the bulk-input path (`urls_to_seed_rows`) is gated by the same
  helper — a violating bulk row is dropped identically.
- Edge: all rows for a batch violate their cells → empty stdout, a per-drop
  recon line each, an end-of-run tally, exit 0 (expected, not an error).
- Edge: the end-of-run drop tally count equals the number of violating rows.
- Edge: a run with one enrolled and one unenrolled money site emits a recon
  summary naming both with their enrollment status.

**Verification:** With a cell declared for a test domain, `plan-backlinks` emits
payloads only for in-cell channels and logs a `recon` line for each drop; exit 0;
sites without cells are unchanged.

## System-Wide Impact

- **Interaction graph:** Unit 3 sits in the `plan-backlinks` row loop — upstream
  of `validate-backlinks` / `publish-backlinks`. Dropped rows simply never enter
  the pipeline; no downstream contract changes.
- **Error propagation:** Cull verb and gate are diagnostic/advisory — both exit 0
  on normal operation. Config errors (unknown cell channel) surface as
  `InputValidationError` at load, consistent with existing config validation
  (`config/parsers/alarm.py`).
- **State lifecycle risks:** None new — no persisted state, no `events.db` writes,
  no credentials touched.
- **API surface parity:** New `cull-channels` is an additive `[project.scripts]`
  entry (CLI contract). `[cells.*]` is an additive config contract. No existing
  verb signatures change.
- **Integration coverage:** The dual-path drop (JSONL + bulk-input through one
  helper) and the save_config round-trip preservation are the cross-layer
  behaviors unit-level mocks won't prove — covered by Unit 2/3 integration
  scenarios.
- **Unchanged invariants:** No `register()` add/remove → the channel-removal sync
  surface (`_AUTH_TYPE_BY_PLATFORM` + 3 sample tests) is deliberately untouched.
  Sites without cells behave exactly as before. The one-platform-per-row contract
  of `plan_backlinks` is preserved (the gate only drops, never multiplies, rows).

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Typo'd channel name silently excludes a channel the operator thinks is enrolled | Fail-loud `InputValidationError` on unknown cell channel at parse (Unit 2) |
| `save_config` eats the new `[cells.*]` section | Keep it an unmanaged root; positive/semantic round-trip test (Unit 2) |
| A dropped row corrupts a per-site link-density/quota count | Grep + reconcile density path in Unit 3; integration scenario asserts |
| Non-disjoint cells defeat containment | Fail-loud `InputValidationError` at parse on overlap (Unit 2) |
| Site silently unenrolled (operator thinks it's contained) | Enrolled-vs-unrestricted recon summary on every run (Unit 3) |
| CI tests against "merged into latest main" — local green misleads | Rebase before relying on green (no registry mutation here, lower risk) |
| Cull verb mistaken for an authoritative delete | Output framed as candidates/evidence; no `register()` removal in scope |

## Documentation / Operational Notes

- Document `[cells.*]` in `config.example.toml` as operator-edit-only (like
  `[anchor.proportions]` / `[anchor_alarm]`), with the disjointness intent stated.
- Note in the example that cells are opt-in: a site with no `[cells.*]` entry
  publishes to any channel the operator runs.

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-27-cross-channel-blast-radius-requirements.md](docs/brainstorms/2026-05-27-cross-channel-blast-radius-requirements.md)
- Related code: `cli/preflight_targets.py`, `cli/report_anchors.py`,
  `publishing/registry.py`, `cli/plan_backlinks/core.py`, `config/parsers/target.py`
- Institutional learnings: `docs/solutions/logic-errors/save-config-write-paths-bypass-preservation-2026-05-15.md`,
  `docs/solutions/logic-errors/argparse-choices-vs-usage-error-exit-clash-2026-05-20.md`,
  `docs/solutions/workflow-issues/grep-dofollow-map-before-shipping-adapter-2026-05-20.md`
