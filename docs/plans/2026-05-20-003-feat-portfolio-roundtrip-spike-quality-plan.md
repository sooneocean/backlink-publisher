---
title: "Portfolio: save_config round-trip + Medium GraphQL spike + dofollow quality audit"
type: feat
status: completed
date: 2026-05-20
completed: 2026-05-20
deepened: 2026-05-20
claims: {}  # opt-out: portfolio plan spanning multiple unrelated PRs;
            # each shipped unit (e.g. A.2 #114/#116, B.1 #119) lives on
            # its own branch with no shared SHA story. Re-tighten if/when
            # a single bundling PR closes the plan.
---

# Portfolio: save_config round-trip + Medium GraphQL spike + dofollow quality audit

## Overview

Three orthogonal workstreams sequenced as separate phases. Each phase ships as 1–3 independent PRs; phases share no production code. Recommended order is **A → B → C**:

- **A** is concrete (extends an existing PR #99 pattern) and unblocks the next round of WebUI binding-card writes (PR #112 follow-up).
- **B** is a timeboxed spike whose GO/NO-GO report unblocks Medium Phase 2 implementation work.
- **C** is read-only measurement; running it *last* lets it benefit from A's clarified config surface and B's negative knowledge about Medium.

This is intentionally a **portfolio plan**, not a single deliverable. The portfolio framing exists so all three workstreams are visible to one reviewer; it does not imply they must land together. The benefit is bounded — see Open Questions for whether to split into 3 sibling plans.

## Problem Frame

### A. save_config round-trip gap

`backlink-publisher/CLAUDE.md` carries a documented caveat:

> `save_config` does **not** round-trip `[targets.*]`, `[sites.*]`, `[anchor_alarm]`, `[anchor.proportions]`, or `[llm.anchor_provider]`.

But the **canonical post-PR-#99 taxonomy** is documented in `src/backlink_publisher/config/writer.py` (the `save_config` docstring at lines ~280-307), which enumerates five branches: (a) emitted on every call, (b) emitted conditionally, (c) preserved as unemitted depth-2 subsections under managed roots, (d) preserved as unmanaged top-level when carrying key=value data, (e) pure-placeholder sections (header + comments only, no data) **intentionally not preserved** — design choice locked by `test_save_config_inplace_preserves_sections_with_keyvalue_data`. The CLAUDE.md caveat is stale outer doc; `config/types.py` docstrings are mostly correct but underspecify branch (e). The plan's audit (A.1) reconciles all three to the writer.py taxonomy.

Separately, three newly-shipped channel adapters — `ghpages` (PR #96), `writeas` (PR #103), `hashnode` (PR #102) — do **not** appear in `_SAVE_CONFIG_KNOWN_ROOTS`. The matching WebUI token-paste cards (PR #112) document them as "operator must edit config.toml; routing fields are read-only in UI." Extending save_config to manage these roots is the prerequisite for the future WebUI routing-config write-back PR.

### B. Medium Phase 0 spike not yet investigated

PR #88 (`fdeaebc`) shipped a cookies-only browser adapter for Medium. Memory [[project-medium-graphql-phase1-pr88]] records Phase 0 spike (Unit 0) unstarted; Phase 2 (Unit 2/3/4) blocked on spike verdict. **The cited Plan 005 source doc is not locatable** on-disk, in any worktree, or in git history matching plausible globs. This plan therefore acts as the spike's authoritative origin; if Plan 005 is recovered later, treat this plan's spike scope as the operative one and reconcile only the high-level intent. The spike's purpose is reconnaissance: can we publish via Medium's GraphQL endpoint, and if so, what is the auth/CSRF/rate-limit surface?

### C. dofollow platform quality is uninstrumented

Six dofollow platforms are live (blogger, medium, velog, ghpages, writeas, telegraph) per [[project-channel-binding-dashboard-plan-006]]. There is **no per-platform scorecard** for the metrics derivable from current on-disk state. Operators discover quality issues anecdotally (e.g., the [[project-phase4-scaffold]] revert was caught by manual `_DOFOLLOW_BY_CHANNEL` grep, not by an alarm). C ships only the **measurement substrate** over fields actually present in `publish-history.json`; richer instrumentation (throttle events, retry counts, latency) requires a separate schema-extension plan deliberately not included here.

## Requirements Trace

- **R-A1.** Every depth-1 root operators want WebUI-writable round-trips through `save_config` without losing user-edited content elsewhere in the file.
- **R-A2.** `backlink-publisher/CLAUDE.md` and `config/types.py` docstrings about which sections round-trip match the writer.py:280-307 5-branch taxonomy after A lands.
- **R-A3.** Channels `ghpages`, `writeas`, `hashnode` join `blogger` / `medium` / `targets` as managed roots with the same subsection-preservation semantics PR #99 established.
- **R-A4.** `save_config`'s atomic-write preserves file mode `0o600` on `config.toml` and `0o700` on the parent dir / `.config-history/` snapshot dir. This invariant survives the managed-root extension.
- **R-B1.** Produce a written GO/NO-GO report covering Medium GraphQL endpoint shape, auth, CSRF, rate-limit, and ToS risk — sufficient for a reviewer to decide whether to fund Phase 2 GraphQL adapter work without re-running reconnaissance.
- **R-B2.** Spike timebox: ≤ 1 working day. ≤ 10 GraphQL requests total. Posts created during probing are unlisted and explicitly deleted by end-of-spike. No production adapter code lands.
- **R-B3.** Network captures and the GO/NO-GO report use indirect framing for endpoint URLs and rate thresholds; raw HAR fragments / cookie values never leave the spike machine.
- **R-C1.** Per-platform scorecard over fields **actually present** in `publish-history.json` today: total publish attempts, status-derived success rate, unverified-suffix rate, last-publish timestamp, dofollow-rel value (joined from `_DOFOLLOW_BY_CHANNEL`). Throttle / retry / 429 / latency metrics are **explicitly out of scope** here (deferred to a follow-on schema-extension plan).
- **R-C2.** Scorecard is reproducible from on-disk JSON snapshots without re-publishing or network access.
- **R-C3.** Scorecard output drives a follow-on requirements doc (or a final section inside the scorecard report itself) seeding a future `ce:brainstorm` round.
- **R-C4.** Scorecard emits a per-platform **log-freshness** field (oldest row timestamp + oldest snapshot across `.config-history/` if helpful) so operators can detect history truncation / wipe events ([[project-pr87-verification-complete]] wipe precedent).
- **R-C5.** Scorecard CLI supports a `--redact` flag that replaces URL-shaped fields with placeholders before any markdown rendering, with fixture-tested coverage across .com/.co/.blog/.dev/.me/.io/.xyz/ccTLDs/IDN/subdomain-only/`@user` shapes.

## Scope Boundaries

**In scope:**
- A: extend `_SAVE_CONFIG_KNOWN_ROOTS` to cover the new channels; audit + lock the 5-branch taxonomy in test form; refresh `CLAUDE.md` caveat.
- B: read-only reconnaissance against Medium GraphQL surface (own throwaway account, ≤ 10 requests); GO/NO-GO report.
- C: read-only collector over on-disk JSON; one CLI subcommand + tests; scorecard report.

**Out of scope:**
- WebUI routing-config write-back (separate PR after A.2 lands; the future PR introduces the `_persist_channel_config_for` helper at first real use rather than as standalone scaffolding).
- Medium GraphQL adapter implementation (gated on B's GO verdict).
- Publish-history schema extension to record throttle / retry / 429 / latency (gated on C's findings; would be a separate plan).
- WebUI write routes for `[sites.*]`, `[anchor_alarm]`, `[anchor.proportions]`, `[llm.anchor_provider]` — see Decision D7 for the falsifiable deferral criterion.
- The two concurrently-drafted plans (`docs/plans/2026-05-20-001-feat-banner-image-gen-plan.md` in `bp-banner-image-gen` worktree, `docs/plans/2026-05-20-002-feat-homepage-url-autoderive-v1-plan.md` in `bp-url-derive-v1` worktree) — orthogonal feature work, ignored.

**Operational caution (not a hard boundary):** before opening any PR for Phase A, re-run `git status` + `ls src/backlink_publisher/persistence/ tests/test_safe_write*` in the main worktree. If new files appear that weren't present at plan-write time (2026-05-20, HEAD `6e95967`), branch off `origin/main` in a fresh worktree to avoid mixing scopes.

## Context & Research

### Relevant Code and Patterns

- **A — config writer** (`src/backlink_publisher/config/writer.py`):
  - `_SAVE_CONFIG_KNOWN_ROOTS = frozenset({"blogger", "medium", "targets"})` (~line 46)
  - `_canon_subsection_key`, `_toml_heading_path`, `_preserve_unknown_sections` (~lines 100-200)
  - `save_config` (~line 211) plus the **5-branch section taxonomy docstring** (~lines 280-307) — canonical truth post-PR-#99, supersedes CLAUDE.md caveat
  - Monolith ceiling 360 (bumped in PR #99); re-measure with `radon raw -s` before opening A.2.
- **A — channel-config dataclasses** (`src/backlink_publisher/config/types.py`): `GhpagesConfig` (~line 94), `HashnodeConfig` (~line 115), `WriteAsConfig` (~line 136) — already defined; loader.py:170-200 parses them as depth-1 sections. **A.2 does not need to design new schemas, only register the existing ones as managed roots.**
- **A — config example** (`config.example.toml`): empirical reference for which sections operators actually edit. ghpages/writeas/hashnode currently appear (if at all) as depth-1 only.
- **A — file-mode invariant** (`config/writer.py` atomic-write path): operators store secrets here; the 0o600 invariant must survive extension.
- **B — Medium adapter** (PR #88 cookies-only browser-bind path) and `recipes/medium.py`.
- **C — publish-history shape** (`webui_app/helpers.py:_push_history_per_row` ~line 444): the canonical writer per [[feedback-publish-history-invariant-helper]]. Row keys: `id`, `target_url`, `platform`, `language`, `status`, `created_at`, `article_urls`, `title`, `adapter`, optional `error`. **No retry / throttle / verify / latency fields exist.** `created_at` format is `'%Y-%m-%d %H:%M'` with no timezone — ambiguous across DST for `--since` windows.
- **C — dofollow map** (`webui_app/binding_status.py:38-48` — `_DOFOLLOW_BY_CHANNEL`). Read-only from C's perspective per [[feedback-grep-dofollow-map-before-shipping-adapter]].

### Institutional Learnings (load-bearing only)

- [[feedback-config-paths-must-respect-env-var]] — A.1 / C.1 tests must use `BACKLINK_PUBLISHER_CONFIG_DIR=/tmp/...`; never smoke against real config dir.
- [[feedback-webui-store-config-dir-frozen]] — C must call `_refresh_paths()` if it imports webui_store APIs.
- [[feedback-grep-dofollow-map-before-shipping-adapter]] — C's scorecard surfaces `_DOFOLLOW_BY_CHANNEL` per platform prominently.

Softer references (pattern citations, conditional follow-ups) are in **Sources & References** rather than treated as load-bearing constraints.

## Key Technical Decisions

- **D1. Portfolio framing, not bundled PR.** Each phase ships independently. Rationale: A is concrete & low-risk, B is investigative (no code shipped), C is measurement-only. Bundling would force the slowest phase to gate the others. The framing's value is reviewer overview, nothing else; splitting into 3 sibling plans is a viable alternative (see Open Questions).
- **D2. Extend the PR #99 pattern; channel-config dataclasses already exist.** All three channels are depth-1-only in the current loader. A.2 adds three strings to `_SAVE_CONFIG_KNOWN_ROOTS` with empty `known_subsections` entries; any operator-added depth-2 (`[ghpages.routing]` etc.) flows through the verbatim-preserve branch. Write-side kwargs (`ghpages_config=`, …) are added only if same-PR tests exercise round-trip; otherwise deferred to the WebUI write-back follow-up.
- **D3. Audit-before-extend for A.** A.1 ships a per-section canary that matches the writer.py:280-307 5-branch taxonomy *before* A.2 extends managed roots. A.1's PR must land (main CI green) before A.2 is opened; A.2's test suite re-runs A.1's canary unchanged.
- **D4. Spike output is a markdown verdict, not code.** B's deliverable is a GO/NO-GO/GO-WITH-CAVEATS doc with indirect framing for sensitive details. Working-notes scratchpad is git-rm'd at end of spike (regardless of verdict) to limit abuse-vector exposure.
- **D5. C is read-only and offline-replayable, over fields that actually exist today.** No re-publish, no network, no schema mutation. Throttle / retry / latency are out of scope; if scorecard findings argue for them, that's a follow-on plan.
- **D6. C produces a brainstorm seed — either as a section of C.2's report or as a separate doc.** Pre-committing the doc as its own implementation unit was redundant.
- **D7. WebUI write-back for unmanaged sections is deferred until a falsifiable condition holds.** Specifically: until either (1) an explicit anchor-pool validator exists at `config/anchor_pool_validator.py`, OR (2) the URL planner is stable enough that operator-editing `[sites.*]` from UI is safe without runtime re-validation. Today neither holds. This is a deferral *until X*, not a permanent carve-out; revisit if either condition changes.
- **D8. Phase A widens the credential surface captured by `.config-history/` snapshot rotation.** After A.2, every save_config rewrite snapshots ghpages/writeas/hashnode tokens. Operators rotating a channel PAT must also purge `.config-history/`; A.1/A.2 tests assert snapshot file mode is `0o600` and parent dir is `0o700`.

## Open Questions

### Resolved During Planning

- **Q: Origin doc?** None. The earlier draft cited `docs/brainstorms/2026-05-20-comprehensive-optimization-proposal.md`; that file does not exist in the worktree, in git history, or in any sibling worktree. This plan stands on its own.
- **Q: Plan 005 (Medium Phase 1)?** Not locatable from any combination of `git log --all -- 'docs/plans/**medium*'`, cross-worktree `find`, or PR #88's file list. Treat this plan as the spike's authoritative origin. If Plan 005 surfaces later, reconcile high-level intent only.
- **Q: Should the four metrics in the earlier draft (throttle / retry / 429 / anchor-keyword) be in C.1?** No. None of those fields exist in `publish-history.json` today. R-C1 is reduced to fields that empirically exist; richer instrumentation is a follow-on schema-extension plan, not C.
- **Q: Should C use a CLI entrypoint or a webui_app/services reporter?** CLI entrypoint `report-quality` mirroring `report-anchors` / `footprint`.
- **Q: Subsection grammar for the three new managed roots?** All depth-1 only per current loader (`config/loader.py:170-200`). Empty `known_subsections` is correct; any operator-added depth-2 flows through verbatim-preserve.

### Resolved by Review Pass (2026-05-20)

- **Portfolio framing kept** (user decision after doc-review). Sibling-plan split is viable but not chosen.
- **PR-numbering convention:** `PR A-1` / `PR A-2` etc. (already applied in Phased Delivery).
- **Citation count:** Institutional Learnings trimmed from 6 to 3 load-bearing; rest moved to Sources & References.

### Deferred to Implementation

- Exact column / row schema of the C scorecard (R-C1 lists fields; precise JSON shape decided in C.1).
- Whether B finds a usable Medium GraphQL endpoint at all (the spike could legitimately conclude "no path").
- Whether A.2's monolith budget bump above 360 is needed (re-measure with `radon raw -s`).
- Whether `[llm.anchor_provider]` should eventually join managed roots — depends on LLM-settings WebUI work per [[project-webui-llm-integration]].

## High-Level Technical Design

> *Directional guidance for review, not implementation specification.*

### Phase A — managed-root extension flow

```
config.toml ──load_config──▶ Config (in-memory)
                                  │  WebUI/CLI mutation
                                  ▼
                          save_config(Config, *,
                              target_three_url=...,
                              # NEW (optional, deferred kwargs):
                              ghpages_config=..., writeas_config=...,
                              hashnode_config=...)
                                  │
                                  ▼
              build known_subsections:
                ("targets",<domain>) ("blogger","oauth") ("medium",…)  existing
                ghpages/writeas/hashnode → depth-1 only, no subs       NEW
                                  │
                                  ▼
              _preserve_unknown_sections(on_disk_text, emit_set,
                                         known_subsections=…)
                                  │
                                  ▼  atomic_write (0o600) + snapshot rotate (0o600 in 0o700 dir)
                            config.toml (rewritten)
```

Unmanaged roots (`[sites.*]`, `[anchor_alarm]`, `[anchor.proportions]`, `[llm.anchor_provider]`) flow through the verbatim-preserve branch per writer.py:280-307 branch (d); empty-placeholder blocks intentionally drop per branch (e). A.1's canary asserts both per-section.

### Phase B — Medium GraphQL reconnaissance

```
preflight     ──▶ locate Plan 005 via git (≤15 min embedded in B.1)
B.1 capture   ──▶ list of GraphQL operations Medium UI invokes for "publish"
                  + endpoint URL, header shape (cookie vs PAT), CSRF plumbing
                  + rate-limit characterisation (≤6 unlisted posts, 30s→5s spacing)
                  + credential-rotation behaviour (logout/login probe)
B.2 verdict   ──▶ docs/spikes/…md with decision matrix and verdict
                                  │
                                  ▼ end-of-spike: delete throwaway posts,
                                                  git-rm scratch notes
```

### Phase C — quality scorecard pipeline

```
webui_store/publish-history.json ┐
webui_store/publish-queue.json   ├──▶ report-quality CLI (--redact, --since)
webui_store/draft-queue.json     │     ├─ aggregate per-platform metrics
.config-history/ snapshots       │     │   (over fields that exist:
config.toml                      │     │    attempts, success rate from status,
                                 ┘     │    unverified-suffix rate, last-publish,
                                       │    dofollow-rel from _DOFOLLOW_BY_CHANNEL,
                                       │    log-freshness from oldest row)
                                       └─▶ JSON to stdout (per CLAUDE.md contract)
                                                  │
                                                  ▼
                                  docs/operations/2026-05-XX-dofollow-scorecard.md
                                  (rendered narrative; final section = brainstorm seed)
```

## Implementation Units

### Phase A — save_config round-trip extension

- [x] **A.1: Canary test for the 5-branch save_config taxonomy + doc reconciliation** — PR #114 opened 2026-05-20 (commit `cf02025`); 13 tests, 2905 full-suite green; ce:review autofix applied 5 cleanups; docstring drift on branch (e) surfaced and fixed in same PR.

**Goal:** Lock in the post-PR-#99 5-branch taxonomy (writer.py:280-307) in test form across `[targets.*]`, `[sites.*]`, `[anchor_alarm]`, `[anchor.proportions]`, `[llm.anchor_provider]`. Reconcile CLAUDE.md caveat and `types.py` docstrings to that taxonomy.

**Requirements:** R-A1, R-A2, R-A4.

**Dependencies:** None.

**Files:**
- Create: `tests/test_save_config_section_taxonomy_canary.py`
- Modify: `backlink-publisher/CLAUDE.md` (replace caveat with the 5-branch summary + link to writer.py docstring)
- Modify: `src/backlink_publisher/config/types.py` (docstring corrections for any drift surfaced by the canary)

**Approach:**
- Build a fixture `config.toml` with realistic-shape blocks for each of the 5 sections plus active operator edits in `[blogger]` / `[medium]` / `[targets.X]`. Use anonymised content (no operator domains).
- For each section, write **per-branch** assertions matching writer.py:280-307: branch (d) sections preserved verbatim when carrying key=value data; branch (e) sections dropped when pure-placeholder.
- After every `save_config()` call, assert `(config_path.stat().st_mode & 0o777) == 0o600` and parent-dir `0o700`.
- Reconcile CLAUDE.md and types.py docstrings to the canary's actual passing assertions.

**Patterns to follow:**
- `tests/test_*config*` round-trip tests added in PR #99 for `[targets.X]`.
- `tests/conftest.py` config-dir env-isolation fixture (mandatory; see [[feedback-config-paths-must-respect-env-var]]).

**Test scenarios:**
- *Happy path:* config.toml with all five sections carrying key=value data → load→save → branch (d) sections preserved verbatim, file mode 0o600, parent dir 0o700.
- *Edge case:* `[sites."https://x".anchor_pools.home]` depth-3 heading — assert per writer.py taxonomy.
- *Edge case:* `[anchor.proportions]` with no body (pure placeholder) — branch (e), dropped on save.
- *Edge case:* `[anchor_alarm]` with custom keys not in the dataclass schema — branch (d), preserved verbatim.
- *Edge case:* mixed file with five unmanaged sections + active edits in managed `[blogger]` / `[medium]` / `[targets.X]` — managed rewrites do not disturb unmanaged.
- *Edge case:* file mode is 0o600 after save even when caller umask is 0o022.
- *Error path:* malformed `[sites.*]` (broken TOML) does not silently drop on save.

**Verification:** Canary green; CLAUDE.md caveat replaced with taxonomy reference + writer.py link; types.py docstrings updated; file-mode assertions pass.

---

- [x] **A.2: Extend `_SAVE_CONFIG_KNOWN_ROOTS` to include `ghpages` / `writeas` / `hashnode`** — PR #116 opened 2026-05-20 (commit `57ce984`). **Not mechanical** — empirical probe revealed that adding to frozenset alone would silently drop the channels' depth-1 blocks (writer.py:170-188 preservation logic drops known-root depth-1 headings, assuming the writer emits them). PR therefore ships emission code (3 conditional blocks under branch (b) in the docstring) alongside the frozenset update + 3 kwargs + 9 regression tests + monolith ceiling bump 360→385.

**Goal:** Register the three new channels as managed roots without modifying their existing dataclasses or sub-grammar.

**Requirements:** R-A1, R-A3, R-A4.

**Dependencies:** A.1 must be merged to main and CI green before A.2 opens.

**Files:**
- Modify: `src/backlink_publisher/config/writer.py` (add 3 strings to `_SAVE_CONFIG_KNOWN_ROOTS`; optionally add `ghpages_config=` / `writeas_config=` / `hashnode_config=` kwargs to `save_config`)
- Create: `tests/test_save_config_new_channel_roots.py`
- Possibly modify: `monolith_budget.toml` (re-measure first; bump only with rationale ≥ 80 chars)

**Approach:**
- Add `"ghpages"`, `"writeas"`, `"hashnode"` to `_SAVE_CONFIG_KNOWN_ROOTS` with **empty** `known_subsections` entries (all three are depth-1-only per current loader).
- Optionally extend `save_config` kwargs if same-PR tests exercise round-trip emission; otherwise the kwargs are deferred to the WebUI write-back follow-up.
- Re-run A.1's canary to verify unmanaged-section semantics are unchanged.
- Assert `.config-history/` snapshot file mode is `0o600`, parent dir `0o700` (R-A4 + D8).

**Execution note:** Test-first. Write the regression tests before extending the frozenset. Enumerate every save site: with-kwargs and without-kwargs-but-channel-block-already-on-disk.

**Patterns to follow:** PR #99 `fc4ca84`. Existing `blogger=` / `medium=` kwargs.

**Test scenarios:**
- *Happy path:* `save_config(cfg)` without channel kwargs preserves an existing `[ghpages]` block verbatim.
- *Happy path:* `save_config(cfg, ghpages_config=…)` writes `[ghpages]` and preserves user-edited `[ghpages.routing]` if operator added it (treated as unknown subsection → verbatim).
- *Edge case:* config.toml has `[hashnode]` but in-memory `Config` has no hashnode bound — block survives.
- *Edge case:* mixed write — `save_config(cfg, blogger=…, ghpages_config=…)` does not lose `[medium]` or `[targets.X]`.
- *Edge case:* reverted PR #108 channels (`[devto]`) are unmanaged → verbatim preserved, not deleted.
- *Integration:* A.1's canary still green.
- *Security:* snapshot file mode 0o600; parent dir 0o700; file mode survives mixed-umask test.

**Verification:** A.2 tests green; A.1 canary still green; SLOC re-measured.

---

### Phase B — Medium GraphQL spike (collapsed to 2 units)

- [ ] **B.1: Reconnaissance + rate-limit + rotation characterisation (single ≤1-day pass)** — **Scaffold landed via PR #119 squash `ba74bd2` 2026-05-20 06:22 UTC** (scrub gate + scratch template + B.2 deliverable skeleton + gitignore for real scratch path). Operator reconnaissance still pending: needs throwaway Medium account on separate VPN/IP, DevTools probe, ≤6-post rate test, ToS read. Preflight already recorded in template header: "Plan 005 = Medium Phase 1" memory pointer was mis-labelled (repo plan-005 is unrelated PR landing cleanup); this plan is the authoritative origin.

**Goal:** In one timeboxed pass, capture the GraphQL operations Medium's authoring UI sends; probe rate limits within a strict budget; observe credential-rotation behaviour. All findings go to a working-notes scratchpad that is **git-rm'd at end of spike** regardless of verdict.

**Requirements:** R-B1, R-B2, R-B3.

**Dependencies:** None. Preflight: `git log --all --oneline -- 'docs/plans/**medium*'` + `gh pr view 88 --json files` (≤ 15 min). If Plan 005 surfaces, link it; otherwise proceed with this plan as origin.

**Files:**
- Create (then delete at spike end): `docs/spikes/2026-05-XX-medium-graphql-spike-notes.md` — local scratch only; never push.

**Approach:**
- **Throwaway account on a separate IP.** Freshly-created Medium account; VPN / dedicated network not shared with any operator account. Do not log into the throwaway account from the operator IP for ≥ 48h after the spike.
- **All test posts: visibility=unlisted (no public URL), explicitly deleted via UI within the same spike session.**
- DevTools network capture → identify endpoint URL, operation names (`CreatePostV2`, `PublishPost`, etc.), header set (Authorization vs cookie, CSRF token plumbing), Set-Cookie behaviour.
- Rate-limit probe: at most 6 posts (3 @ 30s spacing, 3 @ 5s spacing). Stop at first throttle signal. Total GraphQL request budget: ≤ 10.
- Credential rotation: logout → login → observe whether old cookies invalidate.
- Cross-reference Medium's published Terms-of-Service for programmatic-publishing language. Cross-reference community reverse-engineering writeups **as reference only** — never paste their sample tokens, never execute their snippets, treat any cited endpoint as a hypothesis to verify in DevTools.

**Credential-scrub procedure (B.1a, mandatory before any commit of notes):**
- Maintain a scrub allowlist of Medium header/cookie names: `sid`, `uid`, `xsrf`, `lightstep-access-token`, `x-xsrf-token`, `sessionid`, `csrf`, `authorization`.
- Before committing anything, redact every value for those header names + every Set-Cookie line + every standalone string matching `[A-Za-z0-9_-]{20,}` in header position.
- A simple `scripts/scrub-spike-capture.py --check <notes-file>` script (drop into the spike PR if it doesn't already exist; ≤ 30 lines) gates the commit.

**Execution note:** **Reconnaissance, not implementation.** No production adapter code. No raw HAR captures. ≤ 1 working day end-to-end. If endpoint cannot be characterised in that window, B.2 records "NO-GO — endpoint not characterisable in 1 day" and the spike still produces value.

**Test expectation:** none — investigative work.

**Verification:**
- Scratch notes contain: endpoint URL (paraphrased), operation names, header set (names + shapes, no values), observed throttle threshold (or "none within probe budget"), credential-rotation finding, ToS finding.
- All throwaway posts deleted; account health verified (logout/login still works post-probe).
- `scrub-spike-capture.py --check` passes with zero findings.

---

- [ ] **B.2: GO/NO-GO report (the deliverable)**

**Goal:** One reviewable document recording the verdict and the minimum evidence to support it. Indirect framing for sensitive details.

**Requirements:** R-B1, R-B3.

**Dependencies:** B.1.

**Files:**
- Create: `docs/spikes/2026-05-XX-medium-graphql-spike.md` (the deliverable; checked in).

**Approach:**
- Decision matrix per dimension (endpoint feasible / auth feasible / ToS risk / rate-limit headroom / credential rotation / migration cost) → finding + verdict.
- One-paragraph recommendation: GO (proceed to Phase 2), NO-GO (stay on browser cookies), or GO-WITH-CAVEATS.
- If GO and Plan 005 was not recovered: GO means trigger a fresh `ce:brainstorm` round to scope Phase 2, not invoke pre-existing Unit 2/3/4 work.
- **Indirect framing:** refer to operations by published name only; do not paste full request URLs with hostnames; generalise rate observations to order-of-magnitude.
- `git rm docs/spikes/…spike-notes.md` (B.1's scratchpad) as part of B.2's commit, regardless of verdict.

**Test expectation:** none — written deliverable.

**Verification:** Doc exists; clear verdict; indirect framing audit (no full endpoint URLs, no exact throttle numbers, no token-shaped strings); scratchpad removed in same commit.

---

### Phase C — dofollow publish quality audit

- [ ] **C.1: `report-quality` CLI + aggregator over fields that actually exist**

**Goal:** New CLI in the existing `report-anchors` / `footprint` family that ingests `webui_store/*.json` and emits a per-platform JSON scorecard over fields empirically present in `publish-history.json`. Note: this is a CLI entrypoint, not a publishing adapter — the R9 extension-readiness contract does not apply.

**Requirements:** R-C1, R-C2, R-C4, R-C5.

**Dependencies:** None (read-only over existing artifacts).

**Files:**
- Create: `src/backlink_publisher/cli/report_quality.py`
- Modify: `pyproject.toml` (entry-point `report-quality`)
- Create: `tests/test_report_quality_aggregation.py`
- Create: `tests/test_report_quality_redaction.py`

**Approach:**
- Argparse, structured stderr, stdout JSON. Honour `BACKLINK_PUBLISHER_CONFIG_DIR`. Call `_refresh_paths()` for any webui_store access.
- For each platform in `_DOFOLLOW_BY_CHANNEL`, compute:
  - `attempts`: total rows in the `--since` window (default 30d; UTC interpretation explicit in stderr because `created_at` lacks a TZ).
  - `success_rate`: count where `status == "published"` AND `target_url` present, divided by attempts (preserves [[feedback-publish-history-invariant-helper]]).
  - `unverified_rate`: count where `status` ends with `_unverified`, divided by published count (per `_push_history_per_row` line 439).
  - `last_publish`: most recent `created_at` for that platform.
  - `dofollow`: bool from `_DOFOLLOW_BY_CHANNEL` map.
  - `log_freshness`: oldest `created_at` for that platform; flag `truncated: true` if the window is older than the oldest row.
- `--redact`: replace URL-shaped fields with `<host-1>`, `<host-2>`, etc. Stable mapping within a single CLI run.
- Schema-defensive: `.get()` with defaults; unknown row keys tolerated.

**Patterns to follow:** `cli/report_anchors.py`, `cli/footprint.py`.

**Test scenarios:**
- *Happy path:* fixture with rows across **6 platforms** → CLI emits a JSON object keyed by all 6 dofollow platforms (platforms with zero history still appear with `attempts: 0`).
- *Edge case:* empty `publish-history.json` → all platforms have `attempts: 0`, `log_freshness: null`, no crash.
- *Edge case:* `BACKLINK_PUBLISHER_CONFIG_DIR` honoured.
- *Edge case:* `status="published"` row missing `target_url` does NOT count as success.
- *Edge case:* `created_at` is older than `--since` window → row excluded; `log_freshness` reflects in-window oldest.
- *Edge case:* publish-history has been wiped within window (mtime newer than oldest row would suggest) → emit `log_freshness.truncated: true` so operators see the discontinuity.
- *Redaction coverage:* fixture URLs across `.com / .co / .blog / .dev / .me / .io / .xyz / .cn / .jp / .uk / IDN / subdomain-only / @user / user.medium.com` → all replaced with placeholders.
- *Error path:* malformed JSON → exit non-zero with stderr error naming the file; no partial emit.
- *Integration:* conftest socket-block fixture honoured; no network calls.

**Verification:** All tests green; CLI registered; SLOC measured.

---

- [ ] **C.2: Scorecard report + brainstorm-seed section**

**Goal:** Run `report-quality --redact` against the operator's `webui_store/` (or anonymised copy) and produce a dated scorecard markdown. The report ends with an "Open questions / brainstorm seeds" section, eliminating the need for a separate C.3 unit.

**Requirements:** R-C1, R-C3, R-C4, R-C5.

**Dependencies:** C.1.

**Files:**
- Create: `docs/operations/2026-05-XX-dofollow-scorecard.md` (placed under existing `docs/operations/` taxonomy rather than creating a new `docs/reports/` directory).

**Approach:**
- Per-platform table covering all 6 dofollow platforms (or explicit "no data — wiped 2026-05-XX" per [[project-pr87-verification-complete]] precedent).
- Narrative section per platform: notable findings, anomalies, follow-up candidates.
- **Final section: brainstorm seeds.** For each platform with a concern, write 1 paragraph problem-frame + open questions. Explicitly do not propose solutions — these are inputs to a future `/ce:brainstorm` round.
- Cross-reference any items that overlap with adjacent infrastructure work (throttle / retry / lease registry — if a follow-on schema-extension plan emerges, link it here).
- Operator-domain leak check: run `report-quality --redact --check docs/operations/…md` and verify zero non-placeholder hostnames before commit.

**Test expectation:** none — manual artifact production.

**Verification:** Doc exists; 6-platform table present (or zero-data noted with cause); brainstorm-seed section present; redaction check passes.

---

## System-Wide Impact

- **Interaction graph (A):** `save_config` callers in `webui_app/routes/settings_basic.py` and `cli/plan_backlinks/core.py`, `cli/publish_backlinks.py`. New kwargs are additive — old callers unaffected.
- **Interaction graph (B):** none in production code.
- **Interaction graph (C):** `report-quality` reads `webui_store/` and `.config-history/` but does not write. `_push_history_per_row` invariant unchanged.
- **Error propagation:** A.2 follows existing `save_config` error model. C.1 follows CLI exit-code contract (0 success, non-zero with stderr).
- **State lifecycle risks (A):** atomic-write + snapshot rotation are unchanged structurally, but **the credential surface inside snapshots widens** after A.2 (ghpages/writeas/hashnode tokens now snapshot every save). Mitigation: D8.
- **State lifecycle risks (C):** scorecard is offline-replayable; no state mutation.
- **API surface parity (A):** `save_config` gains optional kwargs. Old positional callers continue to work.
- **Integration coverage (A):** A.1 canary spans load→mutate→save→reload; A.2 reruns the canary unchanged plus adds new-channel scenarios.
- **Integration coverage (C):** test must exercise `_refresh_paths()` so env-var override isn't accidentally bypassed.
- **Unchanged invariants (A):** `_LegacyPathFinder` re-export map (`src/backlink_publisher/__init__.py:_REEXPORT_MAP`) **not** modified — A doesn't add new public top-level imports (existing channel-config dataclasses are already importable from `config.types` per their pre-existing definition).
- **Unchanged invariants (C):** `_DOFOLLOW_BY_CHANNEL` read-only from C's perspective. `publish-history.json` schema **not** modified — this is why R-C1 is restricted to fields that already exist.

## Risks & Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| A.2 monolith ceiling exceeded | Med | Low | Re-measure with `radon raw -s` before PR; bump only with rationale ≥ 80 chars. |
| A.1 audit reveals a section silently dropped that should not be (branch (d) failure where branch (e) was assumed) | Med | High | Promote that finding to a same-PR fix; do not let A.2 ship with the regression latent. |
| A.2 widens credential surface in `.config-history/` snapshots | Med | Med | D8: tests assert snapshot mode 0o600, parent dir 0o700. Document in Operational Notes that channel-token rotation requires `.config-history/` purge. |
| A.2 file-mode regression (atomic-write tempfile gets 0o644 default) | Med | High | A.1's canary asserts `stat().st_mode & 0o777 == 0o600` after every save. A.2 reruns canary. |
| B's spike triggers Medium ToS / account flag | Med | High | Throwaway account on separate IP/VPN; unlisted posts deleted in-session; ≤ 10 requests; do not log throwaway from operator IP for ≥ 48h. |
| B notes file pushed with embedded credentials | Med | Critical | Scrub allowlist + `scripts/scrub-spike-capture.py --check` gate before commit. B.2 `git-rm`s scratch notes regardless of verdict. |
| B GO/NO-GO report doubles as abuse-vector documentation | Low | Med | Indirect framing in B.2 (operation names only, no full URLs, order-of-magnitude rates); scratchpad removed. |
| B reaches end of timebox with no verdict | Med | Low | "NO-GO — uncharacterisable in 1 day" is a valid outcome. |
| C.1 misclassifies due to schema fields that exist but were not enumerated | Low | Low | Schema-defensive `.get()` with defaults; test fixture includes "unknown future fields" → tolerated. |
| C.2 leaks operator domains into checked-in markdown despite redaction | Low | High | `--redact` flag at CLI emit time (not post-hoc regex); fixture-tested across 15+ TLD/IDN shapes; redaction-check step in C.2 verification. |
| `publish-history.json` was wiped within scorecard window, masking real attempts | Med | Med | R-C4 emits `log_freshness.truncated` flag; operators see the discontinuity. |
| Future WebUI write-back PR misses the threat-model gates A.3 was supposed to scaffold | Med | High | Threat-model requirements recorded in this plan's Sources & References / D7 + linked from PR #112's follow-up; future PR must enumerate channel allowlist re-check, CSRF protection, off-loopback refusal, payload schema validation, audit log entry. |

## Documentation / Operational Notes

- **A:** CLAUDE.md caveat replaced with a pointer to writer.py:280-307's 5-branch taxonomy. AGENTS.md "section-preservation taxonomy" gets a one-line refresh if drift is found.
- **A:** **Operator runbook addition:** rotating a ghpages / writeas / hashnode token requires purging `.config-history/` snapshots. Document in AGENTS.md or a dedicated runbook line.
- **B:** Scratch notes file is git-rm'd by B.2 regardless of verdict. Indirect framing applied to the public GO/NO-GO report. `scripts/scrub-spike-capture.py` shipped if not already present.
- **B:** **Future WebUI write-back PR (the one that introduces `_persist_channel_config_for` at first use) MUST add:** (1) channel allowlist re-check via `binding_status.is_writable_channel`, (2) Flask CSRF protection (flask-wtf or equivalent), (3) refusal when `BACKLINK_PUBLISHER_ALLOW_NETWORK=1` + remote_addr is off-loopback unless an AUTH_TOKEN env-var is set, (4) payload schema validation before reaching `save_config`, (5) audit log entry on stderr per write. Reconcile with PR #112's existing allowlist (which rejects devto/mastodon/wpcom/hashnode for nofollow reasons) — hashnode becomes writable per A.2 so the allowlist gets updated; devto/mastodon/wpcom remain blocked.
- **C:** Scorecard lives under `docs/operations/` (existing taxonomy). Future runs accumulate with date prefixes. `--redact` is the default for any checked-in artifact.
- **Rollout:** None of A/B/C touches publishing or live verify — zero operational rollout risk.

## Phased Delivery

### Phase A — round-trip extension
- A.1 (PR A-1): canary test + CLAUDE.md / types.py reconciliation
- A.2 (PR A-2): managed-root extension + new regression tests + snapshot security assertions

A.1 lands first; A.2 opens only after A.1 main CI is green.

### Phase B — Medium spike
- B.1 + B.2 ship as a single PR (`docs/spikes/…md` only). Timebox: ≤ 1 working day end-to-end. Scratch notes deleted before merge.

### Phase C — quality audit
- C.1 (PR C-1): CLI + tests (aggregation + redaction)
- C.2 (PR C-2): scorecard markdown with brainstorm-seed section

Total horizon: 1–2 weeks at typical merge cadence.

## Sources & References

- **Anchor PR (A pattern):** PR #99 (`fc4ca84`) — managed-root subsection preservation; 5-branch taxonomy locked in writer.py:280-307.
- **Anchor PR (A motivation):** PR #112 (`278c956`) — token-paste UI cards; future routing-config write-back PR is gated on A.2.
- **Anchor PR (B context):** PR #88 (`fdeaebc`) — current Medium browser-cookie adapter.
- **Anchor PR (C context):** PR #102 (`905c035`), #103 (`f23e845`), #96 (`9a86650`) — the three newer dofollow adapters.
- **Memory (load-bearing):** [[feedback-publish-history-invariant-helper]], [[feedback-config-paths-must-respect-env-var]], [[feedback-webui-store-config-dir-frozen]], [[feedback-grep-dofollow-map-before-shipping-adapter]].
- **Memory (context):** [[project-medium-graphql-phase1-pr88]], [[project-webui-token-paste-cards]], [[project-channel-binding-dashboard-plan-006]], [[project-phase4-scaffold]], [[project-pr87-verification-complete]].
- **Memory (process, conditional):** [[reference-telegraph-adapter-credential-rotation-pattern]] (only if B GO and a follow-on credential store is needed), [[feedback-grep-all-worktrees-before-claiming-existence]], [[feedback-grep-alleged-drift-before-locking-framing]].
- **Repo docs:** `backlink-publisher/AGENTS.md`, `backlink-publisher/CLAUDE.md`.
- **Worktree state at plan-write time (2026-05-20):** main `6e95967` clean; sibling worktrees `bp-banner-image-gen` and `bp-url-derive-v1` carry orthogonal feature work.

## Deepening Notes

Original draft (pre-document-review) cited a non-existent brainstorm origin doc and a non-existent "parallel WIP" set of files (`persistence/`, `test_safe_write_substrate.py`, `test_retry_classification.py`, `test_token_revocation_midrun.py`). These were artefacts of an erroneous Phase 0 reading and have been removed. The 2026-05-20 review surface against an empirically verified clean main at `6e95967` produced 33 findings across 5 reviewers; the auto-fixes consolidated to this version. Three findings (portfolio framing, PR-numbering convention, citation count) remain in Open Questions / Present for user judgment.
