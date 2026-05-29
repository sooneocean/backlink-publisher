---
date: 2026-05-28
topic: remove-csdn-juejin-note-channels
---

# Remove CSDN / Juejin / Note Channels

## Problem Frame

The operator has no accounts on CSDN, 掘金 (Juejin), and note.com and never intends to use them. All three are nofollow platforms (links rewritten through redirect interstitials), making them low-value anyway. Their presence adds visual noise in the WebUI, dead code in tests, and maintenance burden with zero return. Full removal is preferred over `visibility="hidden"` retirement — the operator wants them gone, not just hidden.

## Requirements

**Adapter layer**
- R1. Delete `src/backlink_publisher/publishing/adapters/csdn_api.py`, `juejin_api.py`, `note_api.py`.
- R2. Remove the three `register()` calls and their corresponding imports from `publishing/adapters/__init__.py`.
- R3. Remove the three manifest dicts (`CSDN_MANIFEST`, `JUEJIN_MANIFEST`, `NOTE_MANIFEST`) from `publishing/_manifests.py`.
- R4. Remove the three entries from `_nofollow_rationales.py`.
- R5. Add `"csdn"`, `"juejin"`, `"note"` to `publishing.registry._REJECTED_PLATFORMS` so a future accidental `register()` call raises `RegistryError` at import time.

**Registry & dispatch**
- R6. Remove `"csdn"`, `"juejin"`, `"note"` from `registry._AUTH_TYPE_BY_PLATFORM`.
- R7. Remove the three entries from `idempotency/backfill.py::_ADAPTER_STRING_TO_PLATFORM`.

**WebUI binding**
- R8. Remove `"csdn"`, `"juejin"`, `"note"` entries from `webui_app/routes/channel_bind_save.py::_PASTE_BLOB_CHANNELS`.
- R9. Update the documentation comment in `webui_app/templates/_settings_binding_paste_blob.html` to drop the three names.

**Credential purge**
- R10. Add `"csdn"`, `"juejin"`, `"note"` to `_REMOVED_CREDENTIAL_SLUGS` in `webui_store/channel_status.py` so the startup one-shot purge cleans up `csdn-credentials.json`, `juejin-credentials.json`, `note-credentials.json` from the operator's config dir. Follow the versioning scheme from the existing jianshu entry (bump the purge sentinel version so the one-shot re-runs on operator machines that already ran a previous version).

**Tests**
- R11. Sweep all test files for platform-name references to `csdn`, `juejin`, and `note`. Run `grep -rn '"csdn"\|"juejin"\|platform.*note' tests/` before starting to get the authoritative file list (expected ~14 files). Remove or update fixtures, parametrize cases, and assertions that reference these platforms.
- R12. **Carve-out A:** In `test_cli_canary_targets.py`, leave `receipt["note"]` untouched — it is a generic canary receipt field, not the platform name.
- R13. **Carve-out B:** In `test_inspect_target_anchor.py`, the URL `https://link.juejin.cn/?target=...` tests the generic `_unwrap_interstitial` path. Replace the juejin URL with any other domain (e.g. `https://link.example.com/?target=...`) to preserve coverage without keeping a juejin reference.
- R14. In `test_purge_removed_credentials.py`, replace `_cred("csdn")` (used as the "live channel should not be swept" positive control) with a genuinely live channel such as `_cred("substack")` — after removal, csdn is no longer a live channel and the assertion becomes semantically incorrect.
- R15. In `test_protected_set_coverage.py`, update the inline comment and revisit the minimum protected-file count assertion after removing the three credential files from the expected set.
- R16. If any test file becomes entirely empty after the sweep, delete the file.

**Registry docstrings**
- R17. Update or remove the two docstring/comment references to csdn/juejin/note in `publishing/registry.py` (the comment around line 160 referencing the interstitial pattern, and the docstring around line 424 listing paste_blob targets) so the success-criteria grep returns clean results.

**Config hygiene**
- R18. Verify that `config/writer.py::_SAVE_CONFIG_KNOWN_ROOTS` does not contain `"csdn"`, `"juejin"`, or `"note"`. If present, remove them (full removal, not retirement).
- R19. Update `config.example.toml` if it has any `[csdn]`, `[juejin]`, or `[note]` sections.

## Success Criteria

- `pytest tests/` passes with no skips related to these platforms.
- `grep -rn '"csdn"\|"juejin"' src/ webui_app/ tests/` returns only the `_REJECTED_PLATFORMS` entries — no adapter classes, manifests, register calls, route dispatchers, or docstrings.
- `grep -rn 'platform.*"note"\|"note".*platform' src/ webui_app/ tests/` returns no platform-specific references (only the unrelated `receipt["note"]` field and similar generic uses).
- The WebUI settings page shows no trace of these three channels.
- Operators who previously bound these channels see their credential files purged on next WebUI startup.

## Scope Boundaries

- **Not in scope:** removing the `paste_blob` bind pattern itself — `substack` still uses it.
- **Not in scope:** cleaning up any operator config files (e.g., `~/.config/backlink-publisher/config.toml`) — those are outside the repo.
- **Not in scope:** removing credential files from disk — that is operator-run cleanup, not code.
- **Not in scope:** any behavior change to the remaining channels or the `_PASTE_BLOB_CHANNELS` dispatch path.

## Key Decisions

- **Full delete, not `visibility="retired"`**: The operator explicitly wants no trace. The `retired` path still preserves adapter code and config round-tripping — this goes further.
- **Add to `_REJECTED_PLATFORMS`**: Prevents accidental re-registration in a future PR without a visible un-rejection diff.
- **R10 carve-out for `receipt["note"]`**: The field `receipt["note"]` in `canary_targets.py` is a generic receipt key, not the platform name. Removing it would break unrelated logic.

## Outstanding Questions

### Deferred to Planning
- [Affects R12][Needs research] Confirm whether `_SAVE_CONFIG_KNOWN_ROOTS` in `config/writer.py` contains any of the three platform names — the scan did not surface this, but verify before writing the PR.
- [Affects R10][Needs research] Confirm `test_cli_canary_targets.py`'s `receipt["note"]` is truly unrelated to the platform name (likely a canary receipt field) before leaving it untouched.

## Next Steps

→ `/ce:plan` for structured implementation planning
