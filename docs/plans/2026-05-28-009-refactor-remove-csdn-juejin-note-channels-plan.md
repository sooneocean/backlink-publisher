---
title: "refactor: Remove csdn, juejin, note channels"
type: refactor
status: active
date: 2026-05-28
origin: docs/brainstorms/2026-05-28-remove-csdn-juejin-note-channels-requirements.md
claims: {}
---

# refactor: Remove csdn, juejin, note channels

## Overview

Hard-delete the csdn, juejin, and note publisher adapters from the codebase. The operator has no accounts on these platforms and never intends to use them. All three are nofollow (link-interstitial) platforms offering zero backlink equity. Full deletion is preferred over soft-retirement — no traces in WebUI, no dead code in tests, no stale credential files on disk.

## Problem Frame

Three paste_blob channels remain registered despite having no practical use. They add visual noise in the WebUI settings page, false positives in test parametrize sets, and maintenance surface with zero return. The operator's explicit goal is "never see them again."

(see origin: docs/brainstorms/2026-05-28-remove-csdn-juejin-note-channels-requirements.md)

## Requirements Trace

- R1–R5. Delete adapter files, remove `register()` calls, remove manifests, remove nofollow rationales, add to `_REJECTED_PLATFORMS`.
- R6–R7. Remove from `_AUTH_TYPE_BY_PLATFORM`; remove from `_ADAPTER_STRING_TO_PLATFORM`.
- R8–R9. Remove from `_PASTE_BLOB_CHANNELS`; update template comment.
- R10. Add to `_REMOVED_CREDENTIAL_SLUGS`; bump purge sentinel v1 → v2.
- R11–R16. Sweep test files; fix positive-control in purge test; fix protected-set count; replace juejin URL in anchor-unwrap test.
- R17. Remove csdn/juejin/note from `registry.py` docstrings.
- R18–R19. Verify `_SAVE_CONFIG_KNOWN_ROOTS` and `config.example.toml` are clean.

## Scope Boundaries

- The `paste_blob` bind pattern stays — `substack` still uses `_PASTE_BLOB_CHANNELS`.
- No credential files are deleted from disk by code — the one-shot startup purge handles that on the next WebUI launch.
- No changes to the remaining channels' behavior.
- No changes to `_bind/recipes/` — no recipes exist for these three channels.
- No changes to `config/tokens.py` — these channels have no token/OAuth config dataclass.

## Context & Research

### Relevant Code and Patterns

- **Canonical template:** `docs/plans/2026-05-27-001-refactor-remove-jianshu-channel-plan.md` — identical registry pattern. Follow its unit sequencing and atomicity requirements exactly.
- **`_REJECTED_PLATFORMS`** (`registry.py:~145`): `dict[str, str]`, key = platform slug, value = ≥80-char rationale. Currently 1 entry (jianshu). Adding 3 more → `len == 4`.
- **`_REMOVED_CREDENTIAL_SLUGS`** (`webui_store/channel_status.py:~231`): `tuple[str, ...]`. Currently contains jianshu, zhihu, cnblogs, habr, pikabu, segmentfault. Sentinel: `".removed-channel-purge-v1.done"` — **must bump to v2** when extending the tuple (code comment on line ~227 mandates this).
- **`_PASTE_BLOB_CHANNELS`** (`webui_app/routes/channel_bind_save.py`): not registry-driven, will NOT auto-shrink — must be edited explicitly.
- **`_ADAPTER_STRING_TO_PLATFORM`** (`idempotency/backfill.py:~67`): csdn(67), juejin(71), note(77). Removing these entries causes unknown adapter strings to fall to `quarantine` — correct behavior for retired platforms.
- **Monolith budget** (`monolith_budget.toml`): `adapters/__init__.py` ceiling is 640, current SLOC ~601. After removing ~3 register blocks (~30–40 SLOC), measure with radon and tighten the ceiling in the same commit.
- **`_SAVE_CONFIG_KNOWN_ROOTS`** (`config/writer.py`): canonical value is `{"blogger", "medium", "targets"}` — csdn/juejin/note were never in it. Verify at implementation time.

### Institutional Learnings

- **Atomicity is mandatory for Unit 1** (jianshu plan): adding a `_REJECTED_PLATFORMS` entry makes `register("<slug>")` raise `RegistryError` at import time — the rejected-add, register-delete, adapter-file deletion, and hardcoded test rows must all land in one commit. Any partial state leaves the package un-importable.
- **`_AUTH_TYPE_BY_PLATFORM` is the security chokepoint** (jianshu plan): removing a slug from this map makes the bind-save route fail-closed for that slug. Remove it in Unit 1 alongside the registry changes.
- **Sentinel bump rule** (`channel_status.py` comment): extending `_REMOVED_CREDENTIAL_SLUGS` after v1 sentinels are already in the field requires bumping `_PURGE_SENTINEL_NAME` to v2 so the sweep re-runs on operator machines.
- **Do NOT delete `<slug>-credentials.json` from disk in code** (jianshu plan): the one-shot startup purge handles this. Manual file deletion is an operator step.
- **Grep all 7 import forms** before declaring a module unreferenced (feedback): absolute, relative, multi-line, bare-import, `mock.patch` string targets. Full pytest is the only reliable tripwire.
- **`test_inspect_target_anchor.py`** uses `https://link.juejin.cn/?target=...` as a generic interstitial-unwrap test input — this tests algorithm behavior, not the juejin adapter. Replace URL with a neutral domain; do not delete the test.

### External References

None needed — jianshu removal plan is a direct template.

## Key Technical Decisions

- **Hard-delete, not `HIDDEN_FROM_UI` or `visibility="retired"`**: `HIDDEN_FROM_UI` preserves adapter code; `retired` still round-trips TOML config sections. The operator wants no trace. Full deletion aligns with jianshu precedent.
- **Add to `_REJECTED_PLATFORMS`**: parks the nofollow rationale as negative knowledge, arms the re-add tripwire. Jianshu pattern. Rationale strings should reference the link interstitial mechanism (already documented in `_nofollow_rationales.py`) and the removal date.
- **Sentinel v1 → v2**: v1 sentinels already exist on operator machines from the jianshu/beehiiv sweep. Without bumping to v2, csdn/juejin/note credential files would never be purged.
- **Monolith ceiling must tighten in Unit 1**: `tests/test_no_monolith_regrowth.py` enforces ceilings hard — if SLOC drops below the ceiling after adapter removal, the ceiling becomes misleading. Measure and tighten per the `round_up_to_10(measured_sloc + 30)` policy used in prior bumps.

## Open Questions

### Resolved During Planning

- **Does `config/tokens.py` reference csdn/juejin/note?** No — these are paste_blob channels without OAuth config dataclasses. No `all_token_revs` changes needed. Verify by grepping `tokens.py` at implementation start.
- **Do `_bind/recipes/` files exist for these channels?** No — `_bind/recipes/` contains only `blogger.py`, `medium.py`, `velog.py`. No recipe deletion needed.
- **Is `_SAVE_CONFIG_KNOWN_ROOTS` affected?** No — canonical value is `{"blogger", "medium", "targets"}`. Verify at implementation start.

### Deferred to Implementation

- **Exact SLOC measurement for `adapters/__init__.py`** after removal — measure with `python -m radon raw -s src/backlink_publisher/publishing/adapters/__init__.py` to determine new ceiling value.
- **Residual "note" grep hits** — `grep -rn '"note"' src/ tests/` may surface generic uses; implementer must classify each hit as platform-name or unrelated field.

## Implementation Units

- [ ] **Unit 1: Core adapter registry hard-delete**

**Goal:** Remove all three adapter classes from the import/registry layer in one atomic commit. After this unit, `import backlink_publisher.publishing.adapters` succeeds and csdn/juejin/note are in `_REJECTED_PLATFORMS`.

**Requirements:** R1–R5, R6, R7

**Dependencies:** None — first unit.

**Files:**
- Delete: `src/backlink_publisher/publishing/adapters/csdn_api.py`
- Delete: `src/backlink_publisher/publishing/adapters/juejin_api.py`
- Delete: `src/backlink_publisher/publishing/adapters/note_api.py`
- Modify: `src/backlink_publisher/publishing/adapters/__init__.py`
- Modify: `src/backlink_publisher/publishing/registry.py`
- Modify: `src/backlink_publisher/publishing/_manifests.py`
- Modify: `src/backlink_publisher/publishing/adapters/_nofollow_rationales.py`
- Modify: `src/backlink_publisher/idempotency/backfill.py`
- Modify: `monolith_budget.toml`
- Test: `tests/test_auth_type_classification.py`
- Test: `tests/test_offline_bound_registry_dispatch.py`
- Test: `tests/test_phase1_cookie_adapters.py`
- Test: `tests/test_registry_rejected_platforms.py`
- Test: `tests/test_idempotency_backfill.py`
- Test: `tests/test_platform_lookup.py`
- Test: `tests/test_overview_auth_type_rendering.py`

**Approach:**
- In `adapters/__init__.py`: remove the three `from .<slug>_api import <Slug>APIAdapter` import lines, the three `from ._manifests import <SLUG>_MANIFEST` import lines, and the three `register("<slug>", ...)` call blocks.
- In `registry.py`: remove `"csdn"`, `"juejin"`, `"note"` from `_AUTH_TYPE_BY_PLATFORM`; add three entries to `_REJECTED_PLATFORMS` (≥80-char rationale each, referencing the link-interstitial mechanism and removal date 2026-05-28). **Also update docstring/comment references at lines ~160 and ~424 in the same commit** — the `~424` docstring listing `paste_blob` targets (`"csdn", "juejin", "note", "substack"`) will be stale the instant `_AUTH_TYPE_BY_PLATFORM` is edited; deferring it to Unit 4 leaves the docstring wrong for the entire PR lifetime.
- In `_manifests.py`: delete `CSDN_MANIFEST`, `JUEJIN_MANIFEST`, `NOTE_MANIFEST` dict definitions.
- In `_nofollow_rationales.py`: delete the `"csdn"`, `"juejin"`, `"note"` keys from `NOFOLLOW_RATIONALES`.
- In `backfill.py`: delete the `"csdn": "csdn"`, `"juejin": "juejin"`, `"note": "note"` entries from `_ADAPTER_STRING_TO_PLATFORM`.
- In `monolith_budget.toml`: measure `adapters/__init__.py` SLOC with radon after edits; set `ceiling = round_up_to_10(measured + 30)` and update rationale (≥80 chars).

**Patterns to follow:**
- `_REJECTED_PLATFORMS["jianshu"]` entry in `registry.py` — same rationale structure.
- Prior `monolith_budget.toml` ceiling bumps: `ceiling = round_up_to_10(measured + 30)`.

**Test scenarios:**
- Happy path: `pytest tests/test_auth_type_classification.py` passes — no csdn/juejin/note rows in the parametrize set.
- Happy path: `pytest tests/test_phase1_cookie_adapters.py` passes — no CSDNAPIAdapter/JuejinAPIAdapter/NoteAPIAdapter import rows.
- Happy path: `pytest tests/test_registry_rejected_platforms.py` passes — `len(_REJECTED_PLATFORMS) == 4` (jianshu + csdn + juejin + note).
- Happy path: `pytest tests/test_idempotency_backfill.py` passes — no csdn/juejin/note platform mappings in `_ADAPTER_STRING_TO_PLATFORM`.
- Happy path: `pytest tests/test_platform_lookup.py` passes — the `"paste_blob"` bucket at line 28 is updated from `{"csdn", "juejin", "note", "substack"}` to `{"substack"}` so the exact-match assertion passes.
- Happy path: `pytest tests/test_overview_auth_type_rendering.py` passes — no `get_channel_status("csdn", cfg)["auth_type"]` assertion.
- Happy path (offline dispatch): `test_credential_channel_unbound_without_credentials` parametrize list (line ~87) updated from `["csdn", "note", "tumblr", "wordpresscom", "substack"]` to `["tumblr", "wordpresscom", "substack"]`. `test_cookie_channel_bound_once_cookies_present` (lines ~95–101) which uses `"csdn"` as the concrete cookie-export platform — replace csdn with another cookie-export channel (e.g., tumblr) to preserve behavioral coverage; do not delete the test.
- Happy path (backfill): `test_confirmed_missing_live_url_seeds_uncertain` (line ~65–71) uses `"note"` as the adapter string and asserts `DedupKey(platform="note", ...)`. After removing note from `_ADAPTER_STRING_TO_PLATFORM`, this event will quarantine instead of seed. Replace `"note"` with a still-live adapter string (e.g., `"substack"` or `"tumblr"`) throughout this test function.
- Happy path (auth type): `test_overview_auth_type_rendering.py` line ~36 `get_channel_status("csdn", cfg)["auth_type"] == "paste_blob"` → remove this assertion (csdn is no longer registered).
- Integration: `python -c "import backlink_publisher.publishing.adapters"` exits 0 — package is importable after deletions.
- Error path: `register("csdn", ...)` raises `RegistryError` at import time — the rejection gate fires.
- Edge case: `python -m radon raw -s src/backlink_publisher/publishing/adapters/__init__.py` SLOC ≤ new ceiling — monolith budget gate passes.

**Verification:**
- `pytest tests/test_registry_rejected_platforms.py tests/test_phase1_cookie_adapters.py tests/test_auth_type_classification.py tests/test_idempotency_backfill.py` all pass.
- `grep -rn "CSDNAPIAdapter\|JuejinAPIAdapter\|NoteAPIAdapter" src/ tests/` returns zero hits.
- `python -m py_compile src/backlink_publisher/publishing/adapters/__init__.py` exits 0.

---

- [ ] **Unit 2: WebUI binding cleanup**

**Goal:** Remove the three channels from the WebUI paste-blob credential-binding route and update the template comment.

**Requirements:** R8, R9

**Dependencies:** Unit 1 (adapter classes no longer importable — binding route must not reference them by name either).

**Files:**
- Modify: `webui_app/routes/channel_bind_save.py`
- Modify: `webui_app/templates/_settings_binding_paste_blob.html`
- Test: `tests/test_channel_bind_save.py`
- Test: `tests/test_overview_auth_type_rendering.py` (line ~51 parametrize — settings template no longer renders csdn card)

**Approach:**
- In `channel_bind_save.py`: remove the `"csdn"`, `"juejin"`, `"note"` entries from `_PASTE_BLOB_CHANNELS` dict. Remaining entries: `"substack"` (and any others). This map is not registry-driven — it will not auto-shrink.
- In `_settings_binding_paste_blob.html`: update the documentation comment listing paste_blob channel names to drop the three removed slugs.

**Patterns to follow:**
- The existing `_PASTE_BLOB_CHANNELS` structure with `"substack"` as the surviving example.

**Test scenarios:**
- Happy path: `pytest tests/test_channel_bind_save.py` passes — the seven paste_blob behavioral test functions (lines ~300–389: `test_paste_blob_invalid_json_rejected`, `test_paste_blob_missing_cookies_key_rejected`, `test_paste_blob_wrong_domain_rejected`, `test_paste_blob_missing_name_field_rejected`, `test_paste_blob_round_trip`, `test_paste_blob_size_limit_rejected`, `test_paste_blob_leave_as_is_empty`) must be migrated from `"csdn"` / `"csdn.net"` to `"substack"` / `".substack.com"` (the surviving paste_blob channel) to preserve behavioral coverage. Do not delete these tests — deleting them removes all paste_blob path coverage.
- Happy path: `test_cardless_channel_inline_form_rendered` parametrize row (line ~84) that uses `"csdn"` — remove this row; csdn no longer has a settings form.
- Happy path: `test_overview_auth_type_rendering.py` line ~51 parametrize `test_cardless_channel_has_configure_anchor_and_form` — remove `"csdn"` from the parametrize list; csdn no longer renders a settings card.
- Edge case: POST to `/api/bind/csdn` (or equivalent route) returns error — slug is no longer in `_PASTE_BLOB_CHANNELS`.
- Integration: `test_dispatch_maps_have_no_stale_rows` drift guard (`test_channel_bind_save.py` line ~478) will catch any leftover `_PASTE_BLOB_CHANNELS` entry not in `registered_platforms()` — passes once all three are removed.

**Verification:**
- `grep -rn '"csdn"\|"juejin"\|"note"' webui_app/` returns zero non-comment hits.
- `pytest tests/test_channel_bind_save.py tests/test_platform_lookup.py tests/test_overview_auth_type_rendering.py` all pass.

---

- [ ] **Unit 3: Credential purge extension**

**Goal:** Extend the one-shot startup purge to sweep `csdn-credentials.json`, `juejin-credentials.json`, `note-credentials.json` on the next WebUI launch. Bump the sentinel version so the sweep fires on machines that already ran v1.

**Requirements:** R10

**Dependencies:** Unit 1 (channels must be removed from registry before scheduling their credential cleanup).

**Files:**
- Modify: `webui_store/channel_status.py`
- Test: `tests/test_purge_removed_credentials.py`

**Approach:**
- In `channel_status.py`: add `"csdn"`, `"juejin"`, `"note"` to `_REMOVED_CREDENTIAL_SLUGS` tuple.
- Bump `_PURGE_SENTINEL_NAME` from `".removed-channel-purge-v1.done"` to `".removed-channel-purge-v2.done"` — this causes the one-shot to re-fire on operator machines that already ran v1, picking up the three new slugs.
- No changes to `purge_removed_channel_credentials()` itself — the loop already iterates over the tuple.

**Patterns to follow:**
- Existing `_REMOVED_CREDENTIAL_SLUGS` pattern with jianshu/zhihu/etc.
- Code comment on line ~227 explicitly specifies the v-bump rule.

**Test scenarios:**
- Happy path: `test_deletes_all_orphaned_files_and_writes_sentinel` — the loop now covers csdn/juejin/note credential files, deletes them, and writes `.removed-channel-purge-v2.done`.
- Happy path: `test_sentinel_present_is_noop` — if `.removed-channel-purge-v2.done` already exists, the purge function returns immediately.
- Edge case (regression): replace `_cred("csdn")` in `test_unrelated_channel_file_untouched` with `_cred("medium")` or `_cred("blogger")` — csdn is now a removed slug, so it can no longer serve as the "live channel must not be swept" positive control.
- Edge case: v1 sentinel exists but v2 does not → purge runs again and sweeps the new three slugs.

**Verification:**
- `pytest tests/test_purge_removed_credentials.py` passes with no skips.
- The function's tuple length grows from 6 to 9.

---

- [ ] **Unit 4: Test sweep and final hygiene**

**Goal:** Clean up remaining test references, verify config hygiene, and confirm the success-criteria grep is clean. (Note: `registry.py` docstring cleanup — R17 — was moved to Unit 1 since `registry.py` is already edited there.)

**Requirements:** R11–R16, R18–R19 (remaining items; R17 handled in Unit 1)

**Dependencies:** Units 1–3 must be complete (grep gate runs against final state).

**Files:**
- Modify: `tests/test_protected_set_coverage.py`
- Modify: `tests/test_inspect_target_anchor.py`
- Verify: `src/backlink_publisher/config/tokens.py` (expect zero hits for csdn/juejin/note)
- Verify/update: `config.example.toml`
- Verify: `src/backlink_publisher/config/writer.py` (`_SAVE_CONFIG_KNOWN_ROOTS`)

**Approach:**
- **`test_protected_set_coverage.py`**: Change `assert len(filenames) >= 13` to `assert len(filenames) >= 10` (remove 3 credential-file namers). Update the inline comment listing the credential files to drop csdn/juejin/note entries.
- **`test_inspect_target_anchor.py`**: The test at line ~88 uses `https://link.juejin.cn/?target=...` as input to `_unwrap_interstitial`. Replace the juejin URL with any neutral domain (e.g. `https://link.example.com/?target=...`). Do not delete the test — it covers a generic algorithm path.
- **Config verification**: grep `config/tokens.py` for csdn/juejin/note (expect zero hits). Grep `config/writer.py::_SAVE_CONFIG_KNOWN_ROOTS` for the three slugs (expect zero hits). Check `config.example.toml` for `[csdn]`/`[juejin]`/`[note]` stanzas — remove any found.
- **Residual grep sweep**: run `grep -rn '"csdn"\|"juejin"' src/ webui_app/ tests/` and resolve any remaining hits. For `"note"`, run `grep -rn 'platform.*"note"\|register.*"note"' src/ tests/` and resolve. Unrelated uses of "note" as a generic word are fine.

**Patterns to follow:**
- `test_scanner_finds_at_least_n_namers` assertion pattern from `test_protected_set_coverage.py`.
- Other channel URL replacements in `test_inspect_target_anchor.py` for precedent.

**Test scenarios:**
- Happy path: `pytest tests/test_protected_set_coverage.py` passes with `>= 10` assertion.
- Happy path: `pytest tests/test_inspect_target_anchor.py` passes — the interstitial-unwrap test still runs, now with a neutral URL.
- Edge case: `grep -rn '"csdn"\|"juejin"' src/ webui_app/ tests/` returns only `_REJECTED_PLATFORMS` entries — zero adapter/route/test hits.
- Integration: `pytest tests/` full suite passes — no residual import failures or fixture mismatches.

**Verification:**
- `grep -rn '"csdn"\|"juejin"' src/ webui_app/ tests/` returns only the 3 `_REJECTED_PLATFORMS` entries and zero others.
- `grep -rn 'platform.*"note"' src/ tests/` returns zero hits.
- `pytest tests/` full suite passes.

## System-Wide Impact

- **Interaction graph:** `registered_platforms()` is the single fan-out point (argparse choices, schema validation, content negotiation, dofollow gate, manifest contract test, WebUI publish select) — all shrink automatically once `register()` is removed. No manual edits to CLI or schema files needed.
- **Error propagation:** Any publish attempt targeting csdn/juejin/note after this PR raises `RegistryError` (from `_REJECTED_PLATFORMS`) at import time rather than a runtime dispatch miss. The bind-save route returns an error for unknown slugs (fail-closed via `_AUTH_TYPE_BY_PLATFORM` removal).
- **State lifecycle risks:** Operator machines with `csdn-credentials.json` etc. on disk are the only residual state. The v2 sentinel bump ensures the one-shot purge cleans these on next WebUI start.
- **Unchanged invariants:** The `paste_blob` bind mechanism itself (`_PASTE_BLOB_CHANNELS`, `_settings_binding_paste_blob.html`) is preserved for `substack` and any other remaining paste_blob channels.
- **Integration coverage:** `test_r9_extension_readiness.py` exercises cross-layer wiring via `registered_platforms()` — it will pass automatically after removal since the test loops over registered platforms only.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Stale base branch — CI tests "merged into latest main", not just local | Rebase onto freshly-fetched `origin/main` before running final pytest |
| Partial commit leaves package un-importable | All of Unit 1 (registry + adapters + hardcoded test rows) lands as one atomic commit |
| Sentinel v1 already burned on operator machine | v2 bump forces re-run on all machines |
| `"note"` grep returns false positives (generic word) | Use `platform.*"note"` pattern, not bare `"note"`, in success-criteria grep |
| `monolith_budget.toml` ceiling not updated → budget gate fails | Measure with radon in Unit 1, update ceiling in same commit |

## Documentation / Operational Notes

- Operators with previously-bound csdn/juejin/note channels will see their credential files silently deleted on the next WebUI startup. No user-visible notification is emitted (consistent with prior purge behavior).
- Operators who previously published via csdn/juejin/note will have historic `publish.confirmed` events in `events.db` with those adapter strings. After removal, a backfill run will quarantine those rows rather than seed them (the `_ADAPTER_STRING_TO_PLATFORM` miss routes unknowns to quarantine). This is intentional and consistent with backfill design — event history is preserved, not purged. Quarantined rows remain in reconciliation output until manually acknowledged.
- After this PR, `grep -r "csdn\|juejin" src/` returns only the `_REJECTED_PLATFORMS` entries — the negative-knowledge rationales are the intended survivors.

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-28-remove-csdn-juejin-note-channels-requirements.md](docs/brainstorms/2026-05-28-remove-csdn-juejin-note-channels-requirements.md)
- **Canonical template:** `docs/plans/2026-05-27-001-refactor-remove-jianshu-channel-plan.md`
- Prior bulk removal: `docs/plans/2026-05-26-007-refactor-remove-four-channels-plan.md`
- Registry pattern: `src/backlink_publisher/publishing/registry.py` (`_REJECTED_PLATFORMS`, `_AUTH_TYPE_BY_PLATFORM`)
- Credential purge: `webui_store/channel_status.py` (`_REMOVED_CREDENTIAL_SLUGS`, `_PURGE_SENTINEL_NAME`)
- Dofollow rationale: `docs/solutions/workflow-issues/grep-dofollow-map-before-shipping-adapter-2026-05-20.md`
