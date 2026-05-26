---
title: "ce:review findings — opt verify-consolidation (stash@{0})"
type: review-note
status: open
date: 2026-05-26
reviews: docs/plans/2026-05-26-002-opt-verify-consolidation-plan.md
target: "stash@{0} (do NOT pop) / branch wip/deep-optimization"
claims: {}
---

# Review: verify-consolidation refactor

> Written by `/ce:review` while the change was stashed (`stash@{0}`, marked "do NOT
> pop"). Reviewed **read-only** via `git show stash@{0}:…` — no pop, no fixes applied.
> This is a **sibling note** (distinct filename) so it won't block `git stash pop`
> of the untracked plan doc. **Static-only review: tests were not run against the
> refactored code** (can't, without popping). Verdict: **Ready with fixes.**

## Verified correct ✓
- 6 helpers all defined-once + fully used (`_never_result`×13 uses, others ×4). No dead code. `py_compile` clean.
- SLOC measured: base **545** → refactor **447**; `round_up_to_10(447+30)=480`; 447 ≤ 480 → monolith gate passes.
- `requests` import centralized into `_do_live_request`; no dangling refs in per-platform funcs.
- non-JSON response messages preserved byte-for-byte via label arg.

## Must-fix before landing

### P2-1 — Load-bearing "why" comments deleted (maintainability)
The refactor removed **all** rationale comments, not just duplicated scaffolding. grep on the
post-refactor file: `has_brave`=0, `read-only|never rotat|silent-drop`=0. Lost:
- Medium `has_brave`-exclusion note (prevents a future dev re-adding has_brave → verify passes but publish crashes non-recoverably).
- telegraph / ghpages / blogger "read-only, NEVER triggers token rotation" invariants — these are documented contracts (`reference_telegraph_adapter_credential_rotation_pattern`).
- velog silent-drop (`data.auth is null` → token_expired) explanation.
- `_utc_now_iso` UTC/TZ regression lesson.
- `verify_adapter_setup` three-mode contract docstring.

radon SLOC **counts docstrings**, so deleting them is partly *how* 447 was hit — i.e. documentation
was traded for budget. **Action:** restore at least the Brave trap + the 3 read-only/NEVER-rotate
invariants + the TZ lesson; set the ceiling to whatever dedup-only SLOC yields.

### P2-2 — Plan/budget cite the wrong base SLOC
Plan Results table and `monolith_budget.toml` rationale both say **614 → 447 (−167, 27%)**.
Actual base (the commit the stash applies to, `5c1a7b8`) is **545 SLOC / 809 LOC**, not 614/876.
Real reduction is **545 → 447 (−98, 18%)**. The 447, ceiling 480, and math are correct — only the
"before" number is wrong. **Action:** change `614` → `545` in both the budget rationale and the plan
Results table (and `876 LOC` → `809 LOC`).

### P2-3 — Plan claims `_verify.py` module that was never created
Plan `claims.paths` lists `src/backlink_publisher/publishing/_verify.py` (intended shared-helper
module), but the stash changes only **2** files — helpers were **inlined** into `__init__.py`.
This is why the real reduction (−98) is smaller than the planned −167: extracting helpers to a
separate module would have moved their lines out of `__init__.py`. **Action — decide one:**
(a) extract `_verify.py` as the plan intended (bigger, cleaner reduction; lets docstrings stay
in-module without re-inflating `__init__.py`), **or** (b) update the plan to drop the `_verify.py`
claim and record the inline decision + corrected SLOC.

### P2-3 addendum — stash vs `wip/deep-optimization` diverge on `_verify.py`
Checked the durable landing branch (`origin/wip/deep-optimization`) read-only:
- **Stash@{0}**: no `_verify.py`; helpers inlined in `adapters/__init__.py`.
- **Branch**: `publishing/_verify.py` **exists but is empty (0 SLOC, no defs)** — a dead scaffold;
  helpers (`_SETUP_CHECKS`/`_do_live_request`) are still inlined in `adapters/__init__.py`.

So the planned module extraction never happened in **either** cut. The branch additionally carries
a dead empty `_verify.py` (satisfies plan `claims.paths` existence but holds nothing). Whoever lands
this must reconcile the two cuts: either (a) actually move helpers into `_verify.py` and delete the
stub, or (b) drop `_verify.py` from `claims.paths` and remove the empty file. **Reconciling these two
parallel takes is the owning session's call — not done here (turf boundary).**

## Lower priority
- **P3-1** verify message wording drift, no test lock: timeout `"<ep> timed out after Ns"` → `"<ep> request timed out after Ns"`; network `"blogger network failure"` → `"blogger users.self network failure"`. grep: no test asserts these strings → silent, cosmetic.
- **P3-2** `_do_live_request`/`_check_json_response` return `Response | VerifyResult` discriminated by `isinstance` (`_VerifyOrResp = Any`) — works, mild type-safety smell.
- **P3-3** `except (ValueError, Exception)` in `_check_json_response` — `Exception` subsumes `ValueError`; redundant (pre-existing, carried from old velog func).

## Mandatory re-test on un-stash (review was static-only)
```
pytest tests/test_telegraph_live_verify.py tests/test_blogger_live_verify.py \
       tests/test_velog_live_verify.py tests/test_adapter_ghpages.py \
       tests/test_verify_adapter_setup_modes.py tests/test_publish_verify_integration.py
```
