---
title: "feat: LiveJournal canary closeout + Bloglovin retirement"
type: feat
status: completed
shipped: db82b045 (#303)
date: 2026-05-29
origin: docs/brainstorms/2026-05-28-livejournal-prod-enablement-bloglovin-retire-requirements.md
claims: {}
---

# feat: LiveJournal canary closeout + Bloglovin retirement

## Overview

LiveJournal adapter code is fully shipped (`livejournal_api.py`, WebUI binding card,
manifest, registry entry). The missing step is operator execution: bind a throwaway
account, publish a fresh canary, read the link-attribute verdict, and open a
register-flip PR that resolves `dofollow="uncertain"` to its permanent value.

Bloglovin requires no code — Phase 0 proved it is a dead platform. The only work
is a retirement document so the question "why is Bloglovin missing?" has a durable
answer.

## Problem Frame

Operator perceived both platforms as "not built." Reality: LiveJournal is code-complete
and needs operator-execution only; Bloglovin is dead (NO-GO, 2026-05-25) and needs
formal archival. The plan closes both gaps — one through a canary-driven PR, one
through documentation.

(see origin: docs/brainstorms/2026-05-28-livejournal-prod-enablement-bloglovin-retire-requirements.md)

## Requirements Trace

- R1. Operator binds throwaway LJ account via WebUI → `livejournal-credentials.json` (0o600, `{username, hpassword}`)
- R2. Fresh canary publish — `plan-backlinks → validate-backlinks → publish-backlinks --publish` (NOT `--resume`)
- R3. Verdict: inspect only the `<a>` pointing at target URL on the live post; read `rel`
- R4. Register-flip PR: `dofollow="uncertain"` → `True` or `False`; add regression pin test; for `dofollow=True`: delete orphaned `_R["livejournal"]` key and remove `rationale=`/`referral_value=` kwargs
- R5. Same PR: delete stale comment at `_manifests.py:306-307`
- R6. Create `docs/notes/retired-platforms/bloglovin.md` (evidence + decision date)
- R7. Create `docs/notes/retired-platforms/README.md` (index for future retired platforms)

## Scope Boundaries

- No cross-platform quota allocation — `bound_platforms` dropdown handles rotation implicitly
- No second Phase 0 probe of Bloglovin — 4-source evidence is authoritative
- No new adapter registration for Bloglovin
- No `_REJECTED_PLATFORMS` change — Bloglovin was never `register()`-ed
- LiveJournal canary: exactly 1 seed (runbook constraint)
- Canary seed tuple `(target_url, main_domain, url_mode, target_language)` is a **hard blocking prerequisite** for Unit 2 — operator must provide synchronously before Unit 2 starts; do not attempt canary with a synthetic seed

## Context & Research

### Relevant Code and Patterns

- `src/backlink_publisher/publishing/adapters/__init__.py` — `register()` call site for livejournal
- `src/backlink_publisher/publishing/adapters/_nofollow_rationales.py` line 161 — `"livejournal"` entry (current "uncertain" rationale). Delete if flipping to `dofollow=True`.
- `src/backlink_publisher/publishing/adapters/livejournal_api.py` — `store_credentials()` uses `safe_write.atomic_write(..., mode=0o600)` ✅ PR #140 contract met
- `src/backlink_publisher/publishing/_manifests.py` lines 306-307 — stale comment to delete
- `webui_app/routes/channel_bind_save.py` — `_USERPASS_MODULES["livejournal"]` wired to `livejournal_api.store_credentials`
- `webui_app/templates/_settings_binding_userpass.html` — livejournal branch ships with username/password fields
- `src/backlink_publisher/publishing/registry.py` — `dofollow_status()`, `register()` gate (dofollow=True removes rationale/referral_value requirement)
- `tests/test_adapter_dofollow_gate.py` — parametrized test covering all `registered_platforms()`, auto-passes after flip; add named pin test here
- `tests/test_registry_dofollow_kwargs.py` — register() signature red-path coverage (reference for test shape)
- `docs/runbooks/2026-05-25-dofollow-canary-closeout.md` — authoritative operator procedure
- `docs/spike-notes/2026-05-25-dofollow-tiering-phase0-probes/findings.md` — Bloglovin NO-GO evidence source

### Institutional Learnings

- **Grep `_DOFOLLOW_BY_CHANNEL` first** (`docs/solutions/workflow-issues/grep-dofollow-map-before-shipping-adapter-2026-05-20.md`): livejournal not present in `binding_status.py` `_DOFOLLOW_BY_CHANNEL` (registry-driven, no action needed)
- **`_nofollow_rationales.py` orphan key must be deleted (Branch A only)** (`docs/plans/2026-05-25-003-...`): after flip to True, `_R["livejournal"]` becomes a dead entry; delete it in the same PR. Branch B keeps the entry and updates the rationale text instead.
- **`nofollow_detected` is page-wide noise** (`docs/solutions/integration-issues/dofollow-canary-verdict-dropped-at-publish-output-seam-2026-05-25.md`): verdict comes only from inspecting the specific `<a rel="...">` on the target backlink; do not use the `nofollow_detected` field
- **`safe_write.atomic_write` is the canonical credential write path** (PR #140 contract)

### External References

Not needed — codebase has established patterns for all three work areas.

## Key Technical Decisions

- **Conditional plan branches (dofollow=True vs False)**: Plan documents both paths in Unit 3 because the canary result is unknown at planning time. Implementer executes the correct branch based on the live verdict. (see origin: R3/R4)
- **Units 3+4 in the same PR**: Separating them creates a window where verdict is known but registry still shows "uncertain." Bundling closes the loop atomically. (see origin: Key Decisions)
- **Regression test in `tests/test_adapter_dofollow_gate.py`**: Parametrized test already covers registry-level status checks; adding a named pin test `test_livejournal_dofollow_flipped()` makes the canary-closeout intent explicit and guards against silent regress.
- **`docs/notes/retired-platforms/` as new directory**: Semantically distinct from `docs/solutions/` (lessons from problems we solved) and `docs/spike-notes/` (live research logs). "Retired platforms" is a reference index of platforms deliberately excluded, not a post-mortem.
- **Canary seed deferred to ce:work**: Operator must supply `(target_url, main_domain, url_mode, target_language)` at execution time; plan must not invent a seed.

## Open Questions

### Resolved During Planning

- **Does `livejournal_api.py` use `safe_write.atomic_write`?** Yes — `store_credentials()` calls `safe_write.atomic_write(path, json_str, mode=0o600)` with a post-write `chmod` guard. R1 technical precondition is met.
- **Does `_settings_binding_userpass.html` have the livejournal branch?** Yes — `{% if channel == "livejournal" %}` block with username/password fields and bind/update/clear buttons. WebUI binding is fully shipped.
- **Is livejournal in `_DOFOLLOW_BY_CHANNEL` in `binding_status.py`?** No — it is registry-driven. No change needed there.
- **Which test file for regression?** `tests/test_adapter_dofollow_gate.py` — parametrized coverage already exists; add a named pin test in the same file.
- **Does `docs/notes/` exist?** No — create the directory as part of Unit 5.
- **PR naming**: `feat(livejournal): canary closeout + bloglovin retirement` — confirmed by operator.
- **`monolith_budget.toml` ceiling**: flip to dofollow=True is a net SLOC reduction (`adapters/__init__.py` loses rationale/referral_value args). No ceiling risk; verify with `radon raw` before opening PR.

### Deferred to Implementation

- **Canary seed tuple `(target_url, main_domain, url_mode, target_language)`**: hard blocking prerequisite for Unit 2. If operator is unavailable when ce:work reaches Unit 2, halt — do not proceed with a synthetic seed.
- **LiveJournal default theme preserves `<a rel="noopener noreferrer">`**: Phase 0 spot-check found no nofollow; canary will confirm. If link is stripped entirely, treat as "platform not viable" and return to step 1 (do not flip registry).

## Implementation Units

```
Unit 4 (stale comment) ── independent, ship first ───────┐
Unit 5 (Bloglovin docs) ── independent ──────────────────┤
                                                          │
Unit 1 (bind) → Unit 2 (canary + verdict) → Unit 3 (register-flip PR)
```

---

- [ ] **Unit 1: Operator bind LiveJournal account via WebUI**

**Goal:** Establish confirmed bound credentials at `livejournal-credentials.json` (0o600) before any canary attempt.

**Requirements:** R1

**Dependencies:** None. Throwaway account must exist (operator confirmed).

**Files:**
- Read (verify): `~/.config/backlink-publisher/livejournal-credentials.json` (must exist post-bind, 0o600, schema `{username, hpassword}`)
- Reference: `webui_app/templates/_settings_binding_userpass.html`, `webui_app/routes/channel_bind_save.py`

**Approach:**
- Navigate to WebUI settings page → LiveJournal section → enter throwaway account username + password → submit
- `channel_bind_save.py` dispatches to `livejournal_api.store_credentials()`, which MD5-hashes the password and writes `livejournal-credentials.json` via `safe_write.atomic_write`
- Binding UI uses POST to `/settings/save-channel-credential` with CSRF token (global guard applies)
- After bind: confirm WebUI shows "绑定成功" (or equivalent), then verify the JSON file exists at `0o600`

**Test scenarios:**
- Test expectation: none — this is an operator execution step, not a code change. Verification is via filesystem check and WebUI feedback.

**Verification:**
- `livejournal-credentials.json` exists, mode `0o600`, schema contains `username` and `hpassword` keys (no plaintext password)
- LiveJournal appears in the platform dropdown on the main publish panel (bound_platforms driven)

---

- [ ] **Unit 2: Fresh canary publish + verdict capture**

**Goal:** Produce a live LiveJournal post with the target backlink and capture the link-attribute verdict for the register-flip decision.

**Requirements:** R2, R3

**Dependencies:** Unit 1 complete (credentials bound)

**Files:**
- Reference: `docs/runbooks/2026-05-25-dofollow-canary-closeout.md` (steps 2-3)
- Capture output: `publish-backlinks` stdout JSONL row containing `link_attr_verification`

**Approach:**
- Operator supplies seed tuple at ce:work start: `(target_url, main_domain, url_mode, target_language)` — do NOT use `--resume`
- Pipeline: `plan-backlinks → validate-backlinks → publish-backlinks --publish`
- Capture the full stdout JSONL row; find the `link_attr_verification` field
- **Key inspection rule (from institutional learnings):** Do NOT use `nofollow_detected` — it is a page-wide scan (nav/footer/related links). Open the published canary URL and locate the `<a>` that points at `target_url`; read its `rel` attribute directly
- Record one of three outcomes:
  - **dofollow**: no nofollow token in `rel` → proceed to Branch A in Unit 3
  - **nofollow**: `rel="nofollow"` present → proceed to Branch B in Unit 3
  - **link absent**: LiveJournal stripped the `<a>` entirely → abort flip; open investigation issue; Unit 5 may still ship independently; do not open Unit 3+4 PR
- **Publish failure triage** (before any post is created):
  - `DependencyError` on credential load → return to Unit 1 and re-bind with correct password
  - `ExternalServiceError` from `getchallenge` (stale challenge, network fault) → retry once after 60s; if it persists treat as "platform not viable" and abort
  - `ExternalServiceError` from `postevent` after successful challenge → same as above

**Test scenarios:**
- Test expectation: none — operator execution step. Verification is via live browser inspection of the published post.

**Verification:**
- Live LiveJournal post URL is accessible in a browser
- Specific `<a href="[target_url]">` is found on the post
- Verdict (dofollow / nofollow / link-absent) is recorded and ready for Unit 3

---

- [ ] **Unit 3: Register-flip + regression test (same PR as Unit 4)**

**Goal:** Permanently resolve `dofollow="uncertain"` for LiveJournal based on the canary verdict; add a regression test that pins the final status.

**Requirements:** R4

**Dependencies:** Unit 2 verdict in hand

**Files:**
- Modify: `src/backlink_publisher/publishing/adapters/__init__.py`
- Modify (conditional): `src/backlink_publisher/publishing/adapters/_nofollow_rationales.py`
- Test: `tests/test_adapter_dofollow_gate.py`

**Approach:**

*Branch A — canary confirms dofollow (no nofollow token on target `<a>`):*
1. `adapters/__init__.py`: change `register("livejournal", ..., dofollow="uncertain", rationale=_R["livejournal"], referral_value="high", ...)` → `register("livejournal", ..., dofollow=True, ...)` — remove `rationale=` and `referral_value=` args entirely
2. `_nofollow_rationales.py`: delete the `"livejournal"` entry (now an orphan — dofollow=True gate does not require rationale)
3. `tests/test_adapter_dofollow_gate.py`: add `test_livejournal_dofollow_flipped()` that asserts `dofollow_status("livejournal") is True` and `"livejournal" not in NOFOLLOW_RATIONALES`. Import as `from backlink_publisher.publishing.adapters._nofollow_rationales import NOFOLLOW_RATIONALES` — this name is NOT exported from `registry.py`.

*Branch B — canary confirms nofollow (`rel` contains `nofollow`):*
1. `adapters/__init__.py`: change to `register("livejournal", ..., dofollow=False, rationale=_R["livejournal"], referral_value="high", ...)` — keep rationale/referral_value (gate requires them for False)
2. `_nofollow_rationales.py`: update `"livejournal"` rationale text to record the canary date, the verdict, and the decision to keep as referral channel (≥80 chars required by gate)
3. `tests/test_adapter_dofollow_gate.py`: add `test_livejournal_dofollow_flipped()` that asserts `dofollow_status("livejournal") is False` and `dofollow_rationale("livejournal")` is not empty

*Both branches require:*
- Verify `monolith_budget.toml` ceiling not breached: run `python -m radon raw -s src/backlink_publisher/publishing/adapters/__init__.py` and confirm SLOC ≤ ceiling

**Patterns to follow:**
- Existing `dofollow=True` example: `register("blogger", BloggerAPIAdapter, dofollow=True, **BLOGGER_MANIFEST)` in `adapters/__init__.py`
- Existing `dofollow=False` example: `register("juejin", ..., dofollow=False, rationale=_R["juejin"], referral_value="high", ...)` in `adapters/__init__.py`
- Existing pin test shape: `test_adapter_dofollow_gate.py` parametrized class `TestEveryPlatformHasValidDofollow`; add a standalone named function alongside it

**Test scenarios:**
- Happy path (Branch A): `dofollow_status("livejournal") is True` — no longer "uncertain"
- Happy path (Branch B): `dofollow_status("livejournal") is False` — no longer "uncertain"
- Regression guard: `dofollow_status("livejournal") != "uncertain"` — explicit pin so CI catches any silent regress
- Branch A only: `"livejournal" not in NOFOLLOW_RATIONALES` — orphan key was deleted
- Branch B only: `dofollow_rationale("livejournal")` has ≥80 chars — gate requirement
- Error path: `register()` call with `dofollow="uncertain"` raises `RegistryError` (existing test; verify it still catches if someone reverts)
- monolith budget: SLOC for `adapters/__init__.py` ≤ ceiling in `monolith_budget.toml`
- No-logging guard: any new test must not assert on or log `challenge`/`hpassword`/`auth_response` values; use boolean/presence assertions only (e.g. `assert creds["hpassword"]` — not `assert creds["hpassword"] == <value>`). The structured-log scrubber only redacts extra-dict keys, not f-string message content.

**Verification:**
- `pytest tests/test_adapter_dofollow_gate.py tests/test_registry_dofollow_kwargs.py` passes
- `pytest tests/test_no_monolith_regrowth.py -k "R4"` passes
- `dofollow_status("livejournal")` is `True` or `False` (not `"uncertain"`)

---

- [ ] **Unit 4: Delete stale comment in `_manifests.py` (independent PR — ship first)**

**Goal:** Remove the outdated comment that says "No settings card today; binding lives in CLI" — binding now lives in WebUI. This is correct regardless of canary outcome and should not wait for Unit 3.

**Requirements:** R5

**Dependencies:** None — fully independent; ship before or in parallel with Units 1-3

**Files:**
- Modify: `src/backlink_publisher/publishing/_manifests.py` lines 305-307

**Approach:**
- Lines 305-307 form one sentence split across three lines — the stale text begins mid-line-306 after `{username, hpassword}`.`. Do **not** treat this as a simple 2-line deletion; that leaves line 305 as a dangling fragment. Instead **replace lines 305-307 as a unit**: keep the accurate content (`# shape is \`{username, hpassword}\`.`) and drop ` No settings card today; binding / # lives in CLI.` entirely.
- Lines 300-304 (the `# ── livejournal ──` section header and XML-RPC / hpassword warning) are accurate and must be preserved.
- No behavior change; purely documentary cleanup.

**Test scenarios:**
- Test expectation: none — pure comment deletion, no behavioral change. CI `py_compile` check on the file is sufficient.

**Verification:**
- File compiles: `python -m py_compile src/backlink_publisher/publishing/_manifests.py`
- Lines 306-307 no longer contain the stale comment

---

- [ ] **Unit 5: Bloglovin retirement documentation**

**Goal:** Create a durable, dated answer to "why is Bloglovin missing?" under `docs/notes/retired-platforms/`.

**Requirements:** R6, R7

**Dependencies:** None — fully independent of Units 1-4

**Files:**
- Create (new dir): `docs/notes/retired-platforms/`
- Create: `docs/notes/retired-platforms/bloglovin.md`
- Create: `docs/notes/retired-platforms/README.md`

**Approach:**

`bloglovin.md` must contain:
1. Decision summary: NO-GO, not registered, not planned for registration
2. Evidence (from `docs/spike-notes/2026-05-25-dofollow-tiering-phase0-probes/findings.md`):
   - Rebranded to Activate in 2018
   - Last meaningful activity 2021-12; blog-post service abandoned
   - Homepage returns Cloudflare 403 to crawlers/bots
   - No blog-post API or submission interface remains
3. Decision date: 2026-05-25 (Phase 0 probe date)
4. Link back to `findings.md` (primary evidence source) and `docs/runbooks/2026-05-25-dofollow-canary-closeout.md` (decision table line)

`README.md` must contain:
1. Purpose: "Platforms evaluated and deliberately excluded — check here before opening a feature request for a new platform"
2. Table of excluded platforms (initially: Bloglovin only), columns: Platform | Decision | Date | Evidence
3. Instruction for adding entries (link to findings.md, provide date and 4-source evidence)

**Patterns to follow:**
- Bloglovin evidence: `docs/spike-notes/2026-05-25-dofollow-tiering-phase0-probes/findings.md` (Bloglovin row)
- Canary runbook decision table: `docs/runbooks/2026-05-25-dofollow-canary-closeout.md` line 72 (NO-GO row)
- Documentation tone: operator-facing, concise, no placeholder text

**Test scenarios:**
- Test expectation: none — documentation only, no Python behavior change.

**Verification:**
- `docs/notes/retired-platforms/bloglovin.md` exists with decision date, 4-piece evidence, and link to findings.md
- `docs/notes/retired-platforms/README.md` exists with exclusion table listing Bloglovin
- `plan-check` does not flag these files (they go under `docs/notes/`, not `docs/plans/`, so they won't be scanned for plan frontmatter)

---

## System-Wide Impact

- **Interaction graph:** `register()` call change propagates to `registry.registered_platforms()`, `dofollow_status()`, and `dofollow_rationale()`. All downstream consumers (WebUI platform dropdown, `dofollow_tier_metadata()`, canary-targets CLI) read from registry dynamically — no cascading edits needed.
- **Error propagation:** Registry gate (`_validate_dofollow_args`) fires at import time if rationale/referral_value args are inconsistent with the chosen dofollow value. CI `py_compile` + `ast.parse` will surface import errors; full `pytest` catches gate failures.
- **State lifecycle risks:** `livejournal-credentials.json` is written by `safe_write.atomic_write` with mode `0o600`. If bind fails mid-write, atomic_write ensures no partial file. Post-bind: file exists or doesn't; no partial state.
- **API surface parity:** `dofollow_status("livejournal")` is the primary queried surface. `referral_value("livejournal")` is also read by `cli/_report_format.py` and `cli/plan_backlinks/_payload.py::dofollow_tier_metadata()`. For Branch A (dofollow=True), equity-ledger's `_classify()` short-circuits at `status is True` and never reaches `referral_value()`; `_report_format.py` treats `None` referral_value as a no-op display value. All consumers handle Branch A gracefully.
- **`cull-channels` consumer — Branch B critical:** `cli/cull_channels.py` marks a platform as a cull candidate when `dofollow_status(p) is False AND referral_value(p) == "low"`. Branch B must keep `referral_value="high"` (matching the current registry entry) to prevent livejournal from appearing as a cull candidate immediately after ship.
- **`canary-targets` cohort — Branch A permanent coverage gap:** After flipping to `dofollow=True`, livejournal enters the `canary-targets` CLI cohort. Without a `[canary.livejournal]` config entry, `canary-targets` emits `STATUS_NOT_CONFIGURED` and appends livejournal to a `canary_coverage_gap` RECON alert on every run — exit 0 but permanently loud, not suppressible. This is not an optional advisory; it is a first-class coverage gap. Branch A implicitly creates a required follow-on: seed `[canary.livejournal]` in `config.toml` (or open a tracking issue for it before merging the flip PR).
- **Integration coverage:** Parametrized test in `test_adapter_dofollow_gate.py` covers `dofollow_status` for all `registered_platforms()` — this test will automatically apply to livejournal after the flip.
- **Unchanged invariants:** `_AUTH_TYPE_BY_PLATFORM["livejournal"] = "userpass"` in `registry.py` is unaffected. `LIVEJOURNAL_MANIFEST` in `_manifests.py` is unaffected except for the comment deletion. The adapter class `LivejournalAPIAdapter` is unaffected.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Canary link stripped entirely (LiveJournal theme renders body without `<a>`) | Runbook step 3 covers this: abort flip, treat as "not viable", return to step 1. Do not registry-flip. |
| `_nofollow_rationales.py` orphan key missed (Branch A) | Unit 3 approach explicitly includes deletion as a required step; test scenario verifies `"livejournal" not in NOFOLLOW_RATIONALES` |
| `rationale` too short for Branch B (gate requires ≥80 chars) | `test_registry_dofollow_kwargs.py` catches this at test time; update rationale to include date + evidence |
| Monolith budget ceiling breach | Branch A reduces SLOC (net deletion); Branch B is neutral. Verify with `radon raw` before PR. |
| Throwaway account hpassword = password-equivalent | Documented in `livejournal_api.py` docstring; operator confirmed throwaway account. Never log hpassword/challenge/auth_response. |
| Branch B sets `referral_value="low"` → livejournal becomes cull candidate | Keep `referral_value="high"` in Branch B register() call (matching current entry). The rationale already says "high-DA blogging platform." |
| Branch A creates permanent `canary-targets` coverage gap | Seed `[canary.livejournal]` in `config.toml` or open a tracking issue before merging Branch A. `STATUS_NOT_CONFIGURED` is a first-class coverage gap (always-on RECON alert, not suppressible). Branch A cull-channels risk: none — cull predicate requires `dofollow_status is False`; Branch A is outside that gate. |
| `docs/notes/` path conflicts with `plan-check` scanner | `plan-check` scans `docs/plans/**/*.md` only. `docs/notes/` is not scanned. No conflict. |

## Documentation / Operational Notes

- PR description must record the canary verdict (dofollow/nofollow) and the date of the live check. Do **not** include the published canary URL — the LiveJournal post embeds the operator's target domain, and this repo is public (PR history is permanent). The verdict + date is sufficient for auditability.
- `docs/notes/retired-platforms/bloglovin.md` becomes the canonical answer for operator questions about Bloglovin; link from any future "why no Bloglovin?" support context.
- After PR merge: LiveJournal with `dofollow=True` enters the platform dropdown automatically via `bound_platforms`. No additional wiring needed for production rotation.
- Operator confirmation before ce:work: verify throwaway account is not the operator's primary identity (hpassword is un-revocable without password change).

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-28-livejournal-prod-enablement-bloglovin-retire-requirements.md](../brainstorms/2026-05-28-livejournal-prod-enablement-bloglovin-retire-requirements.md)
- **Runbook:** [docs/runbooks/2026-05-25-dofollow-canary-closeout.md](../runbooks/2026-05-25-dofollow-canary-closeout.md)
- **Phase 0 evidence:** [docs/spike-notes/2026-05-25-dofollow-tiering-phase0-probes/findings.md](../spike-notes/2026-05-25-dofollow-tiering-phase0-probes/findings.md)
- **Dofollow canary solution:** [docs/solutions/integration-issues/dofollow-canary-verdict-dropped-at-publish-output-seam-2026-05-25.md](../solutions/integration-issues/dofollow-canary-verdict-dropped-at-publish-output-seam-2026-05-25.md)
- **Dofollow map solution:** [docs/solutions/workflow-issues/grep-dofollow-map-before-shipping-adapter-2026-05-20.md](../solutions/workflow-issues/grep-dofollow-map-before-shipping-adapter-2026-05-20.md)
- Related plan: [docs/plans/2026-05-25-003-feat-dofollow-canary-closeout-plan.md](2026-05-25-003-feat-dofollow-canary-closeout-plan.md)
- Code: `src/backlink_publisher/publishing/adapters/__init__.py`, `_nofollow_rationales.py`, `_manifests.py`
- Test: `tests/test_adapter_dofollow_gate.py`, `tests/test_registry_dofollow_kwargs.py`
