# AGENTS.md — backlink-publisher

See `README.md` for project overview and `docs/` for plans, brainstorms, ideation, and solutions.

## Lessons capture (dual-track)

The project keeps lessons in two places:

- **Private auto-memory** — Claude Code automatically writes `feedback_*.md` files at `~/.claude/projects/<project-memory-slug>/memory/` during sessions. These are fast-capture, operator-private, and never committed.
- **Public `docs/solutions/`** — High-value or recurring lessons get *promoted* into committed markdown entries under `docs/solutions/<category>/` (categories: `best-practices/`, `logic-errors/`, `test-failures/`, `ui-bugs/`). The promotion tool is `/ce:compound` (a Claude Code skill from the `compound-engineering` plugin — see plugin docs); it generates the frontmatter schema each existing entry uses.

**Promotion is rewriting, not copy-paste. Strip session UUIDs, real domains, absolute paths, and user-identifying quotes; teach the pattern, not the incident.** The grep gates in `docs/plans/2026-05-15-001-refactor-lessons-kit-curation-plan.md` (Unit 5) are the safety net; the gitignored token file at `~/.local/share/backlink-publisher/private-tokens.txt` enumerates what to scrub.

**First-time setup** (per-operator; the token file is local-only and never shared): see `docs/plans/2026-05-15-001-refactor-lessons-kit-curation-plan.md` Unit 1.5 for the bootstrap recipe. A new contributor must populate `~/.local/share/backlink-publisher/private-tokens.txt` with their operator-private patterns (real target domains, operator email, run-ID patterns) before running `/ce:compound`, or the grep gates will vacuously pass against an empty pattern file.

Next curation review: **2026-08-15** — *aspirational quarterly cadence; not enforced by CI or any tool*. This file is static markdown; the actual trigger is "next time `/ce:compound` or `/ce:plan` runs in this repo, scan recent `feedback_*.md` and decide what's worth promoting." Update this date when the review completes; treat skipping a quarter as a soft signal, not a failure.

Soft observation (2026-05-15): historical `docs/brainstorms/` and `docs/plans/` files contain real operator domain references (e.g. target hostnames). The sanitization rule above applies to `docs/solutions/` entries; if the project ever needs to extend it to historical decision artifacts, scope a separate pass — do not retrofit silently.

## Worktree Auto-Cleanup

Sibling `bp-<topic>/` git worktrees accumulate after parallel feature work — even with discipline, fresh clones and concurrent agent sessions reintroduce sprawl. Two scripts manage cleanup:

- **`bash scripts/prune-stale-worktrees.sh`** — interactive helper. Lists worktrees whose branch tip is reachable from `origin/main` (handles squash-merge via `gh pr list` when available; falls back to direct `git merge-base --is-ancestor` otherwise). Skips dirty worktrees and the main worktree. Flags: `--dry-run` (list only), `--force` (cron-safe, no prompts), `--help`.
- **`bash scripts/install-post-merge-hook.sh`** — per-clone installer that writes a `post-merge` hook to `.git/hooks/`. The hook fires after `git merge` / `git pull` on `main` and **notifies by default** about stale worktrees. To enable auto-removal after the hook's dirty-state check, set `export BACKLINK_PUBLISHER_WORKTREE_AUTOREMOVE=1` in your shell rc. Re-run the installer after fresh clones (git hooks are not committed).

Safety: both refuse to remove the worktree the script is running in, both honor the dirty-state guard (no force-remove of uncommitted work), and the prune helper exits 2 if any removal fails so cron-style invocations can alert. Coverage: `tests/scripts/test_prune_stale_worktrees.py`.

## Monolith Budget

`monolith_budget.toml` at repo root tracks radon SLOC ceilings for five named source files: `src/backlink_publisher/cli/plan_backlinks.py`, `src/backlink_publisher/cli/publish_backlinks.py`, `src/backlink_publisher/content/fetch.py`, `src/backlink_publisher/config/writer.py`, `src/backlink_publisher/_util/markdown.py`. Enforced by `tests/test_no_monolith_regrowth.py` (hard-fail R4 + warning canary R7 + radon counter pinning).

**When to edit:** if your PR pushes a monitored file's SLOC past its `ceiling`, the test fails. Edit `monolith_budget.toml` in the same PR — raise the ceiling and rewrite the `rationale` to explain what motivated the growth and the shape this file is expected to settle to over the next few sprints (the rationale field must be ≥80 chars).

**Journal, not gate.** A solo developer can rubber-stamp any bump — the defense is `git blame` on `monolith_budget.toml`. Every intentional bump leaves a reviewable record. There is no override label and no warning-only mode for the primary check.

**F7 does not decompose anything.** The surgical extraction plans (F2 `ErrorClass` oracle, F3 `safe_write` carve from `config/writer.py`, F5 `ThrottleClock`) are separate work. F7 only prevents regrowth after such carves land.

**Bumping `radon` is treated as a budget edit** (pinned exactly in `pyproject.toml`'s `[project.optional-dependencies].dev`). The bump PR must re-measure all five ceilings via `python -m radon raw -s <paths>` and update the SLOC canary fixture's `SLOC_CANARY_EXPECTED` in the test file.

**Recommended branch protection on `main`:** enable "Require branches to be up to date before merging." Protects against two concurrent PRs each bumping the same file's ceiling and producing a post-merge state that fails R4. The existing `push: branches: [main]` CI lane catches violations post-merge regardless, but pre-merge prevention is cheaper than a revert under pressure.

References: `docs/plans/2026-05-18-006-feat-monolith-sloc-ceiling-plan.md`, `docs/brainstorms/2026-05-18-monolith-loc-ceiling-requirements.md`.
