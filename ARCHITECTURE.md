# Backlink Publisher Architecture

This document is the repo-local architecture source of truth. It is descriptive,
not a replacement for `AGENTS.md`: contribution rules, bugfix discipline,
adapter recipes, and operational constraints still live there.

## Purpose

`backlink-publisher` is a local-first Python 3.11+ backlink publishing pipeline.
It plans short backlink article payloads, validates them, and publishes them to
registered platforms through adapter chains. The primary runtime surfaces are:

- Terminal-native JSONL CLIs installed from `pyproject.toml`.
- A Flask WebUI launched by `python webui.py`.
- Operator-local config, cache, JSON stores, and SQLite sidecar stores.
- GitHub Actions gates for unit tests, syntax checks, plan-claims validation,
  and selected Phase 0 seal checks.

Stdout from pipeline CLIs is clean JSONL. Diagnostics, typed error envelopes,
reconciliation lines, and config banners go to stderr.

## Runtime Boundaries

The package code lives under `src/backlink_publisher/`. The WebUI lives at repo
root in `webui_app/` and `webui_store/`, not under `src/`.

Operator state is local by default:

- Config: `~/.config/backlink-publisher/`, override with
  `BACKLINK_PUBLISHER_CONFIG_DIR`.
- Cache/checkpoints: `~/.cache/backlink-publisher/`, override with
  `BACKLINK_PUBLISHER_CACHE_DIR`.
- WebUI JSON stores: files under the config directory, lazily resolved by
  `webui_store`.
- Event projection: `events.db` under the config directory.
- Publish idempotency: `dedup.db` under the config directory.

Credential files and token sidecars are present by path convention but their
values are not architecture evidence and must not be read into docs.

## Active Surfaces

### CLI Pipeline

Console scripts are declared in `pyproject.toml`:

- `plan-backlinks`: reads seed JSONL or CSV/sitemap input and emits planned
  payload JSONL.
- `validate-backlinks`: validates planned payloads and emits enriched payloads.
- `publish-backlinks`: dispatches validated payloads to publisher adapters.
- Diagnostic and support CLIs include `footprint`, `report-anchors`,
  `equity-ledger`, `phase0-seal`, `audit-state`, `preflight-targets`,
  `cull-channels`, `canary-targets`, `plan-check`, `plan-gap`, and
  `recheck-backlinks`.

The core pipeline shape is:

```text
seed JSONL
  -> plan-backlinks
  -> validate-backlinks
  -> publish-backlinks
  -> publish result JSONL
```

CLI shells own argparse, stdin/stdout/stderr, config echo, log level, and exit
mapping. Computation that is shared with the WebUI is extracted into engine
modules where possible, for example `cli/plan_backlinks/_engine.py` and
`validate/engine.py`.

### Flask WebUI

`webui.py` is the launcher and legacy re-export shim. `webui_app.create_app()`
builds the Flask app, registers blueprints, injects platform and CSRF template
context, starts APScheduler outside pytest, and runs startup reconciliation.

Route registration is centralized in `webui_app/routes/__init__.py`. The app
has route modules for pipeline execution, batch flows, checkpoints, history,
drafts, settings, OAuth, profiles, sites, queue, dashboard, bind flows, token
paste, URL verification, image generation, SEO visualization, equity ledger,
health, and channel bind saves.

The WebUI calls pipeline logic through `webui_app/api/pipeline_api.py` and
`webui_app/helpers/cli_runner.py`. The subprocess bridge rewrites bare CLI names
to `python -m backlink_publisher.cli.<module>` with `PYTHONPATH=src` so the
current checkout is used instead of stale editable-install shims.

Security posture:

- Default bind is loopback.
- Off-loopback binding requires `BACKLINK_PUBLISHER_ALLOW_NETWORK=1` and is
  explicitly warned as unsupported without additional hardening.
- `create_app()` enforces a global CSRF guard on POST/PUT/PATCH/DELETE, with
  OAuth callbacks excluded because they verify their own state.
- Tests may disable CSRF through sanctioned app config paths.

## Architecture Layers

### Planning

Planning code is under `cli/plan_backlinks/` plus supporting modules:

- `_engine.py`: shared planning kernel and drop accounting.
- `_payload.py`: payload assembly, article metadata, dofollow-tier metadata.
- `_links.py`: link construction and content-gate candidate collection.
- `_templates.py`: language templates.
- `_zh_short.py`: zh-CN short-form scheduler path.
- `_work_themed.py`: three-URL work-themed path.
- `_banners.py`: optional banner image generation runtime.

Planning consumes config, content fetch results, optional LLM anchor generation,
and optional image generation. The deterministic boundary is documented in
`docs/architecture/deterministic-planning-principle.md`: planning code should
keep pure computation separated from external inputs.

### Validation

Schema-level validation is in `schema.py`, `_schema_input.py`, and
`_schema_output.py`. The CLI wrapper is `cli/validate_backlinks.py`; shared
validation behavior lives in `validate/engine.py`.

Validation checks payload shape, platform support, link structure, required
content fields, URL/language behavior, and enrichment. URL reachability checks
are externally dependent and can be disabled with the CLI flag documented in
README and AGENTS.

### Publishing

Publisher dispatch is registry-driven:

- `publishing/registry.py`: `Publisher` ABC, `register()`, dispatch metadata,
  active/bound platform lookup, dofollow/referral metadata, auth classification.
- `publishing/adapters/__init__.py`: imports adapter classes and registers each
  platform or fallback chain.
- `publishing/_manifests.py`: declarative per-channel UI, binding, policy, and
  visibility metadata.
- `publishing/banner_dispatcher.py`: optional banner embedding before publish.
- `publishing/browser_publish/`: browser-driven channel recipes and dispatcher.
- `publishing/reliability/`: publish policy behavior such as retry or circuit
  style controls.

Adding a publisher should normally mean adding an adapter class and one
`register()` call. The schema, CLI choices, and WebUI platform lists derive from
the registry instead of hardcoded platform lists.

### Config And Secrets

Config dataclasses live in `config/types.py`; loading and parsing live in
`config/loader.py` and `config/parsers/`; writing and section-preservation logic
live in `config/writer.py`.

`load_config()` reads `config.toml` from the resolved config directory and
falls back to selected sidecar settings where implemented. Secret-bearing token
files are deliberately separate from the main TOML for several adapters.

Tests use `BACKLINK_PUBLISHER_TEST_SANDBOX` plus config/cache override env vars
to fail closed when subprocesses accidentally escape the sandbox.

### State And Projections

State is intentionally split by responsibility:

- `checkpoint.py`: cache-directory JSON checkpoints for `publish-backlinks`
  resume and batch status.
- `webui_store/`: lazy JSON stores for WebUI history, profiles, drafts,
  schedule, queue, and channel status.
- `events/store.py`: SQLite-backed append/projected event store at `events.db`,
  with WAL mode, 0600 file hygiene, and schema upgrade on connect.
- `idempotency/store.py`: authoritative SQLite dedup store at `dedup.db`, kept
  separate from `events.db` because publish idempotency is correctness-critical.
- `canary/store.py`: canary health persistence under the config directory.
- `ledger/` and `gap/`: read-side equity aggregation and deficit-driven replans.

The event projection is rebuildable. The dedup store is not just a projection;
it gates publishing with single-flight semantics.

### Content, Link Checking, And SEO

Network and content helpers are separated from pipeline shells:

- `content/`: fetch, scraper, soft-404, HTML helpers, themed generation.
- `linkcheck/`: HTTP checks, language heuristics, link attribute verification.
- `anchor/`: anchor language support, metrics, profile, resolver, scheduler,
  and preflight logic.
- `footprint.py` and `footprint_corpus.py`: link footprint analysis.
- `comment_outreach/`: comment outreach discovery, scoring, brief, and status
  storage.

Tests mock network paths by default. Live checks are opt-in through pytest
markers.

## Project Capabilities And Local Instructions

Repo instruction files:

- `AGENTS.md`: canonical repo governance and contributor workflow.
- `webui_app/AGENTS.md`: WebUI-specific structure, conventions, and
  anti-patterns.
- `tests/AGENTS.md`: test isolation, markers, budget gates, and test quirks.

Repo-local capability directories:

- `.agents/skills/channel-probe/SKILL.md`: local skill for backlink channel
  probe triage.
- `.claude/skills/channel-probe/SKILL.md`: legacy/parallel skill copy.
- `.claude/settings.local.json`: local tool/settings file; do not treat it as
  portable architecture proof.

No `CLAUDE.md` was present during this scan.

## Build, Install, And Run

Install:

```bash
pip install -e .
pip install -e ".[dev]"
```

Run CLIs:

```bash
cat fixtures/seed.jsonl | plan-backlinks | validate-backlinks | publish-backlinks --dry-run
```

Run WebUI:

```bash
python webui.py
```

Developer Make targets are limited to Webwright scaffolding/diagnostics and a
reconcile check. They are not the publishing pipeline.

## CI And Verification Map

GitHub Actions in `.github/workflows/`:

- `ci.yml`: Python 3.11 and 3.12, `pip install -e ".[dev]"`, `pytest tests/`,
  `py_compile`, AST parse, fixture generation, and non-blocking mypy.
- `plan-claims-gate.yml`: validates touched plan docs on PRs to `main`.
- `plan-claims-radar.yml`: scheduled drift scan for post-cutoff plan claims.
- `phase0-seal-check.yml`: verifies selected Telegraph Phase 0 branch families.

Important local gates:

- `pytest tests/`
- `pytest tests/test_no_monolith_regrowth.py -k "R4"`
- `pytest tests/scripts/`
- `python -m radon raw -s <budgeted-file>`
- `plan-check <docs/plans/...-plan.md>`
- `make reconcile-check`

Budget and contract tests enforce monolith SLOC ceilings, adapter dofollow
metadata, manifest shape, orphaned guard-script references, WebUI/security
contracts, and pipeline behavior.

## Known Gaps And Risks

- There is no repo-local `scripts/preflight.py`; the repo-architecture skill
  preflight hook was therefore a no-op in this scan.
- The worktree contained unrelated dirty/untracked files when this document was
  created, including an existing `AGENTS.md` edit and generated cache
  directories. Architecture claims above are based on direct file reads, not on
  a clean worktree assumption.
- Platform behavior is inherently unstable. Dofollow status, remote auth
  behavior, Medium/browser flows, and canary truth must be verified with live
  probes before relying on them operationally.
- Some docs and plans intentionally contain operator domain details. Do not move
  those details into public summaries or solution docs without sanitization.
