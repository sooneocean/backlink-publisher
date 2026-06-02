---
title: "feat: Anti-redrift guard — flag shipped-but-active plan-docs"
type: feat
status: completed
date: 2026-06-02
claims: {}  # opt-out: new files not on main until this PR merges
---

# feat: Anti-redrift guard

## Overview

Direct outcome of the 2026-06-01 convergence audit, where 14+ plans marked
`status: active` turned out already merged — the status field had drifted
behind the code. This adds the systemic prevention so the drift-then-manual-
reconcile cycle stops recurring.

## Detection

Inverse of `plan-check`: `plan-check` *passes* when a plan's declared
`claims:` paths/shas resolve on `origin/main`. The redrift guard *flags*
the same resolution **when the status still says in-progress**:

> status in {active, ready}  AND  claims has paths/shas  AND  every path/sha
> resolves on origin/main  →  REDRIFT (work shipped, status never advanced).

Low false-positive by construction. Honest-partial statuses (`partial`,
`phase1-complete`) and terminal statuses are never flagged.

**Blind spot (accepted):** `claims: {}` declares no artifacts, so drift in
opt-out plans can't be detected — they self-exempt. The forward plan-check /
radar already handles missing/false claims; this only adds the inverse signal
for plans that declared their artifacts.

## What shipped

- `scripts/check_plan_redrift.py` — checker (injectable resolvers; reuses
  `cli._plan_check_git` + `_plan_check_schema`). Skips malformed frontmatter.
- `.github/workflows/plan-redrift-gate.yml` — PR job (blocking, touched docs)
  + scheduled radar (09:15 UTC daily, advisory, step-summary report).
- `tests/test_plan_redrift.py` — 16-case decision matrix (status × claims ×
  resolution), driven by injected resolvers (no git needed).

## Verification

`check_plan_redrift.py --all --advisory` against current main: 121 docs, zero
redrift (confirms the convergence cleanup is complete and the guard runs clean).
