# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Fixed

- txt.fyi adapter now clears the site's anti-spam dwell-time gate before
  submitting. `edit.php` rejects POSTs that arrive too soon after the form was
  served (keyed off the hidden `form_time` field): a sub-second GET→POST — what
  the adapter did — is treated as a bot and silently tarpitted to a 200 "Thank
  you for your submission!" page with no redirect and no permalink, so every
  txt.fyi publish failed with `ExternalServiceError: did not redirect to a
  published URL after submit`. The adapter now waits a configurable dwell time
  (`BACKLINK_TXTFYI_SUBMIT_DELAY_SECONDS`, default 4s; the gate cleared by ~3s
  in 2026-05-29 probing) before the POST, and detects the tarpit page to raise
  an actionable error (raise the delay) instead of the generic no-redirect one.
- All three `urllib.request` fetch sites now normalize non-ASCII URLs before
  opening a connection, preventing `'ascii' codec can't encode characters`
  crashes across the full pipeline: `linkcheck.verify.verify_published`
  (post-publish verifier — the original crash site), `linkcheck.http.check_url`
  (pre-publish reachability), and `content.fetch.verify_url_has_content`
  (planning-phase URL gate). A shared `_util.url.normalize_url_for_fetch`
  helper IDNA-encodes the host and percent-encodes path/query; ASCII URLs
  pass through byte-identical and idempotent. Previously Velog Korean
  `@username` / CJK `url_slug` URLs demoted legitimately-published posts to
  `published_unverified`. Plan 2026-05-21-005.

### Added

- `medium-login` CLI: thin alias for `bind-channel --channel medium`, matching
  the `velog-login` pattern (Plan 2026-05-19-005 Unit 1).
- `ChannelRecipe.post_persist` hook (optional): driver invokes after
  `_persist_storage_state` succeeds and before `mark_bound`, letting recipes
  derive secondary credential files. Used by the medium recipe to convert
  Playwright `storage_state.json` into a cookies-only `medium-cookies.json`
  + a `medium-meta.json` (UA + chromium version, captured live by the
  predicate). velog / blogger recipes leave `post_persist` `None` — no
  behavior change.

### Changed (**Breaking** for existing Medium operators)

- `MediumBrowserAdapter` now reads its credential from
  `<config_dir>/medium-cookies.json` via `context.add_cookies([...])`. The
  pre-Plan-005 path that read `medium-storage-state.json` via
  `new_context(storage_state=...)` is removed; no double-write window, no
  fallback. Operators upgrading across this release must run `medium-login`
  (or `bind-channel --channel medium`) once to populate the new file. The
  adapter's friendly `DependencyError` on first invocation spells out the
  exact command.
- `bind-channel medium` now writes `medium-cookies.json` (the new canonical
  bound credential) and unlinks `medium-storage-state.json` in the same
  bind cycle. The `channel_status_store["medium"]["storage_state_path"]`
  field now points at `medium-cookies.json` (the field name remains
  historical; the value reflects current canonical state).

### Notes

- Hard-cut chosen over a 60-day double-write window: this is a
  single-operator tool per AGENTS.md, so the 2-minute cost of running
  `medium-login` once is lower than the cost of maintaining a dual-format
  compatibility layer with a calendar-driven sunset PR.
- Future `MediumGraphQLAdapter` (Plan 2026-05-19-005 Unit 2, Phase 2,
  gated by spike) will consume the same `medium-cookies.json` +
  `medium-meta.json` for headless GraphQL publishing.

[Unreleased]: https://github.com/redredchen01/backlink-publisher/compare/main...HEAD
