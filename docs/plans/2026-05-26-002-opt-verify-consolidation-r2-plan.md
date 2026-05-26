---
title: "opt: adapters/__init__.py verify consolidation R2"
type: refactor
status: active
date: 2026-05-26
claims: {}
---

# opt: adapters/`__init__.py` verify consolidation R2

## Problem Frame

`adapters/__init__.py` is 545 SLOC (ceiling 560) — only 15 SLOC headroom for any accretion. Across the 4 `_verify_*_live()` functions (telegraph, ghpages, blogger, velog) the same `VerifyResult` construction patterns (success, timeout, network error, non-JSON response, token expired) are duplicated verbatim ~5× each. The `verify_adapter_setup()` function uses an 11-branch if/elif chain that mirrors the same config-check pattern across platforms.

Both patterns were acceptable at implementation time (shipped as standalone units per platform plan), but now that all 4 live verify endpoints are wired and `verify_adapter_setup()` has settled at 8 branches, the duplication exceeds the maintenance-cost threshold — adding a 5th live verify or a 9th setup check requires copying the boilerplate again, which is exactly the friction the monolith budget exists to cap.

## Scope

**Strictly additive-extract:** new shared helpers + dispatch table, existing logic preserved verbatim (error messages, class mapping, timeout values). No behavioral change, no test changes.

| Target | Current SLOC | Target SLOC | Saving |
|---|---|---|---|
| `_verify_telegraph_live` | ~97 | ~35 | −62 |
| `_verify_ghpages_live` | ~96 | ~37 | −59 |
| `_verify_blogger_live` | ~97 | ~31 | −66 |
| `_verify_velog_live` | ~99 | ~36 | −63 |
| `verify_adapter_setup()` if/elif | ~85 | ~6 dispatch | −79 |
| Shared helpers (add) | 0 | ~20 | +20 |
| **Net file** | **545** | **~447** | **−98** |

Ceiling update: 560 → 480.

## Shared Helpers (6)

All return `VerifyResult` with pre-filled fields for the caller's category:

| Helper | last_verify_result | ok |
|---|---|---|
| `_ok_result(identity)` | `"ok"` | True |
| `_timeout_result(platform, timeout_s)` | `"timeout"` | False |
| `_network_error(platform, err)` | `"never"` | False |
| `_non_json(platform)` | `"never"` | False |
| `_token_expired(msg)` | `"token_expired"` | False |
| `_never(msg)` | `"never"` | False |

The caller still provides the platform-specific blocker message (each platform has different wording, status mapping logic, and identity extraction). The helper eliminates the 6-line `VerifyResult(...)` repetition per call site.

## `verify_adapter_setup()` — Dispatch Table

Replace the 85-line if/elif chain with `_SETUP_CHECKS: dict[str, Callable[[Config], str | None]]`.

Each entry returns `None` (ok) or a non-empty error string (raises `DependencyError`). The `"medium"` entry keeps its complex multi-condition logic (3 imports, `has_*` booleans, Brave exclusion rationale) as a standalone `_check_medium_setup(config)` function — the dispatch table delegates to it. The 7 simple entries are lambdas or 1-liner helpers.

## Non-goals

- NOT touching `_verify_live()` (3-line dispatcher, fine as-is)
- NOT touching `_verify_dry_run()` (no duplication)
- NOT extracting verify functions to a separate `_verify.py` — reverse dependency risk (verify helpers would import from the same adapters module that imports them)
- NOT adding `_do_live_request()` HTTP wrapper — the 4 functions use different HTTP methods (POST vs GET), different auth carriers (Bearer header, PAT header, cookies, `data=` fields), and different error interpretation (Telegraph JSON body vs GH status code). A shared wrapper would leak too many platform details.
