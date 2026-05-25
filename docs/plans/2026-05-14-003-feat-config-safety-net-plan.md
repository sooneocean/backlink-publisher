---
title: "feat: Config Safety Net — preserve unknown sections + atomic write + snapshot history"
type: feat
status: completed
date: 2026-05-14
completed: 2026-05-14
origin: docs/ideation/2026-05-14-round3-fresh-pass-ideation.md (idea #1)
---

# Config Safety Net

## Problem Frame

`save_config` (`config.py:741-829`) hand-rolls TOML output for four known section roots: `[blogger]`, `[blogger.oauth]`, `[medium]`, `[targets."<domain>"]`. Any other top-level section on disk — `[anchor.proportions]`, `[anchor_alarm]`, `[anchor_alarm.override]`, `[llm.anchor_provider]`, `[medium.browser]`, `[medium.oauth]`, `[sites."<domain>".url_categories]`, `[sites."<domain>".anchor_pools.*]` — is **silently dropped** on every save.

This is the documented incident class behind MEMORY's `feedback_config-save-overwrite-pattern.md`. It's also a load-bearing dependency for this PR's own `[anchor_alarm]` work: an operator who tunes thresholds in `config.toml`, then triggers any `save_config` code path (Blogger token refresh, target keyword update, Medium token write), would silently lose every override.

## Requirements

- R1. `save_config` MUST preserve every top-level TOML section it does not know about, byte-for-byte (including comments and key order within those sections).
- R2. The set of "known" sections is explicit: `[blogger]`, `[blogger.oauth]`, `[medium]`, `[medium.oauth]`, `[medium.browser]`, `[targets."<domain>"]`. Adding a future known section is a one-line edit to a module constant.
- R3. The write itself is atomic: write to `config.toml.new`, fsync, rename. A crash mid-write leaves the original intact.
- R4. Before each save, snapshot the existing `config.toml` to `.config-history/<UTC-timestamp>.toml` (mode 0600). Cap rolling history at `_CONFIG_HISTORY_MAX = 20` files; oldest is deleted when the cap is exceeded.
- R5. The pre-save snapshot is opportunistic — failure to write it (permissions, disk full) MUST NOT block the main save. Log and continue.
- R6. Atomic-write failures (rename failure mid-flight) leave the original file untouched; the function raises the underlying OSError so the caller can surface it.

## Scope Boundaries

- No `bp config diff` command in this PR. Operators can already use system `diff .config-history/<ts>.toml ~/.config/backlink-publisher/config.toml`. A dedicated CLI command waits until a real operator pain surfaces.
- No CRC / integrity check. The snapshot + atomic write is the recovery mechanism; CRC adds machinery without obvious payoff at solo-operator scale.
- No structured "section quarantine" diff log. If an operator's TOML contains a syntax error, `load_config` already raises `DependencyError` — that's the surface where it matters, not in save.
- No `[medium.oauth]` / `[medium.browser]` writing by save_config in this PR — they remain "preserved verbatim from disk" not "written from config dataclass". Same applies to all other non-blogger sections.

## Key Technical Decisions

- **Preserve via raw-text walk, not parse + re-serialize.** Walking line-by-line and copying bytes for unknown sections preserves comments, key order, and formatting that `tomllib` + a TOML writer would normalize away. The cost is a ~30-line lexer-lite function; the payoff is correctness on every TOML quirk we never anticipated.
- **Known sections are matched on root only.** `[medium.browser.foo]` falls under `medium` root. The save_config writer rebuilds the whole `[medium]` tree from `config`'s fields, so any sub-table is implicitly handled — we accept that operators editing `[medium.browser]` by hand will see their edits overwritten by save_config when it next writes the `[medium]` section. This matches existing behavior (the function already overwrites the `[medium]` block whole). Document this as a known limitation in the docstring.
- **Snapshot uses UTC ISO timestamps to filesystem-safe form.** `2026-05-14T07-23-15Z.toml`. ISO + colons replaced with hyphens for Windows compatibility; Z suffix marks UTC explicitly.
- **Snapshot history lives next to config.toml**, not in cache. The operator should be able to find their recovery files alongside the file they're recovering. `.config-history/` is hidden (dot-prefix) to keep `ls` clean.

## Implementation Units

- [x] **Unit 1: `_preserve_unknown_sections` lexer + integration in save_config**
  - Files: `src/backlink_publisher/config.py`
  - Match top-level headings via regex `^\[(\[?)([^\].]+)`; root segment = the first dotted key segment; preserve sections whose root is NOT in `_SAVE_CONFIG_KNOWN_ROOTS`
  - Preserve every line from a kept heading until the next top-level heading or EOF
  - Append preserved text to the new file's content after the known sections

- [x] **Unit 2: Atomic write + pre-save snapshot**
  - Files: `src/backlink_publisher/config.py`
  - Replace `config_path.write_text(...)` with: write to `config_path.with_suffix(".toml.new")` + fsync + rename
  - Before rename, copy current `config.toml` to `.config-history/<UTC-ts>.toml` if it exists; ignore OSError on snapshot failure (R5)
  - Rotate snapshots: keep `_CONFIG_HISTORY_MAX = 20` newest files by mtime

- [x] **Unit 3: Tests covering the documented bug class**
  - Files: `tests/test_config_safety_net.py` (new)
  - Negative-shape: write a config with `[anchor.proportions]`, call save_config with arbitrary args, assert `[anchor.proportions]` survives
  - Same for `[anchor_alarm]`, `[anchor_alarm.override]`, `[llm.anchor_provider]`, `[sites.*]`
  - Comments inside preserved sections survive
  - Atomic write: simulate rename failure, original file is untouched
  - Snapshot: post-save, `.config-history/*.toml` contains pre-save bytes
  - Rotation: 25 saves yields exactly 20 snapshot files

## Test scenarios

- Happy — save_config called on a config.toml containing `[anchor_alarm]` with global thresholds + 2 override rows. After save, file still contains all three lines and both override blocks verbatim.
- Happy — save_config called on a config.toml containing `[sites."51acgs.com".url_categories]` + `[sites."51acgs.com".anchor_pools.home.branded]`. After save, all deep tables survive byte-for-byte.
- Happy — comments inside `[llm.anchor_provider]` survive (e.g. `# api_key = "sk-..."`).
- Edge — empty file → no snapshot written, save still succeeds.
- Edge — config.toml does not exist → no snapshot, save succeeds.
- Edge — `.config-history/` does not exist → created with mode 0700.
- Error path — atomic rename fails → original file untouched.
- Error path — snapshot directory unwritable → save still succeeds, warning logged.
- Rotation — 25 sequential saves leaves exactly 20 snapshots, the 5 oldest deleted.
- Backward compat — every existing `test_save_config_*` continues to pass (no behavior regression on the four known sections).

## System-Wide Impact

- `save_config` is called from publish-time OAuth refresh paths (Blogger token, Medium token). Every existing caller benefits without code change.
- `.config-history/` is a new on-disk artifact. Document in README. No data privacy concern — snapshot only the file the operator already owns.
- The exit-code namespace is unchanged. This is a library-level fix; no CLI surface changes.

## Sources & References

- `feedback_config-save-overwrite-pattern.md` — documented incident class
- `feedback_test-locks-in-bug.md` — tests must include negative-shape (config WITH the section, then assert it survives) not just positive-shape (config without it)
- `src/backlink_publisher/anchor_profile.py:183` — existing per-site lock pattern is the inspiration; not reused here because save_config is process-local
- `src/backlink_publisher/io_utils.py` — `atomic_write_json` is the existing pattern for atomic writes; we mirror its shape
