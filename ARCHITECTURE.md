# backlink-publisher Architecture

A local-first backlink publishing pipeline with WebUI operations dashboard.

## Layer Map

```
┌──────────────────────────────────────────────────────────┐
│                      CLI Layer (27 entrypoints)          │
│  plan-backlinks | validate-backlinks | publish-backlinks │
│  → JSONL stdout, stderr diagnostics, exit code 0-6      │
├──────────────────────────────────────────────────────────┤
│                    Service Layer                          │
│  config/  content/  anchor/  events/  geo/  linkcheck/   │
│  ledger/  scorecard/  gates/  recheck/                   │
├──────────────────────────────────────────────────────────┤
│                  Publishing Adapters (30+ platforms)      │
│  blogging: blogger/medium/velog/telegraph/ghpages/devto  │
│  social:   mastodon/twitter/linkedin/threads             │
│  others:   notion/wordpresscom/livejournal/tistory/       │
├──────────────────────────────────────────────────────────┤
│                    WebUI (Flask Dashboard)                │
│  routes/  services/  webui_store/  templates/  static/   │
│  → operational UI, no public exposure                    │
├──────────────────────────────────────────────────────────┤
│                    Events System                          │
│  store.py (SQLite) → projector → reducers → query        │
├──────────────────────────────────────────────────────────┤
│                    MCP Server                             │
│  server.py — multi-agent orchestration protocol          │
└──────────────────────────────────────────────────────────┘
```

## Data Flow

### Primary Pipeline (deterministic → non-deterministic)

```
Seed JSONL → plan-backlinks → validate-backlinks → publish-backlinks
  stdin         pure logic       enrich + check         platform adapters
                (deterministic)  (deterministic)        (non-deterministic, network)
```

1. **plan-backlinks**: Reads seed JSONL (target URL, keywords, language). Generates article content deterministically. Output: enriched seed JSONL with article body, title, banner.
2. **validate-backlinks**: Validates content, checks target URLs, enriches metadata. Exits 2 on validation failure.
3. **publish-backlinks**: Dispatches to registered platform adapters. Retry logic, auth expiry handling, checkpoint/resume. Exit 0 on success, 3 on auth expired, 4 on service error.

### Read-Only Advisory Tools

Many CLIs are read-only (exit 0 always):
- `canary-targets` — re-fetch seeded posts, verify dofollow still alive
- `gate-probe` — falsification gates for ideation
- `channel-scorecard` — per-channel quality assessment
- `equity-ledger` — per-target backlink scorecard
- `audit-state` — dual-state divergence auditor

### Events Flow

```
Publish → EventStore.append() → projector → _project_reducers → queries
             (SQLite, WAL mode)    (state machine)   (aggregate)
```

Events are append-only, idempotent (dedup keyed by event hash). The projector reads events and updates read-side state via reducers. events.db is recoverable from JSON store.

## Key Architectural Decisions

### 1. Zero-Build Frontend (Plan 2026-06-01-007)
- Native ES modules, no bundler/framework
- Bootstrap 5 + Icons via CDN (non-defer in `<head>`)
- Server→client data via `window.__pageBootstrap = {{ ... | tojson }}`
- Cross-module signals via DOM `CustomEvent`, never `window.*` globals
- CSS custom properties via `tokens.css` (19 design tokens, 94+ var() references)

### 2. Deterministic Planning Principle
`plan-backlinks` is pure/deterministic (no network, no random). This makes it testable and reproducible. Publishing is inherently non-deterministic (platform APIs, rate limits, auth). The architecture boundary between these phases is enforced at the code level.

### 3. Adapter Registry (R9 Extension Readiness)
```python
from backlink_publisher.publishing.registry import register, Publisher

register("platform", PlatformAdapter, dofollow=True, ui=UiMeta(...), bind=[...])
```
- Adding a platform = one `register()` call + adapter class
- No edits to CLI, schema, or dispatch code
- Dofollow gate enforced at import time
- Manifest metadata (UiMeta, BindDescriptor, Policy) provides SSoT for WebUI wiring

### 4. Config System (Save-Preserve Taxonomy)
- Hierarchical TOML at `~/.config/backlink-publisher/config.toml`
- `save_config` has 5-class taxonomy: (a) emitted every call, (b) conditional, (c) depth-2 preserved, (d) unmanaged preserved, (e) placeholder
- Credential lifecycle: managed subsections persist on save, propagate to `.config-history/` (cap 20)
- Medium sidecar fallback for operators who haven't migrated

### 5. Monolith Budget & Plan Claims
- 14 tracked files with radon SLOC ceilings (enforced by `test_no_monolith_regrowth.py`)
- Plan claims system: YAML frontmatter `claims:` block → CI gate + overnight radar
- Cutoff: 2026-05-20, post-cutoff must have claims block

### 6. Error Taxonomy
```
PipelineError (exit 5)
├── UsageError (exit 1)
├── InputValidationError (exit 2)
├── DependencyError (exit 3)
│   ├── AuthExpiredError
│   ├── BannerUploadError
│   └── ContentRejectedError
├── ExternalServiceError (exit 4)
│   └── AntiBotChallengeError
├── RegistryError (exit 5)
└── InternalError (exit 5)
```

## State Management

### WebUI Stores (Module-Level Singletons)
- 8 store types: history, profiles, drafts, schedule, queue, channel_status, score, seen_urls
- Backed by `JsonStore` (JSON files on disk)
- `_LazyStore` wrapper defers initialization until first access
- `WebUIStores.unload_all()` clears cache; periodic 30-min APScheduler cleanup

### Events Store (SQLite)
- `events.db` with WAL mode, `synchronous=NORMAL`, `busy_timeout=5000`
- Append-only, idempotent (dedup by event hash)
- Recoverable from JSON store via `bp-events-rebuild`

### Channel Status
- `channel-status.json` tracks per-channel bound/expired/running states
- `mark_bound()` / `mark_expired()` called by binding CLI and publish dispatch
- `reconcile_on_load()` checks storage_state file existence at WebUI startup

## Publishing Path

### Auth flow
```
bind-channel --channel <name> → Playwright headed browser → login
  → storage_state.json (0600) → mark_bound()

Publish → adapter.publish() → 401/403? → AuthExpiredError
  → mark_expired() → exit 3 → operator re-binds via WebUI
```

### Adapter dispatch
```
dispatch(payload, mode, config):
  1. Check registered_platforms()
  2. Try each adapter in priority order
  3. Catch DependencyError → fall through to next adapter
  4. Catch ExternalServiceError → propagate immediately
  5. On success → checkpoint + next row
  6. On AuthExpiredError → mark_expired + exit 3
```

## Test Infrastructure

- 410 test files, ~6,270 test functions
- 4 autouse conftest fixtures: isolated config dir, URL checks mocked, content fetch mocked, sockets blocked
- Custom markers: `real_ssrf_check`, `real_content_fetch`, `real_image_gen`, `real_browser_publish_smoke`
- `PYTHONHASHSEED=0` required (enforced by pytest-env)
- JS tests via `node:test` (13 tests for `lib/dom.js esc()`)
- No browser E2E tests yet (deferred)

## Key Constraints

| Constraint | Rationale |
|---|---|
| No JS build step | Double-click → run. No Node/bundler dependency for deployment |
| No inline on* handlers | Delegated `data-action` pattern for module-scope safety |
| No window.* globals as API | ESM modules import from `lib/`, signals via CustomEvent |
| Bootstrap non-defer in `<head>` | Guarantees `window.bootstrap` before any module executes |
| CSRF global guard | All POST/PUT/PATCH/DELETE require `X-CSRFToken` or form field |
| CSRF_ENABLED=False in tests | Tests opt out explicitly |
| Network mocked by default | 4 autouse fixtures prevent accidental live calls |
| Pipeable CLI contract | stdout=JSONL, stderr=diagnostics, exit code 0-6 |
| No `choices=` in argparse | Use post-parse validation → `UsageError` (exit 1), not argparse's exit 2 |

## Related Documents

- `AGENTS.md` — canonical project governance, conventions, commands
- `docs/architecture/deterministic-planning-principle.md` — architecture boundary
- `docs/architecture/io-budgets.md` — I/O budget documentation
- `docs/architecture/events-db-scale-tripwire-register.md` — events DB scale limits
- `docs/optimization-audit-2026-06.md` — comprehensive system audit

*Maintained as architecture SSoT. Update when layers, data flow, or key decisions change.*
