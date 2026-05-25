---
title: "refactor(webui): Plan 013 B-1.1 — mode_toggle.js extensions + shared config selects + sticky CSS"
type: refactor
status: completed
date: 2026-05-20
completed: 2026-05-20
claims: {}
---

# WebUI Plan 013 B-1.1 — mode_toggle.js Extensions + Shared 4-Select Partial + Sticky CSS Scope

## Overview

6 deferred polish items from Plan 012 Phase B-1, grouped into 3 units.
Builds directly on `feat/webui-ia-phase-b` (PR #162, merged 2026-05-21).

Recommended execution order: **U2 → U1 → U3** (extract shared partial first to
avoid Unit 1 conflict; CSS last).

## Scope Boundaries

- Only `webui_app/static/js/mode_toggle.js`, `webui_app/templates/index.html`,
  `webui_app/routes/batch.py`
- No changes to pipeline.py, publish flow, adapters, or any non-WebUI code
- No Plan 012 Phase B-2 (historyPanel three-zone refactor — separate scope)

## Key Technical Decisions

- **`target_language` convergence**: `routes/batch.py:47` reads `request.form.get('language', ...)`;
  `routes/pipeline.py:97` reads `target_language`. Plan 013 converges toward `target_language`
  in batch.py with `language` fallback for backwards compatibility.
- **Priority chain for `?tab=batch` deep-link**: server hint > deep-link > localStorage > DEFAULT.
  `window.__batchTabHint` (injected at `index.html` ~line 1968) takes highest priority.
- **`_plansData` JS state**: injected at `index.html` ~line 1654 from `plans_list` jinja —
  used for mid-pipeline confirm (Unit 1 polish #3).

## Implementation Units

- [ ] **Unit 2: Shared 4-select partial + backend fallback** (do this FIRST)

**Goal:** Extract the 4 language/target selects shared between single-publish
(#configForm) and batch (#batchPanel) into one reusable Jinja macro or include,
and converge the `batch.py` backend field name to `target_language`.

**Files:**
- Create: `webui_app/templates/_shared_config_selects.html`
- Modify: `webui_app/templates/index.html` (replace 2 duplicated select blocks with include)
- Modify: `webui_app/routes/batch.py` — add `target_language = request.form.get('target_language') or request.form.get('language', ...)` fallback chain
- Test: `tests/test_webui_route_contract.py` — add assertion that batch route accepts `target_language`

**Approach:**
- Grep `index.html` for both 4-select blocks; the single-publish one is at form `#configForm`
  (~line 935+), the batch one is at `#batchPanel` (~line 1559+). Extract shared HTML into
  `_shared_config_selects.html` and use `{% include %}`.
- Keep the include simple — no macros unless arguments are needed.

**Test scenarios:**
- Happy path: `POST /api/batch` with `target_language=zh` accepted (status 200 or redirect)
- Backwards compat: `POST /api/batch` with `language=zh` still accepted
- Template: both form sections render with same select options after include

**Verification:**
- `pytest tests/test_webui_route_contract.py` green
- No Jinja2 `UndefinedError` when rendering index

---

- [ ] **Unit 1: mode_toggle.js extensions** (do after U2)

**Goal:** Extend `mode_toggle.js` (~93 lines) with 4 polish behaviors:
1. URL stash — when switching to single mode, stash `window.location.search` to localStorage
2. Mid-pipeline confirm — if `_plansData` shows active plan, prompt before switching to batch
3. `?tab=batch` deep-link — honor URL param on page load (priority: server hint > deep-link > localStorage > default)
4. History panel hide — in single mode, collapse history toggle row

**Files:**
- Modify: `webui_app/static/js/mode_toggle.js` (+~80-100 lines)
- Modify: `webui_app/templates/index.html` (~5 lines to hook history toggle)
- Test: `tests/test_webui_route_contract.py` — add contract assertion for `?tab=batch` routing

**Approach:**
- Read current `mode_toggle.js` fully before editing
- Add each behavior as a named function block — don't inline into the main toggle handler
- For `?tab=batch`: check `new URLSearchParams(window.location.search).get('tab') === 'batch'`
  AFTER checking `window.__batchTabHint`
- For history panel hide: toggle a CSS class on the history toggle wrapper element;
  CSS rule in Unit 3 scopes it

**Test scenarios:**
- Happy path: page load with `?tab=batch` activates batch mode
- Happy path: `window.__batchTabHint = 'single'` overrides URL param
- Edge case: no `_plansData` or empty plans → no confirm dialog on switch
- Edge case: switching modes with active URL stash restores correctly

**Verification:**
- `pytest tests/test_webui_route_contract.py` green
- `python -m py_compile webui_app/static/js/mode_toggle.js` — N/A (JS); check for syntax errors via node if available, else manual review

---

- [ ] **Unit 3: Step-bar sticky scope CSS** (do last)

**Goal:** Limit the sticky step-bar to single-publish mode only. In batch mode,
the step-bar should not stick to prevent overlap with batch form content.

**Files:**
- Modify: `webui_app/templates/index.html` — add CSS rule scoping `.sticky-step-bar`
  to only apply when `body` has `.mode-single` class (set by mode_toggle.js)
- Modify: `webui_app/static/js/mode_toggle.js` — toggle `body.mode-single` / `body.mode-batch`
  class on mode switch (needed for CSS scoping)

**Approach:**
- CSS rule: `.mode-single .sticky-step-bar { position: sticky; top: 0; z-index: ... }`
  vs `.mode-batch .sticky-step-bar { position: static; }`
- mode_toggle.js should already be adding body class from Unit 1 history-panel logic;
  verify and unify

**Test scenarios:**
- Contract: `index.html` contains `.mode-single .sticky-step-bar` or equivalent scoped rule
- Contract: `mode_toggle.js` adds `mode-single`/`mode-batch` class to `document.body`

**Verification:**
- `pytest tests/test_webui_route_contract.py` green
- No regression in existing mode toggle contract tests

---

## Sources & References

- Origin: Plan 012 Phase B-1 deferred polish items (#1-#6 from plan 012 §347-352)
- Merged base: PR #162 `feat/webui-ia-phase-b` — `mode_toggle.js` (93 lines), `index.html` changes
- `webui_app/routes/batch.py:47` — field-name divergence (converge in U2)
- `webui_app/routes/pipeline.py:97` — `target_language` canonical field name
- `index.html` ~line 1654 — `_plansData` injection
- `index.html` ~line 1968 — `window.__batchTabHint` injection
