---
date: 2026-05-12
topic: adapter-retry-backoff
---

# Adapter Auto-Retry with Exponential Backoff

## Problem Frame

When `publish-backlinks` runs a batch, any single transient failure (network timeout, API rate-limit, temporary 5xx) causes the entire process to exit immediately. Already-published articles are written to stdout, but remaining articles are never attempted. Users must restart from scratch, risking duplicate publishes on the items that already succeeded.

This requirement reduces the frequency of such exits by automatically retrying transient failures within each adapter. It does **not** eliminate silent discard: if all retry attempts are exhausted, the batch still exits at that article. Resumable batch execution (checkpoint/resume) is a separate, complementary feature.

## Requirements

### Retry Behaviour

- R1. Each adapter's publish call is retried up to 3 total attempts (1 initial + 2 retries) on transient failures before escalating to `ExternalServiceError`.
- R2. Backoff before each retry follows an exponential schedule: wait `2^retry` seconds — before retry 1: wait 2s, before retry 2: wait 4s — with ±15% random jitter per wait.
- R3a. On each retry, the system emits a structured progress message to stderr: e.g. `retrying (attempt 2/3): HTTP 429 — waiting 4s`. Retry messages include only: HTTP status code, adapter name, attempt count, and wait duration. No response bodies, headers, or credentials are included.

### Error Classification

- R4. Retryable failures: HTTP 429, HTTP 5xx, connection timeout, `ConnectionError` / `ReadTimeout`.
- R5. Non-retryable failures (fail immediately, no retries): HTTP 401, HTTP 403, HTTP 422, `DependencyError`, input validation errors, and CAPTCHA/2FA interruptions from Playwright.
- R6. After all 3 attempts fail, the last caught exception is wrapped as `ExternalServiceError` with the original exception preserved as cause and original message unchanged. No raw HTTP response headers or body are propagated.

### Playwright-Specific Handling

- R7. For Playwright-based publishes (Medium browser fallback), retry re-invokes the full browser publish flow from the start, not an individual interaction step within the flow.
- R8. Playwright CAPTCHA and 2FA blocks are classified as non-retryable (R5); the current behaviour of writing a screenshot and failing is preserved.

### Pipeline Dispatcher

- R12. The `ExternalServiceError` catch block in `publish_backlinks.py` is changed from `emit_error(…)` (which exits the process) to appending a failed-output record and continuing the batch loop — mirroring the existing `except Exception` path. Without this change, per-adapter retry cannot prevent batch abort when all retry attempts are exhausted.

### Scope and Configuration

- R9. Retry parameters (max attempts, backoff base) are not user-configurable in V1. Defaults in R1–R2 apply universally. Parameters are defined as named constants in a shared location — not inline per adapter — so future extraction requires no adapter changes.
- R10. The existing Medium throttle (60–300s between articles in `publish_backlinks.py`) is not affected. Retry backoff waits are additive within a single article's attempts, separate from inter-article throttle.
- R11. `--dry-run` mode is not affected. Dry-run never reaches live network calls, so retry logic is never triggered.

## Success Criteria

- A batch of 20 articles completes fully even when 1–2 articles encounter a transient timeout or 429 during the run, without user intervention.
- A 401 (expired token) fails immediately on the first attempt with the same error message as today, with no delay from retry waits.
- The user sees "retrying (attempt 2/3)…" in the terminal or web UI loading overlay when a retry fires.
- Added latency per article: approximately 2s (±15% jitter) for a transient failure that recovers on retry 1; up to approximately 7s total (2s + 4s + jitter) if all 3 attempts are exhausted.

## Scope Boundaries

- No retry for authentication errors (401/403) — these require human intervention (re-auth).
- No retry for input validation failures — bad data does not fix itself on retry.
- No retry for Playwright CAPTCHA/2FA — interactive recovery is a separate feature.
- No user-configurable retry parameters in V1 — hardcoded sensible defaults only.
- No changes to the inter-article throttle logic in `publish_backlinks.py`.
- No retry at the CLI pipeline level (plan → validate → publish) — only within individual adapter publish calls.
- **HTTP 5xx retry assumes no partial server-side commit**: We assume that a 5xx or timeout response from Blogger API or Medium API means the publish operation was not committed server-side. If this assumption is wrong for a given platform, 5xx should be treated as non-retryable for that platform to prevent duplicate articles.

## Key Decisions

- **Retry in the adapter layer + dispatcher batch-continue**: Retry logic lives inside each adapter (before converting to `ExternalServiceError`) because that is where raw HTTP status codes are visible. The pipeline dispatcher (`publish_backlinks.py`) is also changed: `emit_error()` on `ExternalServiceError` is replaced with log-and-continue so that a single exhausted-retry article does not exit the entire process. Both changes are required — adapter retry alone is insufficient.
- **3 attempts with 2^n backoff**: Standard industry practice for idempotent HTTP operations. Keeps worst-case added latency under 10 seconds for recoverable failures.
- **Show retry progress to users**: Users need confidence the system is recovering, not frozen. A one-line stderr message and loading overlay update achieve this with minimal complexity.
- **Re-invoke full Playwright flow on retry**: Partial Playwright retries (resuming mid-flow) would require stateful browser session management, which is out of scope for V1.

## Dependencies / Assumptions

- The error hierarchy contract (`DependencyError` vs. `ExternalServiceError`) defined in `errors.py` must be preserved. The retry layer must not catch `DependencyError`.
- Blogger API adapter currently catches `HttpError` and raises `ExternalServiceError` with specific messages for 401/403/429. Retry classification (R4–R5) must map to these same HTTP status codes.
- **Idempotency assumption**: We assume that a 5xx response or connection timeout from the target platform means the publish request was not committed server-side. If a platform's API documentation contradicts this (e.g., the request may have been processed before returning 5xx), HTTP 5xx should be removed from the retryable list (R4) for that platform. Verify against Blogger API v3 and Medium API documentation before implementing 5xx retry.

## Outstanding Questions

### Deferred to Planning

- [Affects R1, R4][Technical] Where exactly is retry logic added? Options: (a) helper function in `adapters/base.py` called from each adapter's `publish()` before converting to `ExternalServiceError`, (b) decorator on the adapter `publish()` method. Note: option (c) — wrapper in `adapters/__init__.py` — is not viable; the dispatcher only sees `ExternalServiceError` after it has been raised and cannot re-invoke the adapter without breaking the error contract.
- [Affects R5, R7, R8][Needs research] Does the Medium browser adapter currently raise a typed exception for Playwright failures, or a generic `ExternalServiceError` with only a string distinguishing CAPTCHA vs. network errors? If typed exceptions do not exist, R8's non-retryable classification requires either: (a) adding a `CaptchaError` subclass, or (b) matching on specific message substrings (fragile). Resolve before implementing R5/R8.
- [Affects R3a][Needs research] Does the web UI loading overlay (in `webui.py`) read from stderr lines during the subprocess call, or does it require a separate IPC/event mechanism? If the overlay can parse structured stderr, R3a's stderr message satisfies the web UI update. If not, web UI retry visibility is a separate follow-on task not in scope here.

## Next Steps
→ `/ce:plan` for structured implementation planning
