---
title: "feat: Bridge WebUI llm-settings.json into pipeline config (activate Pro Mode article gen)"
type: feat
status: completed
date: 2026-05-29
claims: {}
---

# feat: Bridge WebUI llm-settings.json into pipeline config (activate Pro Mode article gen)

## Overview

The WebUI **Pro Mode AI 生成** toggle (`use_article_gen`) and its LLM credentials (endpoint /
api_key / model) are saved to `~/.config/backlink-publisher/llm-settings.json`. The actual publish
pipeline, however, reads `config.llm_anchor_provider`, which `load_config()` parses **only** from
`config.toml` `[llm.anchor_provider]` and `BACKLINK_LLM_*` env vars — it never reads
`llm-settings.json`. The two sources never meet, so today `load_config().llm_anchor_provider` is
`None` and flipping Pro Mode on in the WebUI changes nothing at runtime.

This plan adds a **tolerant sidecar-fallback layer** to config loading: when no TOML section and no
env override exist, `load_config()` builds `LLMProviderConfig` from `llm-settings.json`. With the
provider populated, the existing consumer at `_payload.py:144` (`if … use_article_gen:` →
`generate_article_body`) fires for real, end-to-end, through the WebUI's `/ce:generate` path. Scope is
**article generation only** (operator-confirmed); image generation is a separate track (different
config + token store) and is explicitly out of scope.

## Problem Frame

The "LLM 接口" already exists and works in isolation: `OpenAICompatibleProvider.generate_article_body`
is implemented, and the WebUI **Test 生成** button (`routes/llm.py` → `settings_preview_llm`) reads
`llm-settings.json` directly and proves the model is reachable. What is missing is the wire from saved
settings into a real run.

Integration-verified consumer trace (the gap is at the consumer layer, not the leaf):
- `/ce:generate` / `/ce:preview` (`webui_app/routes/pipeline.py:140,294`) → `_api.plan(...)`
- → `webui_app/api/pipeline_api.py:291` `cfg = load_config()`
- → `pipeline_api.py:305` `plan_rows(rows, cfg, …)`
- → `cli/plan_backlinks/_payload.py:144` `if config.llm_anchor_provider and …use_article_gen:`
- `load_config().llm_anchor_provider` is **`None`** (verified live: TOML has no `[llm.anchor_provider]`,
  no `BACKLINK_LLM_*` env), so the branch is dead → article body always falls back to the template.

There is no requirements doc for this exact wiring. The prior brainstorm
`docs/brainstorms/2026-05-28-ai-gen-pro-group-requirements.md` deliberately scoped Pro Mode as a
**UI organizational label, not a feature gate** ("任何用戶都可以手動展開並啟用；折疊純粹是視覺收納"). This
plan is the functional counterpart: make enabling it actually do something.

## Requirements Trace

- R1. When `config.toml` has no `[llm.anchor_provider]` section **and** no `BACKLINK_LLM_*` env var is
  set, `load_config().llm_anchor_provider` is populated from a usable `llm-settings.json`, mapping
  `endpoint → base_url` and carrying `api_key`, `model`, `temperature`, `system_prompt`,
  `use_article_gen`, `article_system_prompt`.
- R2. Precedence is **env var > TOML `[llm.anchor_provider]` > `llm-settings.json` sidecar**. An explicit
  TOML section or env override always wins and the sidecar is not consulted (operator's explicit intent
  beats the GUI convenience file).
- R3. The sidecar reader is **fail-soft**: a missing file, malformed JSON, missing required field
  (endpoint/api_key/model blank), or a non-`https://` endpoint yields `None` (Pro Mode simply stays
  off) and **never raises** — `load_config()` must not start failing for unrelated runs because of a
  bad LLM settings file.
- R4. With the provider populated and `use_article_gen=true`, a real WebUI `/ce:generate` run calls
  `generate_article_body` and emits the existing `plan_logger.info("LLM article body generated …")`
  signal; on LLM error it falls back to the template (existing `_payload.py:162` behavior preserved).
- R5. A non-`https://` endpoint can no longer be saved silently as a dead Pro Mode config: the WebUI
  save path rejects it with a clear message (closes the "saved http endpoint → Pro Mode silently never
  activates" trap), keeping the on-disk file always bridge-usable. *(Recommended guard — see Unit 3.)*

## Scope Boundaries

- **Out of scope: image generation.** `use_image_gen` / `image_gen_api_key` flow through a separate
  config (`config.image_gen`, parsed from `[image_gen]` TOML: `base_url`/`model`/`banner_size`/caps)
  plus a separate token store (`frw-token.json` via `load_frw_token()`). The WebUI Pro Mode UI does not
  capture an image endpoint/model, so wiring image gen end-to-end is a larger, separable effort. The
  sidecar reader will *carry* `use_image_gen`/`image_gen_api_key` onto `LLMProviderConfig` (the fields
  exist) but nothing in this plan acts on them.
- **Not changing** the existing TOML/env parsing in `_parse_llm_anchor_provider` — the sidecar is a
  pure additive fallback below it.
- **Not changing** the anchor-resolver or `_banners.py` consumers — see System-Wide Impact for the
  (intended, low-blast-radius) side effects of merely populating the provider.
- **Not adding** a `--from-llm-settings` CLI flag or any new public surface; the bridge is transparent.
- No new credential file; `llm-settings.json` already exists and is written 0o600 by the WebUI.

## Context & Research

### Relevant Code and Patterns

- **The gap source:** `src/backlink_publisher/config/parsers/llm.py` — `_parse_llm_anchor_provider`
  resolves only env + TOML section; returns `None` when all of base_url/model/api_key are absent
  (`llm.py:93`), and **raises `InputValidationError`** when a section is partially present or the
  endpoint is non-https (`llm.py:99-117`). The sidecar reader must be more lenient than this.
- **The consumer (already correct):** `src/backlink_publisher/cli/plan_backlinks/_payload.py:144-166`
  — gated on `config.llm_anchor_provider and …use_article_gen`, instantiates its own
  `OpenAICompatibleProvider` (passing `article_system_prompt`), calls `generate_article_body`, and
  already falls back to template on exception.
- **The loader seam:** `src/backlink_publisher/config/loader.py:194-197` calls
  `_parse_llm_anchor_provider(...)`; the fallback hooks in immediately after.
- **Sidecar-credential precedent to mirror:** `src/backlink_publisher/_util/secrets.py` —
  `frw_token_path()` / `load_frw_token()` read a JSON sidecar from `_config_dir()`, auto-chmod loose
  perms, and raise a typed error only when genuinely malformed. The core already reads sidecar JSON
  files from the config dir; this is the established shape.
- **The settings writer/loader (field names, defaults):** `webui_app/services/settings_service.py`
  `load_llm_settings()` and `webui_app/routes/llm.py` `_LLM_DEFAULTS` — canonical key set:
  `api_key, endpoint, model, temperature, system_prompt, use_article_gen, article_system_prompt,
  image_gen_api_key, use_image_gen`. Note **`endpoint`** (JSON) ↔ **`base_url`** (`LLMProviderConfig`).
- **Shared config dir:** `settings_service._llm_settings_path()` uses
  `backlink_publisher.config._config_dir`; the loader uses `_resolve_config_dir()` → same directory.
  The sidecar reader must resolve the path the same way so WebUI-write and pipeline-read agree.
- **Field shape (verified):** `LLMProviderConfig(base_url, api_key, model, timeout_s=30.0,
  temperature=0.7, system_prompt=None, use_article_gen=False, article_system_prompt=None,
  use_image_gen=False, image_gen_api_key=None)`. `llm-settings.json` has no `timeout_s` → default 30.0.

### Institutional Learnings

- Memory `project_ideation_integration_grounding`: "verify the caller/consumer layer, not just the leaf;
  'silent gap' claims are often already closed upstream." Applied here — the consumer trace was walked
  end-to-end and the gap is real and live (`llm_anchor_provider is None` confirmed at runtime).
- Memory `feedback_multisession_git_safety`: concurrent agents share the working tree — build this on a
  worktree off `origin/main`; never `git stash` / `git add .`.
- Repo convention (`AGENTS.md`): plans dated ≥2026-05-20 need a `claims:` block (`{}` opt-out used
  here); tests run under `PYTHONHASHSEED=0` (pytest-env); test files are flat in `tests/` as
  `test_<area>_*.py`.

### External References

None required — this is repo-local config plumbing following an existing in-repo sidecar pattern. No
external/library behavior is in question. (External research skipped: ≥3 strong local patterns exist —
`load_frw_token`, the existing parser, the existing consumer.)

## Key Technical Decisions

- **Bridge in the core config layer, not the WebUI.** A core sidecar fallback makes Pro Mode work for
  *every* consumer of `load_config()` (WebUI in-process plan, scheduler, and CLI) with one change,
  versus a WebUI-only injection that would have to be repeated at each run entry point and would leave
  CLI runs unable to use the GUI-saved settings. Rationale: matches the `load_frw_token` precedent (core
  reads config-dir sidecar JSON), keeps the change in one place, and the file already lives in the
  shared config dir. No `webui_app` import is introduced into core.
- **Fail-soft, never fail-loud, for the sidecar.** Unlike `_parse_llm_anchor_provider` (which raises on
  a partial/invalid TOML section because that signals operator misconfiguration), the sidecar is a
  best-effort convenience source. A bad `llm-settings.json` must degrade to "Pro Mode off", not break
  config loading for the whole pipeline. A single `plan_logger.info`/`debug` line explains *why* it was
  ignored (e.g. non-https endpoint) so the operator can diagnose without a crash.
- **Precedence env > TOML > sidecar, realized by ordering.** `_parse_llm_anchor_provider` already
  returns non-`None` whenever env or TOML supplies a provider; the sidecar is consulted **only** when
  that result is `None`. No precedence logic is duplicated.
- **Reuse `LLMProviderConfig` as-is.** No new type. `endpoint → base_url` is the only rename; carry the
  remaining fields 1:1 (including the two image fields, inert in this scope).
- **Keep https enforcement.** The sidecar honors the same `https://` requirement the TOML parser
  enforces (prompt-injection / credential-exfiltration posture). The difference is *how* it fails: TOML
  raises; sidecar returns `None`. Unit 3 stops a non-https endpoint from being saved in the first place.

## Open Questions

### Resolved During Planning

- *Scope — article only or also image?* → **Article only** (operator-confirmed). Image gen deferred
  (separate config + FRW token store).
- *Where to bridge?* → Core config layer (sidecar fallback), per the decision above.
- *What if both TOML and sidecar exist?* → TOML wins (precedence env > TOML > sidecar).
- *What if the saved endpoint is `http://`?* → Sidecar returns `None` (degrade); Unit 3 prevents saving
  it.

### Deferred to Implementation

- Exact home of the reader function (a new private helper in `config/parsers/llm.py` vs a small
  `config/_llm_sidecar.py`). Decide when touching the file; keep it import-light (no `webui_app`).
- Whether to auto-chmod a loose-perm `llm-settings.json` on core read (mirror `load_frw_token`) or rely
  on the WebUI loader's existing auto-chmod. Decide at implementation; lean to a best-effort chmod for
  parity, but it must not raise.
- Log level/wording of the "sidecar ignored because …" line.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation
> specification. The implementing agent should treat it as context, not code to reproduce.*

Resolution order for `config.llm_anchor_provider` (first usable source wins):

```
load_config()
  └─ _parse_llm_anchor_provider(toml["llm"]["anchor_provider"])
       ├─ BACKLINK_LLM_* env vars        ─┐  (highest precedence)
       └─ [llm.anchor_provider] in TOML  ─┤  → returns LLMProviderConfig  ──► used; STOP
                                           │
                                  result is None?
                                           │ yes
                                           ▼
     _llm_provider_from_sidecar()  (NEW, tolerant — never raises)
       read  <config_dir>/llm-settings.json
         file missing / bad JSON ............................ ► None  (Pro Mode off)
         endpoint|api_key|model blank ....................... ► None  + info log
         endpoint not https:// .............................. ► None  + info log "non-https; ignored"
         else map {endpoint→base_url, api_key, model,
                   temperature, system_prompt, use_article_gen,
                   article_system_prompt, use_image_gen,
                   image_gen_api_key} ........................ ► LLMProviderConfig
```

Consumer (unchanged) once the provider is non-`None`:

```
plan_rows(rows, cfg)
  └─ _payload.py: if cfg.llm_anchor_provider and cfg.llm_anchor_provider.use_article_gen:
        body = OpenAICompatibleProvider(...).generate_article_body(...)   # real LLM call
        plan_logger.info("LLM article body generated for <domain>")
     except: body = template(...)   # existing fallback
```

## Implementation Units

- [x] **Unit 1: Tolerant `llm-settings.json` → `LLMProviderConfig` sidecar reader**

**Goal:** A core function that turns a usable `llm-settings.json` into an `LLMProviderConfig`, and
turns anything unusable into `None` without ever raising.

**Requirements:** R1, R3

**Dependencies:** None

**Files:**
- Modify: `src/backlink_publisher/config/parsers/llm.py` (add private `_llm_provider_from_sidecar()`;
  or create `src/backlink_publisher/config/_llm_sidecar.py` if cleaner)
- Test: `tests/test_config_llm_sidecar.py` (new)

**Approach:**
- Resolve the path via the same dir resolution the loader uses (`_resolve_config_dir()` /
  `_config_dir()`), filename `llm-settings.json`.
- Map `endpoint → base_url`; carry `api_key, model, temperature, system_prompt, use_article_gen,
  article_system_prompt, use_image_gen, image_gen_api_key`; `timeout_s` defaults to 30.0.
- Guard rails, each returning `None` (no raise): file absent; `json.loads` failure; any of
  `endpoint`/`api_key`/`model` blank or non-string; `endpoint` not starting `https://`. Emit one
  `plan_logger.info`/`debug` line on the "present but unusable" cases (especially non-https) so the
  operator can diagnose.
- Booleans coerced like the existing parser (`bool(...)`); temperature coerced to float with a safe
  default on bad input.

**Patterns to follow:**
- `src/backlink_publisher/_util/secrets.py::load_frw_token` (sidecar read shape, loose-perm handling)
- `src/backlink_publisher/config/parsers/llm.py` (field names, https rule, bool coercion) — but invert
  the failure mode from raise → `None`.

**Test scenarios:**
- Happy path: complete https `llm-settings.json` → `LLMProviderConfig` with `base_url == endpoint` and
  all fields mapped (incl. `use_article_gen=True` when set).
- Happy path: `temperature`/`system_prompt`/`article_system_prompt` carried through; absent `timeout_s`
  → 30.0.
- Edge case: file does not exist → `None`.
- Edge case: empty/whitespace `api_key` (or `endpoint`, or `model`) → `None`.
- Edge case: defaults-only file (all blanks, both toggles false) → `None`.
- Error path: malformed JSON → `None` (no exception); an info/warn line is logged.
- Error path: `http://` endpoint → `None` + a logged "non-https; ignored" line.
- Edge case: loose file perms (0o644) → still readable, does not raise (chmod best-effort if adopted).

**Verification:**
- `_llm_provider_from_sidecar()` returns the expected object/`None` for each scenario; no scenario
  raises.

- [x] **Unit 2: Wire the sidecar as a fallback in `load_config()` (precedence env > TOML > sidecar)**

**Goal:** `load_config()` populates `llm_anchor_provider` from the sidecar only when env/TOML produced
nothing.

**Requirements:** R1, R2

**Dependencies:** Unit 1

**Files:**
- Modify: `src/backlink_publisher/config/loader.py` (around lines 194-197 / 246)
- Test: `tests/test_config_llm_sidecar.py` (extend), `tests/test_config.py` (regression guard)

**Approach:**
- After `llm_anchor_provider = _parse_llm_anchor_provider(...)`, add:
  `if llm_anchor_provider is None: llm_anchor_provider = _llm_provider_from_sidecar()`.
- Because the existing parser already returns non-`None` for any env/TOML-configured provider, this
  ordering yields env > TOML > sidecar with no extra precedence code.
- Tests must isolate the config dir (monkeypatch `_config_dir` / `BACKLINK_PUBLISHER_CONFIG_DIR`) and
  clear `BACKLINK_LLM_*` env so the sidecar path is deterministic under `PYTHONHASHSEED=0`.

**Patterns to follow:**
- Existing `loader.py` parser-call style; existing config tests' use of a temp config dir + env
  scrubbing (`tests/test_config*.py`).

**Test scenarios:**
- Integration: no TOML `[llm.anchor_provider]`, no env, usable sidecar present →
  `load_config().llm_anchor_provider` is populated with `use_article_gen` reflecting the file.
- Integration (precedence): TOML `[llm.anchor_provider]` present **and** sidecar present → TOML wins;
  sidecar ignored (assert resolved `base_url`/`model` match TOML, not the sidecar).
- Integration (precedence): `BACKLINK_LLM_BASE_URL`/`_API_KEY`/`_MODEL` env set, no TOML, sidecar
  present → env wins.
- Integration: no TOML, no env, no/blank sidecar → `llm_anchor_provider is None` (today's behavior
  preserved; pipeline uses templates).
- Regression: a bad sidecar (malformed JSON / http endpoint) does **not** make `load_config()` raise —
  it still loads, with `llm_anchor_provider is None`.

**Verification:**
- `pytest tests/test_config_llm_sidecar.py tests/test_config.py` green; precedence and fail-soft
  assertions hold.

- [x] **Unit 3 (recommended guard): Reject non-`https://` endpoint at WebUI save time**

**Goal:** Prevent saving an `llm-settings.json` that the bridge will silently ignore, so "I enabled Pro
Mode but nothing happens" cannot be caused by a scheme mismatch.

**Requirements:** R5

**Dependencies:** None (independent of Units 1-2; complements them)

**Files:**
- Modify: `webui_app/routes/llm.py` (`settings_save_llm_config`, around the `endpoint` handling at
  lines 103-111)
- Test: `tests/test_webui_llm_settings_save.py` (new; mirror existing `tests/test_webui_*` route-test
  style)

**Approach:**
- When an `endpoint` is provided and non-empty, require it to start with `https://` (after the existing
  `.strip().rstrip('/')`). On violation, return a clear `_safe_flash_redirect(... flash_type='danger',
  msg='Endpoint 必须以 https:// 开头', fragment='sect-ai')` and do not persist the change. Mirrors the
  core parser's https rationale so WebUI-saved and TOML-configured providers agree.
- Leave the blank-endpoint case (secret-preserve flow) untouched.

**Execution note:** Start with a failing route test asserting an `http://` endpoint is rejected and not
written, then implement the guard.

**Patterns to follow:**
- `webui_app/routes/llm.py` existing `_safe_flash_redirect` usage and the `_guard_llm_endpoint`
  https/scheme posture in `settings_test_llm`.
- An existing `tests/test_webui_*` route test for Flask test-client + form-post shape.

**Test scenarios:**
- Happy path: `https://…` endpoint saved successfully; file updated; success flash.
- Error path: `http://…` endpoint rejected; `llm-settings.json` unchanged; danger flash with the
  scheme message.
- Edge case: blank `endpoint` (secret-preserve path) still works and preserves the stored endpoint —
  guard does not regress the partial-edit flow.

**Verification:**
- `pytest tests/test_webui_llm_settings_save.py` green; a manual save of an `http://` endpoint in
  `/settings` is rejected with the message.

## System-Wide Impact

- **Interaction graph:** Populating `cfg.llm_anchor_provider` activates **all** consumers that key off
  its presence, not only article gen:
  - `_payload.py:144` — article body, gated by `use_article_gen` (the intended target). ✅
  - `cli/plan_backlinks/_engine.py:181` — builds an `OpenAICompatibleProvider` whenever the provider is
    non-`None`; that provider is then used by:
    - `anchor/resolver.py:213` — LLM anchor-candidate generation, used **only as a fallback when the
      configured anchor pool is exhausted** (no separate flag). This becomes live once the provider is
      present. Intended/benign, but it is a real behavior change and makes external LLM calls.
    - `_banners.py:82` — image *prompt* generation, but only when `image_gen_runtime` is active, which
      requires `cfg.image_gen.use_image_gen` (a separate, out-of-scope config). So image gen does **not**
      switch on from this change.
  - `comment_outreach/brief.py:204` — instantiates its own provider; check whether it reads
    `cfg.llm_anchor_provider`; if so, comment-draft LLM also gains the GUI-config source (acceptable,
    same credentials).
- **Error propagation:** Sidecar failures degrade to `None` (R3); article/anchor LLM call failures keep
  the existing per-call fallback (`_payload.py` template fallback; resolver returns `None` candidate;
  `_banners.py` prompt fallback). No new failure path reaches the operator as a crash.
- **State lifecycle risks:** `llm-settings.json` is read fresh inside `load_config()`; the WebUI caches
  config via `_g_cache('config', load_config)` per request — a settings change takes effect on the next
  config load (new request / new run), not mid-run. Acceptable; note in docs.
- **API surface parity:** No new public API, CLI flag, or schema. The only contract widened is "where
  `llm_anchor_provider` may come from" (now also the sidecar). CLI users who never wrote
  `llm-settings.json` are unaffected (file absent → `None`).
- **Integration coverage:** Unit 2's integration tests prove the WebUI-saved file actually surfaces in
  `load_config()` — the cross-layer behavior unit tests of Unit 1 alone would not prove.
- **Unchanged invariants:** `_parse_llm_anchor_provider` env/TOML behavior, its https requirement, and
  the `_payload.py` consumer logic are all untouched; the sidecar is strictly additive and lowest
  precedence.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| A bad `llm-settings.json` breaks `load_config()` for the whole pipeline | R3 fail-soft: reader returns `None`, never raises; regression test asserts `load_config()` still loads with malformed/http sidecar. |
| Operator surprise: enabling the provider also turns on LLM anchor-candidate fallback (extra external calls / latency / cost) | Documented in System-Wide Impact + Operational Notes; it only triggers on pool exhaustion and uses the same credentials the operator already configured. If undesired, a follow-up can gate it behind an explicit flag. |
| `http://` endpoint saved earlier silently yields no Pro Mode | Unit 3 blocks new non-https saves; Unit 1 logs an explicit "non-https; ignored" line for any pre-existing one. |
| Secret hygiene: api_key now read by core from the sidecar | File is already 0o600 (WebUI writer + settings_service auto-chmod); core read mirrors `load_frw_token` (best-effort chmod, no new on-disk copy, never logged — existing redaction in the client covers error paths). |
| Precedence regression (sidecar shadowing an operator's TOML) | Unit 2 precedence tests assert TOML/env win; sidecar consulted only when parser returns `None`. |

## Documentation / Operational Notes

- Note in `README`/settings docs that the WebUI LLM settings now drive real publish runs (article gen)
  when `config.toml` has no `[llm.anchor_provider]`; if a TOML section exists, it takes precedence over
  the WebUI form.
- Operationally, after saving settings the change applies to the **next** run/request (per-request
  config cache), not an in-flight run.
- Mention the (intended) side effect: a configured provider also enables LLM anchor-candidate fallback
  on pool exhaustion.
- Run `plan-check docs/plans/2026-05-29-007-feat-llm-settings-pipeline-bridge-plan.md` before landing
  (claims gate; `{}` opt-out should pass).

## Sources & References

- Gap source: `src/backlink_publisher/config/parsers/llm.py`, `src/backlink_publisher/config/loader.py:194`
- Consumer: `src/backlink_publisher/cli/plan_backlinks/_payload.py:144`, `.../_engine.py:181`,
  `src/backlink_publisher/anchor/resolver.py:213`, `.../_banners.py:15,82`
- WebUI settings: `webui_app/services/settings_service.py`, `webui_app/routes/llm.py`,
  `webui_app/api/pipeline_api.py:291,305`, `webui_app/routes/pipeline.py:140,294`
- Sidecar precedent: `src/backlink_publisher/_util/secrets.py` (`load_frw_token`)
- Prior context: `docs/brainstorms/2026-05-28-ai-gen-pro-group-requirements.md` (Pro Mode = UI label),
  `docs/plans/2026-05-28-010-feat-llm-pro-mode-collapse-plan.md` (the collapse UI),
  `docs/plans/2026-05-27-006-feat-generate-backlink-text-plan.md` (publish-free LLM client)


## Post-Implementation Follow-ups (from code review)

Two pre-existing / out-of-scope items surfaced during review, deferred deliberately to keep this PR focused on the article-gen bridge:

1. **Publish-path SSRF/allowlist gap (pre-existing).** `OpenAICompatibleProvider` POSTs the Bearer api_key to `base_url` with only an `https://` prefix check — no host allowlist / SSRF gate (unlike the WebUI `/settings/test-llm-connection` route) and `requests` follows redirects by default. This affects the TOML/env config paths identically; this bridge mirrors that posture, it does not weaken it. Fix belongs at the provider/publish boundary in a dedicated security plan (apply `guard_llm_endpoint` + `allow_redirects=False` for credentialed LLM calls), closing all config sources at once.
2. **SEC-6 0o600 warning parity for `llm-settings.json`.** Core now reads this credential-bearing sidecar but doesn't emit the loose-perms warning that `config.toml` credential sections get. Low exposure today (the WebUI writes it 0o600 and auto-chmods on load), so deferred.
