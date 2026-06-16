# Solution Blueprint: Resolving Silent Exception Swallowing in Publisher Adapters

## 1. Executive Summary

Operational correctness and error-logging hygiene (O2) are paramount for a resilient multi-platform publishing engine. When publishing tasks interact with external web pages, REST APIs, or system utilities, transient and fatal failures must be explicitly classified, logged, or propagated. 

An audit of the adapter modules under `src/backlink_publisher/publishing/adapters/` revealed several instances where broad `except Exception:` or `except OSError:` clauses are used to catch and silently swallow errors (`pass`) or bypass error logging. This silent swallowing hides failures in background processes, makes debugging extremely difficult, and violates the principle of maximum observability.

This blueprint establishes a **Standardized Logging and Propagation Pattern** for all adapter modules and provides concrete refactoring blueprints for the audited files to eliminate silent exception swallows while preserving proper fallback boundaries.

---

## 2. Current State & Audit Findings

The following files under `src/backlink_publisher/publishing/adapters/` contain silent or sub-optimal broad exception handling:

### 2.1. `linkedin_api.py`
* **Location**: Lines [138-141](file:///Users/dex/YDEX/INPORTANT%20WORK/外链/backlink-publisher/backlink-publisher/src/backlink_publisher/publishing/adapters/linkedin_api.py#L138-L145)
* **Code Block**:
  ```python
  data = {}
  try:
      data = resp.json()
  except Exception:
      pass
  err = (data.get("message") or resp.text)[:200]
  ```
* **Issues**: 
  1. Catches the broad `Exception` instead of specific `ValueError` or `json.JSONDecodeError` for JSON decoding.
  2. Silently swallows (`pass`) without a debug log or warning. If `resp.json()` throws a major system/memory issue, it is hidden.

---

### 2.2. `medium_browser.py`
This module has the most extensive use of browser automation and several silent catches:

* **Location**: Lines [161-164](file:///Users/dex/YDEX/INPORTANT%20WORK/外链/backlink-publisher/backlink-publisher/src/backlink_publisher/publishing/adapters/medium_browser.py#L161-L164)
* **Code Block**:
  ```python
  try:
      live_cookies = context.cookies("https://medium.com") or []
  except Exception:
      live_cookies = []
  ```
* **Issues**: Silently catches any exception while fetching context cookies and proceeds with an empty list. No telemetry or log trace is created, hiding issues with the Playwright browser context.

* **Location**: Lines [257-266](file:///Users/dex/YDEX/INPORTANT%20WORK/外链/backlink-publisher/backlink-publisher/src/backlink_publisher/publishing/adapters/medium_browser.py#L257-L266)
* **Code Block**:
  ```python
  try:
      if page.locator(sel.CAPTCHA_IFRAME_SELECTOR).count() > 0:
          raise ExternalServiceError(...)
  except ExternalServiceError:
      raise
  except Exception:
      pass  # probe failed; let retry handle the timeout
  ```
* **Issues**: The broad `except Exception:` swallows probe errors during a Playwright timeout. While the probe is best-effort, a failed probe due to a detached page or selenium error should log a debug statement so operators know *why* the CAPTCHA probe failed.

* **Location**: Lines [321-325](file:///Users/dex/YDEX/INPORTANT%20WORK/外链/backlink-publisher/backlink-publisher/src/backlink_publisher/publishing/adapters/medium_browser.py#L321-L325)
* **Code Block**:
  ```python
  try:
      page.locator(sel.SAVE_DRAFT).click()
      page.wait_for_timeout(2000)
  except Exception:
      page.wait_for_timeout(3000)
  ```
* **Issues**: This is a critical silent swallow. If clicking `SAVE_DRAFT` fails, it falls back to sleeping 3000ms with zero logs, warnings, or indicators. If Medium's UI layout changes, this failure will remain completely invisible.

* **Location**: Lines [409-421](file:///Users/dex/YDEX/INPORTANT%20WORK/外链/backlink-publisher/backlink-publisher/src/backlink_publisher/publishing/adapters/medium_browser.py#L409-L421)
* **Code Block**:
  ```python
  def _save_screenshot(page: Any, config: Config, article_id: str) -> None:
      try:
          shot_path = _screenshot_path(config, article_id)
          page.screenshot(path=str(shot_path))
          # ...
      except Exception:
          pass
  ```
* **Issues**: Diagnostic unlinking or page screenshot failures are completely swallowed. A filesystem error (e.g., directory permissions, disk space) is hidden.

---

### 2.3. `blogger_api.py`
* **Location**: Lines [95-98](file:///Users/dex/YDEX/INPORTANT%20WORK/外链/backlink-publisher/backlink-publisher/src/backlink_publisher/publishing/adapters/blogger_api.py#L95-L98)
* **Code Block**:
  ```python
  creds: Credentials | None = None
  if token_data:
      try:
          creds = Credentials.from_authorized_user_info(token_data, _SCOPES)
      except Exception:
          creds = None
  ```
* **Issues**: Swallows all exceptions during token deserialization (e.g., format changes, corrupt token data) with no logging.

---

### 2.4. `devto_api.py` & `notion_api.py`
* **Locations**: `devto_api.py` Line 206, `notion_api.py` Line 245
* **Code Block**:
  ```python
  if resp.status_code == 422: # (or 400 for notion)
      try:
          err_body = resp.json()
          msg = err_body.get("error") or ...
      except Exception:
          msg = resp.text[:200]
  ```
* **Issues**: Catches `Exception` broadly during response JSON parsing. Although this fallback is appropriate, catching `Exception` is too broad compared to catching `ValueError` / `json.JSONDecodeError`.

---

## 3. Standardized Exception Handling Policy

To enforce high code quality and error hygiene, all publisher adapters must adhere to the following rules:

### Rule 1: No Unlogged `except Exception:` (or Bare `except:`)
* An `except Exception:` block must never just `pass` or silence the error without generating a telemetry trace.
* If an exception must be swallowed (e.g., to continue a fallback boundary), at least a `log.debug()` or `log.warning()` must capture the exception's name and message.

### Rule 2: Prefer Specific Exceptions Over Broad Catch-Alls
* Do not catch `Exception` if you only expect a standard type:
  * For JSON parsing/decoding, catch `ValueError` or `json.JSONDecodeError`.
  * For file-system operations (read/write/unlink), catch `OSError` or `FileNotFoundError`.
  * For network operations (if not wrapped in our standard helpers), catch `requests.RequestException` or `playwright.async_api.Error`.

### Rule 3: Maintain Fallback Boundaries Safely
* When handling optional adapter features (e.g., tag insertion, draft-saving retries), isolate them in narrow `try...except` blocks.
* Document the fallback behavior with a comment and a `log.debug()` explaining that the operation is optional, but failed.

### Rule 4: Preserving Tracebacks (Re-raising)
* If an exception is wrapped and re-raised, always use `from exc` to preserve the original traceback context:
  ```python
  except Exception as exc:
      raise ExternalServiceError("Description") from exc
  ```
* Use a bare `raise` when no transformation is done to allow unmodified propagation.

---

## 4. Refactoring Blueprints (Proposed Changes)

The following side-by-side code blocks demonstrate how the affected adapters should be modified:

### 4.1. `linkedin_api.py` (JSON extraction fallback)
```diff
-                try:
-                    data = resp.json()
-                except Exception:
-                    pass
+                try:
+                    data = resp.json()
+                except ValueError as exc:
+                    log.debug("Failed to decode JSON response for HTTP 403 error: %s", exc)
+                    data = {}
```

---

### 4.2. `medium_browser.py` (Playwright & cookie fallbacks)
**1. Cookie extraction fallback:**
```diff
-        try:
-            live_cookies = context.cookies("https://medium.com") or []
-        except Exception:
-            live_cookies = []
+        try:
+            live_cookies = context.cookies("https://medium.com") or []
+        except Exception as exc:
+            log.warning("Failed to extract live cookies from Playwright context: %s: %s", type(exc).__name__, exc)
+            live_cookies = []
```

**2. CAPTCHA count probe:**
```diff
                         try:
                             if page.locator(sel.CAPTCHA_IFRAME_SELECTOR).count() > 0:
                                 raise ExternalServiceError(
                                     "Medium CAPTCHA detected after timeout. "
                                     "Solve it manually at medium.com, then retry."
                                 )
                         except ExternalServiceError:
                             raise
-                        except Exception:
-                            pass  # probe failed; let retry handle the timeout
+                        except Exception as exc:
+                            log.debug("Medium CAPTCHA probe failed during timeout: %s", exc)
```

**3. Save draft fallback:**
```diff
                         try:
                             page.locator(sel.SAVE_DRAFT).click()
                             page.wait_for_timeout(2000)
-                        except Exception:
+                        except Exception as exc:
+                            log.warn(
+                                "Failed to click 'Save Draft' button during fallback: %s. "
+                                "Proceeding with standard wait.",
+                                exc,
+                             )
                             page.wait_for_timeout(3000)
```

**4. Screenshot diagnostic fallback:**
```diff
 def _save_screenshot(page: Any, config: Config, article_id: str) -> None:
     try:
         shot_path = _screenshot_path(config, article_id)
         page.screenshot(path=str(shot_path))
         import sys
         import json
         print(
             json.dumps({"level": "ERROR", "screenshot": str(shot_path)}),
             file=sys.stderr,
         )
-    except Exception:
-        pass
+    except Exception as exc:
+        log.debug("Failed to capture diagnostic screenshot: %s", exc)
```

---

### 4.3. `blogger_api.py` (Blogger Credential Loading)
```diff
     creds: Credentials | None = None
     if token_data:
         try:
             creds = Credentials.from_authorized_user_info(token_data, _SCOPES)
-        except Exception:
+        except Exception as exc:
+            log.warn("Failed to load Blogger credentials from config token: %s", exc)
             creds = None
```

---

### 4.5. `devto_api.py` & `notion_api.py` (Error body json parser)
*(Example from Dev.to, same applies to Notion)*
```diff
             if resp.status_code == 422:
                 try:
                     err_body = resp.json()
                     msg = (
                         err_body.get("error")
                         or str(err_body.get("errors", ""))
                         or resp.text[:200]
                     )
-                except Exception:
+                except ValueError:
                     msg = resp.text[:200]
```

---

## 5. Verification Plan

Since we do not want to introduce functional regression or break exit code policies, all changes must be verified using:
1. **Lint/AST checks**: Ensure no syntax errors or AST breaks exist.
2. **Pytest Suite**: Execute all adapter mock tests to verify that normal flow and error wrapping work exactly as expected.
   ```bash
   pytest tests/test_medium_browser.py
   pytest tests/test_linkedin_api.py
   pytest tests/test_blogger_api.py
   pytest tests/test_devto_api.py
   pytest tests/test_notion_api.py
   ```
3. **Structured Log Verification**: Run draft/publishing flows in dry-run/mock mode and check that logs reflect warnings/debug levels correctly rather than masking them.
