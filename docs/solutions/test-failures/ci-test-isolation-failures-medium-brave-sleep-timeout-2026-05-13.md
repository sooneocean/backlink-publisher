---
title: "CI test isolation failures: missing pytest-timeout, unmocked MediumBraveAdapter, and unmocked time.sleep"
date: 2026-05-13
category: test-failures
module: backlink-publisher
problem_type: test_failure
component: testing_framework
severity: medium
symptoms:
  - "pytest --timeout=30 unrecognized: error: unrecognized arguments: --timeout=30 (exit code 4)"
  - "AssertionError: assert 'medium-brave' == 'medium-browser' (only on macOS with Brave running)"
  - "Failed: Timeout (>30.0s) from pytest-timeout on test_external_service_error_mid_batch_continues"
root_cause: test_isolation
resolution_type: test_fix
tags:
  - pytest
  - ci-failure
  - test-isolation
  - mocking
  - macos-specific
  - pytest-timeout
  - time-sleep
  - platform-dependent
---

# CI test isolation failures: missing pytest-timeout, unmocked MediumBraveAdapter, and unmocked time.sleep

## Problem

Three interrelated CI failures in the adapter dispatcher and publish pipeline tests, all stemming from gaps between test assumptions and the runtime environment. The first blocked the test runner entirely (unrecognized CLI flag), the second was environment-dependent (only reproducible on macOS with Brave Browser running), and the third caused a silent timeout that consumed the full 30-second budget.

## Symptoms

**Failure 1 — test runner crash (exit code 4):**
```
ERROR: usage: __main__.py [options] [file_or_dir] [file_or_dir] [...]
__main__.py: error: unrecognized arguments: --timeout=30
inifile: .../pyproject.toml
```

**Failure 2 — wrong adapter returned on macOS:**
```
AssertionError: assert 'medium-brave' == 'medium-browser'
```
Passed on Linux CI, failed only on macOS developer machines with Brave Browser running.

**Failure 3 — test timeout:**
```
E   Failed: Timeout (>30.0s) from pytest-timeout.
```
`test_external_service_error_mid_batch_continues` hung for the full 30-second budget before failing.

## What Didn't Work

- Assuming `--timeout=30` in CI would work without verifying `pytest-timeout` was installed. The flag is silently unrecognized without the package, producing exit code 4 (argument error), not a test failure — it looks like a misconfigured test runner, not a missing dependency.
- Mocking only the first and last adapter in a three-level fallback chain. The middle adapter (`MediumBraveAdapter`) was left unmocked, so on macOS with Brave running it executed for real via AppleScript and short-circuited the intended fallback.
- Not mocking `time.sleep` in a batch test that processes rows sequentially. The throttle logic sleeps 60–300 seconds between Medium publishes; the first successful row triggered a real sleep that exceeded the timeout.

## Solution

**Fix 1 — add `pytest-timeout` to dev dependencies in `pyproject.toml`:**

```toml
# Before (broken):
dev = ["pytest>=7", "pytest-mock", "requests", "pytest-asyncio>=0.23"]

# After (fixed):
dev = ["pytest>=7", "pytest-mock", "pytest-timeout", "requests", "pytest-asyncio>=0.23"]
```

**Fix 2 — mock all three levels of the Medium adapter fallback chain:**

```python
# Before (broken — MediumBraveAdapter unmocked):
@patch("backlink_publisher.adapters.MediumBrowserAdapter.publish", return_value=MEDIUM_BROWSER_RESULT)
@patch("backlink_publisher.adapters.MediumAPIAdapter.publish", side_effect=DependencyError("no token"))
def test_medium_fallthrough_to_browser_on_dependency_error(mock_api, mock_browser):
    result = publish(MEDIUM_PAYLOAD, mode="draft", config=CONFIG_NO_TOKEN)
    assert result.adapter == "medium-browser"  # FAILS on macOS with Brave running

# After (fixed — all three levels mocked):
@patch("backlink_publisher.adapters.MediumBrowserAdapter.publish", return_value=MEDIUM_BROWSER_RESULT)
@patch("backlink_publisher.adapters.MediumBraveAdapter.publish", side_effect=DependencyError("brave not running"))
@patch("backlink_publisher.adapters.MediumAPIAdapter.publish", side_effect=DependencyError("no token"))
def test_medium_fallthrough_to_browser_on_dependency_error(mock_api, mock_brave, mock_browser):
    result = publish(MEDIUM_PAYLOAD, mode="draft", config=CONFIG_NO_TOKEN)
    assert result.adapter == "medium-browser"  # PASSES
```

**Fix 3 — mock `time.sleep` to eliminate throttle delays:**

```python
# Before (broken — real 60-300s sleep fires):
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_external_service_error_mid_batch_continues(mock_pub, mock_verify):
    ...

# After (fixed — sleep is a no-op):
@patch("backlink_publisher.cli.publish_backlinks.time.sleep")
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_external_service_error_mid_batch_continues(mock_pub, mock_verify, mock_sleep):
    ...
```

## Why This Works

**Fix 1:** `pytest-timeout` provides the `--timeout` CLI argument. Without it installed, pytest treats the flag as an unrecognized argument and exits with code 4 before running any tests. Adding it to dev dependencies ensures `pip install -e ".[dev]"` installs it in CI.

**Fix 2:** Python's `@patch` decorators apply bottom-up, so the function argument order is reversed from the decorator stack. When testing a fallback chain, every branch that could succeed in the real environment must be patched. `MediumBraveAdapter` uses AppleScript on macOS — if Brave is running, its `publish()` call succeeds for real, preventing the fallthrough to `MediumBrowserAdapter`. This bug only surfaces on macOS developer machines and silently passes on Linux CI.

**Fix 3:** `time.sleep` is called with values derived from `os.environ` (defaults: 60–300 seconds). Tests exercising multi-row Medium publish batches will hit the throttle on the first successful row. Patching `backlink_publisher.cli.publish_backlinks.time.sleep` (the module-level reference, not the stdlib directly) makes the sleep a no-op without affecting production behavior.

## Prevention

**Audit CI flags against installed packages.** Any pytest flag used in the CI workflow (`--timeout`, `--cov`, `--randomly-seed`, etc.) must have its corresponding package in the `[dev]` dependency group. Cross-reference `.github/workflows/ci.yml` against `pyproject.toml` before merging CI changes.

**Mock every branch of a fallback chain.** When testing "falls through to adapter N," patch adapters 1 through N-1 with `side_effect=DependencyError(...)`. Platform-conditional branches (`if platform.system() == "Darwin"`) are especially hazardous — they pass on Linux CI and fail silently on macOS developer machines. Document the chain in a comment above the test:

```python
# Adapter selection order: API → Brave (macOS, AppleScript) → Browser (Playwright)
# All three must be mocked when testing final-level fallthrough
```

**Mock all I/O with wall-clock cost in batch tests.** Any function that sleeps, makes network calls, or writes to disk should be mocked in unit and integration tests. `time.sleep` is easy to miss because it does not raise errors — it just burns time. `pytest-timeout` turns silent hangs into loud failures, which is why Fix 1 and Fix 3 are coupled: the timeout makes the sleep detectable, and the sleep mock makes the timeout irrelevant.

## Related Issues

- Memory entry: `feedback_macos-adapter-test-isolation.md` — captured the macOS Brave / time.sleep patterns in project memory (auto memory [claude])
