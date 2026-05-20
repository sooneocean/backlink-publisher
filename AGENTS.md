# AGENTS.md — backlink-publisher

See `README.md` for project overview and `docs/` for plans, brainstorms, ideation, and solutions.

## Dev Commands

```bash
# Install
pip install -e .          # full package
pip install -e .[dev]     # + dev deps (pytest, radon==6.0.1, etc.)

# Test (PYTHONHASHSEED=0 required — set by pytest-env in pyproject.toml)
pytest tests/
pytest tests/test_no_monolith_regrowth.py -k "R4"   # single budget test
pytest tests/scripts/                               # worktree script tests
pytest -m real_ssrf_check                           # live SSRF checks (off by default)
pytest -m real_content_fetch                        # live content fetching (module-wide in test_content_fetch.py)

# Lint (CI uses py_compile + ast.parse, not Black/flake8 — local-only)
black --check src/
flake8 src/ --count --select=E9,F63,F7,F82 --show-source --statistics

# SLOC measurement (for monolith budget edits)
python -m radon raw -s src/backlink_publisher/cli/plan_backlinks.py

# WebUI
python webui.py                                    # start dev server on :8888
```

## Repo Layout

Workspace root (not a git repo) holds `backlink-publisher/` (canonical) and `bp-<topic>/` (`git worktree` checkouts sharing `.git/`). Edit `backlink-publisher/`, never `bp-*/`, unless on that branch.

### WebUI

Flask app at `webui_app/` (12 route modules, `create_app()` factory). State persistence at `webui_store/` (4 module-level singletons). Launcher: `python webui.py`.

### CLI entrypoints (6)

```bash
cat seeds.jsonl | plan-backlinks | validate-backlinks | publish-backlinks --mode draft
```

| Command | Source | Role |
|---|---|---|
| `plan-backlinks` | `cli/plan_backlinks.py` | Generate articles from seed JSONL |
| `validate-backlinks` | `cli/validate_backlinks.py` | Validate + enrich |
| `publish-backlinks` | `cli/publish_backlinks.py` | Publish via platform adapters |
| `report-anchors` | `cli/report_anchors.py` | Post-hoc anchor profile |
| `footprint` | `cli/footprint.py` | Link footprint analysis |
| `phase0-seal` | `cli/phase0_seal.py` | Phase0 seal operations |

### Output contract

stdout = clean JSONL; stderr = diagnostics; exit code 0 on success. No human-readable output.

### Config

`~/.config/backlink-publisher/config.toml` (override via `BACKLINK_PUBLISHER_CONFIG_DIR`). Template: `config.example.toml`.

`save_config` taxonomy (Plan 2026-05-19-010):

- (a) **Emitted every call:** `[blogger]`, `[medium]`, one `[targets."<domain>"]` per resolved domain in the kwargs/Config emit set.
- (b) **Emitted conditionally:** `[blogger.oauth]` only when at least one credential field is non-empty.
- (c) **Depth-2 subsections under managed roots not emitted on this call** (`[medium.oauth]`, `[medium.browser]`, operator-added `[targets.X]` / `[blogger.X]` / `[medium.X]`, dormant `[blogger.oauth]`) — **preserved verbatim**.
- (d) **Unmanaged top-level sections** (`[sites.*]`, `[anchor.*]`, `[anchor_alarm]`, `[llm.*]`, arbitrary operator-added tables) — preserved verbatim when carrying key=value data.
- (e) **Pure-placeholder sections** (header + comments only, no data) — intentionally not preserved.

Note: `merge_site_url_categories` is a second writer that text-edits `[sites."<main>".url_categories]` blocks in place and does not interact with the preservation pass.

**Credential-lifecycle note (post-2026-05-19):** managed-root credential subsections (`[medium.oauth]`, `[blogger.oauth]`) now persist on save and propagate into `.config-history/` rolling snapshots (cap 20). After credential rotation, up to 20 historical copies of revoked secrets remain on disk until aged out. If `BACKLINK_PUBLISHER_CONFIG_DIR` points to synced storage (Dropbox, NFS, dotfiles repo), credentials now propagate through the sync surface — keep the config dir on local-only storage.

**Medium sidecar precedence:** when both `[medium.oauth]` in `config.toml` and the sidecar file from Plan 2026-05-18-013 are populated, `[medium.oauth]` wins. The sidecar continues to provide fallback for operators who haven't migrated.

Note: operator-archival `[targets_meta.<domain>]` blocks are preserved-only — no pipeline code reads them; treat as documentation, not a functional override.

## Import Conventions

Old flat imports (`from backlink_publisher.errors import ...`) still work via `_LegacyPathFinder`. **New code should import from refactored paths:**

| Legacy | New |
|---|---|
| `anchor_lang`, `anchor_metrics`, `anchor_profile`, `anchor_resolver`, `anchor_scheduler` | `anchor.lang`, `.metrics`, `.profile`, `.resolver`, `.scheduler` |
| `content_fetch`, `work_scraper`, `work_themed_generator` | `content.fetch`, `.scraper`, `.themed_gen` |
| `language_check`, `verify_publish` | `linkcheck.language`, `.verify` |
| `errors`, `io_utils`, `jsonl`, `logger`, `markdown_utils`, `url_utils` | `_util.errors`, `.io`, `.jsonl`, `.logger`, `.markdown`, `.url` |
| `adapters.*` | `publishing.adapters.*` |

Full map: `src/backlink_publisher/__init__.py:_REEXPORT_MAP` (17 entries). Most tests, CLI code, and WebUI still use legacy paths — the finder makes it transparent, but don't add new legacy imports.

## Test Patterns

Network is mocked by 4 autouse conftest fixtures (config isolated, URL checks pass, content fetch passes, sockets blocked). Test live paths with:

```bash
pytest -m real_ssrf_check        # exercise real _check_url_for_ssrf
pytest -m real_content_fetch     # exercise real verify_urls_batch (module-wide in test_content_fetch.py)
```

Test fixtures: `fixtures/seed.jsonl` (E2E, at repo root), `tests/fixtures/sloc_canary.py` (radon), `tests/fixtures/footprint_attack/` (HTML samples).

**YAML fixtures — quote SHA values always.** PyYAML int-coerces unquoted all-digit scalars (`1234567` parses as `int`, not `str`); roughly 5% of 7-char hex SHAs are all-digit and fail schema validation only on Python 3.11+ CI, not local 3.12. Use `f"    - '{sha[:7]}'\n"` when embedding short SHAs in test fixtures. Precedent: PR #98 commit `3444cb6`.

## CI (GitHub Actions)

`backlink-publisher/.github/workflows/ci.yml` triggers on push to `main`/`develop`, PRs to `main`. Python 3.11+3.12, all steps blocking (no `|| true`). `PYTHONHASHSEED=0` at job level for footprint regression gate.

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v --tb=short --timeout=30
python -m py_compile src/backlink_publisher/**/*.py
# style check (not Black): ast.parse each .py via pathlib.Path("src").rglob("*.py")
cat fixtures/seed.jsonl | plan-backlinks | validate-backlinks | publish-backlinks --dry-run
```

NOTE: A stale copy exists at workspace root `./.github/workflows/ci.yml` (references `core/`, `|| true`). Ignore it — canonical CI is inside the git repo.

## Environment Variables

| Var | Purpose |
|---|---|
| `BACKLINK_PUBLISHER_CONFIG_DIR` | Override config dir (default `~/.config/backlink-publisher/`) |
| `BACKLINK_PUBLISHER_CACHE_DIR` | Override cache dir (default `~/.cache/backlink-publisher/`) |
| `BACKLINK_LLM_API_KEY` | LLM API key for anchor generation |
| `BACKLINK_NO_FETCH_VERIFY` | Skip content fetch verification |
| `BACKLINK_GATE_CACHE_TTL_SECONDS` | Override gate cache TTL |
| `BACKLINK_PUBLISHER_ALLOW_NETWORK=1` | Bind WebUI to non-loopback |
| `BACKLINK_PUBLISHER_WORKTREE_AUTOREMOVE=1` | Auto-remove stale worktrees |
| `MEDIUM_THROTTLE_MIN`, `MEDIUM_THROTTLE_MAX` | Inter-post delay (default 60-300s) |
| `OAUTHLIB_INSECURE_TRANSPORT` | Allow HTTP for OAuth loopback |
| `BIND_HOST` / `PORT` | WebUI address |
| `PYTHONHASHSEED=0` | Required for footprint regression tests |

## Known Quirks

- `GEMINI.md` is the Gemini CLI counterpart to this file — kept at repo root for Gemini project detection
- `planning/` subpackage exists but has no source files (only `__pycache__/`)
- `webui_app/services/` directory exists but has no source files (only `__pycache__/`)
- `src/backlink_publisher/adapters/` is a stale dir; real adapters are in `publishing/adapters/`
- `INSECURE_TLS` is mentioned in docs but not implemented in source yet
- Exit code table (0-6) is a documented contract, not enforced by `sys.exit()` in CLI code
- `bp-*/AGENTS.md` are stale copies — update this file, not those
- `docs/plans/`, `docs/brainstorms/` contain real operator domain names — don't propagate to `docs/solutions/`
- `develop` branch doesn't exist (locally or remote) despite CI triggering on `branches: [main, develop]`

## Lessons capture (dual-track)

The project keeps lessons in two places:

- **Private auto-memory** — Claude Code automatically writes `feedback_*.md` files at `~/.claude/projects/<project-memory-slug>/memory/` during sessions. These are fast-capture, operator-private, and never committed.
- **Public `docs/solutions/`** — High-value or recurring lessons get *promoted* into committed markdown entries under `docs/solutions/<category>/` (categories: `best-practices/`, `logic-errors/`, `test-failures/`, `ui-bugs/`). The promotion tool is `/ce:compound`.

**Promotion = rewriting, not copy-paste. Strip UUIDs, domains, absolute paths, user-identifying quotes.** The grep gates check against patterns in `~/.local/share/backlink-publisher/private-tokens.txt` — populate this file before first use of `/ce:compound` or gates pass vacuously.

Next curation review: **2026-08-15**. Next `/ce:compound` run should scan recent `feedback_*.md` and promote what's worth keeping.

## Plan-doc claims contract

> **Status (2026-05-20):** Cutoff is now in effect. Any plan-doc dated `2026-05-20` or later **must** include a `claims:` block (or explicit `claims: {}` opt-out) — otherwise `plan-check` exits 8 and the `plan-claims-gate` check fails. The gate is currently a non-required check during a 14-day soak; promotion to a required status check is scheduled for **2026-06-02** (see `docs/plans/2026-05-19-010-feat-plan-claims-gate-followups-plan.md`).

Plans authored on or after **2026-05-20** must carry a `claims:` block in their YAML frontmatter declaring the repo paths and SHAs that must still be reachable from `origin/main` at merge time. The `plan-check` CLI validates the block locally; the `plan-claims-gate` and `plan-claims-radar` workflows enforce it in CI and overnight. Plans dated before 2026-05-20 are grandfathered and silently skipped.

### Authoring

Frontmatter shape:

```yaml
---
title: "feat: ..."
type: feat
status: active
date: 2026-05-20            # ISO-8601; filename prefix must match (R11b lock)
origin: docs/brainstorms/...
claims:
  paths:
    - src/backlink_publisher/foo.py
    - tests/test_foo.py
  shas:
    - 7387953                # 7..40 lowercase hex chars
---
```

Two ways to opt out of drift detection on a plan that has no code anchors (governance docs, process changes):

```yaml
claims: {}                    # explicit empty escape hatch — still passes lint
```

The schema rejects unknown keys, glob characters (`*`, `?`, `[`) in paths, mixed-case or non-hex SHAs, and a frontmatter `date:` that disagrees with the filename's `YYYY-MM-DD-NNN-` prefix (the backdate exploit).

### Running locally

```bash
plan-check docs/plans/2026-05-20-001-feat-foo-plan.md            # human output
plan-check --json docs/plans/2026-05-20-001-feat-foo-plan.md     # JSON output
```

Exit codes:

| Code | Meaning |
|---:|---|
| 0 | pass, grandfathered, or empty-claims escape hatch |
| 1 | `UsageError` (missing/bad argument) |
| 2 | schema violation — malformed frontmatter, unknown key, glob in path, bad SHA format, filename/date mismatch |
| 7 | drift — one or more paths missing or SHAs unreachable on `origin/main` |
| 8 | post-cutoff plan with no `claims:` block |

`plan-check` emits a `RECON info fetch_head_age_seconds=<n>` line on stderr whenever it resolves claims against `origin/main` (the happy/drift paths) so freshness is visible. Grandfathered (`date < 2026-05-20`) and explicit-empty-claims (`claims: {}` or bare `claims:`) paths return silently with no stderr — they exit 0 before reaching the resolution stage. On offline fetch failure during resolution it emits `RECON warn fetch_skipped reason=<r> fetch_head_age_seconds=<n>` and still exits 0 — authoring should not be hostage to flaky networks (D16). CI never hits the skip path because its checkout step always succeeds.

### CI surfaces

- **`plan-claims-gate`** (`.github/workflows/plan-claims-gate.yml`) — runs on every PR with base `main`. Enumerates the plan-docs touched by the diff and runs `plan-check` against each. Non-required at ship; promote to required after 14 days clean (D9). Stack PRs whose base is not `main` won't fire this gate — workaround per `reference_ci_workflow_pr_filter` is `gh pr close && gh pr reopen` after rebasing onto `main`.
- **`plan-claims-radar`** (`.github/workflows/plan-claims-radar.yml`) — runs on a 09:00 UTC cron (and `workflow_dispatch`). Enumerates all post-cutoff plans, files a single rolling open issue titled `plan-claims drift radar: open since YYYY-MM-DD` summarizing the drifting plans. The radar is **never** a required check — informational only. Operator closes the issue manually after acknowledging the drift.

### Update-plan-on-ship discipline

When an implementing PR lands, the author flips `status: active → shipped` and re-resolves the `claims:` block against post-merge `origin/main`. **Do NOT bump the `date:` field** — it stays pinned at the original authoring date, preserving grandfather status for plans that pre-date the cutoff. The R11b filename↔date lock also requires `date:` to match the filename prefix, so bumping it would break the lock.

### Canonical reference

Implementation plan: `docs/plans/2026-05-19-009-feat-plan-claims-and-head-drift-gate-plan.md`.

## Worktree Cleanup

Accumulated `bp-<topic>/` worktrees can be cleaned with:

- **`bash scripts/prune-stale-worktrees.sh`** — detects worktrees merged into `origin/main` (via `gh pr list` or `merge-base`), skips dirty dirs. Flags: `--dry-run`, `--force`, `--help`. Exit 2 on failure.
- **`bash scripts/install-post-merge-hook.sh`** — installs a `post-merge` hook that notifies after `git pull` on `main`. Auto-remove via `BACKLINK_PUBLISHER_WORKTREE_AUTOREMOVE=1`.

Shared safety in `scripts/_worktree_safety.sh`. Tests: `tests/scripts/test_prune_stale_worktrees.py`.

## Monolith Budget

`monolith_budget.toml` tracks radon SLOC ceilings for **6** source files. Enforced by `tests/test_no_monolith_regrowth.py` (R4 hard-fail + R7 warning canary + radon version pinning).

| File | Ceiling |
|---|---|
| `cli/plan_backlinks.py` | 1270 |
| `cli/publish_backlinks.py` | 730 |
| `content/fetch.py` | 370 |
| `config/writer.py` | 340 |
| `_util/markdown.py` | 320 |
| `events/projector.py` | 580 |

If a PR exceeds a ceiling, edit `monolith_budget.toml` in the same PR — raise it and add `rationale` (≥80 chars). `git blame` is the defense; no override label. Bumping `radon` (pinned `==6.0.1`) requires re-measuring all 6 ceilings + updating `SLOC_CANARY_EXPECTED` in `tests/fixtures/sloc_canary.py`.

References: `docs/plans/2026-05-18-006-feat-monolith-sloc-ceiling-plan.md`, `docs/brainstorms/2026-05-18-monolith-loc-ceiling-requirements.md`.

## Adding a new publisher adapter

Post-R9, a new platform is one `register("x", XAdapter)` call away from reaching both the CLI argparse layer and `schema.validate_publish_payload`. The dispatcher, schema enum, throttle gating, and LinkedIn-style rejection all read from `publishing.registry.registered_platforms()` — you do not edit any CLI file or `schema.py` to add a new platform.

### 1. Subclass `Publisher`

Reference: `src/backlink_publisher/publishing/adapters/blogger_api.py::BloggerAPIAdapter`.

```python
# src/backlink_publisher/publishing/adapters/yourplatform.py
from typing import Any

from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult


class YourPlatformAdapter(Publisher):
    @classmethod
    def available(cls, config: Config) -> bool:
        return True

    def publish(self, payload: dict[str, Any], mode: str, config: Config) -> AdapterResult:
        ...
```

### 2. Implement `publish()`

- Call `extract_publish_html(payload, "yourplatform")` from `publishing.content_negotiation`
- Wrap remote calls in `retry_transient_call` from `.retry` for 429/5xx backoff
- Return `AdapterResult(status="drafted"|"published", ...)`
- Set `post_publish_delay_seconds=N` for rate-limit avoidance
- Raise `DependencyError` (falls through to next adapter) or `ExternalServiceError` (propagates immediately)

### 3. Register

Add one line to `src/backlink_publisher/publishing/adapters/__init__.py`:

```python
from .yourplatform import YourPlatformAdapter
register("yourplatform", YourPlatformAdapter)
```

Do NOT edit:
- `cli/publish_backlinks.py` (reads `registered_platforms()` dynamically)
- `cli/plan_backlinks.py` `--default-platform` choices
- `cli/validate_backlinks.py` unsupported-platform rejection
- `schema.py` `supported_platforms()` or `reject_unsupported_platform()`

For fallback chains (like Medium's `APIAdapter → BraveAdapter → BrowserAdapter`), pass all classes in one `register()` call.

### 4. Add config (if needed)

Follow `BloggerOAuthConfig` pattern: frozen dataclass → `Config` field → TOML key → loader path → token helpers.

### 5. Add an optional dependency (if needed)

```toml
[project.optional-dependencies]
yourplatform = ["yourplatform-sdk>=2.0"]
```

### 6. Add tests

Minimum: happy-path mock test, `DependencyError` test, `ExternalServiceError` test. XSS contract test required if adding a `ROUTE_TIER_MATRIX["yourplatform"] = "a"` entry.

The R9 proof in `tests/test_r9_extension_readiness.py` already exercises cross-layer wiring — registering is sufficient to inherit it.

### PR checklist

- [ ] Adapter file under `src/backlink_publisher/publishing/adapters/`
- [ ] One-line `register(...)` in `adapters/__init__.py`
- [ ] Config dataclass / loader / TOML example (if needed)
- [ ] `pyproject.toml` optional-dependency entry (if needed)
- [ ] 3+ adapter tests (happy / DependencyError / ExternalServiceError)
- [ ] XSS contract test (if tier-`"a"` entry added)
- [ ] `README.md` Prerequisites updated
- [ ] `git diff --stat src/backlink_publisher/cli/ src/backlink_publisher/schema.py` is empty

Related: `docs/plans/2026-05-18-009-refactor-cli-extension-readiness-plan.md` (the R9 plan that made this recipe possible), `src/backlink_publisher/publishing/registry.py` (the `Publisher` ABC and dispatcher).

## Adding banner embedding to an adapter

When `Config.image_gen` is set, `plan-backlinks` produces a `banner` dict per row containing a local file path. To get that banner onto the platform's own CDN at publish time (so the embedded URL survives the upstream image-gen CDN's TTL), an adapter opts in by defining `embed_banner(self, artifact_path: Path, alt: str) -> str | None`. The dispatcher in `publish-backlinks` (Unit 5) checks `hasattr(adapter, "embed_banner")` — no registration, no protocol class — and:

- Returns the platform-hosted URL on success → dispatcher prepends `![alt](platform_url)\n\n` to the body.
- Returns `None` → dispatcher falls back to the source URL (where available) with a warning that the link may rot.
- Raises → handled by `config.image_gen.strict`: `false` (default) logs warn and publishes without the banner; `true` propagates and fails the row.

Per-platform upload contract (existing references):
- **telegraph**: `POST https://telegra.ph/upload` with raw bytes; returns `telegra.ph/file/<sha>.<ext>` URL.
- **hashnode**: `uploadMedia` GraphQL mutation (the existing hashnode adapter already maintains a GraphQL client).
- **velog**: `image_upload_url` GraphQL mutation returns a presigned URL → PUT bytes.
- **ghpages**: commit the file to `<repo>/assets/banners/<sha>.<ext>` and return the `raw.githubusercontent.com` URL.
- **writeas**: NO media-upload API → `embed_banner` returns `None`; dispatcher falls back to source URL (or omits entirely if `source_url` is also None from b64-only providers).
- **blogger**: Blogger API `images.insert` returns a Blogger-hosted URL.

Reference: Plan 2026-05-20-001 Unit 5 + `src/backlink_publisher/publishing/adapters/image_gen/` for the artifact contract.

## Binding a channel

Browser-based credential binding is **orthogonal** to publisher adapters. Adding a new publish-platform follows the recipe above; teaching the platform's credential lifecycle to the operator-facing surface follows this section. Plan: `docs/plans/2026-05-19-001-feat-settings-browser-binding-plan.md`.

### Channels

The closed set lives in one place: `src/backlink_publisher/cli/_bind/channels/__init__.py::CHANNELS = frozenset({"velog", "medium", "blogger"})`. Every entry point (CLI argparse, webui routes, `AuthExpiredError` ctor, `mark_bound` / `mark_expired`) imports from there and validates membership before constructing paths or argv — defense in depth against `channel=../traversal` injection. Adding a fourth channel means: (1) extend `CHANNELS`; (2) ship its `ChannelRecipe` in `src/backlink_publisher/cli/_bind/recipes/<name>.py`; (3) update the CLI argparse `--channel` choices (auto-derived from `CHANNELS` already).

### Entry points

- `bind-channel --channel <velog|medium|blogger>` — single binding CLI, drives a headed Playwright session, emits RECON events on stdout as JSONL, writes `<config_dir>/<channel>-storage-state.json` with mode `0600`.
- `velog-login` — transparent alias for `bind-channel --channel velog`. Honored for backwards compatibility with plan-012. Prints an alias banner to stderr; otherwise identical.

Storage state always lands inside `BACKLINK_PUBLISHER_CONFIG_DIR` (defaults to `~/.config/backlink-publisher/`). The driver writes to a temp file then `os.rename`s — partial writes never leave a half-bound file. `mark_bound` happens after the rename so a kill in between leaves the file but keeps the status as `unbound` / `expired` (next click re-binds idempotently).

### Settings UI flow

`GET /settings` shows each channel card with a binding subsection (rendered from `webui_app/templates/_settings_channel_binding.html`):

- **Badge states** (rendered via `role="status" aria-live="polite"`):
  - `已绑定 ✓` — last `mark_bound` succeeded and the storage_state file still exists on disk.
  - `已过期 ⚠` — adapter raised `AuthExpiredError` at publish time, **or** `reconcile_on_load` found the storage_state file missing on app start.
  - `未绑定` — no record in `channel-status.json`.
  - `绑定中…` — JS poller saw `status: "running"` from `GET /settings/channels/<channel>/bind/<job_id>`.
- **Re-bind button** issues `POST /settings/channels/<channel>/bind` with the page CSRF token; both routes are loopback-only (`Blueprint.before_request` rejects non-`127.0.0.1`/`::1` with 403). The button writes `sessionStorage["bind:lastChannel"]` so a page reload re-opens the same card.
- **Failed binds** map their `error_code` to a Chinese operator message via `webui_app.services.bind_job.BIND_ERROR_MESSAGES` — adding a new `error_code` requires a Chinese mapping (the `tests/test_bind_error_messages.py` gate enforces this).

### Publish-time auth flip

When a publish adapter hits a 401/403 it raises `AuthExpiredError(channel="...", reason="...")` (the ctor revalidates `channel ∈ CHANNELS`). The `publish_backlinks` dispatch site catches this **before** the generic `except DependencyError`, calls `webui_store.channel_status.mark_expired(exc.channel)`, writes a checkpoint row with `error_class="auth_expired"`, then exits with code 3. Because `AuthExpiredError` inherits from `DependencyError`, callers that still `except DependencyError` keep working — they just lose the channel-specific side effects.

### Operator script — "how do I re-bind Medium?"

1. Open the WebUI (`webui` or `python webui.py`).
2. Navigate to `/settings`, expand the Medium card.
3. Click **重新绑定**. A headed Chromium window opens; complete the Medium login.
4. The badge transitions `绑定中…` → `已绑定 ✓`. The card stays open after the page reload thanks to `sessionStorage["bind:lastChannel"]`.

Alternative CLI path: `bind-channel --channel medium` (then complete login in the headed browser).

### What about Velog?

Velog is the **adapter** in plan-012 but its **credential lifecycle** lives here. plan-012 originally specified a standalone `velog-login` CLI and a `DependencyError("velog cookie expired")` raise on auth failure; plan 2026-05-19-001 unified that with the cross-channel surface. See the inline amendment in plan-012 (Unit 3 + Unit 4) for the exact contract changes.
