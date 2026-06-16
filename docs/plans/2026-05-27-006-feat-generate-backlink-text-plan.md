---
title: "feat: Add generate-backlink-text LLM content-generation CLI stage"
type: feat
status: completed
shipped: cf496e4a (#275)
date: 2026-05-27
deepened: 2026-05-27
origin: docs/brainstorms/2026-05-27-llm-backlink-text-generation-requirements.md
claims: {}
---

# feat: Add `generate-backlink-text` LLM content-generation CLI stage

## Overview

Add a standalone, opt-in CLI verb `generate-backlink-text` that reads backlink
*candidate* records (`{target_url, anchor_text, mode}`) from stdin/file, calls an
OpenAI-compatible LLM via a small **publish-free `llm/` module** (reusing the
codebase's existing sanitize/redact primitives and the WebUI's already-hardened
endpoint guard) to draft higher-quality backlink text, validates the output
deterministically, and emits a reviewable JSONL/JSON artifact with per-record
`status`. It is a content-drafting tool for human review — it does not publish,
and it does not wire into the `seeds → plan → validate → publish` pipeline.

## Problem Frame

The operator has candidate records but no quality-controlled way to turn them into
publishable backlink text. The only LLM content path today
(`generate_article_body`) is buried in the publish pipeline's anchor provider,
can't be run standalone, hard-codes "≥2 links", and emits no reviewable artifact.
(See origin: `docs/brainstorms/2026-05-27-llm-backlink-text-generation-requirements.md`.)

**Project-policy gate (resolved):** `docs/solutions/best-practices/no-runtime-llm-2026-05-15.md`
is a hard owner rule that shipped code calls no LLM. The owner **authorized this
verb as an explicit exception** (2026-05-27) under guardrails — recorded in that
doc's new "Authorized Exceptions" section. Every guardrail below is load-bearing,
not optional.

## Requirements Trace

- R1. New `generate-backlink-text` command — `[project.scripts]` + `python -m`, stdin/stdout-JSONL + stderr-diagnostics + exit-0-on-success.
- R2. Flags: `--input/-i`, `--endpoint`, `--api-key-env` (default `BACKLINK_LLM_API_KEY`), `--model`, `--temperature` (0.4), `--timeout` (60), `--retries` (1), `--output-format` (`jsonl|json`), `--max-input-bytes` (2_000_000), `--max-records` (200), `--dry-run`.
- R3. `--dry-run` emits prompts only — no key, no allowlist requirement, no HTTP.
- R4. Accept JSON object / array / JSONL; require `target_url`, `anchor_text`, `mode`; `target_url` https-scheme-gated before embedding.
- R4b. Unknown/unsupported `mode` → per-record `rejected` (`unsupported_mode:<value>`), never aborts the batch.
- R5. `--max-input-bytes` / `--max-records` enforced before any LLM POST (fail-closed).
- R5b. Empty input → exit 0, empty output, stderr summary `0`.
- R6. Reuse the codebase's existing LLM primitives — sanitize/redact + the WebUI's hardened endpoint guard (`_guard_llm_endpoint`/`_safe_post_json`) — and `BACKLINK_LLM_*` config/env; do not hand-roll a duplicate redaction/SSRF/POST stack; CLI flags override config.
- R7. MVP modes `article` (adapt to single-link prompt) + `comment` (new method); `profile`/`bio` deferred.
- R8. `--retries` = transient transport retries only. On *validation* failure, do **one** bounded corrective regeneration (re-prompt telling the model which check failed); if it still fails, `rejected`.
- R9. Output embeds a link to `target_url` whose `<a>` text contains `anchor_text`, case/whitespace-normalized.
- R10. Per-mode length bounds; out-of-bounds → `rejected`.
- R11. Input sanitization (before prompt) vs output filtering (after generation) — two distinct boundaries.
- R12. Language match is an **advisory flag** on `ok` records (never a rejection), via `linkcheck/language.py`.
- R13. Every record emitted with `status` + fixed `rejection_reason` enum; never silently dropped.
- R14/R14b. `ok` carry text, `rejected` carry context; stderr summary; per-record rejections do not change exit code (exit 0).
- R15. CLI must run the full hardened endpoint guard (scheme + `is_allowlisted` + `_check_url_for_ssrf` + redirect-off + body cap) on the resolved endpoint before any key is sent — the LLM client does not self-gate.
- R16. No credentials in any log/error/output — route through provider redaction.

## Scope Boundaries

- No publishing, login, browser automation, captcha, anti-bot, comment-spamming, UI, scheduler, or DB migration.
- Not wired into `seeds → plan → validate → publish`; output is a review artifact.
- `profile`/`bio` mode out of MVP.
- **Guardrails (from the authorized no-runtime-LLM exception):** never on a default/cron/`--replay` path; never imported by publish/plan/validate; LLM key never a required env var; output never auto-published.

## Context & Research

### Relevant Code and Patterns

- **CLI skeleton to mirror:** `src/backlink_publisher/cli/preflight_targets.py` (`main(argv)`, argparse imported inside `main`, `read_jsonl`→`write_jsonl`, module-level `PipelineLogger`, `try/except PipelineError: handle_error(exc)`, inline `if __name__ == "__main__": main()`).
- **JSONL helpers:** `src/backlink_publisher/_util/jsonl.py` — `read_jsonl(source=None, strict=True)` (yields dicts only; `strict=True` calls `emit_error(..., exit_code=2)` on malformed/non-dict/**empty**; `MAX_LINE_LENGTH=65536`), `write_jsonl(rows, dest=None)`. Note: `read_jsonl` handles **JSONL only**, not a bare object/array — see Decision on input parsing.
- **Provider:** `src/backlink_publisher/publishing/adapters/llm_anchor_provider.py` — `OpenAICompatibleProvider(base_url, api_key, model, timeout_s=30.0, temperature=0.7, system_prompt=None, article_system_prompt=None)`; module-level `_sanitize_input(text)` (200-char cap + control/bidi strip + HTML-attr escape), `_redact_for_log(text)`, `_post_chat_completions`, `_build_request_body`, `_build_user_prompt`; `retry_transient_call(fn, *, is_retryable, max_attempts=...)` from `.retry`; HTTP via `from backlink_publisher.http import post as http_post`. **Not** monolith-budget-tracked. Instantiation pattern: `cli/plan_backlinks/core.py:257-266` builds from `cfg.llm_anchor_provider`.
- **Config:** `config/parsers/llm.py` (`BACKLINK_LLM_*` env, `https://` enforced at parse), `config/types.py` `LLMProviderConfig`, `config/loader.py` `load_config(path=None) -> Config` exposing `Config.llm_anchor_provider: LLMProviderConfig | None`.
- **Allowlist:** `_util/llm_allowlist.py` — `is_allowlisted(url) -> bool`, opt-out `BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST=1`. Called only from `webui_app/routes/llm.py` today; the provider does not call it.
- **URL gate:** `_util/url.py` `validate_https_url(url) -> str | None` (https-only). Trap: `urlparse` raises `ValueError` on malformed IPv6 — guard.
- **Language:** `linkcheck/language.py` — `SUPPORTED_LANGUAGES={zh-CN,ru,en,ko}`, `detect_language_from_markdown`, `detect_language_from_html`, `language_matches(detected, requested)` (unknown on either side → True).
- **Errors/exit:** `_util/errors.py` — `PipelineError`(5), `UsageError`(1), `InputValidationError`(2), `DependencyError`(3), `ExternalServiceError`(4); `handle_error(exc)` raises `SystemExit(exc.exit_code)`; convention is `try/except PipelineError: handle_error(exc)` in `main()`, no decorator.
- **Packaging/tests:** `pyproject.toml [project.scripts]` flat `name = "module:main"`; single-file CLI needs no `__main__.py`, just the inline guard. `tests/test_cli_python_m_entrypoints.py` `_CLI_ONLY_MODULES` must gain the new verb. CLI tests run **in-process** `main(argv)` + `capsys` with JSONL temp files; LLM is **not** auto-mocked — patch `http_post` at the module reference.

### Institutional Learnings

- `best-practices/no-runtime-llm-2026-05-15.md` — the policy gate; authorized exception now recorded there. Guardrails above derive from it.
- `logic-errors/argparse-choices-vs-usage-error-exit-clash-2026-05-20.md` — closed-set args (`--output-format`, `mode`) must validate post-parse and raise `UsageError` (exit 1); never `choices=` (exit 2). Put the valid set in `help=`.
- `best-practices/embed-banner-lazy-config-load-contract-2026-05-20.md` — call `load_config()` lazily inside the function, never cache; honors `BACKLINK_PUBLISHER_CONFIG_DIR` (test sandbox + mid-session rotation). Map "no LLM configured" to a purpose-specific error, distinct from "command broke".
- `test-failures/tests-coupled-to-operator-config-state-2026-05-18.md` + memory `feedback_mock_patch_paths_after_extraction.md` — tests must explicitly mock the provider HTTP (sockets are blocked; unmocked → confusing `JSONDecodeError`); patch at the use-site.
- `logic-errors/python-m-needs-main-module-after-package-split-2026-05-19.md` — keep it a single module (works with `-m`); if it ever becomes a package, add `__main__.py` in the same PR.

### External References

None — local patterns (provider, allowlist, redaction, CLI convention) are strong and directly reusable; external research adds nothing here.

## Key Technical Decisions

- **Single new CLI module, flat file:** `src/backlink_publisher/cli/generate_backlink_text.py`. Rationale: mirrors `preflight_targets.py`, avoids the `__main__.py` package trap, not monolith-budget-tracked.
- **Small publish-free `llm/` module by *lifting* existing code, not relocating the provider (P0 decoupling, right-sized):** the carve-out is conditioned on the verb being decoupled from publish, and importing anything under `publishing/adapters/` (the provider, or `retry.py`) executes `adapters/__init__.py` → eagerly registers ~27 adapters + the browser/CDP stack. **But the original plan's "move the provider's POST/retry primitives" was over-scoped and infeasible as framed** (verified: `_post_chat_completions`/`_build_request_body` are *instance* methods using `self.*`, not movable module-level functions; `retry_transient_call` lives in `adapters/retry.py` with 24 importers). The right-sized fix reuses code that is **already** publish-free and already hardened:
  - **Lift** `_guard_llm_endpoint` + `_safe_post_json` + `_LLM_TEST_MAX_BYTES` from `webui_app/routes/llm.py` (self-contained, bare-`requests`, already implements scheme + `is_allowlisted` + `_check_url_for_ssrf` + `allow_redirects=False` + 3xx-reject + 64KB streamed cap) into `src/backlink_publisher/llm/http_guard.py`; the WebUI route re-imports from the new home (one consumer updated).
  - **Lift** the two module-level pure functions `_sanitize_input` + `_redact_for_log` from `llm_anchor_provider.py` into `llm/`; the provider re-imports them (no behavior change; the 4 provider call sites are untouched — they use the class, not these funcs directly).
  - Leave `OpenAICompatibleProvider`, its instance methods, and `retry.py` **in place** — the verb does not use them.
  An import-isolation test asserts importing `backlink_publisher.llm.*` pulls in no `publishing.registry`/`*_api`/`browser_publish` (achievable because the lift uses only `_util/net_safety`, `_util/errors`, and bare `requests`).
- **Generation entry `generate_link_text(*, mode, target_url, anchor_text, language)`** in `llm/client.py` — single-link, language-pinned prompt per mode; POSTs via the lifted `_safe_post_json`; sanitizes inputs via the lifted `_sanitize_input`; redacts via the lifted `_redact_for_log`. Do **not** reuse `generate_article_body` (it hard-codes ≥2 links / `anchors[0],[1]`). R8 transient retry is a **small bounded loop in `llm/client.py`** around `_safe_post_json` (not the adapter's `retry_transient_call`, which would boot the registry; `_safe_post_json` mirrors the WebUI's deliberate no-shared-retry POST). This is the security-required hardened variant for an *untrusted* endpoint, not a duplicate of the provider's config-trusted POST — R6 permits it.
- **Endpoint normalization (ground-truth resolved):** the verb builds the request URL as `{base}/chat/completions` (matching the WebUI route), where `base` = `--endpoint` with a trailing `/chat/completions` and trailing slash stripped (so a full `…/chat/completions` input does not double-append; `/v1`-style is the canonical input). The config parser enforces `https://` literally (blocks `http://localhost` — Ollama over plain HTTP cannot pass); keep https-only by default, gate loopback behind the existing `ALLOW_LOOPBACK` opt-in. The endpoint string that is **guarded** must be the same host the request connects to.
- **Input parsing reuses `read_jsonl(strict=False)` for the JSONL branch:** read raw text (enforce `--max-input-bytes` first), attempt `json.loads`; dict → `[dict]`, list → list, else feed lines to `read_jsonl(strict=False)` (skips malformed, returns `[]` on empty — satisfies R5b without the `strict=True` exit-2). Rationale: confines divergence to "accept object/array + empty→exit-0"; single-sources JSONL row semantics. (JSONL-only is the cleaner default — see Open Questions.)
- **`--api-key-env` defaults to `BACKLINK_LLM_API_KEY`**, key read from the named env var; base/model/timeout/temperature resolve from CLI flags over `[llm.anchor_provider]`/`BACKLINK_LLM_*`, passed explicitly into the client (provider defaults 0.7/30 differ from R2's 0.4/60). **The key is never accepted as a flag** (env-var-only — keeps it out of `ps`/argparse usage text). Rationale: origin Key Decisions + R16.
- **`--retries`** bounds the verb's small transient-retry loop around `_safe_post_json`; validation failures are never retried (see Open Questions for the bounded-regenerate product decision). Rationale: R8.
- **Closed-set `--output-format` validated post-parse as `UsageError`** (no `choices=`, exit 1 not 2). `mode` is per-record `rejected`, not a usage error.
- **Per-record `rejected`, never abort:** invalid record (missing/malformed required field, incl. `target_url` that fails `validate_https_url` or raises on malformed IPv6) and unknown `mode` become `rejected` rows. Adds `invalid_record` to the rejection-reason enum.
- **No credentials in output records:** only candidate fields (`target_url`/`anchor_text`/`mode`/`text`/`status`/`rejection_reason`/`language_flag`) are emitted — never the endpoint, key, or env-var name. `--endpoint` userinfo (`https://user:secret@host`) is rejected at resolution (redaction does not cover URL userinfo).

## Open Questions

### Resolved During Planning

- **Where do the hardened POST / SSRF / body cap come from?** They already exist in `webui_app/routes/llm.py` (`_guard_llm_endpoint`/`_safe_post_json`/`_LLM_TEST_MAX_BYTES`, verified) — lift to `llm/`, don't re-derive from the provider's unhardened bare POST (Unit 2).
- **Is the big provider/`retry.py` relocation needed?** No (it was over-scoped and infeasible — instance methods + 24 `retry.py` importers). Lift only the already-publish-free `_safe_post_json`/`_guard_llm_endpoint` + the two module-level sanitize/redact funcs; leave the provider/`retry.py`/4 call sites untouched (Unit 2).
- **Endpoint normalization** — build `{base}/chat/completions`; strip a trailing `/chat/completions`+slash from `--endpoint`; `/v1`-style canonical (resolved via repo ground truth).
- **`https://` for loopback/Ollama?** Config parser blocks `http://localhost`; keep https-only by default, gate loopback behind the existing `BACKLINK_PUBLISHER_LLM_ALLOW_LOOPBACK=1` opt-in (no new relaxation).
- **Can we reuse `read_jsonl` for all three formats?** Partly — reuse `read_jsonl(strict=False)` for the JSONL branch; wrap object/array + empty→exit-0 around it (Unit 1).
- **Language detection dependency?** None — `linkcheck/language.py` exists (Unit 4).
- **Exit code on rejections?** 0 (R14b); non-zero only for usage/IO/config/allowlist/SSRF.

### Resolved During Planning (product behavior, owner-decided 2026-05-27)

- **Bounded regenerate on validation failure:** YES — one corrective re-prompt (naming the failed check) before `rejected` (R8). Raises usable yield; cost ≤ one extra LLM call per failing record.
- **`extra_links` in `article` mode:** **strip + re-validate**, not hard-reject — a citation shouldn't kill a good article. Removed links set a `stripped_extra_links` advisory flag for review (R11/Unit 4).

### Deferred to Implementation

- **DNS-rebinding residual:** `_check_url_for_ssrf` resolves at gate time; `requests` re-resolves at connect time, so a rebind could still reach a private IP. For an opt-in operator tool this is accepted residual risk (note it); connect-time IP pinning is a possible future hardening, not MVP.
- **Comment-mode length cap value** (`≤ ~60 words` brainstorm target) — finalize when writing the prompt + validator.
- **Refusal/jailbreak phrasing detection** — exact deterministic heuristic decided when writing the R11 output filter.
- **Model-emitted link host canonicalization** — `extra_links` must canonicalize hosts (reject `target.com@evil.com`, `//evil.com`, etc.) with the same discipline as the `--endpoint` userinfo check.
- **Optional scope simplifications (operator discretion):** restrict R4 to JSONL-only (cleaner default); trim R2 flag set; ship `article`-only and defer `comment` (no comment publish path exists).

## Implementation Units

```mermaid
graph TB
    U1[Unit 1: CLI scaffold + input parsing + packaging]
    U2[Unit 2: Publish-free llm/ client + generate_link_text]
    U3[Unit 3: Hardened endpoint resolution + guard]
    U4[Unit 4: Deterministic validation + record assembly]
    U5[Unit 5: Orchestration + dry-run + redaction + docs]
    U1 --> U3
    U1 --> U4
    U2 --> U3
    U2 --> U5
    U3 --> U5
    U4 --> U5
```

- [ ] **Unit 1: CLI scaffold, input parsing, packaging**

**Goal:** A runnable `generate-backlink-text` skeleton: argument parsing, multi-format input parsing with size/count guards, stdout/stderr/exit-code plumbing, package registration.

**Requirements:** R1, R2 (parsing only), R4 (field presence + structure), R4b, R5, R5b, R14b (exit-code skeleton).

**Dependencies:** None.

**Files:**
- Create: `src/backlink_publisher/cli/generate_backlink_text.py`
- Modify: `pyproject.toml` (`[project.scripts]`: `generate-backlink-text = "backlink_publisher.cli.generate_backlink_text:main"`)
- Modify: `tests/test_cli_python_m_entrypoints.py` (add verb to `_CLI_ONLY_MODULES`)
- Test: `tests/test_cli_generate_backlink_text.py`

**Approach:**
- `main(argv=None)`, `import argparse` inside `main`; all R2 flags; module-level `PipelineLogger("generate-backlink-text")`; inline `__main__` guard.
- `_read_candidates(raw_text) -> list[dict]`: enforce `--max-input-bytes` on raw length first; `json.loads` → dict wraps to `[dict]`, list as-is, `JSONDecodeError` → line-delimited JSONL parse; enforce `--max-records` after; empty → `[]`.
- Per-record field validation produces a normalized internal record or a deferred `rejected` marker (`invalid_record`) — does not abort.
- `--output-format` validated post-parse → `UsageError` (exit 1) on bad value (no `choices=`).
- Body wrapped `try/except PipelineError: handle_error(exc)`.

**Patterns to follow:** `cli/preflight_targets.py` (main/argparse/logger/error-wrap), `_util/jsonl.py:write_jsonl`, `argparse-choices-vs-usage-error` learning.

**Test scenarios:**
- Happy path: a 3-record JSONL stream parses to 3 internal records.
- Happy path: a single JSON object and a JSON array each parse to the same record list as the equivalent JSONL.
- Edge case: empty stdin / empty file → exit 0, empty stdout, stderr summary `0` (R5b).
- Edge case: record count = `--max-records` passes; count+1 → `InputValidationError` exit 2 before any generation.
- Edge case: raw input > `--max-input-bytes` → exit 2 before parse.
- Error path: `--output-format=xml` → `UsageError` exit 1 (asserts code 1, not 2).
- Error path: record missing `target_url`/`anchor_text` → `rejected` row with `invalid_record`, batch continues.
- Integration: `python -m backlink_publisher.cli.generate_backlink_text --help` emits a usage banner (guard present).

**Verification:** Command parses every flag, round-trips all three input formats to a uniform record list, enforces guards before generation, and is invokable via both the console script and `python -m`.

- [ ] **Unit 2: Publish-free `llm/` module (lift guard + sanitize/redact) + `generate_link_text`**

**Goal:** Stand up a small publish-free `llm/` module by lifting code that is already publish-free and already hardened, then add the two-mode single-link generation entry. No provider/`retry.py` relocation.

**Requirements:** R6, R7, R8, R11 (input sanitization), R16; and the P0 decoupling decision.

**Dependencies:** Unit 1 (agreed record/return shape). Feeds Unit 3 (the guard + client) and Unit 5; can proceed in parallel with Unit 4 once the `generate_link_text` return shape is agreed.

**Files:**
- Create: `src/backlink_publisher/llm/__init__.py`, `src/backlink_publisher/llm/http_guard.py` (lifted `_guard_llm_endpoint` + `_safe_post_json` + `_LLM_TEST_MAX_BYTES`), `src/backlink_publisher/llm/client.py` (lifted `_sanitize_input` + `_redact_for_log`; new `generate_link_text` + small bounded transient-retry loop)
- Modify: `webui_app/routes/llm.py` (re-import the guard from `llm/http_guard.py` — one consumer), `src/backlink_publisher/publishing/adapters/llm_anchor_provider.py` (re-import `_sanitize_input`/`_redact_for_log` from `llm/`; behavior unchanged)
- Test: `tests/test_llm_client.py`; existing `tests/test_llm_anchor_provider.py` and the WebUI llm-route tests must stay green (update patch targets to follow the lifted funcs — patch where *used*)

**Approach:**
- Lift the two self-contained, bare-`requests` WebUI helpers and the two module-level pure funcs; update their original homes to re-import. The 4 provider call sites (`core.py`, `_payload.py`, `_zh_short.py`, `resolver.py`) use the *class* and are untouched. `OpenAICompatibleProvider`, its instance methods, and `retry.py` stay put.
- `generate_link_text(*, mode, target_url, anchor_text, language, client_cfg)`: per-mode prompt — `article` → 200–400w SEO body, **exactly one** link to `target_url` via `anchor_text`; `comment` → short natural text, one link; both language-pinned. Untrusted fields go through `_sanitize_input` then the `<input>` block (escape-then-wrap, never raw). POST via the lifted `_safe_post_json` (already redirect-off + 3xx-reject + 64KB cap). Wrap transient failures in a small bounded retry (`--retries`); redact via `_redact_for_log`.

**Execution note:** Characterization-first — keep `tests/test_llm_anchor_provider.py` and the WebUI llm-route tests green through the lift before adding `generate_link_text`.

**Technical design:** *(directional, not implementation spec)*
```
# src/backlink_publisher/llm/client.py  (publish-free; imports only llm/http_guard, _util/*, requests)
generate_link_text(mode, target_url, anchor_text, language, cfg):
    safe = {k: _sanitize_input(v) for k,v in fields}        # escape THEN wrap
    prompt = ARTICLE_PROMPT if mode=="article" else COMMENT_PROMPT  # one link, language-pinned
    payload = {model, messages:[system, user("<input ...='{safe}'/>")], temperature}
    for attempt in range(cfg.retries + 1):                  # bounded, transient-only
        status, body = _safe_post_json(f"{cfg.base}/chat/completions", headers, payload)
        if not transient(status): break
    return body["choices"][0]["message"]["content"]
```

**Patterns to follow:** `webui_app/routes/llm.py:_safe_post_json`/`_guard_llm_endpoint` (the code being lifted); existing `generate_article_body` prompt shape; `_sanitize_input`, `_redact_for_log`.

**Test scenarios:**
- Integration: importing `backlink_publisher.llm.client` / `llm.http_guard` pulls in **no** `publishing.registry` / `*_api` adapter / `browser_publish` (assert via `sys.modules` after a clean import).
- Integration: WebUI llm-route tests and `test_llm_anchor_provider.py` still pass after the lift (re-import works, no behavior change).
- Happy path (mock the POST): `article` returns body; prompt requests one link + 200–400 words + the target language.
- Happy path: `comment` mode produces a short-form prompt.
- Security: endpoint returning `302 Location: evil` → `_safe_post_json` raises `redirect_not_allowed`, no second request, Bearer not re-sent.
- Security: response body exceeding `_LLM_TEST_MAX_BYTES` → raises `response_too_large`, not OOM.
- Edge case: `anchor_text` with a `</input>`/attribute-breakout payload is escaped before reaching the model.
- Error path: transient 5xx within `--retries` → retried then succeeds/raises; exception text contains no bearer token.

**Verification:** The `llm/` module is import-isolated from the publish stack; the lift leaves the WebUI route and provider green; both modes produce single-link prompts; redirects/oversized bodies cannot exfiltrate the key or OOM; the bearer token never appears in raised/logged text.

- [ ] **Unit 3: Hardened endpoint resolution + guard**

**Goal:** Resolve endpoint/key/model from flags-over-config and gate the operator-supplied endpoint with the full hardened guard before any key is sent.

**Requirements:** R2 (resolution), R6, R15; security findings (redirect/SSRF/scheme/userinfo).

**Dependencies:** Unit 1, Unit 2.

**Files:**
- Modify: `src/backlink_publisher/cli/generate_backlink_text.py` (`_resolve_client(args)`)
- Test: `tests/test_cli_generate_backlink_text.py`

**Approach:**
- Lazy `load_config()` inside the resolver (never cached; honors `BACKLINK_PUBLISHER_CONFIG_DIR`). Overlay CLI flags onto `cfg.llm_anchor_provider`; read key from the env var named by `--api-key-env` (never a flag).
- **Reject `--endpoint` containing userinfo** (`user:secret@host`) — redaction does not cover URL userinfo.
- Normalize endpoint (strip trailing `/chat/completions`+slash). The string that is **gated** must be byte-identical to the `base_url` handed to the client (no re-normalization after the check).
- **Call the lifted `llm.http_guard._guard_llm_endpoint`** (scheme allowlist `https`-only by default → `is_allowlisted` unless `ALLOW_ANY_HOST=1` → `_check_url_for_ssrf` loopback via `ALLOW_LOOPBACK=1`; guard `urlparse` ValueError) on the resolved endpoint host **before** constructing the client. Reuse it; do not re-derive a weaker variant.
- Unconfigured (missing key/endpoint/model) → `DependencyError` (exit 3), message clearly "LLM not configured for this opt-in verb" (distinct from "command broke"). Pass `temperature`/`timeout_s` explicitly (R2 defaults, not provider defaults).

**Execution note:** Security boundary — start with a failing test asserting no `http_post` when the endpoint is not allowlisted or is a private/loopback IP.

**Patterns to follow:** the lifted `llm/http_guard.py` (from Unit 2), `_util/llm_allowlist.py`, `_util/net_safety.py`, `embed-banner-lazy-config-load` learning.

**Test scenarios:**
- Happy path: allowlisted https endpoint + key → client built with resolved base/model/temperature/timeout (assert args).
- Security: non-allowlisted host → rejected before any `http_post`.
- Security: `http://10.0.0.5/...` (private) and `http://127.0.0.1/...` (loopback w/o opt-in) → rejected by SSRF gate; `ALLOW_LOOPBACK=1` permits loopback.
- Security: `https://user:secret@api.openai.com` → rejected (userinfo); secret in no error line.
- Security: `https://api.openai.com@evil.com/` → host resolves to `evil.com` → rejected.
- Edge case: `--endpoint .../v1/chat/completions` → normalized base does not double-append.
- Error path: no key in named env var → `DependencyError` exit 3 ("not configured"); `--api-key-env=CUSTOM_KEY` reads `CUSTOM_KEY`.
- Error path: malformed endpoint (`http://[invalid`) → clean error, no uncaught `ValueError`.

**Verification:** The operator's key reaches only an allowlisted, non-private, redirect-free https host; the gated string equals the connected base_url; unconfigured runs fail clearly.

- [ ] **Unit 4: Deterministic validation + record assembly + emit**

**Goal:** Validate generated text deterministically, assemble per-record output with `status`/`rejection_reason`/advisory language flag, and emit in the chosen format with a stderr summary.

**Requirements:** R4 (`target_url` https gate), R9, R10, R11 (output filtering), R12, R13, R14.

**Dependencies:** Unit 1; consumes the agreed `generate_link_text` return shape from Unit 2 (the contract, not its code — can proceed in parallel).

**Files:**
- Modify: `src/backlink_publisher/cli/generate_backlink_text.py` (validation helpers + emit)
- Test: `tests/test_cli_generate_backlink_text.py`

**Approach:**
- `validate_https_url(target_url)` at the input boundary, **wrapped against `urlparse` ValueError** (malformed IPv6) so a bad `target_url` becomes `rejected` (`bad_target_url_scheme`/`invalid_record`), never an uncaught crash that aborts the batch. Use the **validated** `target_url` (not the 200-char-`_sanitize_input`-truncated copy — that form is for the prompt only) for the embedded `<a href>` and the link-match check.
- Output checks: link to `target_url` present with `anchor_text` in the `<a>` text (case/whitespace-normalized) → else `missing_link`/`missing_anchor`; per-mode length bounds → `length_out_of_bounds`; control/bidi chars → `unsafe_chars`; refusal phrasing → `llm_refusal`.
- **Extra links (non-`target_url` domains): strip + re-validate, do not reject.** Canonicalize each model-emitted link host (reject `target.com@evil.com`/`//evil.com`-style confusion with the same discipline as the endpoint check); remove any link whose host ≠ `target_url` host; set a `stripped_extra_links` advisory flag; then re-run the link/anchor checks on the cleaned text.
- **Bounded corrective regeneration (R8):** on a validation failure, re-prompt once telling the model which check failed; re-validate; only then `rejected`. (The regenerate loop is driven from orchestration, Unit 5; validation returns a structured reason the orchestrator feeds back.)
- Language: `language_matches(detect_language_from_markdown(text), requested)` → on `ok` records, attach an advisory `language_flag` (never reject).
- Assemble record: `status`, `rejection_reason` (from the fixed enum + `invalid_record`), advisory flags (`language_flag`, `stripped_extra_links`), generated text on `ok`, original fields for context. Emit via `write_jsonl` (jsonl) or a single JSON array (`--output-format=json`). stderr summary counts (generated/rejected/skipped + how many needed regeneration).

**Patterns to follow:** `_util/url.py:validate_https_url`, `linkcheck/language.py`, `_util/jsonl.py:write_jsonl`, RECON-level always-on logging.

**Test scenarios:**
- Happy path: text with `anchor_text` inside the link to `target_url` → `ok`, carries text.
- Edge case: anchor present in body but not in the `<a>` text → `rejected` `missing_anchor`.
- Edge case: anchor differs only by case/whitespace → `ok` (normalized match).
- Error path: no link to `target_url` → `missing_link`.
- Edge case: an extra link to another domain → stripped, `stripped_extra_links` flag set, record still `ok` if the target link/anchor survive; a `target.com@evil.com`/`//evil.com` confusion link is stripped (host canonicalized), not treated as the target.
- Edge case: validation fails first pass → one corrective regeneration → passes → `ok` (assert exactly one re-prompt; summary counts it).
- Error path: `article` body > 400 words / `comment` over cap → `length_out_of_bounds` (after the corrective regeneration also fails).
- Error path: output contains a control/bidi char → `unsafe_chars`; refusal phrase → `llm_refusal`.
- Edge case: `target_url` = `javascript:alert(1)` → `bad_target_url_scheme`, never embedded.
- Edge case: malformed-IPv6 `target_url` (`https://[invalid`) → `rejected`, no uncaught `ValueError`, batch continues.
- Edge case: long (>200 char) `target_url` → embedded href is the full validated URL, not the truncated prompt copy.
- Edge case: zh output for an `en` request → `ok` + `language_flag` set (NOT rejected).
- Integration: every input record appears in output exactly once; `--output-format=json` emits one array, `jsonl` one line per record; stderr summary counts match.

**Verification:** Validation is reproducible with no LLM call; every record is accounted for with a status; language mismatch flags but never drops.

- [ ] **Unit 5: Orchestration, `--dry-run`, CLI-boundary redaction, docs**

**Goal:** Wire Units 1–4 into the per-record pipeline, implement `--dry-run`, ensure CLI-level output is redacted, and document the verb as opt-in.

**Requirements:** R1, R3, R8 (wiring), R14b, R16.

**Dependencies:** Units 1–4.

**Files:**
- Modify: `src/backlink_publisher/cli/generate_backlink_text.py` (orchestration, dry-run, redaction)
- Modify: `config.example.toml` (document the opt-in verb; do **not** list the LLM key as required)
- Test: `tests/test_cli_generate_backlink_text.py`

**Approach:**
- Orchestrate: read → resolve client (skip under `--dry-run`) → per record: validate target_url → generate (or build prompt for dry-run) → validate output → **on validation failure, one corrective regeneration then re-validate** → assemble → emit → summary.
- `--dry-run`: build and emit the constructed prompts only; no key required, no allowlist/SSRF check, no `http_post`; exit 0.
- Route any CLI-level error/summary text that could contain endpoint/key through `_redact_for_log` (R16 covers client lines, not new CLI lines). Output records carry **only** candidate fields — never endpoint/key/env-var-name.
- Per-record rejections keep exit 0; usage/IO/config/allowlist errors propagate via `handle_error` (R14b).
- `config.example.toml`: a short commented note that `generate-backlink-text` is an opt-in tool reusing `[llm.anchor_provider]`; the tool runs without it. Operator docs must not list the LLM key as a required env var.

**Test scenarios:**
- Happy path (mock `http_post`): mixed input → output with both `ok` and `rejected` rows; exit 0.
- Happy path: `--dry-run` emits prompts, `http_post` never called, no key/allowlist needed, exit 0.
- Error path: simulated provider failure → record `rejected` `transport_error`; stderr/output contain no bearer token (assert redaction).
- Security: endpoint given with userinfo (`https://x:secret@api.openai.com`) → `secret` appears in no stderr line, no exception text, no output record.
- Security: output records contain none of endpoint/key/env-var-name (assert field allowlist on emitted rows).
- Edge case: all records rejected → still exit 0.
- Integration: end-to-end through the console-script entrypoint with a temp config dir; assert no config write occurs.

**Verification:** One command turns a candidate stream into a reviewable artifact; dry-run is fully offline; no credential leaks; rejections never change the exit code.

## System-Wide Impact

- **Interaction graph:** New CLI module → new publish-free `llm/` module (lifted guard + sanitize/redact + `generate_link_text`). Two existing consumers re-import the lifted code: `webui_app/routes/llm.py` (guard) and `llm_anchor_provider.py` (sanitize/redact) — both behavior-unchanged. `OpenAICompatibleProvider`, its instance methods, `retry.py`, and the 4 provider call sites are **untouched**. The verb does **not** import `publishing/` — enforced by the import-isolation test (Unit 2). WebUI route behavior unaffected (it just imports the guard from a new home).
- **Error propagation:** Per-record failures → `rejected` rows (exit 0). Process-level failures (usage/IO/config/allowlist/SSRF) → `PipelineError` → `handle_error` → documented exit code.
- **State lifecycle risks:** Verb is stateless and writes nothing. Note `load_config()` pulls config/token/writer modules into the process image; the verb must trigger **no** `save_config` round-trip (canonical config doesn't round-trip `[llm.anchor_provider]`) — asserted by a no-config-write test (Unit 5). Key is read-only from the named env var.
- **API surface parity:** Adds a `[project.scripts]` entry + a `python -m` entrypoint; both registered and guarded (`test_cli_python_m_entrypoints.py`). The lift must keep `tests/test_llm_anchor_provider.py` **and** the WebUI llm-route tests green (patch targets follow the lifted funcs to `llm/`).
- **Integration coverage:** End-to-end CLI test (Unit 5, mocked POST); the import-isolation test (Unit 2) and the redirect/SSRF/guard-before-HTTP assertions (Units 2–3) are the key cross-boundary proofs.
- **Unchanged invariants:** The shipped pipeline remains LLM-free; this verb is the authorized opt-in exception and never becomes load-bearing for publish/plan/validate. `OpenAICompatibleProvider` (incl. `generate_article_body`), `retry.py`, and the 4 provider call sites are behaviorally unchanged — the lift only relocates pure/self-contained functions and updates their two import sites.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Verb reverse-couples to publish stack → violates the carve-out's decoupling condition | Lift only already-publish-free code into `llm/`; verb imports only `llm/`+`_util/`; import-isolation test asserts no `publishing.registry`/`*_api`/`browser_publish` (Unit 2). |
| **Operator's API key exfiltrated via HTTP redirect** (the `sec-001` bug) | Use the lifted `_safe_post_json` (`allow_redirects=False`, 3xx → `redirect_not_allowed`); test: 302 → no second request. |
| Key sent to a private/loopback/metadata IP (SSRF) | Use the lifted `_guard_llm_endpoint`: `_check_url_for_ssrf` + loopback only via `ALLOW_LOOPBACK=1` (Unit 3). |
| DNS-rebinding between gate and connect (gate-time resolve ≠ connect-time resolve) | Accepted residual for an opt-in operator tool; documented; connect-time IP pin is future hardening (Open Questions). |
| Hostile endpoint returns multi-GB body → OOM (×200 records) | Lifted `_safe_post_json` streamed `_LLM_TEST_MAX_BYTES=64KB` cap (Unit 2). |
| Key/endpoint leaks via URL userinfo or output records | Reject `--endpoint` userinfo; key env-only never a flag; output records carry only candidate fields (Units 3, 5) + leak test. |
| Model emits a same-domain-looking second link (`target.com@evil.com`, `//evil.com`) passing `extra_links` | Canonicalize model-emitted link hosts with the same discipline as the endpoint check (Unit 4 / deferred). |
| Lift breaks existing consumers / stale mock targets | Characterization-first: keep `test_llm_anchor_provider.py` + WebUI llm-route tests green through the lift; patch targets follow funcs to `llm/`. |
| Tests hang/JSONDecodeError because LLM HTTP isn't mocked (sockets blocked) | Every test mocks the POST at the use-site; dry-run tests assert it is never called. |
| `--endpoint` double-appends `/chat/completions` | Normalize (strip trailing `/chat/completions`+slash); guarded host == connected host; dedicated test. |
| Closed-set arg uses `choices=` and breaks the exit-code contract | Post-parse `UsageError` (exit 1); explicit test asserts code 1. |
| `article` mode rejects most output (validation brittleness) | One bounded corrective regeneration on validation failure (R8) + strip-and-revalidate for extra links (Unit 4); stderr summary reports yield so low rates are visible. |

## Documentation / Operational Notes

- `config.example.toml` gains a brief opt-in note (Unit 5); operator docs must **not** list the LLM key as required (no-runtime-LLM guardrail).
- No rollout/monitoring/migration concerns — additive, stateless, opt-in.
- When writing/reviewing any doc describing redaction, name token *categories*, not values; run the private-tokens grep gate on changed docs before commit.

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-27-llm-backlink-text-generation-requirements.md](docs/brainstorms/2026-05-27-llm-backlink-text-generation-requirements.md)
- Policy gate + authorized exception: `docs/solutions/best-practices/no-runtime-llm-2026-05-15.md`
- Related code: `cli/preflight_targets.py`, `publishing/adapters/llm_anchor_provider.py`, `webui_app/routes/llm.py` (hardened `_guard_llm_endpoint`/`_safe_post_json` — the gate to mirror), `_util/llm_allowlist.py`, `_util/net_safety.py`, `http.py` (follows redirects, own Retry layer), `_util/url.py`, `linkcheck/language.py`, `_util/jsonl.py`, `_util/errors.py`, `cli/plan_backlinks/core.py:257-266` (provider call site to keep stable)
- Learnings: `argparse-choices-vs-usage-error-exit-clash-2026-05-20.md`, `embed-banner-lazy-config-load-contract-2026-05-20.md`, `tests-coupled-to-operator-config-state-2026-05-18.md`, `python-m-needs-main-module-after-package-split-2026-05-19.md`
