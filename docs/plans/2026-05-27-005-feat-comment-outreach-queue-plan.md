---
title: "feat: Comment Outreach Queue (comment_outreach module)"
type: feat
status: active
date: 2026-05-27
origin: docs/brainstorms/2026-05-27-comment-outreach-queue-requirements.md
claims: {}
---

# feat: Comment Outreach Queue (comment_outreach module)

## Overview

Add an isolated `comment_outreach` package plus a single `comment` console_script
(argparse subparsers: `discover` / `import` / `qualify` / `brief` / `status`) that
chains via stdin/stdout JSONL like the existing pipeline. It finds comment-outreach
opportunities on operator-supplied public pages, scores them conservatively, drafts
guardrailed comment briefs (optional LLM, template fallback), and tracks a manual
review queue. **It never posts comments and never automates login.** The module is
fully isolated from the publishing adapter registry and never touches `events.db`.

WebUI surface and the assisted-navigation handoff (R12) are explicitly a later
round — this plan delivers the CLI engine and safety boundary only.

## Problem Frame

The existing tool publishes backlinks to owned/semi-owned platforms — one
relatively high-footprint surface. Operators want a lower-risk outreach layer:
legitimate comment participation on already-indexed public pages plus manually
entered social threads, aimed at referral traffic / brand mention / co-citation,
**not** PageRank transfer. The hard constraint is that this must never become a
comment-spam engine — the tool finds, qualifies, and drafts; a human always decides
and posts. (See origin: `docs/brainstorms/2026-05-27-comment-outreach-queue-requirements.md`.)

## Requirements Trace

- R1. `comment discover` — fetch operator-supplied **exact** public URLs (no
  link-following), detect a usable comment region, emit `CommentTarget` with
  tri-state `comment_open`; reuse SSRF primitives via a new body-returning wrapper;
  fetch failures → `comment_open=null` and bias the ladder toward review/reject;
  no SERP, no social/login-only fetch.
- R2. `comment import` — ingest external `CommentTarget` JSONL (only entry path for
  `x`/`facebook`/`linkedin`/`reddit`); validate each record against schema + the
  same URL rules as fetched targets; skip+log malformed records.
- R3. Model `CommentTarget` (source-spec fields); `comment_open`/`indexed`/
  `link_allowed` are tri-state (`null`=unknown). `QualificationResult` /
  `CommentBrief` / `ReviewStatus` modelled analogously.
- R4. `comment qualify` — score 0-100 across the (indicative) signal set, emit
  `QualificationResult` with `decision` (accept/review/reject) + `action`
  (manual_comment_brief/skip), `reasons[]`, resolved `link_policy`/`anchor_policy`.
- R5. Conservative decision ladder: social + unknown/closed comment availability →
  review/reject, never silent accept.
- R6. `comment brief` — generate `suggested_comment` via the existing LLM provider
  with a deterministic template fallback; treat scraped `page_title`/`thread_summary`
  as untrusted data; guardrails enforced as a post-LLM validator.
- R7. Conservative comment text: context-responsive, no generic praise, no repeated
  exact-match anchor, no keyword stuffing, ≤1 link, brand mention without link
  allowed, explicit no-link flag, human review required.
- R8. Each `CommentBrief` carries `suggested_anchor_policy`, `suggested_link_policy`,
  `human_checklist`, and explicit `prohibited_actions`.
- R9. `comment status` — track `ReviewStatus` (pending/approved/rejected/posted/
  skipped/hidden/removed) set **manually**; tool never infers "posted".
- R10. Single `comment` console_script + argparse subparsers; no import/reuse of the
  publishing adapter registry or any publish-flow adapter; no change to existing
  publish/validate flows. (R6's provider import is a narrow, **brief-verb-only, lazy**
  carve-out — the other four verbs import nothing from `publishing.*`.)
- R11. Reuse `_util/errors.py`, `_util/logger.py`, `safe_write.atomic_write` (0o600),
  `BACKLINK_PUBLISHER_CONFIG_DIR`; ReviewStatus store enforces 0o600 (incl. SQLite if
  chosen), classifies `reviewer`/`final_comment_text` sensitive, defines deletion path.
- R12. *(Deferred — next round, not implemented here.)* WebUI "Comment Outreach Queue" +
  assisted-navigation handoff. Listed for trace completeness; see Scope Boundaries.

## Scope Boundaries

- **No automatic comment publishing, no automated login, no posting browser
  automation.** No account/proxy rotation, CAPTCHA solving, anti-bot/rate-limit/
  moderation bypass, fingerprint spoofing — and no design that *depends* on these.
- No SERP/search-engine discovery; discovery is operator-seed, exact-URL only (no
  link-following / site-walking).
- No scraping of private or login-only platform data; social is import-only.
- No bulk/spam comment generation, fake engagement, exact-match anchor spam.
- No reuse of publishing adapters for comment publishing.
- **R12 (WebUI "Comment Outreach Queue" + assisted-navigation handoff) is out of
  scope this round** — captured for the next round; see Deferred to Implementation.

## Context & Research

### Relevant Code and Patterns

- **CLI subparser + console_script:** `cli/phase0_seal.py` — `_build_parser()` with
  `add_subparsers(dest="command", required=True)`, `sub.add_parser(...).set_defaults(
  handler=_handle_x)`, `main(argv=None) -> int` returning `args.handler(args) or
  EXIT_OK`, `if __name__ == "__main__": sys.exit(main())`. Wire in `pyproject.toml`
  `[project.scripts]`: `comment = "backlink_publisher.cli.comment:main"`. Keep
  `cli/comment.py` a **single thin module** (not a package) so no `__main__.py` is
  required and the `python -m` package trap is avoided.
- **JSONL I/O:** `_util/jsonl.py` — `read_jsonl(source=None, strict=True)`,
  `write_jsonl(rows, dest=None)` (`ensure_ascii=False`), and
  `atomic_write_jsonl(rows, path, mode=0o600)` (wraps `safe_write.atomic_write`).
  Usage model: `cli/preflight_targets.py` (`list(read_jsonl(args.input))` → process →
  `write_jsonl`). Use `strict=False` for `comment import` row-skip semantics.
- **Schema:** `schema.py` — dict `*_FIELDS` + `*_ENUM` sets + `_check_*(row) ->
  list[str]` helpers + fixed-order aggregator (`validate_output_payload`,
  `validate_publish_payload`); order is a characterized contract
  (`tests/test_schema_output_payload_characterization.py`). Reuse
  `_check_input_urls_and_normalize` for R2 URL rules.
- **SSRF primitives:** `_util/net_safety.py` — `_check_url_for_ssrf(url)` (raises
  `ValueError` on malformed IPv6 → must guard), `_make_ssrf_opener(max_redirects)`
  (re-checks every redirect hop, blocks https→http downgrade). `content/_http.py`
  `_safe_get` returns `(Response, bytes)` body capped at 2 MiB, **https-only**.
  `content/_preflight_fetch.py` `fetch_target` is http+https, never-raises (reason
  taxonomy), post-redirect SSRF re-check, `_read_body_prefix(resp, max_bytes)` reads
  ≤768 KB then **discards** it (confirms the origin doc's premise — a new
  body-returning helper is needed).
- **LLM provider:** `OpenAICompatibleProvider` in
  `publishing/adapters/llm_anchor_provider.py`. **⚠ Verified leak:** although the
  class itself is not a registry adapter, importing the module first runs
  `publishing/adapters/__init__.py`, which eagerly `register()`s all 21 platforms
  **and** imports the `browser_publish`/Chrome stack — ~49 `publishing.*` modules
  land in `sys.modules`, including `publishing.registry`. Relocation does **not**
  cleanly fix this: the provider imports `.retry`, and importing
  `publishing.adapters.retry` alone still drags in the registry (38+ modules), so a
  true sever would also have to relocate `retry.py` (imported by every adapter). →
  **Mitigated by a lazy/local import in the brief handler (Unit 7)**, confining the
  load to the one verb that needs it; non-brief verbs keep the strong isolation
  invariant. Constructed from `[llm.anchor_provider]`
  (`config/parsers/llm.py`, `config/types.py:294`); see
  `cli/plan_backlinks/core.py:256-265`. Reuse `_sanitize_input` — a **module-level
  function** (not a method; `llm_anchor_provider.py:323`): length-cap (`_INPUT_MAX_LEN
  = 200`, anchor-tuned) + strip control/bidi + XML-attr escape. The `<input>`-as-data
  framing lives inside the **private, anchor-specific** `_build_user_prompt`, so
  `comment brief` needs a **new** generation method/prompt, not a reuse of an existing
  one. Raises `DependencyError` (exit 3) on failure (redacts secrets from logs).
- **Persistence/config:** `persistence/safe_write.atomic_write(path, text, mode=0o600)`;
  `config/loader.py::_config_dir()` re-reads `BACKLINK_PUBLISHER_CONFIG_DIR` **at call
  time** — the store path resolver must be a function, never a frozen `Path.home()`.
- **Errors/exit:** `_util/errors.py` (`UsageError`=1, `InputValidationError`=2,
  `DependencyError`=3, `ExternalServiceError`=4, base=5) + `handle_error(exc)`.
  Data-verdict verbs stay exit-0 (model: `preflight_targets`). `logger.recon()`
  bypasses the WARN gate for always-on operator signals.
- **Tests:** flat `tests/`, single `conftest.py` with 4 autouse fixtures
  (sandbox config dir, URL-check pass, content-fetch pass, sockets blocked). Opt-in
  markers `real_ssrf_check` / `real_content_fetch`. `test_cli_python_m_entrypoints.py`
  parametrizes all entrypoints.

### Institutional Learnings

- `best-practices/no-runtime-llm-2026-05-15.md` — keep the **publish/validate** path
  LLM-free; `comment brief` is a discrete drafting verb (like plan-backlinks content
  generation) and is LLM-optional with a template fallback, so no LLM key is required.
- `workflow-issues/grep-dofollow-map-before-shipping-adapter-2026-05-20.md` — comment
  links are typically `nofollow`/`ugc`; the module disclaims dofollow value by design
  (referral/brand/co-citation; no-link briefs allowed), so the negative-value trap
  does not apply.
- `logic-errors/argparse-choices-vs-usage-error-exit-clash-2026-05-20.md` — do **not**
  use argparse `choices=` for closed enums (exits 2, clashes with `UsageError`=1);
  validate post-parse.
- `logic-errors/python-m-needs-main-module-after-package-split-2026-05-19.md` — keeping
  `cli/comment.py` a single module sidesteps the missing-`__main__.py` trap; still
  extend `test_cli_python_m_entrypoints.py`.
- `logic-errors/invert-drift-check-when-invariant-becomes-dynamic-2026-05-18.md` — make
  the registry-isolation assertion test-time (AST/subprocess), not a module-level
  drift check against `registered_platforms()`.
- `best-practices/stream-to-needed-tag-not-cap-then-reject-2026-05-15.md` — stream the
  fetch with an early-exit predicate rather than read-all-then-cap.
- `best-practices/recon-log-level-for-always-on-signals-2026-05-15.md` — use
  `logger.recon()` for skip/silent-drop tripwires operators must always see.
- `integration-issues/dofollow-canary-verdict-dropped-at-publish-output-seam` &
  `logic-errors/projector-silent-drop-status-vocabulary-drift` — add survive-the-seam
  tests for any new JSONL field/status; never a silent `else` in a status classifier.
- Memory-only (no solution doc): `monkeypatch.setenv` not `del os.environ`;
  `normalize_url_for_fetch` before `urllib.request`; guard `urlparse` malformed-IPv6;
  `atomic_write` canonical for state JSON; JSONL-with-rewrite preferred over `events.db`
  WAL complexity for simple new state.

## Key Technical Decisions

- **Single thin `cli/comment.py` module + `comment_outreach/` domain package.** The
  CLI module only wires argparse + dispatch; all logic lives in `comment_outreach/`
  (schema, fetch, detect, score, brief, store). Keeps the entrypoint a module (no
  `__main__.py` trap) and keeps each domain file small (monolith-budget friendly).
- **New body-returning fetch wrapper modelled on `_preflight_fetch.fetch_target`
  (http+https, never-raises, reason taxonomy), not `_http._safe_get` (https-only,
  raises).** Comment-able blogs/forums are frequently `http://`, and never-raises +
  reason taxonomy maps cleanly onto R1's "fetch failure → `comment_open=null` → bias
  review/reject." Delegates SSRF/host/body-cap to `net_safety` primitives; guards
  malformed-IPv6 `ValueError`; normalizes non-ASCII URLs.
- **ReviewStatus store = JSONL-with-atomic-rewrite via `atomic_write_jsonl` (0o600)
  under function-reresolved `CONFIG_DIR`.** Single operator, low volume, no
  concurrency; avoids `events.db` WAL-read complexity. SQLite only if a concrete need
  appears later.
- **LLM is optional; `_sanitize_input` + `<input>`-data-block is the injection
  defense; a deterministic post-LLM validator enforces R7.** The validator runs on
  *both* LLM output and the fallback template, so guardrails hold regardless of source
  and no LLM key is ever required.
- **Registry isolation enforced by a test-time AST + subprocess check**, not a
  module-level assertion (which fires during half-loaded imports). The AST walk must
  cover **nested/function-body imports** (`ast.walk`, not just `module.body`); the
  subprocess check must **run a verb** (not just import) and assert
  `publishing.registry not in sys.modules`, no `events.db` file created, and no
  network — call-time coupling is invisible to an import-only probe.
- **Provider isolation via lazy/local import, not relocation.** Importing
  `publishing.adapters.llm_anchor_provider` transitively loads the whole registry +
  browser stack (verified ~49 modules) — and relocating the provider does **not** fix
  it cleanly because the provider imports `.retry`, whose own package-init pulls the
  registry too; severing it would mean relocating `retry.py` (used by every adapter)
  behind a re-export shim. Instead the provider is imported **lazily, inside the brief
  handler only**: `discover`/`import`/`qualify`/`status` keep the **strong** invariant
  (`publishing.registry not in sys.modules`, proven by run-a-verb subprocess); `brief`
  keeps the **sufficient** invariant — no events.db, no `dispatch`/`register`/`.publish`,
  no network POST — and the registry merely sitting in `sys.modules` (memory only) is
  accepted. R10's carve-out stays as a narrow, brief-scoped exception.
- **A new posting-boundary AST test** (`ast.walk`, **nested/function-body nodes too**)
  asserts `comment_outreach/*` imports no posting/browser-automation primitive (no
  selenium/playwright/chrome, no `webui`) and makes no `requests.post`/`http.post` **or
  `urllib.request.Request(..., data=...)`** to target URLs — the structural proof that
  this is not a spam engine, mirroring the registry-isolation guarantee.
- **ReviewStatus upsert is read-modify-write → guarded by an advisory file lock**
  (`fcntl.flock` on a 0o600 lock file under CONFIG_DIR) for the whole RMW. Atomic
  replace prevents torn files but **not** lost updates when two `comment status`
  processes race; the lock closes that window without adopting SQLite.
- **Data-verdict verbs (`discover`, `qualify`) stay exit-0**; non-zero reserved for
  usage/input errors via `handle_error`. Closed-enum args validated post-parse.

## Open Questions

### Resolved During Planning

- *Fetch base layer?* → New wrapper over `net_safety` + `_read_body_prefix`, http+https,
  never-raises (see Key Decisions). The public `fetch_target`/`fetch_work_metadata`
  discard the body, so they cannot be reused directly.
- *Store mechanism?* → JSONL-with-atomic-rewrite, 0o600, function-reresolved CONFIG_DIR.
- *Does R6 violate the no-runtime-LLM rule?* → No; that rule governs publish/validate.
  `comment brief` mirrors plan-backlinks' runtime LLM use and degrades to a template.
- *CLI shape / python-m trap?* → Single thin `cli/comment.py` module, post-parse enum
  validation, extend the python-m smoke test.
- *qualify + brief separability* (3 reviewers flagged collapsing) → Keep separate per
  operator decision to hold scope; they are distinct subcommands of one script, so the
  JSONL seam is cheap and `QualificationResult`/`CommentBrief` are distinct artifacts.
- *Is the LLM-provider carve-out registry-clean?* → **No (verified):** importing
  `publishing.adapters.llm_anchor_provider` runs the package `__init__` and pulls in the
  full registry + browser stack (~49 modules); even the provider's `.retry` import drags
  the registry in, so relocation alone wouldn't sever it (would need to move `retry.py`,
  used by every adapter). Resolved (operator decision) by a **lazy/local import confined to
  the brief handler** — non-brief verbs stay strongly isolated; brief accepts
  registry-in-memory but posts nothing / touches no events.db. No shipped code refactored.
- *Reuse an existing provider method for comment drafts?* → No existing method fits
  (`generate_candidates`/`generate_article_body` are task-specific; `_build_user_prompt` is
  private + anchor-shaped). Unit 8 adds a new `generate_comment_draft` method.
- *ReviewStatus concurrency?* → The "no concurrency" assumption is not enforceable; resolved
  with an `fcntl.flock` advisory lock around the read-modify-write (not SQLite).

### Deferred to Implementation

- Exact scoring weights / accept-review-reject thresholds and how `compliance_score` /
  `platform_risk_score` are derived (R5). Ship a conservative starter ladder; signal
  derivation sources (operator-entered vs heuristic) confirmed in Unit 6.
- Comment-region detection precision/recall on real public HTML, incl. JS-lazy-loaded
  regions invisible to a non-JS fetch (R1). Unit 4 begins with a small fixture-backed
  accuracy spike before committing detector breadth.
- Where `domain_rank_signal` / `authority_score` / `link_allowed` actually originate
  given SERP + ToS parsing are out of scope.
- Whether `discover` should honor `robots.txt` / per-host rate limiting (largely moot
  given exact-URL, no link-following — confirm during Unit 5).
- **R12 (next round):** WebUI queue + assisted-navigation handoff. Open product/design
  question carried from brainstorm: clipboard-copy only vs driven-navigation in the
  logged-in profile with host-allowlist + no-auto-submit guards.
- **Rubber-stamp guard (product, optional):** per-domain/day brief caps or a divergence
  flag when `final_comment_text` closely matches a recent brief — the manual-status
  guarantee bounds the tool, not operator discipline.
- **Premise validation (product):** "lower-risk than publish flow" and the value of
  seed-only discovery are asserted, not validated — confirm before over-investing.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not
> implementation specification. The implementing agent should treat it as context, not
> code to reproduce.*

```
                       comment_outreach/ (domain package, registry-isolated)
                       ├── schema.py     entity validation (dict-fields + _check_*)
                       ├── fetch.py      body-returning SSRF wrapper (never-raises)
                       ├── detect.py     comment-region signatures → comment_open
                       ├── score.py      signals + conservative decision ladder
                       ├── brief.py      LLM(opt) + sanitize + post-LLM guardrail validator
                       └── store.py      ReviewStatus JSONL-atomic-rewrite (0o600)

 cli/comment.py (thin):  comment <discover|import|qualify|brief|status>

 discover:  exact URLs ──fetch.py──> html ──detect.py──> CommentTarget{comment_open}  (stdout JSONL, exit 0)
 import:    targets.jsonl ──schema.validate(strict=False)──> valid CommentTarget       (skip+recon malformed)
 qualify:   CommentTarget ──score.py(ladder)──> QualificationResult{decision,action}    (stdout JSONL, exit 0)
 brief:     accept-targets ──brief.py(sanitize→LLM|template→guardrail)──> CommentBrief
 status:    target_id+status ──store.py(atomic_write_jsonl)──> ReviewStatus (CONFIG_DIR, 0o600)
```

## Implementation Units

```mermaid
graph TB
  U1[U1 scaffold + CLI skeleton + isolation/posting guards] --> U2[U2 entity schemas]
  U2 --> U3[U3 comment import]
  U1 --> U4[U4 SSRF body fetch + region detect - top-risk spike, parallel with U2]
  U4 --> U5[U5 comment discover + bounded summary]
  U2 --> U5
  U2 --> U6[U6 comment qualify + scoring]
  U6 --> U7[U7 comment brief + LLM + guardrails - lazy provider import]
  U2 --> U7
  U2 --> U8[U8 comment status + locked ReviewStatus store]
```

*Note: U4 depends only on U1 (it returns raw html + tri-state bool; `CommentTarget` is
assembled in U5), so the top-risk detection spike can run in parallel with U2.*

### Phase 1 — Boundary & Data

- [x] **Unit 1: Package scaffold, CLI skeleton, isolation guard**

**Goal:** Establish `comment_outreach/` package + thin `cli/comment.py` with all five
subparsers dispatching to stubs (exit 0), wire the console_script, and lock the
registry/events.db isolation boundary *before* any logic exists.

**Requirements:** R10

**Dependencies:** None

**Files:**
- Create: `src/backlink_publisher/comment_outreach/__init__.py`
- Create: `src/backlink_publisher/cli/comment.py`
- Modify: `pyproject.toml` (`[project.scripts]`: `comment = "backlink_publisher.cli.comment:main"`)
- Modify: `tests/test_cli_python_m_entrypoints.py` (add `comment` to **`_CLI_ONLY_MODULES`**, not `_CLI_MODULES` — no WebUI this round)
- Test: `tests/test_comment_outreach_isolation.py`

**Approach:**
- Mirror `cli/phase0_seal.py`: `_build_parser()` with `add_subparsers(dest="command",
  required=True)`, one `set_defaults(handler=...)` per subcommand, `main(argv=None) ->
  int` returning `handler(args) or 0`, `if __name__ == "__main__": sys.exit(main())`.
- **Handlers are ≤10-line shims** that parse args, call one `comment_outreach.<verb>`
  entry function, and write JSONL. Domain logic and post-parse enum validation live in
  `comment_outreach/`, not the CLI module — keeps the dispatcher thin. Import each
  domain module **lazily inside its handler** so importing `cli/comment.py` never
  transitively loads heavy/registry code.
- Stubs return 0 and emit nothing on stdout (or a clear "not implemented" to stderr).
- No `choices=` for the eventual closed-enum args — post-parse validation only.

**Patterns to follow:** `cli/phase0_seal.py`, `cli/footprint.py`; isolation test
**inverts** `tests/test_r9_extension_readiness.py` (which imports the whole `adapters`
package on purpose) — do not reuse its `fake_platform_registered` fixture.

**Test scenarios:**
- Happy path: `comment --help` and each `comment <sub> --help` exit 0 with usage text.
- Integration (registry isolation, AST): `ast.walk` every `comment_outreach/*.py` +
  `cli/comment.py` covering **nested/function-body imports too**; assert no import
  references `publishing.registry` / `publishing.adapters` — **except** the single
  allowed lazy import of `llm_anchor_provider` inside the brief handler (and it must be
  function-local, never module-level).
- Integration (posting-boundary, AST): `ast.walk` (nested nodes too) asserts no import
  of selenium/playwright/chrome, no `webui`, and no `requests.post`/`http.post` **or
  `urllib.request.Request(..., data=...)`** to target URLs — the structural "not a spam
  engine" guarantee.
- Integration (runtime, non-brief verbs): in a clean subprocess, **run a verb**
  (`main(["import", ...])` / `main(["status", ...])` / `main(["discover", ...])` against
  a sandbox) then assert `"backlink_publisher.publishing.registry" not in sys.modules`
  and that no `events.db` file exists under the sandboxed `CONFIG_DIR` (file-existence
  check, not connect-hook). The `brief` verb's weaker invariant is covered in Unit 7.
- Edge case: `python -m backlink_publisher.cli.comment --help` exits 0, non-empty output.
- Error path: unknown subcommand / no subcommand exits non-zero (`required=True`).

**Verification:** Console script and `python -m` both resolve; AST + run-a-verb subprocess
tests prove no registry/posting/browser coupling and no events.db file for the non-brief
verbs. (The lazy/local import in Unit 7's brief handler keeps this invariant holding for the
other four verbs even after brief is wired.)

- [x] **Unit 2: Entity schemas + validation**

**Goal:** Define and validate `CommentTarget`, `QualificationResult`, `CommentBrief`,
`ReviewStatus` following `schema.py` conventions, in an isolated module.

**Requirements:** R3 (and the validation half of R2)

**Dependencies:** Unit 1

**Files:**
- Create: `src/backlink_publisher/comment_outreach/schema.py`
- Test: `tests/test_comment_outreach_schema.py`

**Approach:**
- `*_FIELDS` dicts + enums (`_PLATFORM_ENUM = {x, facebook, linkedin, reddit, medium,
  blog, forum, other}`, `_DECISION_ENUM`, `_ACTION_ENUM`, `_STATUS_ENUM`), `_check_*(row)
  -> list[str]`, fixed-order aggregators `validate_comment_target/_qualification_result/
  _comment_brief/_review_status`. Empty list = valid.
- Tri-state `comment_open`/`indexed`/`link_allowed` validate as `bool | None`.
- **Caveat:** `schema._check_input_urls_and_normalize` is **hardcoded** to the fields
  `("target_url", "main_domain")`, only checks the `^https?://` prefix, and has a
  side-effect writing `row["main_domain_normalized"]` — it will **not** validate a
  field named `source_url`. Extract its inner regex/normalize logic into a shared
  helper (or write a dedicated field loop) for `source_url`/`target_url`; do not call
  it as-is.

**Patterns to follow:** `schema.py` `validate_output_payload` / `validate_publish_payload`
and `tests/test_schema_output_payload_characterization.py`.

**Test scenarios:**
- Happy path: a fully-populated valid `CommentTarget`/`QualificationResult`/`CommentBrief`/
  `ReviewStatus` each return `[]`.
- Edge case: tri-state fields accept `None`, `True`, `False`; reject non-bool.
- Edge case: missing required field (`source_url`, `topic`, `target_url`) → specific error.
- Error path: bad `platform`/`decision`/`status` enum value → specific error.
- Error path: malformed/non-https-where-required/non-ASCII URL handled per the reused
  URL checks (incl. malformed-IPv6 does not crash).
- Characterization: error-message order is stable (lock the aggregator order).

**Verification:** Validators return error lists (never raise on bad data); order locked.

- [x] **Unit 3: `comment import`**

**Goal:** Ingest external `CommentTarget` JSONL (the only social entry path), validating
and skip-logging malformed rows.

**Requirements:** R2

**Dependencies:** Unit 2

**Files:**
- Create: `src/backlink_publisher/comment_outreach/io_import.py` (or inline in `cli/comment.py` dispatch)
- Modify: `src/backlink_publisher/cli/comment.py` (wire `import` handler)
- Test: `tests/test_comment_outreach_import.py`

**Approach:**
- `read_jsonl(args.input, strict=False)` → validate each via Unit 2 → valid rows to
  `write_jsonl` (stdout); malformed/disallowed rows logged via `logger.recon()` to
  stderr with reason, skipped. Stay exit-0 when ≥1 valid; `UsageError` (exit 1) only on
  genuinely unusable input (e.g., missing `--input`).
- No fetching here — social targets keep `comment_open=null`.

**Patterns to follow:** `cli/preflight_targets.py` I/O; `_util/jsonl.read_jsonl(strict=False)`.

**Test scenarios:**
- Happy path: 3 valid records in → 3 records out on stdout, exit 0.
- Edge case: mixed file (2 valid, 1 malformed JSON, 1 schema-invalid) → 2 out, 2 skipped
  with reasons on stderr, exit 0.
- Edge case: social platform record with `comment_open=null` passes through unchanged.
- Error path: missing `--input` → exit 1 (`UsageError`, not argparse exit 2).
- Integration: a record whose URL fails the shared URL rules is rejected (no unvalidated
  URL reaches downstream).

**Verification:** Valid records flow to stdout; every skip has a stderr reason; only valid
URLs survive.

### Phase 2 — Discovery

- [x] **Unit 4: SSRF-safe body fetch + comment-region detection**

**Goal:** Provide a body-returning, never-raises fetch wrapper and a comment-region
detector that sets tri-state `comment_open`.

**Requirements:** R1 (fetch + detect halves)

**Dependencies:** Unit 1 only (returns raw html + tri-state bool; no `CommentTarget`
assembly here — that is U5). Can run in parallel with U2; it carries the top risk.

**Files:**
- Create: `src/backlink_publisher/comment_outreach/fetch.py`
- Create: `src/backlink_publisher/comment_outreach/detect.py`
- Test: `tests/test_comment_outreach_fetch.py`, `tests/test_comment_outreach_detect.py`
- Test fixtures: `tests/fixtures/comment_outreach/*.html` (WordPress, Disqus, native form,
  forum reply, no-comment, JS-lazy-loaded)

**Approach:**
- `fetch.py`: wrapper over `net_safety._make_ssrf_opener(...)` that **replicates
  `fetch_target`'s full prelude in order** — scheme-gate → initial SSRF check →
  post-redirect final-URL re-check — and **reimplements** the streamed reader (do
  **not** import `_preflight_fetch._read_body_prefix`: it is module-private and its
  early-exit sentinel is hardcoded to `</h1>`, tuned for title extraction, not comment
  regions). http+https, capped body, **never raises** — returns `(html | None, reason)`
  with a taxonomy string (`ssrf_blocked`/`non_200`/`timeout`/`oversized`/`ok`).
- **Mandatory reuse / invariants:** use the never-raise guards `_safe_ssrf_check` +
  `_safe_hostname` (not a generic `urlparse` guard) at every call site;
  `normalize_url_for_fetch` (`_util/url.py:204`) before any `urllib.request`; **no
  `Accept-Encoding: gzip`** and count **wire bytes** at read time (else the cap is
  bypassable by a compression bomb); the fetch path must **not** route through the
  process-global cached `http.get_session()` (its urllib3 `Retry` + lack of per-redirect
  SSRF re-check would violate the reason taxonomy and the SSRF contract).
- `detect.py`: signature heuristics over the HTML → tri-state `comment_open` (true =
  region found, false = page fetched but no region, null = not fetchable). Begin with a
  small fixture-backed accuracy spike (Execution note) before broadening signatures.

**Execution note:** Start Unit 4 with a fixture-backed detection-accuracy spike across the
six fixture pages before committing detector breadth; record JS-lazy-loaded as a known
null case. **Define a measurable go/no-go gate** for the spike (e.g. ≥X recall / ≥Y
precision on the fixture classes); below the floor, escalate before broadening signatures
or descope `discover` to import-only for this round rather than ship a detector that flags
nearly everything `null` (which would starve `qualify` of accept targets).

**Patterns to follow:** `content/_preflight_fetch.py` (never-raises + reason taxonomy +
post-redirect re-check), `content/_html_utils.py`, `_util/net_safety.py`.

**Test scenarios:**
- Happy path (`real_ssrf_check`/fixture): WordPress + Disqus + native-form + forum fixtures
  → `comment_open=true`; no-comment fixture → `false`.
- Edge case: JS-lazy-loaded comment region (no server-side markup) → `comment_open` null/false,
  documented as a known limitation.
- Error path: URL resolving to a private/loopback IP → `reason=ssrf_blocked`, `html=None`,
  no raise.
- Error path: malformed IPv6 host, non-200, timeout, oversized body → distinct reasons, no raise.
- Security (compression cap): the outgoing request sends **no `Accept-Encoding: gzip`**, and a
  fixture whose decompressed size far exceeds the cap but whose **wire** size is under it →
  `reason=oversized` based on wire bytes (the cap can't be bypassed by a compression bomb).
- Edge case: non-ASCII URL normalized before fetch.

**Verification:** Fetch never raises and returns a reason; detector maps fixtures to the
expected tri-state with documented JS-lazy-load caveat.

- [x] **Unit 5: `comment discover`**

**Goal:** Wire the `discover` subcommand: for each operator-supplied exact URL, fetch +
detect, emit `CommentTarget` with tri-state signals; fetch failures → `comment_open=null`.

**Requirements:** R1

**Dependencies:** Unit 4, Unit 2

**Files:**
- Modify: `src/backlink_publisher/cli/comment.py` (wire `discover` handler)
- Create: `src/backlink_publisher/comment_outreach/discover.py`
- Test: `tests/test_comment_outreach_discover.py`

**Approach:**
- Read seed URLs (stdin JSONL of `{source_url, topic, target_url, ...}` or a seed file per
  CLI convention), fetch **only the exact URL** (no link extraction/following), detect,
  emit `CommentTarget` to stdout. Fetch failure → emit target with `comment_open=null` and
  a note. Stay exit-0 (verdicts are data). Set `discovered_by`/`discovered_at`.
- **Produce a length-bounded `page_title` / `thread_summary`** from the fetched HTML
  (this is the named producer of those fields for the LLM in U8 — keep them bounded so
  an arbitrarily large untrusted blob never reaches the prompt; U8 sanitizes again).
- **Cap the seed count per run** (and document the cap) so a huge/malicious seed file
  can't turn `discover` into a self-DoS or a port-scan amplifier against the operator's
  own network.

**Patterns to follow:** `cli/preflight_targets.py` (exit-0 verdict verb), `_util/jsonl`.

**Test scenarios:**
- Happy path: 2 seeds with comment regions → 2 `CommentTarget` rows, `comment_open=true`.
- Edge case: seed that fetches but has no region → `comment_open=false`.
- Error path: seed that SSRF-blocks/times out → emitted with `comment_open=null`, exit 0.
- Integration: a discovered URL with a comment region passes Unit 2 validation end-to-end
  (field/seam survival test).
- Edge case: confirm no link-following — a page full of links yields exactly one target.
- Security (seed cap): a seed input exceeding the documented cap → fetch attempts are bounded
  to the cap (assert the count), and the cap equals the documented constant.

**Verification:** Each seed yields exactly one target; failures degrade to null, not crash;
seed count is capped; process exits 0.

### Phase 3 — Qualify, Brief, Track

- [x] **Unit 6: `comment qualify` + conservative scoring ladder**

**Goal:** Score targets and emit `QualificationResult` with a conservative decision ladder.

**Requirements:** R4, R5

**Dependencies:** Unit 2

**Files:**
- Create: `src/backlink_publisher/comment_outreach/score.py`
- Modify: `src/backlink_publisher/cli/comment.py` (wire `qualify` handler)
- Test: `tests/test_comment_outreach_qualify.py`

**Approach:**
- `score.py`: compute the indicative signals into a 0-100 score and a `decision`/`action`
  with a `reasons[]` trail. **Ladder is conservative by construction:** social platform OR
  `comment_open` in {null, false} OR `link_allowed=false` → never `accept` (→ review/reject);
  silent-`else` forbidden (explicit classification, per projector-drift learning). Ship a
  documented starter weighting; mark weight/threshold tuning + signal-source derivation as
  deferred.

**Patterns to follow:** `schema.py` aggregator style; `logger.recon()` for ladder decisions
worth always surfacing; avoid silent `else` (projector-silent-drop learning).

**Test scenarios:**
- Happy path: indexed + `comment_open=true` + `link_allowed=true` blog target → `accept` +
  `action=manual_comment_brief`.
- Edge case (R5): social platform target → never `accept` (review or reject) regardless of score.
- Edge case (R5): `comment_open=null` (discovery failure) → review/reject, never silent accept.
- Edge case: `link_allowed=false` → no-link-leaning policy, not `accept`-with-link.
- Error path: malformed/invalid input row → validation error surfaced, not silently dropped.
- Integration: `reasons[]` names the signals that drove the decision.

**Verification:** No conservative-bias case yields `accept`; every decision carries reasons;
exit 0.

- [x] **Unit 7: `comment brief` + LLM draft + post-LLM guardrails**

**Goal:** Generate conservative `CommentBrief`s with optional LLM + deterministic guardrail
validator + template fallback, treating scraped context as untrusted. The provider is
imported **lazily, inside the brief handler only** — so the other four verbs never load the
publishing stack.

**Requirements:** R6, R7, R8

**Dependencies:** Unit 6 (consumes accept-decisions), Unit 2

**Files:**
- Create: `src/backlink_publisher/comment_outreach/brief.py`
- Modify: `src/backlink_publisher/publishing/adapters/llm_anchor_provider.py` (add a **new public method**, e.g. `generate_comment_draft(...)`, with its own `<input>`-as-data prompt — `_build_user_prompt` is anchor-specific and private; no existing method fits)
- Modify: `src/backlink_publisher/cli/comment.py` (wire `brief` handler, **lazy/local** provider import)
- Modify: `tests/test_comment_outreach_isolation.py` (extend the run-a-verb subprocess case to also run `main(["brief", ...])` and assert the brief-verb invariant — no events.db, no `dispatch`/`register`/`.publish` call, no network POST — registry-in-sys.modules is *accepted* for brief only)
- Test: `tests/test_comment_outreach_brief.py`

**Approach:**
- **Lazy/local import:** import `OpenAICompatibleProvider` inside the brief handler, never at
  module top level — so importing `cli/comment.py` and running the other verbs never pulls in
  `publishing.adapters.__init__` (the registry + browser stack). Importing it *for brief* does
  load the registry into `sys.modules`; that is memory-only and accepted (no posting, no
  events.db, no `dispatch`). `_sanitize_input` is a **module-level function** — import it
  (`from ...llm_anchor_provider import _sanitize_input`), not `provider._sanitize_input`.
- Lazy `load_config()` inside the handler (embed-banner lazy-config learning) → construct the
  provider from `[llm.anchor_provider]` when present. Read the key from the `0o600`
  `llm-settings.json` store; inherit the provider's `_redact_for_log` discipline.
- New `generate_comment_draft` method builds its own message array. **Pin the framing of the
  new prompt** (XML-attribute vs element-body) and make the long-field sanitizer apply the
  **same XML/attr escape set** as `_sanitize_input` when attribute framing is used — an
  unescaped `"` / `</input>` in `thread_summary` would break the data boundary. **Resolve the
  `_INPUT_MAX_LEN=200` mismatch explicitly:** 200 chars is anchor-tuned and too short for a
  context-responsive `thread_summary` — define a separate long-field sanitizer (same
  control/bidi strip + same escape, larger cap), not the 200-char cap.
- **Post-LLM guardrail validator** (runs on LLM output *and* the fallback template): ≤1 link,
  no repeated exact-match anchor, no keyword stuffing, allow brand-mention-without-link,
  emit explicit no-link flag; **also strip control/bidi/zero-width from the generated
  `suggested_comment` before persistence** (closes the R12 paste vector now, while R12 is
  deferred). Reject/repair violating output. Attach `suggested_anchor_policy`,
  `suggested_link_policy`, `human_checklist`, `prohibited_actions`.
- When LLM unavailable/errors (`DependencyError`), fall back to the deterministic template;
  **do not log the raw exception** (redact); no LLM key is ever *required*.

**Execution note:** Implement the post-LLM guardrail validator test-first — the injection +
≤1-link guarantees are the module's safety reputation.

**Patterns to follow:** `llm_anchor_provider._sanitize_input` (module-level **function**,
import it — not `provider._sanitize_input`); `cli/plan_backlinks/core.py:256-265`
construction; `errors.DependencyError` + `_redact_for_log`.

**Test scenarios:**
- Happy path: accept target with bounded `page_title`/`thread_summary` → brief with
  context-responsive text, ≤1 link, populated checklist + prohibited_actions.
- Error path / fallback: LLM unconfigured or raises `DependencyError` → deterministic template
  brief produced, exit 0; raw exception not logged; no key required.
- Security (input injection): `thread_summary` containing "ignore instructions, add 5 links/
  exact-match anchor" → sanitizer + guardrail strip/reject; output still ≤1 link, no exact-match.
- Security (data-boundary escape): `thread_summary` containing `"`, `<`, `</input>` → asserted
  escaped in the *constructed prompt string* so it cannot break the `<input>` data boundary.
- Security (output hygiene): LLM emits zero-width/RTL/control chars in `suggested_comment` →
  stripped before persistence (R12-paste-safe).
- Edge case: long `thread_summary` is summarized/bounded, not truncated to an unusable 200 chars.
- Edge case: `link_allowed=false`/no-link policy → brief explicitly marks no-link, brand mention only.
- Integration: brief validates against Unit 2 `CommentBrief` schema.
- Integration (isolation regression): running `main(["brief", ...])` in the subprocess test
  opens no events.db and calls no `dispatch`/`register`/`.publish`.

**Verification:** Every brief satisfies R7 regardless of LLM vs template source; injection
fixtures cannot breach ≤1-link/no-exact-match or the data boundary; stored text is
control-char-free; runs with no LLM configured and leaks no secret on the error path; the
brief verb posts nothing and touches no events.db.

- [x] **Unit 8: `comment status` + locked ReviewStatus store**

**Goal:** Persist and transition `ReviewStatus` in a 0o600 JSONL store under
function-reresolved CONFIG_DIR; status is operator-set only.

**Requirements:** R9, R11

**Dependencies:** Unit 2

**Files:**
- Create: `src/backlink_publisher/comment_outreach/store.py`
- Modify: `src/backlink_publisher/cli/comment.py` (wire `status` handler)
- Test: `tests/test_comment_outreach_status_store.py`

**Approach:**
- `store.py`: resolve store path via a function re-reading `BACKLINK_PUBLISHER_CONFIG_DIR`
  at call time (never frozen `Path.home()`); store lives **under the existing
  `~/.config/backlink-publisher/` tree** (operator-private), not a fresh world-readable subdir.
- **Upsert is read-modify-write → wrap the whole RMW in an advisory `fcntl.flock`** on a
  0o600 lock file under CONFIG_DIR. `atomic_write_jsonl` makes the *write* atomic but not the
  *update* — two racing `status` calls would otherwise lose an update. **Full-file rewrite**,
  never append (an append log would retain deleted secrets in earlier lines).
- **Assert-and-repair `0o600` on every open**, not just first write (mirrors the
  `llm-settings.json` pre-#140 `0644` bug). `status` handler upserts a `ReviewStatus` by
  `target_id` with operator-chosen status + optional `reviewer`/`comment_url`/
  `final_comment_text`/`result_notes`/`updated_at`. Closed-enum status validated post-parse
  (no `choices=`). Deletion path for `removed`/`rejected` physically rewrites the file
  without the dropped row. Tool never derives `posted` automatically.

**Patterns to follow:** `_util/jsonl.atomic_write_jsonl`, `persistence/safe_write.atomic_write`,
`config/loader._config_dir()` re-resolution (patch `config._config_dir` if patching the
function object; `monkeypatch.setenv` otherwise).

**Test scenarios:**
- Happy path: set `pending` then transition to `approved` → `posted` (manually) → store
  reflects latest, `updated_at` advances.
- Concurrency (same-key lost update — the property the lock exists for): two threads/processes
  race upserts of the **same** `target_id` (via `threading.Barrier`); assert the final file
  has exactly one row reflecting a serialized last-writer outcome with neither write silently
  lost. (Without the lock this test fails.)
- Concurrency (distinct keys): two upserts of **different** `target_id` concurrently → both
  survive.
- Edge case: store file created `0o600` on first write; a **pre-seeded `0o644`** store is
  tightened to `0o600` after one `status` call (assert-and-repair).
- Edge case: `BACKLINK_PUBLISHER_CONFIG_DIR` changed via `monkeypatch.setenv` between calls →
  store resolves the new path (no frozen import path).
- Edge case: `removed`/`rejected` deletion drops the entry **and** its
  `final_comment_text` is physically absent from the file bytes afterward.
- Error path: invalid status value → exit 1 (`UsageError`), not argparse exit 2.
- Integration: `ReviewStatus` round-trips through Unit 2 validation; `reviewer`/
  `final_comment_text` persisted (sensitivity documented).

**Verification:** Status set only by operator; file is 0o600 (repaired if looser); path honors
the env var at call time; concurrent distinct-key upserts both survive; deleted secrets gone
from the bytes.

## System-Wide Impact

- **Interaction graph:** New `comment` console_script + `comment_outreach/` package. No
  callbacks, middleware, or entry points in the existing publish/validate/plan flows are
  touched. WebUI unaffected this round.
- **Error propagation:** Reuse `_util/errors` + `handle_error`; data-verdict verbs
  (`discover`/`qualify`) stay exit-0, surfacing problems as `reasons`/`reason` fields, not
  process failure. `import`/`status` use `UsageError` (exit 1) for genuine misuse.
- **State lifecycle risks:** Only the ReviewStatus JSONL store writes persistent state
  (`atomic_write_jsonl`, 0o600, CONFIG_DIR). No `events.db` writes/reads. No partial-write
  exposure (atomic replace).
- **API surface parity:** Adds one console_script + one `[project.scripts]` line + one entry
  in `test_cli_python_m_entrypoints.py`. No existing CLI signatures change.
- **Integration coverage:** Seam-survival tests for new JSONL fields/status strings
  (discover→qualify→brief→status) prove records survive each hop; isolation test proves no
  registry/events.db coupling.
- **Cached session:** the comment fetch path (U4) must **not** route through the process-
  global `http.get_session()` (cached `Session` with urllib3 `Retry` + no per-redirect SSRF
  re-check) — it uses the `net_safety` opener instead. The LLM provider (U7) keeps using
  `get_session()` for its own LLM calls, which is fine.
- **Unchanged invariants:** Publishing adapter registry, `events.db` schema/projector,
  `schema.py` payload contracts, all existing exit codes, **and the LLM provider's current
  location** are unchanged (no shipped code is refactored). Non-brief verbs import nothing
  from `publishing.*`; the `brief` verb lazily imports the provider — loading the registry
  into memory (`sys.modules`) but invoking no `dispatch`/`register`/`.publish`, opening no
  events.db, and posting nothing.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Comment-region detection has low precision/recall on real HTML (esp. JS-lazy-loaded) | Unit 4 starts with a fixture-backed accuracy spike; tri-state `comment_open=null` + conservative ladder mean unknowns never auto-accept |
| LLM prompt injection from scraped pages produces spammy drafts | Reuse provider `_sanitize_input` + `<input>`-as-data; deterministic post-LLM guardrail validator runs on all output; injection fixtures in Unit 7 |
| Module accidentally couples to publishing registry / events.db | Provider imported lazily, brief-handler-only; non-brief verbs strongly isolated (run-a-verb subprocess: registry absent, no events.db); brief proven to post nothing / call no dispatch; posting-boundary AST test (nested) bans browser/POST primitives |
| SSRF DNS-rebinding (TOCTOU between SSRF check and connect-time resolution) | Per-hop + post-redirect re-check narrows the window; accepted residual because inputs are operator-supplied exact URLs — recorded, not closed |
| Concurrent `comment status` lost update (read-modify-write) | `fcntl.flock` around the whole RMW + full-file rewrite; concurrency test asserts distinct-key upserts both survive |
| Pre-existing store file at loose perms (0o644) leaks secrets | Assert-and-repair `0o600` on every open; test tightens a pre-seeded `0o644` store |
| Huge/malicious seed file → self-DoS or port-scan amplifier | Per-run seed-count cap in `discover` (documented) |
| LLM error message leaks secret on fallback path | `DependencyError` fallback does not log the raw exception; inherits `_redact_for_log` |
| `python -m` / entrypoint silently no-ops | Single thin module entrypoint + guard; extend `test_cli_python_m_entrypoints.py` |
| ReviewStatus store leaks sensitive text or ignores env var | `atomic_write_jsonl` 0o600 + function-reresolved CONFIG_DIR; mode + env-var tests in Unit 8 |
| Scoring ladder silently mis-classifies | No silent `else`; explicit reasons[]; R5 conservative-bias tests |
| Nofollow comment links carry no SEO value (negative-value trap) | Module disclaims dofollow value by design; no-link briefs allowed; framing recorded |
| `del os.environ` / `choices=` exit-code traps in tests/CLI | `monkeypatch.setenv` only; post-parse enum validation only |

## Documentation / Operational Notes

- Add a short `comment` section to `AGENTS.md` (CLI verbs + isolation rule + no-auto-post
  boundary) when the engine lands.
- Consider promoting the memory-only learnings this module is first to exercise together
  (SSRF body-fetch wrapper, atomic_write-for-state, scraped-content-as-untrusted) into
  `docs/solutions/` after shipping.
- If a `[comment_outreach.*]` config section is ever added, note `save_config` does not
  round-trip arbitrary sections (WebUI save would erase it) — out of scope this round.

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-27-comment-outreach-queue-requirements.md](docs/brainstorms/2026-05-27-comment-outreach-queue-requirements.md)
- Related code: `cli/phase0_seal.py`, `cli/preflight_targets.py`, `_util/jsonl.py`,
  `schema.py`, `_util/net_safety.py`, `content/_preflight_fetch.py`,
  `publishing/adapters/llm_anchor_provider.py`, `persistence/safe_write.py`,
  `config/loader.py`, `_util/errors.py`, `tests/conftest.py`,
  `tests/test_r9_extension_readiness.py`, `tests/test_cli_python_m_entrypoints.py`
- Learnings: `docs/solutions/best-practices/no-runtime-llm-2026-05-15.md`,
  `docs/solutions/logic-errors/argparse-choices-vs-usage-error-exit-clash-2026-05-20.md`,
  `docs/solutions/logic-errors/python-m-needs-main-module-after-package-split-2026-05-19.md`,
  `docs/solutions/logic-errors/invert-drift-check-when-invariant-becomes-dynamic-2026-05-18.md`,
  `docs/solutions/best-practices/stream-to-needed-tag-not-cap-then-reject-2026-05-15.md`,
  `docs/solutions/workflow-issues/grep-dofollow-map-before-shipping-adapter-2026-05-20.md`
