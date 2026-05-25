---
title: "feat: Medium browser-bind recipe hardening + adapter convergence on Plan 001 framework"
type: feat
status: completed
date: 2026-05-19
completed: 2026-05-19
deepened: 2026-05-19
origin: docs/brainstorms/2026-05-19-medium-browser-bind-flow-requirements.md
depends_on: docs/plans/2026-05-19-001-feat-settings-browser-binding-plan.md
revision: 3
rebaselined: 2026-05-19
---

## Re-baseline Note (2026-05-19, post-ce:work setup)

While setting up the worktree for implementation, discovered Plan 001 has shipped Units 4-7 since this plan was written:

- **Plan 001 Unit 4** (`79d2441`): `webui_app/routes/bind.py` blueprint with loopback assertion + CSRF on POST + channel allow-list against CHANNELS. **Subsumes my Unit 3 partially** — loopback + CSRF already done.
- **Plan 001 Unit 5** (`147b0ac`): `webui_app/templates/_settings_channel_binding.html` shared partial (3 states: bound/expired/unbound) + `bind_channel.js` (~150 lines) + `BIND_ERROR_MESSAGES` Chinese mapping + 8 tests. **Subsumes most of my Unit 4** — Settings UI integration done for the 3 base states.
- **Plan 001 Unit 6** (`3f71abe`): `medium_api.py` + `blogger_api.py` translate HTTP 401/403 to `AuthExpiredError`. **Does NOT touch `medium_browser.py`** — my Unit 6 (storage convergence `launch_persistent_context` → `new_context(storage_state=...)`) is **still fully needed**.
- **Plan 001 Unit 7** (`f859e62`): AGENTS.md + README docs.

### Updated unit scope (for next session)

| Unit | Status after Plan 001 4-7 ship |
|---|---|
| **Unit 0** (schema extension + spikes) | Still needed. `last_verified_at` + `identity_mismatch` state are new additions to `channel_status.py` schema. |
| **Unit 1** (recipe hardening + IdentityMismatch) | Still fully needed. |
| **Unit 3** (security helpers) | **REDUCED**: drop the loopback helper (Plan 001 Unit 4 already wires loopback). Keep `_check_bind_origin_or_abort` (Origin/Referer + DNS rebind defense) + `_refuse_when_allow_network` (ALLOW_NETWORK=1 refuse). Both still missing from Plan 001's bind blueprint. |
| **Unit 4** (Settings UI integration) | **REDUCED**: 3 base states already rendered by `_settings_channel_binding.html`. Only need to extend the partial (or add a Medium-specific override) for the `identity_mismatch` state with confirm prompt. |
| **Unit 5** (liveness probe + probe-copy) | Still fully needed. Plan 001 did not ship liveness probing. |
| **Unit 6** (adapter convergence for browser adapter) | Still fully needed. Plan 001 Unit 6 covers API adapters (`medium_api`, `blogger_api`) only; `medium_browser.py` untouched. |

### Next session starting point

- Worktree: `bp-medium-bind-hardening/`, branch `feat/medium-bind-hardening` based on `feat/settings-browser-binding @ f859e62`
- Recommended start: Unit 0 (smallest unblock), then Unit 1 (test-first per Execution note)
- Order option (parallel-safe): Units 1 + 5 + 6 don't share files; Unit 0 unblocks all three
- Plan 001 author may push more commits on `feat/settings-browser-binding`; rebase when needed
- Main `backlink-publisher/` worktree has foreign-session WIP — do not touch unless coordinated
- BIND_ERROR_MESSAGES dict already in `webui_app/services/bind_job.py` — Unit 1 must add Chinese message for `identity_mismatch` error_code (coordinate with Plan 001 author)
- `_settings_channel_binding.html` is a shared partial per channel — Unit 4's identity_mismatch state should be added at the partial level (benefits velog + blogger when their recipes get the same hardening), not Medium-specific override

---

# Medium browser-bind recipe hardening + adapter convergence

## Overview

Plan 2026-05-19-001 (settings-browser-binding) ships a generalized `bind-channel` CLI + recipe + driver framework. Units 1+2+3 are merged on `feat/settings-browser-binding`:
- Unit 1 (`8fbf0b3`): `AuthExpiredError`, `channel_status_store`, `CHANNELS` frozenset
- Unit 2 (`3f37c18`): `bind-channel` CLI, driver (non-persistent Playwright `launch()` + `new_context()`), recipes (velog/medium/blogger), EVENTS frozenset
- Unit 3 (`f773758`): `velog-login` alias

Units 4-7 are pending (webui bind routes, Settings UI, adapter wiring, docs).

This plan adds Medium-specific hardening to Plan 001's framework plus the adapter convergence that makes the framework actually usable for Medium. Scope reduced significantly from earlier revisions after reading Plan 001 source: many security/state primitives v2 proposed were solving non-problems given Plan 001's non-persistent design.

**What's actually needed (v3, post-source-read)**:
1. **Recipe hardening**: tighten Medium's bound predicate to require URL + HttpOnly cookie sanity + identity-mismatch detection + idle-detection timeout.
2. **Adapter convergence**: rewrite `MediumBrowserAdapter.publish` to load `storage_state.json` via `new_context(storage_state=...)` instead of `launch_persistent_context(user_data_dir, ...)`. Currently the adapter and bind driver write to two different artifacts; without this Unit, bind succeeds but publish still fails.
3. **`channel_status_store` schema extension**: add `last_verified_at` field + recognize `identity_mismatch` state so the Settings UI has a stable contract.
4. **Settings UI integration**: render the channel-status-store state in `_settings_channel_medium.html`.
5. **Liveness probe on Settings GET**: stamp-store-only or probe-copy approach (avoid anti-bot poisoning of the live credential).
6. **Webui security helpers** for Plan 001 Unit 4 to adopt on its bind routes.

**Deliberately out** (per scope-guardian + product-lens consensus during v2 review):
- Log value-pattern scrubber (separate infra plan)
- 4-helper CI gate test (deferred until 3 channels validate the pattern)
- `BACKLINK_PUBLISHER_BIND_TOKEN` env name reservation (YAGNI; let future plan name it)
- `LockBusyError` + CLI exit code 5 (YAGNI; use existing error class)
- Profile dir at XDG (non-problem — Plan 001 doesn't use persistent profile)
- Legacy `chrome-profile-default/` migration module (5-line log notice in Unit 6 is enough)

## Problem Frame

See origin brainstorm. Summary: Medium deprecated Integration Token issuance in March 2023; Phase A (`7868656`) already deleted the dead OAuth/Token UI. Plan 001 framework is the right home for the replacement, but its Medium recipe needs hardening, and `MediumBrowserAdapter.publish` currently reads from a Chromium SQLite profile while Plan 001 writes a JSON storage_state — they don't compose.

## Requirements Trace

Carrying forward from origin brainstorm (R-numbers preserved):

- **R1** Settings card shows status-aware CTA (bind / re-bind)
- **R4** Bind output (`<config_dir>/medium-storage-state.json`) is loaded by `MediumBrowserAdapter.publish` via `new_context(storage_state=...)`. Same artifact, one source of truth.
- **R5** Login-success = URL pattern AND HttpOnly cookie sanity. URL via `wait_for_url(_BOUND_URL_PATTERN)` (Plan 001's existing negative-match). Cookie sanity via `context.cookies(domain='medium.com')` reading HttpOnly cookies (Spike 1 confirmed `document.cookie` doesn't show them).
- **R6** Identity-mismatch detection via DOM `@username` scrape compared to `<config_dir>/medium-last-account.txt` (single canonical path; no diagram/prose drift). Mismatch raises `IdentityMismatch(RuntimeError)` from the predicate; driver catches with a new `except IdentityMismatch:` arm in `run_bind`.
- **R7** Idle-detection timeout (90s no nav) + 20-min absolute wall inside the predicate. Uses `framenavigated` listener.
- **R9** Liveness probe on Settings GET (10s budget) reads `channel_status_store["medium"]` first; if `last_verified_at < 5 min`, returns cached. Otherwise runs a probe on a **copy** of storage_state.json (`storage_state.json.probe`) — never on the live credential — to avoid anti-bot poisoning the headed publish credential.
- **R12** `MediumBrowserAdapter.publish` detects `/m/signin` redirect → calls `mark_expired("medium")` (Plan 001 API) inside a `try/except` (filesystem failure on mark_expired must not mask the auth error) → raises `AuthExpiredError(channel="medium")` (Plan 001 class).
- **R13** Successful publish refreshes `storage_state.json` via `context.storage_state(path=...)` with `tempfile.mkstemp` atomic write.
- **R14** Webui security helpers (Origin/Referer + ALLOW_NETWORK refuse) ship; Plan 001 Unit 4 author wires them onto bind routes when that unit lands.
- **R15** `BACKLINK_PUBLISHER_ALLOW_NETWORK=1` → 403 from `_refuse_when_allow_network()` helper.

## Scope Boundaries

- **Not done** — generic `bind-channel` framework (Plan 001 owns)
- **Not done** — webui bind routes (Plan 001 Unit 4)
- **Not done** — velog/blogger recipe hardening (separate plans per channel)
- **Not done** — Magic Link auth path (operator must use Google/email-password)
- **Not done** — multi-account Medium binding
- **Not done** — cross-platform browser-auth framework abstraction
- **Not done** — log value-pattern scrubber (separate infra plan when needed)
- **Not done** — 4-helper CI gate (deferred until 3 channels)
- **Not done** — `BACKLINK_PUBLISHER_BIND_TOKEN` reservation (YAGNI)
- **Not done** — `LockBusyError` / CLI exit code 5 (use existing error)
- **Not done** — XDG temp profile dir (Plan 001 driver is non-persistent; no temp dir needed)
- **Not done** — `profile.lock` coordination across publish/bind/probe (the probe-copy design in R9 removes the contention)

## Context & Research

### Relevant Code and Patterns (verified by reading `feat/settings-browser-binding` source)

**Plan 001 framework, current shape**:
- `src/backlink_publisher/cli/_bind/channels/__init__.py`:
  - `CHANNELS: frozenset = frozenset({"velog", "medium", "blogger"})`
  - `EVENTS: frozenset = frozenset({"channel.bind.start", "channel.bind.browser_ready", "channel.bind.login_detected", "channel.bind.persisted", "channel.bind.failed"})` — **prefixed namespace; my Unit 1's new event matches**
- `src/backlink_publisher/cli/_bind/driver.py`:
  - Exception classes: `PlaywrightLaunchError(RuntimeError)` (with `error_code`), `BoundPredicateTimeout(RuntimeError)`, `PersistIOError(RuntimeError)`. **No `BoundPredicateError` base** — Unit 1 uses concrete classes only.
  - `BindResult` frozen dataclass: `success: bool, channel: str, storage_state_path: Path | None, error_code: str | None`. **error_code is free-form string, not closed enum.**
  - `run_bind()` catches `PlaywrightLaunchError`, `BoundPredicateTimeout`, `UsageError`, `PersistIOError`. **NO catch-all `except Exception:`** — uncaught exceptions propagate out and crash the subprocess. Unit 1 adds a new `except IdentityMismatch:` arm before the persist block.
  - `_PlaywrightBrowserRunner.launch_and_wait` uses `chromium.launch(headless=False)` + `browser.new_context()` (no `user_data_dir`, **non-persistent**). Recipe's `bound_predicate(page)` is called; any non-`PWTimeoutError` exception is re-raised after cleanup (`context.close()`, `browser.close()`, `pw.stop()`). **No rmtree, no user_data_dir cleanup needed.**
  - `_persist_storage_state` uses `tempfile.mkstemp(prefix=..., suffix='.tmp', dir=target.parent)` + `os.chmod(0o600)` + `os.replace`. Atomic.
  - `_validate_storage_state_path` pins to `_config_dir()`. **Not amended.**
- `src/backlink_publisher/cli/_bind/recipes/medium.py` (45 lines): negative-match URL regex `https?://(?:[^/]*\.)?medium\.com/(?!m/signin)(?:.*)?$`; cookie host filter apex-only. Predicate is `page.wait_for_url(_BOUND_URL_PATTERN)` single call. **Unit 1 replaces predicate body.**
- `src/backlink_publisher/cli/bind_channel.py`: 170 lines, exit codes 0/1/3. **Unit 1 adds exit code 4 for `error_code="identity_mismatch"`.**
- `webui_store/channel_status.py`:
  - Schema: `{status, bound_at, storage_state_path}`. **Status enum: `unbound`/`bound`/`expired` only.**
  - API: `mark_bound`, `mark_expired`, `get_status`, `list_all`, `reconcile_on_load`. **No `update()` method exposed.**
  - **Unit 0** (this plan) extends schema with `last_verified_at: ISO | None` and `identity_mismatch` state value. Adds `mark_verified(channel)` + `mark_identity_mismatch(channel, old, new)` API.

**Adapter to converge** (R4, R12):
- `src/backlink_publisher/publishing/adapters/medium_browser.py:67-87`: currently `chromium.launch_persistent_context(user_data_dir, headless=False, args=[...])` reading Chromium SQLite. **Unit 6 rewrites to `chromium.launch(headless=False) + new_context(storage_state=<config_dir>/medium-storage-state.json)`.**

**Webui patterns** (`webui_app/`):
- `helpers.py:534-546` — `_ensure_csrf_token()` + `_check_csrf_or_abort()` (form field `csrf_token`)
- `helpers.py:502` — `_check_localhost()`
- `helpers.py:521` — `_resolve_bind_host()` consumes `BACKLINK_PUBLISHER_ALLOW_NETWORK`
- `helpers.py:_get_velog_status` — filesystem-first status idiom (template for `_get_medium_status`)
- `routes/settings_basic.py:135-143` `/api/velog/login` — subprocess.Popen pattern (Plan 001 Unit 4 will mirror)

### Institutional Learnings (consumed; only the relevant subset)

- **`ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md`** — Unit 6 dispatch-chain test patches all three adapters (`MediumAPIAdapter.publish` + `MediumBraveAdapter.publish` + `MediumBrowserAdapter.publish`) + `time.sleep`.
- **`webui-blocking-subprocess-and-missing-progress-feedback-2026-05-12.md`** — Unit 5 liveness probe uses `concurrent.futures.ThreadPoolExecutor` with 10s timeout, never blocks the Flask request thread.
- **`tests-coupled-to-operator-config-state-2026-05-18.md`** — Plan 001's autouse `_isolate_user_dirs` already covers `BACKLINK_PUBLISHER_CONFIG_DIR`; storage_state.json lives there → no fixture extension needed.
- **`stream-to-needed-tag-not-cap-then-reject-2026-05-15.md`** — predicate uses positive predicates (URL + cookie); no open-ended waits.
- **`negative-assertion-locks-in-bug-2026-05-15.md`** — Unit 1 adds `channel.bind.identity_mismatch` event; audit `tests/` for `assert ... not in` before merge.

### External References

Not run. Local + Plan 001 source is sufficient.

## Key Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Build on Plan 001 framework | Avoid duplicate paths. User confirmed during brainstorm reconciliation. |
| Concrete exceptions only — no `BoundPredicateError` base class | Plan 001's exception hierarchy is flat. `IdentityMismatch(RuntimeError)` joins as a peer of `BoundPredicateTimeout`, `PlaywrightLaunchError`, etc. Driver catches `IdentityMismatch` with a new typed arm. |
| Extend `channel_status_store` schema in **this plan** (own the change explicitly) | Plan v2 falsely claimed "not amended". Reality: TTL cache + `identity_mismatch` state require schema extension. Unit 0 of this plan ships the schema change as a small Plan-001-coordinated edit. Document in coordination note for Plan 001 author. |
| Liveness probe operates on **copy** of `storage_state.json` | Security-lens P0: headless probe sharing the live credential with headed publish risks anti-bot server-side flagging that invalidates cookies. Probe-copy isolation: webui copies `storage_state.json` → `storage_state.json.probe` (in-memory or atomic copy), probe loads the copy, if Cloudflare flags it the live credential is untouched. |
| No XDG temp profile dir for headed Chromium | Plan 001's `_PlaywrightBrowserRunner` uses `chromium.launch()` (no user_data_dir) — Playwright manages an ephemeral profile internally and cleans up via `browser.close()`. No persistent dir for the plan to own. |
| No `profile.lock` cross-process mutex | Probe-copy isolation (above) removes the contention reason. Bind/publish/probe each use independent non-persistent contexts; the only shared file is `storage_state.json`, and atomic `os.replace` plus single-retry on `json.load` covers the read/write race. |
| `last_account` at `<config_dir>/medium-last-account.txt` (single canonical path) | One path, named in HLDS diagram + R6 + Unit 1 consistently. Atomic temp+rename per `tempfile.mkstemp` pattern. |
| URL pattern + HttpOnly cookie sanity (positive predicate) | Spike 1 confirmed `/me` → `/@username` redirect; existing Plan 001 negative-match handles this. Spike 3 confirmed Medium auth cookie is HttpOnly. Cookie sanity via `page.context.cookies(domain='medium.com')` (Playwright reads HttpOnly). |
| Idle-detection (90s no nav) + 20-min wall | Replaces Plan 001's `BIND_TIMEOUT_MS = 5min` for Medium predicate. Predicate hand-rolls `framenavigated` listener + short `wait_for_url(timeout=1000)` loop. If Unit 0 spike #7 shows framenavigated unreliable for SPA-2FA, fall back to plain `wait_for_url(timeout=1_200_000)` (20-min wall only). |
| Webui security helpers ship; Plan 001 Unit 4 author wires them | Helpers (`_check_bind_origin_or_abort`, `_refuse_when_allow_network`) are ready when bind routes land. Documented in this plan + PR review hand-off. No CI gate — trust the author + PR review, validate behavior with integration tests once routes exist. |

## Open Questions

### Resolved During Planning

- **Q: Does Plan 001's driver create a persistent user_data_dir?** → No. Uses `launch()` + `new_context()`. Ephemeral. No XDG temp dir needed.
- **Q: Does `channel_status_store` have `update()` or `last_verified_at`?** → No to both. Must extend in Unit 0.
- **Q: Does `BoundPredicateError` base class exist?** → No. Use concrete `IdentityMismatch(RuntimeError)`.
- **Q: Does Plan 001 driver have `finally: rmtree(user_data_dir)`?** → No. No `user_data_dir` exists.
- **Q: Storage convergence approach?** → Adapter rewrite (`launch_persistent_context` → `launch + new_context(storage_state=...)`). Real Unit 6 work.
- **Q: Probe safe to share credential?** → No (security-lens F5). Probe operates on `storage_state.json.probe` copy.
- **Q: profile.lock needed?** → No. Probe-copy removes contention. Confirmed by reading driver source.
- **Q: 4-helper CI gate?** → Deferred. Helpers ship; gate later when ≥3 channels validate the pattern.

### Deferred to Implementation

- Exact HttpOnly cookie name(s) from Spike 3a (Medium may use 1 or 2 stable names; Unit 1's whitelist constant)
- Cloudflare/anti-bot probe behavior from Spike 2 (drives Unit 5 default — active probe OR stamp-store-only)
- SPA-2FA `framenavigated` reliability from Spike 7 (drives Unit 1's idle-detection vs wall-clock-only fallback)
- DOM selector for `@username` scrape (small inside-Unit-1 sub-spike: `[data-testid="headerUserIcon"]` parent's `href`, fall back to `og:url` meta, fall back to URL parse)
- Plan 001 Unit 4 route paths (this plan's webui security helpers are wired by Unit 4 author; specific decorator-vs-inline call style up to that PR)

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification.*

```
┌───────────── bind-channel medium (Plan 001 subprocess) ─────────────┐
│                                                                       │
│  recipes/medium.py predicate (Unit 1):                                │
│    idle_timer = IdleTimer(90s, 1200s)                                 │
│    page.on('framenavigated', idle_timer.reset)                        │
│    loop until idle_timer.expired():                                   │
│      try: page.wait_for_url(_BOUND_URL_PATTERN, timeout=1000)         │
│      except PWTimeoutError: continue (re-check idle, retry)           │
│      else:                                                            │
│        cookies = page.context.cookies(domain='medium.com')            │
│        if not cookie_sanity_passes(cookies): continue                 │
│        user = scrape_username(page)                                   │
│        last = read_last_account()  # <config_dir>/medium-last-account │
│        if last and last != user: raise IdentityMismatch(old, new)     │
│        write_last_account_tentative(user)                             │
│        return  # success                                              │
│    raise BoundPredicateTimeout()  # idle or absolute                  │
│                                                                       │
│  driver.run_bind (Unit 1 adds):                                       │
│    try: runner.launch_and_wait(...)                                   │
│    except IdentityMismatch as e:                                      │
│      return BindResult(success=False, error_code="identity_mismatch") │
│    ... existing arms unchanged                                        │
│    on success: _persist_storage_state(...)                            │
│      then rename medium-last-account.tentative → medium-last-account  │
│      then mark_bound(...)                                             │
└───────────────────────────────────────────────────────────────────────┘
                  │                              ▲
                  │ writes                       │ reads
                  ▼                              │
    ┌──────────────────────────────────────────────────────────────┐
    │  <config_dir>/                                                │
    │    medium-storage-state.json   (Plan 001 contract, 0600)      │
    │    medium-last-account.txt     (Unit 1, atomic, 0600)         │
    │    channel-status.json         (Plan 001 store + Unit 0       │
    │                                  schema extension)             │
    │  Each Settings GET produces an in-memory copy of               │
    │  medium-storage-state.json → passed to headless probe; never   │
    │  reused for publish.                                           │
    └──────────────────────────────────────────────────────────────┘
                  ▲                              ▲
                  │ reads + refreshes            │ reads on Settings GET
                  │                              │
┌─── publish-backlinks ──────────┐  ┌─── webui Flask process ────────┐
│                                 │  │                                  │
│ medium_browser.py (Unit 6):     │  │ helpers._get_medium_status      │
│   was: launch_persistent_context│  │   (Unit 4):                     │
│   now: launch + new_context(    │  │   status = get_status('medium') │
│         storage_state=cfg/...)  │  │   if status.last_verified_at <  │
│   on /m/signin:                 │  │      5min: return cached        │
│     try: mark_expired('medium') │  │   else: medium_liveness_check() │
│     except: log.warning(...)    │  │     (Unit 5, probe-copy)        │
│     raise AuthExpiredError(...)│  │   render template badge         │
│   on success:                   │  │                                  │
│     context.storage_state(      │  │ helpers._check_bind_origin (U3) │
│       path=tmp); os.replace(...)│  │ helpers._refuse_when_allow_net  │
│                                 │  │   (consumed by Plan 001 U4)     │
└─────────────────────────────────┘  └──────────────────────────────────┘
```

## Implementation Units

### Dependency graph

```mermaid
graph TB
  U0[Unit 0: channel_status_store schema extension + Spikes 3a/2/7]
  U1[Unit 1: Medium recipe hardening + driver IdentityMismatch arm + CLI exit code 4]
  U3[Unit 3: Webui security helpers — Origin + ALLOW_NETWORK refuse]
  U4[Unit 4: Settings UI integration with channel_status_store]
  U5[Unit 5: Liveness probe — probe-copy + TTL cache]
  U6[Unit 6: Adapter convergence — launch_persistent_context → new_context(storage_state)]
  U0 --> U1
  U0 --> U5
  U1 --> U4
  U5 --> U4
  U6 --> U4
```

---

- [ ] **Unit 0: `channel_status_store` schema extension + Unit 0 spikes**

**Goal:** Extend Plan 001's `channel_status_store` schema with `last_verified_at` and `identity_mismatch` state. Run 3 small spikes that feed Unit 1/5 constants.

**Requirements:** R6, R9, R5

**Dependencies:** Plan 001 Unit 1 merged (`feat/settings-browser-binding`).

**Files:**
- Modify: `webui_store/channel_status.py` — extend schema, add `mark_verified(channel)` and `mark_identity_mismatch(channel, old_account, new_account)` APIs
- Modify: `tests/test_channel_status_store.py` (Plan 001 test file)
- Create: `scripts/medium_bind_spike.py` (throwaway)

**Approach:**
- Schema extension: each channel record gains `last_verified_at: ISO | None` (default `None`) and an extended `status` enum: `unbound | bound | expired | identity_mismatch`. `mark_bound` initializes `last_verified_at = None`; `mark_verified(channel)` sets to `now()`; `mark_identity_mismatch` writes a transient state with `old_account` + `new_account` fields. `reconcile_on_load` doesn't change behavior for the new state (operator decides via Settings UI).
- Coordination note: this is a cross-plan edit to Plan 001 Unit 1's already-merged file. Open PR against `feat/settings-browser-binding`; merge with Plan 001 Unit 4 timing if convenient.
- Spike 3a: log in to Medium in dev Chromium (operator's existing profile), capture HttpOnly cookies on `medium.com` apex with `expires > now + 7 days`. Output: 1-3 cookie names → Unit 1's `MEDIUM_AUTH_COOKIE_WHITELIST`.
- Spike 2: headless `goto('https://medium.com/me')` × 10, 5-min interval. Output: boolean → Unit 5's `MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED` default.
- Spike 7: log out, click Google SSO → walk through 2FA. Attach `framenavigated` listener. Output: boolean → Unit 1's timeout strategy (idle-detection if reliable, wall-clock-only fallback otherwise).

**Test scenarios:**
- Schema migration: existing records with no `last_verified_at` field load with `None` default
- `mark_verified('medium')` updates only `last_verified_at`; leaves `status`, `bound_at`, `storage_state_path` untouched
- `mark_identity_mismatch` flips state and records old/new accounts
- `reconcile_on_load` ignores `identity_mismatch` state (no demote)
- Spike outputs: cookie names list non-empty, anti-bot decision documented, framenavigated reliability documented in commit message

**Verification:**
- All 4 new schema tests pass
- Existing Plan 001 Unit 1 tests still pass
- Three spike outputs land as constants:
  - `MEDIUM_AUTH_COOKIE_WHITELIST: frozenset[str]` in `src/backlink_publisher/cli/_bind/recipes/medium.py`
  - `MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED: bool` in `webui_app/medium_liveness.py`
  - framenavigated reliability documented in Unit 1 commit message

**Execution note:** Spikes are estimated ~45 min IF Cloudflare is benign and Google 2FA works first try; budget half a day if not. Schema extension is ~30 min.

---

- [ ] **Unit 1: Medium recipe hardening**

**Goal:** Replace `_medium_bound_predicate(page)` with URL + cookie sanity + identity-mismatch + idle-detection. Add `IdentityMismatch` exception + driver `except` arm + CLI exit code 4 + new RECON event `channel.bind.identity_mismatch`.

**Requirements:** R5, R6, R7

**Dependencies:** Unit 0 (cookie whitelist, framenavigated reliability, schema with `identity_mismatch` state)

**Files:**
- Modify: `src/backlink_publisher/cli/_bind/recipes/medium.py` (currently 45 lines → ~120 lines)
- Modify: `src/backlink_publisher/cli/_bind/driver.py` — add `IdentityMismatch(RuntimeError)` class near top with `old_account`, `new_account` attributes; add `except IdentityMismatch as exc:` arm in `run_bind` before persist; map to `BindResult(success=False, error_code="identity_mismatch")`
- Modify: `src/backlink_publisher/cli/bind_channel.py` — map `error_code="identity_mismatch"` → exit code 4 (existing exit-code logic is loose; refactor to a small dict if not already one)
- Modify: `src/backlink_publisher/cli/_bind/channels/__init__.py` — extend `EVENTS` with `"channel.bind.identity_mismatch"`
- Test: `tests/test_bind_channel_recipes.py` (extend)
- Test: `tests/test_bind_channel_driver.py` (extend with IdentityMismatch arm test)
- Test: `tests/test_bind_channel_cli.py` (extend with exit-code-4 test)

**Approach:**
- Predicate body:
  1. `idle_timer = IdleTimer(idle_seconds=90, absolute_seconds=1200)`
  2. `page.on('framenavigated', lambda _: idle_timer.reset())`
  3. Loop: `try: page.wait_for_url(_BOUND_URL_PATTERN, timeout=1000); except PWTimeoutError: if idle_timer.expired(): raise BoundPredicateTimeout(); continue`
  4. On URL match: `cookies = page.context.cookies(domain='medium.com')`; if `cookie_sanity_passes(cookies)` is False → continue loop (URL fluke)
  5. Scrape `@username` from DOM (`[data-testid="headerUserIcon"]` parent's `href`, fall back to `og:url`, fall back to URL parse)
  6. Read `<config_dir>/medium-last-account.txt` (text). If absent → first-bind; if present and != current → raise `IdentityMismatch(old=last, new=current)`
  7. Write `<config_dir>/medium-last-account.tentative` (atomic via `tempfile.mkstemp`, mode 0600)
  8. Return — predicate success
- Driver coordination: `run_bind` after `_persist_storage_state` success, BEFORE `mark_bound`: `os.replace(tentative_path → final_path)`. If rename fails, the storage_state is already persisted but `last_account` is stale (one-bind-cycle stale, harmless — next bind re-checks).
- `cookie_sanity_passes(cookies)`:
  - any cookie in `MEDIUM_AUTH_COOKIE_WHITELIST` (Unit 0 spike output) → True
  - else: any `httpOnly=True AND expires - now > 7 days AND name NOT in MEDIUM_ANONYMOUS_TRACKING_NAMES` (`{"uid", "lightstep_guid", "lightstep_*", "_ga*", "optimizely*"}`) → True
  - else → False
- Fallback if Unit 0 Spike 7 shows framenavigated unreliable: replace the loop with `page.wait_for_url(_BOUND_URL_PATTERN, timeout=1_200_000)` (20-min wall, no idle detection). Use `default_timeout=None` at context level so Playwright doesn't fire its own 5-min default. Decision documented in commit message.

**Test scenarios:**
- Happy: URL match + whitelisted cookie + first-bind (no last_account) → predicate returns; driver writes `medium-last-account.txt`
- Happy: URL match + cookie matches structural fallback (HttpOnly, long expires, not in tracking list) → success
- Edge: URL match but no auth cookie → loop continues
- Edge: URL match + only anonymous-tracking cookies (uid, _ga) → loop continues
- Edge: existing `last_account=user1`, scrape `@user1` → predicate returns
- Edge: existing `last_account=user1`, scrape `@USER1` (case differ) → normalize lowercase → returns
- Edge: DOM scrape returns None, URL is `/@user1` → fall back to URL parse → success
- Error: existing `last_account=user1`, scrape `@user2` → `IdentityMismatch(old="user1", new="user2")` raised; driver returns `BindResult(error_code="identity_mismatch")`; CLI exits 4; `channel.bind.identity_mismatch` event emitted (after `failed` event ordering)
- Error: 91s no framenavigated → idle timer fires → `BoundPredicateTimeout` raised → driver returns existing `bound_predicate_timeout` arm
- Error: 1201s absolute wall, frames kept arriving → `BoundPredicateTimeout` raised
- Regression: 29 existing recipe tests pass (apex cookie filter, negative URL match, etc.)
- Integration: full predicate run via fake `BrowserRunner` confirms event emission order (`browser_ready` → `login_detected` → `persisted`) or (`browser_ready` → `failed` on identity mismatch)

**Verification:**
- 11 new test scenarios pass
- Plan 001's 29 recipe + 20 driver + 7 CLI tests still pass
- EVENTS frozenset contains `"channel.bind.identity_mismatch"`
- Exit code 4 returned only on IdentityMismatch

**Execution note:** Test-first.

---

- [ ] **Unit 3: Webui security helpers — Origin + ALLOW_NETWORK refuse**

**Goal:** Ship two helpers in `webui_app/helpers.py` for Plan 001 Unit 4's bind routes to consume.

**Requirements:** R14, R15

**Dependencies:** None.

**Files:**
- Modify: `webui_app/helpers.py` (near line 546)
- Test: `tests/test_webui_bind_security.py` (new)

**Approach:**
- `_check_bind_origin_or_abort()`:
  - `origin = request.headers.get('Origin')`
  - If `origin == 'null'`: abort 403 (`file://`, sandboxed iframe)
  - If origin present: parse with `urllib.parse.urlparse`; host case-insensitively in `{127.0.0.1, localhost, [::1]}`; port equals `_FLASK_PORT`; scheme is `http`
  - If origin absent + Referer present + allowlisted → pass (some legitimate browsers strip Origin)
  - If both absent → 403
- `_refuse_when_allow_network()`:
  - `if os.environ.get('BACKLINK_PUBLISHER_ALLOW_NETWORK') == '1': abort(403, json={"error": "bind_disabled_under_allow_network"})`

**Test scenarios:**
- Happy: Origin `http://127.0.0.1:8888` → pass
- Happy: Origin `http://localhost:8888` → pass
- Happy: Origin `http://[::1]:8888` → pass
- Happy: Origin absent + Referer same-host → pass
- Edge: Origin `null` → 403
- Edge: Origin `http://evil.com:8888` → 403
- Edge: Origin + Referer mismatch → 403
- Edge: both absent → 403
- Edge: `BACKLINK_PUBLISHER_ALLOW_NETWORK=1` → 403
- Edge: `=0` → not refused

**Verification:**
- 10 scenarios pass
- Helpers callable from blueprint routes; idiomatic match to `_check_csrf_or_abort`
- Plan 001 Unit 4 wiring: documented in PR review comment; no CI gate

---

- [ ] **Unit 4: Settings UI integration with channel_status_store**

**Goal:** Render `channel_status_store["medium"]` state in `_settings_channel_medium.html` with state-aware CTA.

**Requirements:** R1

**Dependencies:** Unit 0 (schema), Unit 1 (identity_mismatch state writer), Unit 5 (`_get_medium_status` helper that wraps liveness probe).

**Files:**
- Modify: `webui_app/templates/_settings_channel_medium.html` (Phase A version)
- Modify: `webui_app/helpers.py:_settings_context()` — inject `medium_status`

**Approach:**
- Template badge ladder, 4 visible states (per design-lens recommendation; collapse `bind_in_progress`/`needs_recheck`/`lock_busy` into `needs_attention`):
  - `never_bound` → red badge "未绑定" + 「立即绑定 Medium」CTA POST to `/channels/medium/bind` (Plan 001 Unit 4 route)
  - `bound` → green badge "已绑定 @username" + secondary 「重新登录」 link
  - `expired` → red badge "会话已过期" + 「重新登录」 CTA
  - `identity_mismatch` → yellow card "检测到登录账号变更：@old → @new" + 「保留旧账号」 / 「替换为新账号」 buttons (both CSRF'd, distinct POST targets — coordinate with Plan 001 Unit 5)
- CSRF token via existing `{{ csrf_token() }}` Jinja helper
- A11y: status badge wrapped in `<span role="status" aria-live="polite">`; identity-mismatch buttons in `<form>` with default button = "保留旧账号" (non-destructive default); Tab order: keep → replace
- Integration Token `<details>` block (Phase A) unchanged

**Test scenarios:**
- Each of 4 state branches renders without Jinja errors
- All CSRF forms include `csrf_token`
- `_settings_context()` returns dict with `medium_status` key
- a11y: aria-live region present; non-destructive default verified via DOM order
- Regression: Integration Token block still saves/clears

**Verification:**
- 6 scenarios pass
- Manual: render each state via monkeypatched store; visual check

---

- [ ] **Unit 5: Liveness probe — probe-copy + TTL cache**

**Goal:** `medium_liveness_check(timeout_s=10.0)` for Settings GET. TTL-cached (5 min). Active probe operates on **copy** of storage_state.json — never the live credential.

**Requirements:** R9, R10

**Dependencies:** Unit 0 (`last_verified_at` field + `MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED` constant)

**Files:**
- Create: `webui_app/medium_liveness.py`
- Modify: `webui_app/helpers.py` — `_get_medium_status` calls `medium_liveness_check`
- Test: `tests/test_medium_liveness.py`

**Approach:**
- `medium_liveness_check(timeout_s=10.0) -> LivenessResult`:
  1. `status = get_status('medium')`
  2. If status is `unbound`: return `LivenessResult.UNBOUND`
  3. If status is `expired`: return `LivenessResult.EXPIRED`
  4. If `status.last_verified_at` set AND `(now - last_verified_at) < 5min`: return `LivenessResult.CACHED_BOUND`
  5. If `not MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED` (Spike 2 said challenges fire): return `LivenessResult.NEEDS_RECHECK` (operator sees yellow badge; no false expired)
  6. Active probe:
     - Read live `<config_dir>/medium-storage-state.json` (single retry on `JSONDecodeError`)
     - Hold contents in memory (do NOT write a probe-copy file; in-memory only)
     - `concurrent.futures.ThreadPoolExecutor(max_workers=1).submit(_active_probe, contents).result(timeout=timeout_s)`
     - `_active_probe`: `chromium.launch(headless=True)` + `new_context(storage_state=contents)` (Playwright accepts dict directly via `storage_state` arg) + `page.goto('https://medium.com/me', timeout=8000)` + inspect final URL
     - URL matches `/@*` or `/me/*` → `LivenessResult.LOGGED_IN`; call `mark_verified('medium')`
     - Redirects to `/m/signin` → `LivenessResult.EXPIRED`; call `mark_expired('medium')`
     - Cloudflare/Datadog challenge URL → `LivenessResult.NEEDS_RECHECK`; **no store mutation**
  7. On thread timeout: `LivenessResult.NEEDS_RECHECK`
- Cache invalidation: `mark_expired('medium')` writes `expired` state which short-circuits step 3; successful bind via Plan 001 driver calls `mark_bound` which initializes `last_verified_at = None` (stale-by-default for first probe after rebind, ensuring fresh probe on next Settings GET); `mark_verified` only called on definite `LOGGED_IN` outcomes.

**Test scenarios:**
- Cached: `last_verified_at` 2min ago → `CACHED_BOUND`, no probe
- TTL expired: 10min ago, probe returns `/me` → `LOGGED_IN`, `mark_verified` called
- Probe-disabled (active=False): always `NEEDS_RECHECK` after cache miss; no probe spawn
- Probe timeout: future result exceeds 10s → `NEEDS_RECHECK`
- Probe lands on Cloudflare URL → `NEEDS_RECHECK`, no mutation
- Probe lands on `/m/signin` → `EXPIRED`, `mark_expired` called
- `unbound` short-circuit → no probe
- `expired` short-circuit → no probe
- Playwright `ImportError` → `NEEDS_RECHECK` + log warning
- Two concurrent `_get_medium_status` calls (e.g., two Settings tabs) → both probe with independent in-memory copies; both call `mark_verified` on success (last-writer-wins on timestamp is acceptable, both timestamps would be within ms)
- Race: `storage_state.json` being rewritten by Unit 6 publish during probe read → single retry on `JSONDecodeError`; if still fails → `NEEDS_RECHECK`

**Verification:**
- 11 scenarios pass
- Total round-trip for cached call <50ms
- `grep -n "launch_persistent_context" webui_app/medium_liveness.py` returns 0 hits
- Probe never reads/writes a copy file (in-memory only)

---

- [ ] **Unit 6: Adapter convergence + AuthExpiredError + mark_expired + legacy notice**

**Goal:** Rewrite `MediumBrowserAdapter.publish` to use `new_context(storage_state=...)` instead of `launch_persistent_context`. On `/m/signin` redirect, `mark_expired('medium')` then raise `AuthExpiredError(channel='medium')`. Log one-time legacy-dir deprecation notice.

**Requirements:** R4 (convergence), R12, R13

**Dependencies:** Plan 001 Unit 1 merged.

**Files:**
- Modify: `src/backlink_publisher/publishing/adapters/medium_browser.py` (significant rewrite, lines 67-87 and surrounding publish flow)
- Modify: `src/backlink_publisher/cli/publish_backlinks.py` — `except AuthExpiredError:` arm at dispatch level (if Plan 001 didn't add it)
- Modify: `CHANGELOG.md`
- Test: `tests/test_adapter_medium_browser.py` (extend)
- Test: `tests/test_publish_backlinks_auth_expired.py` (new — dispatch chain with full 4-mock)

**Approach:**
- `MediumBrowserAdapter.publish` outline:
  1. Read `storage_state_path = <config_dir>/medium-storage-state.json`. If absent → `mark_expired('medium')` + raise `AuthExpiredError(channel='medium', message='never bound')` immediately
  2. `with sync_playwright() as pw:` `browser = pw.chromium.launch(headless=False, args=[...])` (NOT persistent)
  3. `context = browser.new_context(storage_state=storage_state_path)` (Playwright loads from path)
  4. Publish flow proceeds (open page, paste content, draft/publish, etc. — existing logic, unchanged)
  5. After flow: if `sel.LOGIN_PATH in page.url`:
     ```
     try: mark_expired('medium')
     except Exception as exc: log.warning(...)  # Don't mask the auth error
     raise AuthExpiredError(channel='medium', message='session expired during publish')
     ```
  6. On success: `context.storage_state(path=<tmp_via_mkstemp>)`; `os.chmod(0o600)`; `os.replace` to `storage_state_path`. Atomic refresh of cookies (Medium rotates session cookies; refreshing keeps them fresh).
- Remove `user_data_dir` parameter resolution from this adapter. The `Config.medium_user_data_dir` field becomes vestigial (still parsed for backward-compat); see legacy notice below.
- Legacy notice (collapse v2's Unit 2.5 into here): in `MediumBrowserAdapter.__init__`, if `~/.config/backlink-publisher/chrome-profile-default/` exists, log once via `opencli_logger.info("Legacy Chromium profile dir at <path> is unused by Medium adapter as of <date>. Safe to delete after verifying Settings page shows Medium bound. Suppress this notice by setting BACKLINK_PUBLISHER_MEDIUM_LEGACY_NOTICE=0.")`. Use a module-level `_LEGACY_NOTICE_LOGGED` flag for per-process idempotency (no marker file).

**Test scenarios:**
- Happy: storage_state.json present + publish succeeds + no `/m/signin` redirect → success; storage refreshed (atomic)
- Happy: existing 0o600 mode preserved after refresh
- Edge: storage_state.json absent → `mark_expired` + `AuthExpiredError`, raised before Playwright launches
- Error: page lands on `sel.LOGIN_PATH` → `mark_expired` called inside try/except; `AuthExpiredError` raised
- Error: `mark_expired` raises OSError → warning logged; `AuthExpiredError` still raised (the auth error wins)
- Error: `new_context(storage_state=path)` fails on corrupt JSON → existing `ExternalServiceError`-class error preserved (do not wrap)
- Integration (dispatch chain): patch `MediumAPIAdapter.publish` with `side_effect=DependencyError(...)`, `MediumBraveAdapter.publish` with `side_effect=DependencyError(...)`, `MediumBrowserAdapter.publish` with `side_effect=AuthExpiredError(channel='medium')`, `time.sleep` no-op. Run `publish-backlinks --mode draft --channel medium`. Expect: CLI exit 3, stderr contains `AuthExpiredError`, `channel_status_store["medium"]["status"] == "expired"`
- Integration (adapter-isolation): patch `sync_playwright`; assert `chromium.launch(headless=False, ...)` called (NOT `launch_persistent_context`); assert `new_context(storage_state=<path>)` called; assert `mark_expired` called on `/m/signin` redirect
- Legacy notice: legacy dir exists → notice logged once per process; legacy dir absent → no notice; env `BACKLINK_PUBLISHER_MEDIUM_LEGACY_NOTICE=0` → no notice
- Regression: existing tests asserting `launch_persistent_context` updated to assert `chromium.launch + new_context(storage_state)`
- Regression: existing tests pinning on `ExternalServiceError("Medium login expired...")` updated to `AuthExpiredError`

**Verification:**
- 11 scenarios pass
- `grep -n launch_persistent_context src/backlink_publisher/publishing/adapters/medium_browser.py` → 0 hits
- `grep -n 'new_context.*storage_state' src/backlink_publisher/publishing/adapters/medium_browser.py` → ≥1 hit
- `mark_expired('medium')` called inside try/except in the `/m/signin` redirect branch
- CHANGELOG entry under "Unreleased > Changed"

---

## System-Wide Impact

- **Storage authority**: `<config_dir>/medium-storage-state.json` is the single persistent Medium credential. Writers: bind driver (Plan 001) + publish adapter (Unit 6 refresh on success). Readers: publish adapter + Unit 5 probe (reads in-memory copy, never writes).
- **State authority**: `channel_status_store["medium"]` is the single state source. Schema after Unit 0: `{status, bound_at, storage_state_path, last_verified_at, ...optional identity_mismatch fields}`. Writers: bind driver (`mark_bound`), publish adapter (`mark_expired`), Unit 5 probe (`mark_verified` on definite `LOGGED_IN`; `mark_expired` on definite signin redirect). Readers: webui Settings GET, Plan 001 `reconcile_on_load`.
- **Last-account file**: `<config_dir>/medium-last-account.txt`. Single writer (Plan 001 driver via Unit 1's recipe → `.tentative` → atomic rename). Single reader (Unit 1's predicate on next bind).
- **No cross-process lock**: probe-copy + non-persistent Playwright + atomic file writes obviate the need.
- **Error propagation**: `AuthExpiredError` (Plan 001) raised by adapter → caught at `publish_backlinks` dispatch → CLI exit 3. `IdentityMismatch` raised by predicate → caught by driver's new `except` arm → `BindResult(error_code="identity_mismatch")` → CLI exit 4. `BoundPredicateTimeout` (existing) for idle/wall timeout → exit 3 (existing).
- **API surface parity**: 3 Medium adapters unchanged (API/Brave/Browser); only Browser converges to storage_state model. velog/blogger recipes untouched (separate follow-up plans if same hardening desired).
- **Unchanged invariants**:
  - Plan 001 driver, recipe contract (`ChannelRecipe` frozen dataclass), `_validate_storage_state_path`, `_persist_storage_state`, EVENTS values — all preserved
  - storage_state.json location (`_config_dir()`) — unchanged
  - Phase A's UI Integration Token `<details>` block — unchanged
  - `_check_csrf_or_abort`, `_check_localhost` semantics — unchanged
  - Integration Token adapter (`medium_api`) — unchanged

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Plan 001 Unit 1+2 not merged to main before work starts | Branch this plan off `feat/settings-browser-binding`; merge together (single branch into main) |
| Plan 001 Unit 4 lands with conflicting webui-route shape | Helpers ship in Unit 3 standalone; Plan 001 Unit 4 author adopts in PR review. No CI gate. |
| Unit 0 spike outputs ambiguous (Cloudflare unstable, framenavigated unreliable) | Conservative defaults: `MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED = False` (Unit 5 reports `NEEDS_RECHECK` instead of probing); Unit 1 falls back to 20-min wall-clock predicate. v1 still ships; spike results unlock v1.1 optimizations. |
| Schema extension to `channel_status_store` requires Plan 001 author coordination | Unit 0 ships the schema change in this plan with explicit PR comment + courtesy ping. Plan 001 Unit 1 already merged; extending it on the same branch before main-merge is low-risk. |
| Medium HttpOnly cookie name changes server-side | Unit 1 sanity uses whitelist OR structural fallback (HttpOnly + expires + not in tracking list). Resilient. |
| Anti-bot poisons probe-copy session | Probe-copy isolation: even if probe is challenged, live credential untouched. Worst case: probe reports `NEEDS_RECHECK` repeatedly → operator sees yellow badge → re-binds. |
| Existing `bp-medium-auth` branch (commits `7868656` Phase A + `28d3d06` v1 Phase B) | Phase A UI kept (cherry-pick `7868656`). v1 Phase B (`28d3d06`) dropped — superseded by Plan 001 framework. |
| Plan numbering | `2026-05-19-001` (settings-browser-binding) + `-002` (telegraph) taken; this is `-003`. |

## Documentation / Operational Notes

- **CHANGELOG.md** "Unreleased > Changed":
  - "Medium adapter now uses `<config_dir>/medium-storage-state.json` (managed by `bind-channel medium` CLI) as its credential source. Previously read from Chromium profile at `~/.config/backlink-publisher/chrome-profile-default/`. Existing operators: rebind once via the webui Settings page after upgrade."
  - "WebUI bind endpoints disabled when `BACKLINK_PUBLISHER_ALLOW_NETWORK=1`. Bind requires loopback-only access in v1."
- **AGENTS.md** (project root, channel-binding section after Plan 001 lands):
  - "Browser-binding security: `/channels/<channel>/bind*` routes adopt `_check_localhost` + `_check_bind_origin_or_abort` + `_check_csrf_or_abort` + `_refuse_when_allow_network`. Disabled under `ALLOW_NETWORK=1`."
- **No runbook / monitoring** — single-operator local tool.
- **Rollout** — no feature flag. Profile-dir notice + CHANGELOG cover operator-visible change.

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-19-medium-browser-bind-flow-requirements.md](../brainstorms/2026-05-19-medium-browser-bind-flow-requirements.md) (revision 3)
- **Upstream plan:** [docs/plans/2026-05-19-001-feat-settings-browser-binding-plan.md](2026-05-19-001-feat-settings-browser-binding-plan.md) (Units 1+2+3 merged on `feat/settings-browser-binding`: `8fbf0b3`/`3f37c18`/`f773758`)
- **Phase A (merged on `fix/medium-auth-ui-redesign`):** commit `7868656` — UI restructure (cherry-pick onto new branch)
- **Phase B v1 (superseded, on `bp-medium-auth`):** commit `28d3d06` — dropped
- **Plan 001 source (read 2026-05-19):**
  - `cli/_bind/channels/__init__.py` (CHANNELS + EVENTS frozensets)
  - `cli/_bind/driver.py` (exceptions, BindResult, run_bind, _PlaywrightBrowserRunner)
  - `cli/_bind/recipes/medium.py` (negative-match predicate + apex filter)
  - `webui_store/channel_status.py` (mark_bound / mark_expired / get_status / reconcile_on_load)
- **Adapter to converge:** `src/backlink_publisher/publishing/adapters/medium_browser.py`
- **Webui CSRF idiom:** `webui_app/helpers.py:534-546`
- **Institutional learnings:** see Context & Research
