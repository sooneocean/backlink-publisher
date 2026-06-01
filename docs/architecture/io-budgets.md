# I/O Budgets — File-Surface Sizing & Mode Discipline

**Status:** Advisory — codifies the I/O surface contract; cross-references the gates that enforce it.

> **Principle:** every file the pipeline writes has a *named owner*, a *mode*
> ceiling, and an *atomic-write* primitive. New surfaces are additive only —
> never replace an existing path's mode or rename an existing file underfoot.

Origin: Wave 1 audit (2026-06-01) — 246 .py files touching 18+ filesystem
surfaces across config dir, cache dir, working dir, and runtime SQLite DBs.
The atomic-write primitives and mode discipline exist; this register names
every surface so a reviewer can answer "where does the pipeline write?" by
reading one table.

---

## How to use this register

Three checks, in order:

1. **Adding a new file** — extend the table below in the same PR that writes
   the new file. The defense is `git blame` on the table row.
2. **Changing a mode or path** — same PR, same row, with a one-line rationale
   in the commit body.
3. **Reading a file the table does not list** — the reader is also new, so
   also extend the table (readers are part of the contract).

The table is **advisory**; the *gates* that enforce it are listed in the last
section. Those gates are CI-blocking and have been so since their respective
PRs (see cross-references).

---

## I/O surface register

| # | Path (relative to `BACKLINK_PUBLISHER_CONFIG_DIR` unless noted) | Mode | Writer primitive | Owner module | Reader | Notes |
|---|---|---|---|---|---|---|
| 1 | `config.toml` | 0o600 | `config/writer.py::save_config` (atomic text-edit, preserves unmanaged sections) | `backlink_publisher.config.writer` | `backlink_publisher.config` loaders | Operator-only; synced-storage note in AGENTS.md |
| 2 | `config.toml`'s parent dir | 0o700 | `config/writer.py` (`os.chmod(parent, 0o700)` after first write) | same | n/a | Config dir itself is owner-only |
| 3 | `llm-settings.json` | 0o600 | `persistence/safe_write.atomic_write` (post-#140) | `backlink_publisher._util.secrets` | LLM-anchor generators | Pre-#140 files may still be 0644 until next save |
| 4 | `livejournal-credentials.json` | 0o600 | `publishing/_manifests.py` credential helper | `backlink_publisher.publishing._manifests` | `publishing/adapters/livejournal_api.py` | Per-platform credential file pattern |
| 5 | `<channel>-storage-state.json` (velog, medium, blogger) | 0o600 | `cli/_bind/_driver_impl.py` (tempfile + `os.rename`) | `backlink_publisher.cli._bind._driver_impl` | Playwright `browser.new_context(storage_state=…)` | 3-channel closed set; `CHANNELS` frozenset |
| 6 | `events.db` (SQLite) | 0o600 | `events/_db.py` connection (WAL mode) | `backlink_publisher.events._db` | `events/projector.py` + `webui_app/health_metrics.py` | Read-side projection; see `events-db-scale-tripwire-register.md` for tripwires |
| 7 | `dedup.db` (SQLite) | 0o600 | `idempotency/store.py` (single-flight claim gate) | `backlink_publisher.idempotency.store` | `cli/_publish_helpers.py::gate_with_force` | Per-row publish-loop dedup |
| 8 | `canary-health.json` | 0o600 | `publishing/canary/store.py` (atomic write via `_util/io.atomic_write_json`) | `backlink_publisher.publishing.canary.store` | `canary-targets`, `recheck-backlinks` CLI | Forward-path + evergreen decay verdicts |
| 9 | `<platform>-canary.json` (per platform, plan-2026-05-27-006) | 0o600 | same as #8 | same | same | Sub-record under `_publish_path` sibling key |
| 10 | `circuit-state.json` (per adapter) | 0o600 | `publishing/reliability/circuit.py::save_state` (`os.chmod(state_path, 0o600)`) | `backlink_publisher.publishing.reliability.circuit` | circuit-breaker init | Breaker state across CLI invocations |
| 11 | `checkpoint.json` (publish-backlinks run state) | 0o600 | `_util/io.atomic_write_json` | `cli/publish_backlinks.py` | `--resume` reload path | Per-run resume file |
| 12 | `anchor-profile.json` | 0o600 | `_util/io.atomic_write_json` | `cli/report_anchors.py` | `report-anchors` reload path | Post-hoc anchor profile snapshot |
| 13 | Output JSONL files (stdout capture, `> plan-rows.jsonl`) | 0o600 | `_util/jsonl.atomic_write_jsonl` | caller-supplied path | operator-defined | Mode is caller-set; default 0o600 for safety |
| 14 | `comment_outreach/*.json` | 0o600 | `comment_outreach/store.py` (asserts `path.stat().st_mode & 0o777 == 0o600` on write) | `backlink_publisher.comment_outreach.store` | `comment` CLI | First-party gate: writes FAIL if mode regresses |
| 15 | Chrome profile dir (browser_publish) | 0o700 | `publishing/browser_publish/_chrome_session_impl.py::_ensure_profile_dir_mode` (`stat + chmod 0o700`, raises on mismatch) | `backlink_publisher.publishing.browser_publish._chrome_session_impl` | Playwright `launch_persistent_context` | First-party gate: launches FAIL on permission mismatch |
| 16 | `<config_dir>/debug/velog-null-<article_id>.json` (plan-2026-05-22-004) | 0o600 | `publishing/adapters/velog_graphql.py::_save_null_artifact` | `backlink_publisher.publishing.adapters.velog_graphql` | operator forensics | Diagnostic artifact for null-after-retry |
| 17 | `fixtures/seed.jsonl` (E2E, at repo root) | 0o644 | not written by pipeline (read-only fixture) | repo | `pytest tests/` | Not a runtime surface |
| 18 | `tests/baselines/*.json` (footprint, cli-timing) | 0o644 | test-driven (atomic write in test) | `tests/baselines/` | `test_footprint_regression.py`, `test_cli_timing_regression.py` | Test fixtures, not operator data |

> Path #2 (config dir 0o700) is set by `config/writer.py` on first write but
> **inherited from the OS at first install** — operators who hand-create the
> dir (e.g. via `mkdir`) must `chmod 0o700` themselves, otherwise a world-
> readable parent dir is a no-op for child 0o600 files (`/proc/self/fd/...`
> is still readable through the dir mode).

---

## Atomic-write primitives — the two load-bearing helpers

```python
# src/backlink_publisher/_util/io.py
def atomic_write_json(path: Path, data: Any, mode: int = 0o600) -> None:
    """write JSON to <path>.tmp → chmod → Path.replace (atomic on POSIX)."""

# src/backlink_publisher/_util/jsonl.py
def atomic_write_jsonl(rows: Iterable[dict[str, Any]], path: Path, mode: int = 0o600) -> None:
    """buffer → atomic_write (safe_write.atomic_write) → 0o600 default."""
```

Two additional helpers specialize the pattern:

- `persistence/safe_write.atomic_write(path, bytes, mode)` — bytes-typed,
  used by the credential-rotator and comment-outreach store.
- `config/writer.save_config(...)` — TOML-aware atomic text editor that
  preserves unmanaged sections (Plan 2026-05-19-010 taxonomy). Not a
  primitive; it composes the text-edit + replace pattern by hand because
  TOML section preservation is not a one-shot write.

**All new file surfaces MUST go through one of these three.** Bypassing the
primitive (e.g. `path.write_text(...)` directly) is a budget violation and
should be caught by review; there is no automated lint for it yet (deferred
to follow-up — see "Out of scope" below).

---

## First-party mode gates (CI-enforced)

These are in-tree checks that run on every CI build and fail on regression:

| Gate | Test file | Enforces |
|---|---|---|
| Section preservation | `test_save_config_section_taxonomy_canary.py` | `save_config` (a)/(b)/(c)/(d)/(e) taxonomy — operator's `[sites.*]`, `[anchor.*]`, `[anchor_alarm]`, `[llm.*]` survive a round-trip |
| No orphaned guard scripts | `test_no_orphaned_guard_scripts.py` | Every `scripts/check_*.py` is referenced by a CI workflow / pre-commit hook (else false-confidence code) |
| No raw CSRF mutation | `test_security_toggle_mutation_gate.py` | Tests use the `disable_csrf` fixture, never raw `app.config["CSRF_ENABLED"] = False` (ratchets down grandfather pairs) |
| Comment-store mode | `tests/comment_outreach/store.py::assert_mode_0600` (inline) | Comment-outreach writes FAIL if `path.stat().st_mode & 0o777 != 0o600` |
| Browser profile dir mode | `tests/publishing/browser_publish/_chrome_session_impl.py::_ensure_profile_dir_mode` | Playwright launch FAILs if profile dir is not 0o700 |

---

## Out of scope (deferred to follow-up)

| Deferred enforcement | Trigger to ship | Why deferred |
|---|---|---|
| Lint for `path.write_text` / `open(..., 'w')` outside the three primitives | A real bypass lands in review (none observed) | Regex-based lint is too noisy (too many legitimate `open(..., 'r')`); AST-grep pattern is the right tool, needs evaluation |
| Auto-fixer for stale 0644 credential files (PR #140 left a migration gap) | Operator reports a 0644 file in the wild | Pre-#140 install surface is small; one-shot chmod at first save handles the rest (Plan 2026-05-19-010 §(e) covers emission) |
| Per-platform credential rotation cadence policy | A platform's `--rotate` CLI ships | Out of registry-scope; lives in `secrets.py` once the cadence policy is decided |
| Fsync-before-rename hardening for power-loss safety | Operator reports data loss on a crash | Current `Path.replace` is atomic for readers; fsync before rename is a stronger guarantee with a measurable cost. Cheap to add when needed |

---

## Cross-references

- `docs/architecture/deterministic-planning-principle.md` — the determinism
  contract (this doc is the I/O twin: same status, same defense model).
- `docs/architecture/events-db-scale-tripwire-register.md` — the per-DB
  scaling tripwires for `events.db` (the largest single-file surface).
- `AGENTS.md` §"Config" — the `save_config` section-taxonomy canon.
- `AGENTS.md` §"Monolith Budget" — the SLOC budget for the writers
  themselves (`config/writer.py` ceiling 240, `idempotency/store.py` 758 SLOC
  un-budgeted as of 2026-06-01).
- `src/backlink_publisher/_util/io.py` — the atomic-write JSON primitive.
- `src/backlink_publisher/_util/jsonl.py` — the atomic-write JSONL primitive.
- `src/backlink_publisher/persistence/safe_write.py` — the bytes-typed
  atomic-write primitive used by the credential rotator.
