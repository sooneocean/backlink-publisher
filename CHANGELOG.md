# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.3.0] - 2026-06-01

### Added

- `POST /copilot/ask` LLM-backed Q&A route (`routes/copilot.py`). Accepts natural-language questions, calls the configured LLM via `safe_post_json`, returns answers as JSON. Returns `400` when unconfigured, `502` on LLM failure. Plan U5.
- Q&A panel (`_copilot_panel.html`) — unlocked/locked state driven by server-side `llm_configured` context processor. Includes copilot.css (form, bubbles, loading/error states) and copilot.js (ESM module, CSRF-safe `postJson`). Plan U6.
- `llm_configured` context processor in `webui_app/__init__.py` — checks `llm-settings.json` existence and validity to drive UI state.
- `src/backlink_publisher/_util/ssl_ctx.py` — SSL context utility module.
- Full test suite: 14 route tests (`test_copilot_qna_route.py`), 8 panel render tests (`test_copilot_panel_render.py`), 3 Q&A render tests (`test_copilot_qa_render.py`), 3 asset version cache tests (`test_asset_version_cache.py`), 3 SSL context tests (`test_linkcheck_ssl_ctx.py`), route contract extension (`test_webui_route_contract.py`).

### Fixed

- `test_pipeline_inprocess_characterization.py` — expanded coverage for edge cases.
- `webui_app/helpers/cli_runner.py` — adjusted for API changes.

- URL verify throttle rollback now removes the caller's own session-window
  slot by value (`remove(now)`) instead of the last-appended entry (`pop()`).
  Under concurrent calls sharing the same session, `pop()` (LIFO) could remove
  a *peer's* slot instead of the failing caller's own, leaking the failing
  caller's timestamp in the window. This caused the session's rate-limit counter
  to over-count rejected requests: a session could be rate-limited for a 10s
  window even though its requests were turned away before being served. Both
  rollback paths (`upstream_overloaded` and `host_busy`) are fixed.

- `spawn_browser_login` now prepends the **absolute** path to `src/` in
  `PYTHONPATH` for the detached subprocess, matching the pattern established in
  `bind_job.py`. The previous relative `"src"` prefix only resolved when the
  WebUI was started from the repository root; starting from any other directory
  (e.g. a system-service working directory) would cause the subprocess to fail
  with an `ImportError` on `backlink_publisher`.

- G3 referer-audit evidence now correctly shows `preserving=none` when every
  render path strips `referer`. The previous `or "preserving=none"` fallback
  was unreachable: `"preserving=" + ""` (an empty join over a zero-length list)
  is truthy, so the `or` short-circuited and the evidence cell always read
  `"preserving="`. Operators reading the committed `gate-verdicts.md` ledger
  or the raw JSONL evidence when all paths strip referer would have seen a
  misleading empty `preserving=` token instead of the explicit `preserving=none`.

- SSRF blocklist now rejects `168.63.129.16` (Azure wireserver). This address
  is not RFC 1918, not link-local, and not covered by the existing
  `169.254.0.0/16` range, so it previously passed the IP guard. Azure wireserver
  exposes DHCP, platform key management, and health-probe endpoints that are
  reachable only from inside Azure VMs; an attacker-controlled redirect or a
  domain that resolves to it could exfiltrate instance metadata. The address is
  now blocked as a dedicated `/32` entry in `_BLOCKED_NETWORKS`.

- `upgrade_target_to_threeurl` (the `/sites` "upgrade legacy target → three-URL"
  path) now finds an existing `anchor_keywords` pool keyed by the bare domain or
  a scheme variant, via the canonical `get_anchor_keywords` accessor. Previously
  it tried only the scheme-exact key plus a trailing-slash variant that stored
  keys never carry (`_parse_target_anchor_keywords` rstrip's them), so a
  `[targets."legacy.com"]` pool was silently dropped and the target bootstrapped
  to just the domain label — losing the operator's curated keywords on upgrade.
- `[anchor_alarm]` override parsing now rejects unknown keys in an
  `[[anchor_alarm.override]]` row instead of silently ignoring them, mirroring
  the global-scope unknown-key guard. Previously a misspelled threshold field
  (e.g. `exact_ratio_ceil`) was dropped without error whenever the row also
  carried a valid field, so the operator's intended override silently never
  applied. The row now raises `InputValidationError` at config load.
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

[Unreleased]: https://github.com/redredchen01/backlink-publisher/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/redredchen01/backlink-publisher/releases/tag/v0.3.0
