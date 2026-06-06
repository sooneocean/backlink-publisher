---
date: 2026-06-06
topic: dofollow-channel-expansion
type: requirements
status: draft
---

# Dofollow Channel Expansion — Canary Blitz + Discovery Pipeline

## Summary

Increase the pipeline's confirmed-dofollow channel count from 5 to ≥15 through a two-track campaign: (1) a **Canary Blitz** that runs OUR-pipeline canary publishes on all 15 "uncertain" dofollow platforms to definitively confirm their dofollow status, and (2) a **Discovery Pipeline** that systematically sources and triages new platform candidates beyond the current 28 registered. This is the single highest-leverage move for the operator's #1 bottleneck: not enough dofollow channels.

## Problem Frame

The pipeline has 28 registered publisher platforms, but only **5 are confirmed dofollow** (blogger, medium, telegraph, velog, ghpages). Another **15 are registered as "uncertain"** — they have working adapters, operator accounts exist, 3rd-party evidence suggests most are actually dofollow, but the dofollow status was never verified through the pipeline's own canary mechanism (`canary-targets` CLI + `verify_link_attributes`). The "uncertain" status blocks them from being used by `plan-gap` (deficit-driven replan, which only targets `dofollow=True` platforms) and means the operator cannot confidently include them in dofollow-focused campaigns.

Meanwhile, the platform landscape is vast — dozens of free publishing platforms exist that may produce dofollow+indexed backlinks. The current evaluation process is ad-hoc ("let's try X"), with no systematic sourcing layer that feeds candidates into the existing `channel-probe` skill for structured triage. The channel-discovery-funnel brainstorm (2026-06-01) identified this gap but has not been executed.

## Actors

- **Operator**: Runs canary publishes, reviews results, flips dofollow status flags. Also operates the discovery pipeline: sources candidates, runs `channel-probe`, decides GO/NO-GO.
- **Canary Target (`canary-targets` CLI)**: Existing infrastructure that verifies dofollow attribute on published posts. Needs per-platform config entries.
- **Consumer Systems (`plan-gap`, `channel-scorecard`)**: Downstream consumers that read `dofollow_status()` from the registry. Derive value from more platforms being `dofollow=True`.

## Key Flows

### Track A: Canary Blitz

1. Operator selects a target "uncertain" platform (prioritized by dofollow likelihood × referral value)
2. Configures a canary entry for the platform in `canary-targets` config (post_url / expected_target / marker)
3. Publishes one post via the existing pipeline (or directly via adapter)
4. `canary-targets` re-fetches the post, runs `verify_link_attributes`, checks rel attribute
5. If dofollow confirmed: operator flips `dofollow="uncertain" → True` in `adapters/__init__.py` and removes the `rationale=` argument
6. If nofollow confirmed: operator leaves as-is (or flips to `False` with rationale)
7. Result recorded in a tracking artifact under `docs/discovery/`

### Track B: Discovery Pipeline

1. Operator runs discovery queries against platform directories (AlternativeTo, Wikipedia lists, niche publishing directories)
2. Candidates are cross-referenced against `registered_platforms()`, retired platforms (`docs/notes/retired-platforms/`), and rejected platforms (`_REJECTED_PLATFORMS`)
3. Surviving candidates ranked by: likely dofollow × indexation potential × adapter complexity × referral value
4. Ranked list feeds the `channel-probe` skill for HTTP-UA matrix + `site:` index check + real-browser `<a> rel` inspection
5. `channel-probe` emits a GO / NO-GO / NEEDS-CANARY verdict for each
6. GO candidates enter the queue for adapter development and eventual canary verification
7. NO-GO candidates recorded in the retired-platforms ledger with reopen conditions

## Requirements

### Track A — Canary Blitz (confirm existing uncertain platforms)

- **R1.** All 15 "uncertain" platforms must receive an OUR-pipeline dofollow canary within the campaign. Platforms with strong 3rd-party dofollow evidence are prioritized: **pubmark** (browser-probe confirmed dofollow), **brewpage** (live probe confirmed no nofollow), **hatena** (11/12 3rd-party dofollow), **hackmd** (188/0 3rd-party dofollow), **mataroa** (6/0 dofollow), **substack** (3rd-party dofollow), **writeas** (3rd-party dofollow), **rentry** (3rd-party dofollow, `rel=noreferrer`), **wordpresscom** (evidence conflict, needs resolution), **txtfyi** (Phase 0 preliminary = dofollow), **gitlabpages** (operator-controlled rel), **hashnode** (3rd-party dofollow, retiring), **posteasy** (client-side render), **htmldrop** (needs confirmation), **nonograph** (needs confirmation).

- **R2.** Each canary follows the existing `canary-targets` protocol: publish one post → re-fetch → `verify_link_attributes` → verdict.

- **R3.** Each confirmed-dofollow platform gets its `dofollow` flag flipped to `True` in `src/backlink_publisher/publishing/adapters/__init__.py` and the `rationale=` argument removed.

- **R4.** Each canary result (regardless of outcome) is recorded in a tracking artifact under `docs/discovery/` with: platform name, date, publish URL, rel attribute observed, verdict, and any notes.

- **R5.** The campaign targets a minimum outcome of 8 additional confirmed-dofollow platforms (from 5 → ≥13 total), with a stretch goal of 12+.

### Track B — Discovery Pipeline (source new candidates)

- **R6.** A repeatable discovery process must be documented and executed, sourcing candidates from: publishing-platform directories (AlternativeTo, G2, Wikipedia "List of blog publishing platforms"), platform-family enumeration (WriteFreely instances, Plume instances, Ghost blogs), and niche/topical publishing communities (dev platforms, writing communities, knowledge bases).

- **R7.** Each candidate is cross-referenced against: `registered_platforms()`, `docs/notes/retired-platforms/`, `_REJECTED_PLATFORMS` in `registry.py`, and the running canary-tracking artifact. Candidates that fail all dedup checks are discarded.

- **R8.** Surviving candidates are scored on: dofollow likelihood (preliminary HTTP/curl check), indexation potential (`site:` search), adapter complexity estimate (API vs form-post vs browser), referral value (DA estimate), and bad-neighborhood risk.

- **R9.** The top-ranked candidates (target: ≥10 per discovery batch) are fed through the `channel-probe` skill for full triage (HTTP reachability matrix + `site:` index check + real-browser `<a> rel` inspection).

- **R10.** `channel-probe` results are recorded in the NO-GO ledger (`docs/notes/retired-platforms/`) or the canary-queue tracking artifact.

## Acceptance Examples

- **R1 (canary blitz):** "After running canary-targets on platform X, the output confirms `rel=dofollow` on the published backlink. The registry entry is flipped to `dofollow=True` in `adapters/__init__.py`."
- **R6 (discovery):** "A new discovery batch produces 15 candidates. After dedup against registered/retired/rejected, 8 survive. After scoring and `channel-probe` triage, 3 are GO, 2 NEEDS-CANARY, 3 NO-GO."

## Success Criteria

- Confirmed dofollow platforms: 5 → **≥13** (stretch: ≥17)
- "Uncertain" platforms remaining: 15 → **≤7** (i.e., at least 8 resolved)
- Discovery batch produced ≥10 unique candidates evaluated through `channel-probe`
- All 15 canary results documented in a tracking artifact
- 0 regressions in existing test suite

## Scope Boundaries

### In Scope
- Running canary publishes on all 15 existing "uncertain" platforms
- Flipping dofollow flags in the registry upon confirmation
- Documenting canary results in tracking artifacts
- Documenting and executing a repeatable platform discovery process
- Running `channel-probe` on discovered candidates
- Recording NO-GO results in the retired-platforms ledger

### Deferred for Later
- Adapter Generalization (Approach 3 — parameterizing adapters for WriteFreely/Plume/Telegraph-clone families)
- Converting confirmed-nofollow platforms (linkedin, tumblr, devto, notion, mastodon, qiita, zenn) — separate strategic conversation
- Publishing throughput optimization / throttle tuning
- Building new adapters for discovery candidates — this campaign produces the queue; adapter development is a downstream milestone
- Survival-rate improvements or recheck enhancements — lifecycle monitoring is already in-flight
- WebUI or CLI changes for the pipeline itself

### Outside This Campaign's Identity
- No changes to the pipeline's CLI verbs or publish logic — this is about canary operations and platform research
- No changes to `plan-gap` algorithm or `equity-ledger` schema — they already consume `dofollow_status()` from the registry and benefit transparently from more `dofollow=True` entries

## Key Decisions

- **Priority order for canary blitz**: confirmed by 3rd-party evidence first (pubmark, brewpage, hatena, hackmd, mataroa, substack, writeas, rentry), then conflicted/uncertain (wordpresscom, txtfyi, gitlabpages, hashnode), then lowest-confidence (posteasy, htmldrop, nonograph)
- **Discovery scope**: platform directories + platform-family enumeration + niche communities. Not web scraping at scale (spam risk, low signal/noise).
- **No automatic adapter building**: The discovery pipeline feeds `channel-probe` and produces a queue. Building adapters is a separate planning milestone.

## Dependencies / Assumptions

- Operator has or can create accounts on all 15 "uncertain" platforms (most already have accounts per existing pipeline usage)
- `canary-targets` CLI and `verify_link_attributes` infrastructure are functional (verified: they exist in the codebase)
- `channel-probe` skill is available and working (verified: `.claude/skills/channel-probe/SKILL.md`)
- 3rd-party dofollow evidence for pubmark, brewpage, hatena, hackmd, mataroa, substack, writeas, rentry is reliable and will hold under OUR-pipeline canary

## Outstanding Questions

- What is the operator's account status on each of the 15 uncertain platforms? (Need to verify before canary planning)
- Which platforms require binding/browser-login vs. just API credentials? (affects canary effort per platform)
- What's the desired cadence for discovery batches — one-time research or recurring monthly?
- Should platforms currently flagged as "retiring" (hashnode, writeas) still receive a canary, or be excluded?

---

## Annex: Platform Dofollow Status Reference

| Platform | Current Status | 3rd-Party Evidence | Canary Priority |
|----------|---------------|-------------------|-----------------|
| blogger | ✅ True | — | — |
| medium | ✅ True | — | — |
| telegraph | ✅ True | — | — |
| velog | ✅ True | — | — |
| ghpages | ✅ True | — | — |
| **pubmark** | ❓ uncertain | Browser-probe confirmed dofollow | P1 |
| **brewpage** | ❓ uncertain | Live probe confirmed no nofollow | P1 |
| **hatena** | ❓ uncertain | 11/12 dofollow | P1 |
| **hackmd** | ❓ uncertain | 188/0 dofollow | P1 |
| **mataroa** | ❓ uncertain | 6/0 dofollow | P1 |
| **substack** | ❓ uncertain | 3rd-party dofollow | P1 |
| **writeas** | ❓ uncertain | 3rd-party dofollow (retiring) | P1 |
| **rentry** | ❓ uncertain | 3rd-party dofollow (rel=noreferrer) | P1 |
| **wordpresscom** | ❓ uncertain | Evidence conflict (#108→#109 revert vs 2026-05 recheck) | P2 |
| **txtfyi** | ❓ uncertain | Phase 0 preliminary = dofollow | P2 |
| **gitlabpages** | ❓ uncertain | Operator-controlled rel | P2 |
| **hashnode** | ❓ uncertain | 3rd-party dofollow (retiring) | P2 |
| **posteasy** | ❓ uncertain | Client-side render pending | P3 |
| **htmldrop** | ❓ uncertain | Needs confirmation | P3 |
| **nonograph** | ❓ uncertain | Needs confirmation | P3 |
| linkedin | ❌ False | — | — |
| tumblr | ❌ False | — | — |
| livejournal | ❌ False | — | — |
| devto | ❌ False | — | — |
| notion | ❌ False | — | — |
| mastodon | ❌ False | — | — |
| qiita | ❌ False | — | — |
| zenn | ❌ False | — | — |
