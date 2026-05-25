---
title: "feat: Real Chrome channel binding backend"
type: feat
status: completed
date: 2026-05-20
completed: 2026-05-20
claims: {}  # opt-out: plan dated on cutoff (2026-05-20); paths/SHAs land
            # incrementally across multiple PRs. The first PR introduces
            # chrome_backend.py + its test; downstream PRs add the WebUI
            # integration and recipe migrations. Re-tighten the claim block
            # once the plan reaches `status: complete`.
---

# feat: Real Chrome channel binding backend

## Overview

Replace Playwright-headed Chromium as the default channel-binding path with a
Real Chrome / DevTools Protocol backend. Playwright remains available as a
fallback, but the operator-facing default becomes a persistent, user-like
Chrome profile that can survive Cloudflare, social OAuth, and 2FA without
forcing repeated login loops.

The implementation is intentionally scoped to the binding layer. Publishing
adapters continue using their existing credential artifacts:

- `velog` publishes through `velog-cookies.json`.
- `medium` publishes through API token / Brave / `medium-cookies.json`.
- `blogger` continues to prefer official OAuth/API credentials.
- `ghpages`, `writeas`, and `hashnode` remain token-paste channels and do not
  enter `bind-channel`.

## Problem Frame

The current browser binding path in `cli/_bind/driver.py` launches Playwright's
Chromium with a persistent profile. That helps compared with fresh ephemeral
contexts, but it is still Playwright-controlled Chromium. Medium and Velog
flows can still trip Cloudflare / anti-bot checks because the browser identity
and automation surface do not match the operator's real day-to-day browser.

The product requirement is not "bypass Cloudflare automatically." The product
requirement is: when a legitimate operator must pass Cloudflare, OAuth account
selection, or 2FA, the browser should be a real Chrome session and the system
should wait for the operator to complete the challenge, then persist only the
allowed session material needed for later publishing.

Old decision to reverse:

- `docs/brainstorms/2026-05-19-settings-browser-binding-requirements.md`
  explicitly scoped out existing Chrome / remote-debugging attach mode.
- That was reasonable before live Cloudflare friction. It is no longer the
  right default for real external-link publishing.

## Requirements Trace

- **R1.** `bind-channel` supports `--backend auto|chrome|playwright`, default
  `auto`.
- **R2.** `auto` attempts Real Chrome binding first and falls back to
  Playwright only when Chrome backend prerequisites are unavailable before any
  login page is opened.
- **R3.** Real Chrome binding uses a persistent dedicated Chrome profile by
  default, not a fresh temporary profile.
- **R4.** Operators can override the Real Chrome profile path and debugging
  port through environment variables for recovery and testing.
- **R5.** Real Chrome binding never stores full cross-site browser state. It
  exports only host-filtered cookies / storage state through existing recipe
  filters.
- **R6.** Cloudflare / OAuth / 2FA pages are treated as operator work, not as
  automation failures. The binder waits until the recipe's logged-in predicate
  passes or the timeout expires.
- **R7.** The persisted credential artifacts and `channel-status.json` semantics
  stay compatible with existing adapters and WebUI rendering.
- **R8.** WebUI binding jobs carry the selected backend, display Chrome as the
  recommended path, and surface clear backend-specific failure messages.
- **R9.** `ghpages`, `writeas`, and `hashnode` are explicitly documented as
  token-paste channels, not `bind-channel` browser channels.
- **R10.** Tests mock CDP/Chrome process surfaces. CI does not need a running
  Chrome instance.

## Scope Boundaries

**In scope:**

- Browser backend abstraction inside the binding driver.
- Real Chrome backend implemented through Chrome DevTools Protocol.
- CLI flag + WebUI backend selection.
- Velog and Medium binding through the new backend.
- Blogger compatibility through the same backend abstraction, without changing
  Blogger publish semantics.
- Documentation cleanup for browser-binding vs token-paste channels.

**Out of scope:**

- Solving or bypassing Cloudflare without operator action.
- Reading passwords, saved browser secrets, or arbitrary Chrome profile data.
- Automating Google/GitHub login forms.
- Migrating publishing adapters to a new credential schema.
- Adding browser binding for `ghpages`, `writeas`, or `hashnode`.
- Multi-account rotation, proxy rotation, or anti-fraud evasion.
- Running this on headless VPS / Docker-only environments.

## Key Technical Decisions

- **D1. Real Chrome is the default, Playwright is the fallback.** The stable
  production goal is fewer repeat logins and fewer anti-bot dead ends. Using
  Playwright as the first path recreates the current failure mode.
- **D2. Use a dedicated persistent Chrome profile, not the operator's personal
  default profile.** This preserves the important property of a real Chrome
  browser while avoiding broad reads of a personal profile. Default:
  `<config_dir>/real-chrome-profile`.
- **D3. Backend selection happens before browser launch.** If Chrome is missing,
  already bound to another debug session, or cannot expose CDP, `auto` falls
  back to Playwright before opening login URLs. If the login page is already
  opened in Real Chrome, no fallback happens silently; the operator should see
  a clear Chrome-backend failure.
- **D4. Recipes remain the authority for "bound" detection and host filtering.**
  The backend launches / connects / exports. Recipe code decides whether a
  channel is logged in and which hosts may persist.
- **D5. Persisted artifact compatibility beats perfect abstraction.** Medium's
  `post_persist` conversion to `medium-cookies.json` and Velog's cookies file
  shape should continue to work so publishing adapters do not need a same-PR
  rewrite.
- **D6. Do not depend on Codex's Chrome plugin at runtime.** Codex's Chrome
  tooling is useful for development and manual verification, but this project
  needs a standalone CLI/WebUI feature. The product implementation uses local
  Chrome + CDP directly.

## Proposed Architecture

```
bind-channel --channel velog --backend auto
        │
        ▼
BackendResolver
        ├─ chrome available? yes ─▶ RealChromeBrowserRunner
        │                             ├─ launch/connect dedicated Chrome profile
        │                             ├─ open recipe.login_url
        │                             ├─ wait for recipe.bound_predicate
        │                             └─ export filtered state
        │
        └─ otherwise ───────────▶ PlaywrightBrowserRunner
                                      └─ current implementation
        │
        ▼
_persist_storage_state(...)
        │
        ├─ recipe.post_persist(...) for medium
        ▼
mark_bound(channel, canonical_path)
```

### New Public CLI Shape

```bash
bind-channel --channel velog --backend auto
bind-channel --channel medium --backend chrome
bind-channel --channel blogger --backend playwright
```

Environment overrides:

```bash
BACKLINK_PUBLISHER_BIND_BACKEND=chrome
BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR=~/.config/backlink-publisher/real-chrome-profile
BACKLINK_PUBLISHER_REAL_CHROME_PORT=9222
BACKLINK_PUBLISHER_REAL_CHROME_BIN=/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome
```

### Real Chrome Backend Contract

The backend implements the existing `BrowserRunner` protocol:

```python
launch_and_wait(
    recipe,
    on_browser_ready,
    on_login_detected,
) -> storage_state_provider
```

Internally:

1. Start Chrome with:
   - `--remote-debugging-port=<port>`
   - `--user-data-dir=<real-chrome-profile>`
   - a visible window
2. Connect to `http://127.0.0.1:<port>/json/version`.
3. Open a new target for `recipe.login_url`.
4. Wrap the target in a page-like adapter sufficient for current recipe
   methods:
   - `wait_for_url(pattern)`
   - `url`
   - `query_selector(...)` for Medium username scrape
   - `context.cookies()` equivalent for cookie sanity checks
   - `evaluate(...)` for metadata capture if required
5. Run `recipe.bound_predicate(page_like)`.
6. Export cookies / origins into Playwright-compatible storage-state shape,
   then apply `_apply_host_filter`.

Implementation can use a small stdlib + websocket client dependency if already
available in the environment, but should prefer minimal dependencies. If a new
dependency is required, it lands in `pyproject.toml` and tests mock it.

## Phased Delivery

### Unit 1: Backend selection and CLI plumbing

**Goal:** Add `--backend`, resolver, result metadata, and tests without adding
real Chrome behavior yet.

**Files:**

- Modify `src/backlink_publisher/cli/bind_channel.py`
- Modify `src/backlink_publisher/cli/_bind/driver.py`
- Modify `webui_app/services/bind_job.py`
- Modify `tests/test_bind_channel_cli.py`
- Modify `tests/test_bind_channel_driver.py`

**Acceptance:**

- `bind-channel --channel velog --backend playwright` uses the existing fake
  runner tests unchanged.
- `--backend unknown` exits as `UsageError`.
- `BACKLINK_PUBLISHER_BIND_BACKEND` is honored when CLI flag is absent.
- JSONL events include `backend` on start / browser_ready / persisted.

### Unit 2: Real Chrome process + CDP connection scaffold

**Goal:** Implement a `RealChromeBrowserRunner` that can launch/connect and
open a visible tab, with all network/process work mocked in tests.

**Files:**

- Create `src/backlink_publisher/cli/_bind/chrome_backend.py`
- Modify `src/backlink_publisher/cli/_bind/driver.py`
- Create `tests/test_bind_channel_chrome_backend.py`

**Acceptance:**

- Missing Chrome binary maps to `error_code="chrome_not_available"`.
- Debug port unavailable maps to `error_code="chrome_cdp_unavailable"`.
- Successful mocked CDP open emits `browser_ready`.
- Profile dir is created under `_config_dir()` by default and mode-checked
  where POSIX allows it.
- No test launches real Chrome.

### Unit 3: Page-like adapter and recipe compatibility

**Goal:** Make current `velog` and `medium` recipes work against the Chrome
backend page adapter without changing their public shape.

**Files:**

- Modify `src/backlink_publisher/cli/_bind/chrome_backend.py`
- Modify `src/backlink_publisher/cli/_bind/recipes/medium.py` only if the
  adapter cannot support an existing method cleanly.
- Modify `tests/test_bind_channel_recipes.py`
- Modify `tests/test_bind_channel_chrome_backend.py`

**Acceptance:**

- Mocked Velog flow: URL leaves `/login`, host-filtered `velog.io` cookies
  persist, prefix/suffix host confusion still rejected.
- Mocked Medium flow: `sid` / `rid` cookie sanity works, username scrape works,
  `medium-meta.json` tentative data still promotes through `post_persist`.
- Cloudflare-like intermediate URLs do not count as failure while timeout has
  not expired.

### Unit 4: WebUI backend selection

**Goal:** Settings UI recommends Real Chrome binding and lets the user choose
Playwright fallback.

**Files:**

- Modify `webui_app/templates/_settings_channel_binding.html`
- Modify `webui_app/routes/bind.py`
- Modify `webui_app/services/bind_job.py`
- Modify `tests/test_webui_bind_routes.py`
- Modify `tests/test_webui_bind_job_service.py`

**Acceptance:**

- POST `/settings/channels/<channel>/bind` accepts `backend=auto|chrome|playwright`.
- Invalid backend returns 400.
- UI renders "真实 Chrome（推荐）" and "Playwright（备用）".
- Poll response includes selected backend.
- Existing loopback + CSRF + identity-mismatch guards still pass.

### Unit 5: Operator docs and channel matrix cleanup

**Goal:** Remove ambiguous instructions that tell operators to browser-bind
token-paste channels.

**Files:**

- Modify `README.md`
- Modify `docs/runbooks/RUNBOOK-2026-05-20-operator-gated.md`
- Optionally create `docs/operations/real-chrome-binding.md`

**Acceptance:**

- Docs state that `bind-channel` supports only `velog`, `medium`, `blogger`.
- Docs state that `ghpages`, `writeas`, `hashnode` use token files / token
  paste and are not Chrome-bound.
- Troubleshooting table includes:
  - Chrome missing
  - debug port unavailable
  - profile locked
  - login timeout
  - Cloudflare waiting for operator action

### Unit 6: Manual smoke / operator gate

**Goal:** Validate the real machine flow before making Chrome default in WebUI.

**Manual script:**

```bash
cd backlink-publisher
PYTHONPATH=src bind-channel --channel velog --backend chrome
PYTHONPATH=src bind-channel --channel velog --backend chrome
PYTHONPATH=src bind-channel --channel medium --backend chrome
```

**Pass criteria:**

- First Velog bind lets operator complete login / Cloudflare once.
- Second Velog bind does not force a full OAuth + 2FA loop.
- Medium bind writes `medium-cookies.json` mode `0600` and `medium-meta.json`.
- Settings page shows backend as Chrome-bound.

## Error Codes

Add backend-specific codes to `BIND_ERROR_MESSAGES`:

- `chrome_not_available`
- `chrome_launch_failed`
- `chrome_cdp_unavailable`
- `chrome_profile_locked`
- `chrome_login_timeout`

Keep existing Playwright codes:

- `playwright_not_installed`
- `playwright_launch_failed`
- `bound_predicate_timeout`
- `persist_io_error`

## Security and Privacy

- Do not read arbitrary Chrome profile databases.
- Do not use the operator's default Chrome profile unless explicitly provided
  through an environment variable.
- Do not persist cookies outside recipe host filters.
- Do not log cookie values, Authorization headers, CSRF tokens, profile paths
  containing usernames beyond existing path-display norms.
- Treat Cloudflare pages as interactive operator screens, not as targets to
  solve programmatically.

## Tests

Minimum test set:

```bash
pytest \
  tests/test_bind_channel_cli.py \
  tests/test_bind_channel_driver.py \
  tests/test_bind_channel_recipes.py \
  tests/test_bind_channel_chrome_backend.py \
  tests/test_webui_bind_routes.py \
  tests/test_webui_bind_job_service.py
```

Before merge:

```bash
pytest tests/
plan-check docs/plans/2026-05-20-007-feat-real-chrome-channel-binding-plan.md
```

## Rollout

1. Ship backend plumbing with `playwright` still selected explicitly in tests.
2. Ship Chrome backend behind `--backend chrome`.
3. Update WebUI to default to `auto`, but show Chrome as recommended.
4. After manual smoke passes on Velog + Medium, update docs to tell operators
   to use Chrome first.
5. If Chrome backend proves unstable on a platform, keep Playwright fallback
   available without reverting the whole feature.

## Open Questions

- Should `auto` fallback to Playwright if Chrome launches but the login flow
  times out? Proposed answer: no. Timeout after visible Chrome means the
  operator was already in an interactive flow; silently switching browsers
  restarts the exact login loop this feature is meant to avoid.
- Should Real Chrome use Chrome.app only or also Brave/Chromium? Proposed
  answer: Chrome.app first. Brave publishing is already a separate Medium
  adapter; binding should keep one default browser surface.
- Should the backend attach to an already-running default Chrome with the
  Codex Chrome Extension? Proposed answer: no for product code. Useful for
  development verification, but not a runtime dependency.

