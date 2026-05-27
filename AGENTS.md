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

Flask app at `webui_app/` (20 route modules, `create_app()` factory). State persistence at `webui_store/` — five module-level singletons in `webui_store/__init__.py` (`history_store`, `profiles_store`, `drafts_store`, `schedule_store`, `queue_store`) plus `channel_status_store` from the `channel_status` submodule. Launcher: `python webui.py`.

App-level CSRF guard `_global_csrf_guard` (PR #143, `webui_app/__init__.py`) enforces a token on every POST/PUT/PATCH/DELETE. Tests opt out via `app.config['CSRF_ENABLED'] = False` (or the legacy `WTF_CSRF_ENABLED` flag — both honored). The `_check_csrf_or_abort` helper has a single production call site inside the global guard; PR #148 removed all inline per-route calls.

### CLI entrypoints (7)

```bash
cat seeds.jsonl | plan-backlinks | validate-backlinks | publish-backlinks --mode draft
```

| Command | Source | Role |
|---|---|---|
| `plan-backlinks` | `cli/plan_backlinks.py` | Generate articles from seed JSONL |
| `validate-backlinks` | `cli/validate_backlinks.py` | Validate + enrich |
| `publish-backlinks` | `cli/publish_backlinks.py` | Publish via platform adapters |
| `report-anchors` | `cli/report_anchors.py` | Post-hoc anchor profile |
| `equity-ledger` | `cli/equity_ledger.py` | Per-target backlink scorecard (read-only JSONL) |
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
- (e) **Pure-placeholder sections** (header + comments only, no data) — never *emitted* by the writer ab initio (a fresh `save_config` of an empty `Config` produces only `[blogger]` and `[medium]`). Placeholder sections that already exist on disk are preserved verbatim by the same pass as branches (c)/(d); branch (e) is about emission, not deletion. Canary: `tests/test_save_config_section_taxonomy_canary.py`.

Note: `merge_site_url_categories` is a second writer that text-edits `[sites."<main>".url_categories]` blocks in place and does not interact with the preservation pass.

**Credential-lifecycle note (post-2026-05-19):** managed-root credential subsections (`[medium.oauth]`, `[blogger.oauth]`) now persist on save and propagate into `.config-history/` rolling snapshots (cap 20). After credential rotation, up to 20 historical copies of revoked secrets remain on disk until aged out. If `BACKLINK_PUBLISHER_CONFIG_DIR` points to synced storage (Dropbox, NFS, dotfiles repo), credentials now propagate through the sync surface — keep the config dir on local-only storage.

**Medium sidecar precedence:** when both `[medium.oauth]` in `config.toml` and the sidecar file from Plan 2026-05-18-013 are populated, `[medium.oauth]` wins. The sidecar continues to provide fallback for operators who haven't migrated.

Note: operator-archival `[targets_meta.<domain>]` blocks are preserved-only — no pipeline code reads them; treat as documentation, not a functional override.

## Import Conventions

Plan 2026-05-20-006 deleted the legacy `sys.meta_path` bridge. The old flat names (`backlink_publisher.errors`, `backlink_publisher.adapters.*`, `backlink_publisher.content_fetch`, …) **no longer resolve** — `from backlink_publisher.errors import X` now raises `ModuleNotFoundError`. Use the canonical paths:

| Subpackage | Contents |
|---|---|
| `backlink_publisher.anchor.*` | `lang`, `metrics`, `profile`, `resolver`, `scheduler`, `preflight` |
| `backlink_publisher.content.*` | `fetch`, `scraper`, `themed_gen`, `body` |
| `backlink_publisher.linkcheck.*` | `http`, `language`, `verify` |
| `backlink_publisher._util.*` | `errors`, `io`, `jsonl`, `logger`, `markdown`, `url`, `net_safety`, `secrets`, `url_derive` |
| `backlink_publisher.publishing.adapters.*` | All publisher adapters (blogger, medium, telegraph, ghpages, devto, notion, mastodon, velog, …) |

Note: `from backlink_publisher.linkcheck import check_url` still works — `linkcheck` is a real package whose `__init__.py` does `from .http import *`, independent of the deleted bridge.

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

- `webui_app/services/` is real: 5 source modules (`bind_job`, `browser_login`, `recheck`, `seo_viz`, `url_verify_throttle`). Earlier drafts of this doc claimed it was empty; obsolete.
- Exit code table (0-6) is a documented contract, not enforced by `sys.exit()` in CLI code
- `bp-*/AGENTS.md` are stale copies — update this file, not those
- `docs/plans/`, `docs/brainstorms/` contain real operator domain names — don't propagate to `docs/solutions/`
- `develop` branch doesn't exist (locally or remote) despite CI triggering on `branches: [main, develop]`
- `~/.config/backlink-publisher/llm-settings.json` holds the LLM `api_key`; PR #140 routed writes through `safe_write.atomic_write` so the file lands `0o600`. Files written by pre-#140 code may still be `0644` until the next save.

## Lessons capture (dual-track)

The project keeps lessons in two places:

- **Private auto-memory** — Claude Code automatically writes `feedback_*.md` files at `~/.claude/projects/<project-memory-slug>/memory/` during sessions. These are fast-capture, operator-private, and never committed.
- **Public `docs/solutions/`** — High-value or recurring lessons get *promoted* into committed markdown entries under `docs/solutions/<category>/` (categories: `best-practices/`, `developer-experience/`, `integration-issues/`, `logic-errors/`, `test-failures/`, `ui-bugs/`, `workflow-issues/`). Searchable by YAML frontmatter fields (`module`, `tags`, `problem_type`). The promotion tool is `/ce:compound`.

**Promotion = rewriting, not copy-paste. Strip UUIDs, domains, absolute paths, user-identifying quotes.** The grep gates check against patterns in `~/.local/share/backlink-publisher/private-tokens.txt` — populate this file before first use of `/ce:compound` or gates pass vacuously.

Next curation review: **2026-08-15**. Next `/ce:compound` run should scan recent `feedback_*.md` and promote what's worth keeping.

### Bugfix discipline

**Don't patch blindly.** Every bugfix runs five steps: **reproduce → identify root cause → classify → apply the smallest safe fix → leave traceable evidence.** This applies to *all* fixes — only the written depth scales (table below), never the obligation to reproduce and classify.

- **Smallest safe fix** — change only what the root cause requires. No opportunistic refactors, scope creep, or unrelated cleanups in a bugfix; a reviewer should be able to tie the diff line-by-line to the cause.
- **Classify** using the `docs/solutions/` categories listed above (`logic-errors`, `test-failures`, `integration-issues`, `ui-bugs`, `workflow-issues`, `best-practices`, `developer-experience`). The fix-time label is the same one the fix carries if promoted via `/ce:compound`.
- `/investigate` is an optional aid for the reproduce + root-cause phases — it's a generic skill, not a project command, so the contract stands without it.

| Fix size | Reproduce | Root cause | Evidence carried |
|---|---|---|---|
| One-liner / typo / rename / doc | one-line note (no test) | one sentence | inline in commit/PR body |
| Normal bug | failing test or repro steps | short paragraph | commit/PR body |

**Overlay (not a size tier):** if the bug is a regression / recurring / subtle class — a judgment call, not a function of fix size — add a failing test to the suite, write *why prior code allowed it*, and promote via `/ce:compound`. Authors self-classify, so the floor (never exempt from reproduce + classify) is the load-bearing rule; the table is guidance, not a loophole.

### Before opening a PR

- [ ] **Bugfix?** Carry repro + root cause + a `docs/solutions/` label + smallest-safe-fix rationale in the PR body — see **Bugfix discipline** above.

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

**No orphaned guard scripts.** A quality guard must live as a CI-executed test or workflow step — never an inert `scripts/check_*.py` that nothing runs (which gives every parallel agent false confidence). Any `scripts/check_*.py` must be referenced by a REPO_ROOT-reachable CI surface (a `.github/workflows/*.yml` workflow, a `scripts/install-*.sh` hook installer, or `.pre-commit-config.yaml`); `tests/test_no_orphaned_guard_scripts.py` enforces this and fails the build naming any unreferenced guard. Wire a guard into CI, or delete it.

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
register("yourplatform", YourPlatformAdapter, dofollow=True)
```

`dofollow=` is a **required** keyword argument (Plan 2026-05-20-009). Legal values are `True`, `False`, or `"uncertain"`. Anything other than `True` additionally requires `rationale=` of ≥80 stripped chars explaining why a non-dofollow platform is shipping (mirrors `monolith_budget.toml` rationale discipline; length-only — content is reviewer concern). The gate is enforced at import time (missing `dofollow=` raises `TypeError`) and at CI time by `tests/test_adapter_dofollow_gate.py`.

### 3b. Declare manifest metadata (Plan 2026-05-25-002)

The same `register()` call accepts four optional declarative kwargs that collapse channel-specific wiring across `binding_status.py`, `webui_app/__init__.py`, `helpers/contexts.py`, and templates into a single SSoT. Reference: the **Velog pilot** at `adapters/__init__.py` (lines starting `register("velog", ...)`).

```python
from .._manifest_types import BindDescriptor, Policy, UiMeta

register(
    "yourplatform",
    YourPlatformAdapter,
    dofollow=True,
    ui=UiMeta(
        display_name="Your Platform",         # used by inject_platforms
        domain="yourplatform.com",
        category="dev-blog",                  # or "social", "wiki", ...
        icon="bi-globe2",                     # Bootstrap icon name
    ),
    bind=[
        BindDescriptor(
            backend="token-paste",            # or "cookie", "oauth", "chrome", "cdp"
            storage_state_path="<config_dir>/yourplatform-token.json",
            login_endpoint="/api/yourplatform/login",  # if applicable
            card_template="_settings_channel_yourplatform.html",  # under webui_app/templates/
            extras={                          # escape hatch for platform-specific paths
                "browser_recipe": "backlink_publisher.publishing.browser_publish.recipes.yourplatform",
            },
        ),
    ],
    policy=Policy(
        throttle_band=(60, 180),              # tuple[int, int] seconds
        env_keys={"min": "YOURPLATFORM_THROTTLE_MIN",
                  "max": "YOURPLATFORM_THROTTLE_MAX"},
        retry_id="default",
        liveness_probe_sec=900,
        language_whitelist=("en", "ko"),      # () = no restriction
    ),
    visibility="active",                      # default; or "experimental" / "hidden" / "retired"
)
```

**Why bother**:
- `inject_platforms()` automatically picks up `display_name` from `UiMeta` (no template edit)
- `hidden_from_ui()` / `_settings_context.dashboard_channels` filter automatically via `visibility="hidden"` / `"retired"` (no second wire site)
- `tests/test_manifest_contract.py` validates the manifest shape on every CI run and prints a migration progress board

**`visibility` lifecycle**:

| state | behaviour |
|---|---|
| `"active"` | default; listed everywhere |
| `"experimental"` | opt-in only (CLI `--include-experimental`, WebUI advanced mode) |
| `"hidden"` | UI suppressed; existing bound configs still work (PR #136 write.as pattern) |
| `"retired"` | UI suppressed + `save_config` stops round-tripping its TOML sections (Unit 2b — pending) |

**All four kwargs are optional**. Omitting them is the "legacy" path — channel still registers, but won't benefit from the reverse-lookup wiring. `tests/test_manifest_contract.py` prints `legacy_platforms()` count to surface migration progress.

If the platform name appears in `publishing.registry._REJECTED_PLATFORMS` (the negative-knowledge map seeded from PR #108→#109's `devto` / `mastodon` / `wordpresscom` reverts), `register()` raises `RegistryError` at import time. Un-rejection path: delete the entry from `_REJECTED_PLATFORMS` in the same PR as the new `register()` call — the deletion diff makes the un-rejection visible to reviewers; no `accept_rejection_override` kwarg exists.

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

When `Config.image_gen` is set, `plan-backlinks` produces a `banner` dict per row containing `{path, alt, mime, sha, source_url}` (the `source_url` field was added in Plan 2026-05-20-004 Unit 1 R12 so the fallback path documented below is actually reachable; rows produced before that amendment treat the missing key as `None`). To get that banner onto the platform's own CDN at publish time (so the embedded URL survives the upstream image-gen CDN's TTL), an adapter opts in by defining `embed_banner(self, artifact_path: Path, alt: str) -> str | None`. The dispatcher (`publishing.banner_dispatcher.apply`, called from `publishing.registry.dispatch` when the caller passes `banner_emit=...`) checks `hasattr(adapter, "embed_banner")` — no registration, no protocol class — and:

- Returns the platform-hosted URL on success → dispatcher prepends `![alt](platform_url)\n\n` to `payload["content_markdown"]` before `adapter.publish()` runs. Emits `banner.embedded`.
- Returns `None` → dispatcher falls back to `banner["source_url"]` (when truthy). Emits `banner.source_url_fallback` with `reason="adapter_returned_none"`. If `source_url` is also `None`/missing (b64-only provider OR pre-R12 row), the banner is silently omitted with `banner.skipped_no_artifact`.
- Raises `BannerUploadError` → handled by `config.image_gen.strict`: `false` (default) logs warn and publishes without the banner (emits `banner.failed`); `true` propagates out of `dispatch()` and the publish loop records a row-level `error_class="banner_upload"` checkpoint (the run continues with the next row, NOT exit-3 like other DependencyError families).
- Raises non-`BannerUploadError` (adapter bug) → propagates unconditionally, even when `strict=False`. Strict gating governs only banner-specific failures, never adapter implementation bugs.

Adapters that don't define `embed_banner` are handled by the same dispatcher: `source_url` is prepended via the not-opted-in branch, emitting `banner.source_url_fallback` with `reason="adapter_no_method"`. If no `source_url` either, emits `banner.skipped_no_method` and the body is unchanged.

Per-platform upload contract:
- **telegraph**: `POST https://telegra.ph/upload` with raw bytes; returns `telegra.ph/file/<sha>.<ext>` URL.
- **velog**: `image_upload_url` GraphQL mutation returns a presigned URL → PUT bytes.
- **ghpages**: commit the file to `<repo>/assets/banners/<sha>.<ext>` and return the `raw.githubusercontent.com` URL.

- **blogger**: data-URI base64 inline (probe-confirmed at Unit 3 time) or the legacy `images.insert` backdoor if still alive.

**Medium does NOT implement `embed_banner`.** All three Medium fallback adapters (`MediumAPIAdapter`, `MediumBraveAdapter`, `MediumBrowserAdapter`) omit the method so the dispatcher reaches the not-opted-in branch and prepends `![alt](source_url)`. Medium's publish-time auto-rehost then snapshots the upstream provider's CDN URL into Medium's own image hosting, yielding a Medium-hosted URL in the published post without us writing platform-specific upload logic for each Medium fallback. **Verification required at implementer time**: confirm Medium auto-rehost still works in the current year by publishing one row to a scratch Medium account and inspecting the rendered `<img src>`. If auto-rehost is dead, Medium needs its own upload path or banners must be explicitly disabled for Medium.

Error classes related to banner embedding:
- `BannerUploadError(DependencyError)` — raised by per-adapter `embed_banner` implementations on media-API failure (4xx/5xx, multipart serialization error, presign failure, etc.). NOT a credential failure; channel-status `mark_expired` must NOT fire on this exception. Strict-mode propagation lands a row-level checkpoint with `error_class="banner_upload"` — distinct from `AuthExpiredError`'s `error_class="auth_expired"`.

Reference: Plan 2026-05-20-001 Units 1-6 + Plan 2026-05-20-004 Unit 1 (this dispatcher + R12) + `src/backlink_publisher/publishing/adapters/image_gen/` for the artifact contract.

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

#### Velog null-after-retry diagnostics (plan 2026-05-22-004)

When `writePost` returns `null` on both the initial attempt and the silent-drop
retry, the adapter now runs a lightweight `currentUser` liveness probe before
deciding the error class:

- **Cookie dead** (`probe_reason=no_current_user|http_4xx|probe_unreachable`) →
  `AuthExpiredError` → channel flips to expired → operator must re-bind.
- **Cookie alive** (`probe_reason=<username>`) → `ContentRejectedError` →
  row fails, batch continues, channel status unchanged. The WebUI history card
  shows an amber "内容被拒（Cookie 有效）" hint. **Do not re-bind** — inspect
  the `debug/velog-null-<article_id>.json` artifact in `config_dir` instead.

The debug artifact (0600, written by `_save_null_artifact`) contains the full
response body, response headers, and any GraphQL `errors[]` array — none of
which appear in the 200-char-truncated log that was there before this fix.
