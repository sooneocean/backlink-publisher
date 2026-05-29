---
title: "feat: Collapse AI generation features into Pro Mode sub-section"
type: feat
status: complete
date: 2026-05-28
origin: docs/brainstorms/2026-05-28-ai-gen-pro-group-requirements.md
---

# feat: Collapse AI generation features into Pro Mode sub-section

## Overview

Move `use_article_gen` toggle and the `image_gen_api_key` + `use_image_gen` block from their flat position in the LLM settings card body into a labelled Bootstrap collapse sub-section ("Pro Mode AI 生成"). The collapse header is always visible; the body defaults to folded. If either Pro toggle is already enabled in saved config, the body renders expanded on page load via a Jinja2 conditional `show` class — no JS required.

## Problem Frame

The LLM settings card currently flat-renders both basic connection fields and AI generation features in the same visual layer. Non-Pro users see the AI generation options as visual noise. Collapsing them into a clearly-labelled Pro sub-section keeps the settings page clean for standard users while letting Pro users find their features with a single click.

## Requirements Trace

- R1. AI 內容生成引擎 (`use_article_gen`) and AI Banner 生成 (`use_image_gen` + `image_gen_api_key`) are wrapped in `id="llm-pro-mode-collapse"` Bootstrap collapse. (`article_system_prompt` is excluded — backend save route does not read it.)
- R2. Sub-section body defaults collapsed (`class="collapse"`).
- R3. Jinja2 conditional `show` class auto-expands body when either toggle is true: `class="collapse{% if llm_settings.use_article_gen or llm_settings.use_image_gen %} show{% endif %}"`.
- R4. Collapse header: full-width `<button class="llm-pro-toggle" data-bs-toggle="collapse">` containing label text, `bg-warning text-dark` Pro badge, and `bi-chevron-right chevron` icon. `aria-expanded` set server-side to match initial state.
- R5. No Python, route, form field name, or test changes.
- R6. Connection fields (endpoint, api_key, model, temperature, system_prompt) remain outside the collapse, always visible.
- R7. When both toggles are false, collapse body is hidden; header row remains visible.

## Scope Boundaries

- Files changed: `_settings_llm_integration.html` + `settings.css` (one new CSS rule only).
- `article_system_prompt` is NOT added to the UI — backend save route does not persist it from `request.form`.
- No JS changes — Bootstrap 5 handles the toggle; Jinja2 handles initial state.
- No feature gating — all users can expand and enable Pro features manually.

## Context & Research

### Relevant Code and Patterns

- **Target file**: `webui_app/templates/_settings_llm_integration.html` — included by `settings.html:348` via bare `{% include %}`, inherits full parent context.
- **Jinja2 variable**: `llm_settings` (dict, from `helpers/contexts.py:_load_llm_settings()`). Keys: `.use_article_gen` (bool), `.use_image_gen` (bool), `.image_gen_api_key` (str), all guaranteed present with default values.
- **Canonical collapse HTML pattern** (`settings.html` overview panel):
  ```html
  <button class="overview-collapse-toggle"
          data-bs-toggle="collapse" data-bs-target="#PANEL-ID"
          aria-expanded="{{ 'true' if cond else 'false' }}"
          aria-controls="PANEL-ID">
    Label <i class="bi bi-chevron-right chevron" aria-hidden="true"></i>
  </button>
  <div id="PANEL-ID" class="collapse{% if cond %} show{% endif %}">
    ...
  </div>
  ```
- **Canonical chevron CSS** (`settings.css:134–136, 150`):
  ```css
  .chevron { transition: transform 180ms ease-out; font-style: normal; }
  .channel-toggle[aria-expanded="true"] .chevron { transform: rotate(90deg); }
  .overview-collapse-toggle[aria-expanded="true"] .chevron { transform: rotate(90deg); }
  ```
  Pattern: one CSS selector per button class. The new `.llm-pro-toggle` button class needs its own rule appended.
- **Bootstrap version**: 5.3.0 (CDN, loaded synchronously in `<head>`). `aria-expanded` is auto-updated by Bootstrap on every toggle — initial value must match the Jinja2-rendered `show` class.
- **JS interference**: `settings_main.js` collapse logic is scoped to `overview-panel` and `channel-*` IDs. `llm-pro-mode-collapse` will not be touched by any existing JS.
- **`_openCollapseForHash()`** in `settings_main.js`: auto-opens any `.collapse` panel containing the URL hash target — the new Pro section gets deep-link support for free if inner elements have IDs.

### Institutional Learnings

- `aria-expanded` must match the server-rendered `show` class on initial load (Bootstrap reads `aria-expanded` to determine state for the first toggle).
- Bootstrap collapse uses `display:none` (not `visibility:hidden`) — collapsed inputs are removed from tab order automatically. No `disabled` attributes needed.
- Checked-but-collapsed checkboxes still POST: `use_article_gen` and `use_image_gen` will submit their saved values even when the body is folded. This is intentional — the collapse is cosmetic, not functional gating.
- Sticky nav active state on short pages: the settings page currently has a click-pin guard (`pinnedUntil` + 800ms) in `settings_main.js`. Adding the Pro section increases page height when expanded — verify the last sticky nav section still activates correctly. (Existing guard should handle this; no code change expected.)

## Key Technical Decisions

- **`bi-chevron-right` not `bi-chevron-down`**: Existing codebase uses `bi-chevron-right` + `rotate(90deg)` for all collapses. Using `bi-chevron-down` would need a different rotate angle and break the visual convention. Follow the existing pattern.
- **New `.llm-pro-toggle` button class**: Each collapse button type in this codebase has its own CSS selector. Adding `.llm-pro-toggle` to `settings.css` mirrors the established pattern without modifying existing selectors.
- **`bg-warning text-dark` badge**: Confirmed as the decision from brainstorm. This is the project's first Pro-tier badge — no existing convention to clash with. (See origin: `docs/brainstorms/2026-05-28-ai-gen-pro-group-requirements.md` Key Decisions.)
- **No JS for initial state**: Server-rendered `show` class is simpler, cheaper, and more reliable than a JS-driven expand. No race conditions with Bootstrap initialization.
- **Outer card header unchanged**: The "快速填入預設" dropdown remains in the existing outer `card-header`, which is outside the new Pro collapse. No event propagation conflicts.
- **`article_system_prompt` excluded**: Backend `save-llm-config` route reads `endpoint`, `api_key`, `model`, `temperature`, `system_prompt`, `use_article_gen`, `image_gen_api_key`, `use_image_gen` from `request.form` — but NOT `article_system_prompt`. Adding a textarea for it would silently drop user input. Excluded from scope.

## Open Questions

### Resolved During Planning

- **Arrow direction**: `bi-chevron-right` + `rotate(90deg)` — matches existing codebase convention. (`bi-chevron-down` rejected.)
- **Initial state mechanism**: Jinja2 conditional `show` class — no JS needed.
- **Which fields move**: `use_article_gen` checkbox only (from inside the `system_prompt` col-12 div); `image_gen_api_key` input + `use_image_gen` checkbox (existing col-12 div). Both move into the Pro collapse body.
- **`article_system_prompt`**: Excluded from scope — backend route does not persist it.

### Deferred to Implementation

- **Sub-label styling inside Pro section**: Whether each sub-item (content gen vs banner gen) gets its own `<hr>` divider or a small sub-heading is left to implementer judgment — either works, success criteria only test toggle visibility.
- **Scroll-into-view on manual expand**: Low-priority UX polish (P3 from design-lens review); implementer may add `shown.bs.collapse` → `scrollIntoView` if desired. Not required.

## Implementation Units

- [ ] **Unit 1: Add `.llm-pro-toggle` chevron CSS rule**

**Goal:** Extend `settings.css` with one new CSS rule so the chevron on the Pro collapse toggle button rotates correctly on expand.

**Requirements:** R4 (arrow rotates with expand/collapse state)

**Dependencies:** None

**Files:**
- Modify: `webui_app/static/css/settings.css`

**Approach:**
- Append after the existing `overview-collapse-toggle` rule (line 150):
  `.llm-pro-toggle[aria-expanded="true"] .chevron { transform: rotate(90deg); }`
- The base `.chevron` transition (line 134) and `@media prefers-reduced-motion` rule (line 136) already cover all chevron elements — no duplication needed.

**Patterns to follow:**
- `settings.css:150` — `.overview-collapse-toggle[aria-expanded="true"] .chevron { transform: rotate(90deg); }`

**Test scenarios:**
- Test expectation: none — pure CSS addition with no behavioral logic. Visual verification in Unit 2 covers correctness.

**Verification:**
- CSS file contains the new `.llm-pro-toggle[aria-expanded="true"] .chevron` rule and no existing rules are modified.

---

- [ ] **Unit 2: Restructure `_settings_llm_integration.html` to add Pro collapse**

**Goal:** Wrap `use_article_gen` toggle and the image-gen block in a labelled Bootstrap collapse sub-section within the LLM card body. Connection settings remain flat and always visible.

**Requirements:** R1, R2, R3, R4, R5, R6, R7

**Dependencies:** Unit 1 (CSS rule must exist before visually testing the chevron)

**Files:**
- Modify: `webui_app/templates/_settings_llm_integration.html`

**Approach:**
- Inside the existing `<form>` `<div class="row g-3">`, after the `system_prompt` textarea div (and after removing `use_article_gen` from that div), add:
  1. A separator row containing the Pro collapse toggle button:
     ```
     <button class="llm-pro-toggle btn btn-link p-0 …"
             data-bs-toggle="collapse"
             data-bs-target="#llm-pro-mode-collapse"
             aria-expanded="{{ 'true' if llm_settings.use_article_gen or llm_settings.use_image_gen else 'false' }}"
             aria-controls="llm-pro-mode-collapse">
       Pro Mode AI 生成
       <span class="badge bg-warning text-dark">Pro</span>
       <i class="bi bi-chevron-right chevron" aria-hidden="true"></i>
     </button>
     ```
  2. The collapse body div:
     ```
     <div id="llm-pro-mode-collapse"
          class="collapse{% if llm_settings.use_article_gen or llm_settings.use_image_gen %} show{% endif %}">
       <div class="mt-3">
         <!-- AI 内容生成引擎 -->
         <div class="form-check form-switch">
           <input … name="use_article_gen" …>
           <label>启用 AI 全文生成</label>
         </div>
         <!-- AI Banner 生成 -->
         <div class="mt-3">
           <label>AI Cover Image Generation (FRW API)</label>
           <input … name="image_gen_api_key" …>
           <div class="form-check form-switch">
             <input … name="use_image_gen" …>
             <label>启用 AI 封面生成</label>
           </div>
         </div>
       </div>
     </div>
     ```
- Remove `use_article_gen` checkbox from its current location inside the `system_prompt` col-12 div.
- Remove the `image_gen_api_key` + `use_image_gen` col-12 div from its current location.
- All `name` attributes, `id` attributes, and `{% if %}` checks for existing field values remain unchanged.

**Patterns to follow:**
- `settings.html` overview panel collapse structure (lines 49–82) for button + panel pairing
- Existing `use_article_gen` / `use_image_gen` checked states: `{% if llm_settings.use_article_gen %}checked{% endif %}` — preserve exactly

**Test scenarios:**
- **Happy path — both toggles false**: Open `/settings`, scroll to LLM card. Pro section header is visible ("Pro Mode AI 生成 [Pro] ▷"). Body is collapsed — `use_article_gen`, `use_image_gen`, `image_gen_api_key` inputs are not visible. Click header → body expands, inputs appear. Click again → body collapses.
- **Happy path — use_article_gen true**: Page loads with Pro body already expanded (Jinja2 `show` class rendered). `use_article_gen` checkbox is checked inside expanded section.
- **Happy path — use_image_gen true**: Same as above for `use_image_gen`; `image_gen_api_key` shows existing masked value.
- **Happy path — form POST round-trip**: Enable `use_article_gen`, save. Page reloads with Pro body expanded and checkbox still checked. Disable both toggles, save. Page reloads with Pro body collapsed.
- **Edge case — both toggles enabled**: Both toggles show as checked inside expanded body on load.
- **Edge case — `image_gen_api_key` has stored value, toggles false**: Page loads with body collapsed. Existing key value is not visible (inside collapsed body). Expand → key placeholder shows "已設置 (留空保留現值)".
- **Integration — form submission**: POST `/settings/save-llm-config` with the restructured form. Verify `use_article_gen`, `use_image_gen`, `image_gen_api_key` values are saved correctly by the backend route (field names unchanged → route reads them identically).
- **Integration — keyboard nav**: Tab through the LLM card when Pro body is collapsed → `use_article_gen`, `use_image_gen`, `image_gen_api_key` inputs are skipped (Bootstrap `display:none` removes them from tab order). Tab through when expanded → inputs are reachable.

**Verification:**
- `/settings` page loads without Jinja2 or Python errors.
- Pro collapse header visible; body hidden when both toggles false.
- With both toggles false in saved config, AI generation fields are not visible on page load without user interaction (core UX goal: reduced noise for non-Pro users).
- Pro collapse body auto-expanded on load when either toggle is true in saved config.
- Form POST saves all field values correctly (smoke-test via save + reload).
- Existing connection fields (endpoint, api_key, model, temperature, system_prompt) are unaffected.

## System-Wide Impact

- **Interaction graph:** `_settings_llm_integration.html` is a bare `{% include %}` — no macros, no render helpers, no blueprint involvement. Change is fully isolated to this partial.
- **Form surface:** `name` attributes unchanged — `settings_save_llm_config` route in `webui_app/routes/llm.py` continues to read all fields identically.
- **JS:** `settings_main.js` collapse logic is scoped to `overview-panel` and `channel-*`. `llm-pro-mode-collapse` is unaffected by existing JS.
- **Hash-fragment deep links:** `_openCollapseForHash()` auto-expands any `.collapse` containing the anchor target — Pro section gets this for free. No risk of ID collisions (existing IDs: `channel-blogger`, `channel-medium`, etc.; `overview-panel` — `llm-pro-mode-collapse` is unique).
- **Unchanged invariants:** Outer card structure, "快速填入預設" dropdown, save/test/clear buttons, and all connection-setting fields remain exactly as-is.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `aria-expanded` initial value mismatches Jinja2 `show` class | Set both from the same Jinja2 condition in one place — `{% if llm_settings.use_article_gen or llm_settings.use_image_gen %}` |
| `use_article_gen` moved from `system_prompt` div leaves orphaned whitespace/divider | Inspect the col-12 div after removing the form-check; remove any trailing `<hr>` or `mt-2` spacer if present |
| Bootstrap 5.3 `collapse` + inline `show` class: first toggle fires `hide` not `show` if `aria-expanded` is wrong | Jinja2 must set `aria-expanded="true"` when `show` class is present — always derive both from the same condition |
| Page height increase when Pro section is expanded affects sticky nav last-section detection | Existing `pinnedUntil` click-pin guard in `settings_main.js` covers this — verify visually; no code change expected |

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-28-ai-gen-pro-group-requirements.md](../brainstorms/2026-05-28-ai-gen-pro-group-requirements.md)
- Target template: `webui_app/templates/_settings_llm_integration.html`
- CSS file: `webui_app/static/css/settings.css` (lines 134–150 — existing chevron pattern)
- Collapse pattern reference: `webui_app/templates/settings.html` lines 49–82 (overview panel)
- Route (unchanged): `webui_app/routes/llm.py` — `settings_save_llm_config`
- Context loader (unchanged): `webui_app/helpers/contexts.py` — `_load_llm_settings()`
