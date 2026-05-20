---
title: Homepage URL Auto-Derive v1.0 — Single-Paste Three-Tier Derivation + Verify Endpoint
type: feat
status: completed
date: 2026-05-20
deepened: 2026-05-20
origin: docs/brainstorms/2026-05-20-homepage-url-autoderive-and-ui-polish-requirements.md
---

# Homepage URL Auto-Derive v1.0

## Overview

Operator pastes one URL into a new `#derive_source` input on homepage `/`. Frontend JS derives main/category/work via path-depth heuristic, writes to existing `main_url`/`category_url`/`work_url` inputs, fires sequential `POST /url-verify` calls behind full SSRF+CSRF+rate-limit security perimeter.

## Requirements Trace (v1.0 only — see origin doc for full)

- **R1** path-depth derivation + R2 normalization → `_util/url_derive.py` + `static/js/url_derive.js`
- **R3** async verify with closed-enum reason → `/url-verify` route + `content_fetch.verify_url_has_content` extensions
- **R3.5** title pairwise compare (FE) + body_too_small (BE) → JS verifyAll + fetch.py body-size gate
- **R3.6** `max_age_seconds=0` cache bypass → already supported by content_fetch
- **R5** paste_url entry point → nameless `#derive_source` input
- **R7** `/ce:plan` handler unchanged → tested via regression test
- **R8a-g** full security perimeter → 4-guard stack + throttle module + closed-enum response

## Scope Boundaries

v1.1+ deferred: R4 lock state machine, R6 chip cards + responsive baseline, R10 drawer fold, R11/R12 a11y. R9 dropdown cut entirely. CLI parity ships in v1.0 (Python deriver is canonical, JS mirrors 1:1).

## Key Technical Decisions

- **Reuse `verify_url_has_content`** with additive `timeout_seconds`/`max_redirects` kwargs (zero CLI risk)
- **Factory `_make_ssrf_opener(max_redirects)`** over subclass-per-cap (reusable across future callers)
- **Stdlib-only throttle module** in `webui_app/services/` (no Flask-Limiter dep; single-worker dev posture)
- **Per-host lock `acquire(timeout=0)`** + atomic check-then-reserve under `_window_locks_guard` (TOCTOU defense)
- **CSRF: synchronizer via `_check_csrf_or_abort`** (helpers.py:639), NOT double-submit (per OWASP cited research)
- **4-guard stack** mirrors `routes/bind.py`: loopback + ALLOW_NETWORK refusal + Origin + CSRF
- **Always 200 + closed-enum reason** for verification outcomes; 403 only for auth/loopback; 204 only for BACKLINK_NO_FETCH_VERIFY
- **IDN→ASCII via stdlib idna** (IDNA2003, no homograph defense — accepted as v1.0 residual risk)
- **Accept DNS-rebinding TOCTOU under default loopback** (operator-only trust); HARD-refuse under ALLOW_NETWORK=1
- **`derive_source` nameless input** — structurally impossible to capture by `/ce:plan` extras-loop
- **Sequential verifyAll (NOT Promise.all)** — Promise.all of 3 same-host calls would burn per-host=1 throttle
- **R3.5 split**: title pairwise compare lives FE (cross-URL); body-size check lives BE (per-fetch)
- **v1.0 status display = inline text + status spans** with full WCAG aria-live wiring (chip cards = v1.1)
- **Ship Python deriver `_util/url_derive.py`** day-one as canonical implementation; JS mirrors 1:1 (resolves CLI parity question)

## Resolved Strategic Questions (from brainstorm)

SQ1=A heuristic+verify, SQ2=B cut R9, SQ3=A toast notification, SQ4=B FE pre-submit, SQ5=B phase v1.0/v1.1

## Implementation Units

- [x] **Unit 1**: `_util/url_derive.py` deriver + `content_fetch.verify_url_has_content` kwargs + `body_too_small` reason + `_make_ssrf_opener` factory → commit `5eedf86`
- [x] **Unit 2**: `webui_app/services/url_verify_throttle.py` sliding-window + Semaphore + per-host Lock + RECON cap → commit `f616140`
- [x] **Unit 3**: `webui_app/routes/url_verify.py` 4-guard stack + IDN+userinfo+scheme normalization + throttle integration + closed-enum response → commit `033be0b`
- [x] **Unit 4+5**: `static/js/url_derive.js` mirror + `templates/index.html` wire-up + `<meta csrf-token>` + `app_context_processor` → commit `b198bfb`
- [x] **Unit 6**: `derive_source` field regression for R7 invariant → commit `7fdc015`

## Closed-Enum `reason` Taxonomy

`{ok, invalid_url, network_error, ssrf_blocked, timeout, http_<NNN>, http_200_no_title, soft_404_title, body_too_small, blocked_scheme, rate_limited, host_busy, upstream_overloaded}` — whitelist enforced at response boundary; no IP/hostname/error-string leakage.

## Risk Acceptance Log

- **DNS-rebinding TOCTOU under loopback**: accepted; v1.1 hardening candidate (custom HTTPAdapter pin-IP)
- **IDN homograph (stdlib IDNA2003)**: accepted; v1.1 candidate (`idna>=3.0` with `uts46=True`)
- **Title field 24-byte exfil under DNS-rebinding window**: accepted (rate × `host_hash` masking limits to ~14 B/s under attacker-controlled DNS)
- **Timing side-channel (5s/6s budget bands)**: accepted; v1.1 candidate (random jitter on non-200)
- **Multi-worker future**: documented; project ships single-worker dev only

## Test Coverage

- `tests/test_url_derive.py`: 24 deriver cases (path-depth + R2 normalization + boundary)
- `tests/test_content_fetch.py`: +10 cases (kwarg overrides + body_too_small + factory)
- `tests/test_url_verify_throttle.py`: 16 cases (atomicity, rollback, threading.Barrier × 50 stress, RECON cap)
- `tests/test_webui_url_verify_routes.py`: 31 cases (4 guards, throttle, closed-enum, no-IP-leak, title truncation)
- `tests/test_webui_route_contract.py`: +1 regression (`derive_source` ignored by `/ce:plan`)
- Full suite: **3001 passed in 70s**

## Operational Notes

- Single-worker Flask dev `:8888` loopback only; ALLOW_NETWORK=1 hard-refuses `/url-verify`
- RECON events at `recon` log level with `host_hash` + `request_id` (8-byte hex); never raw URL/IP
- SLOC: `content/fetch.py` 266/290 ceiling (post-Unit 1)

## Sources

- Origin: `docs/brainstorms/2026-05-20-homepage-url-autoderive-and-ui-polish-requirements.md`
- Branch: `feat/homepage-url-autoderive-v1`
- Reviewers consulted: coherence, feasibility, design-lens, product-lens, security-lens, security-sentinel, architecture-strategist, adversarial × 2 passes
