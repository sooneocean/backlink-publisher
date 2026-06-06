---
title: Post-publish rendered-link verification for backlinks
date: 2026-06-05
category: design-patterns
module: backlink_publisher.publishing
problem_type: design_pattern
component: service_object
severity: medium
applies_when:
  - "After publishing a backlink to a public zero-auth platform (PostEasy, BrewPage, Nonograph, HtmlDrop, Telegraph, Rentry), verify whether the backlink anchor actually renders as a dofollow link in the published HTML"
  - "Any third-party platform publish where the API does not return a rendered snapshot and the platform may silently transform content after accepting the publish request"
tags:
  - backlink-verification
  - post-publish
  - rendered-link
  - dofollow
  - zero-auth
  - backlink-outcome
  - quality-assurance
---

# Post-publish rendered-link verification for backlinks

## Context

The gap between "publish succeeded" (the adapter returns HTTP 200) and "the
backlink actually works" (the rendered page contains a dofollow anchor) is
wide — especially on zero-auth platforms where the acceptance API is a
thin HTTP POST with no post-publish content guarantee. Platforms may:

- Accept the post but silently strip `<a>` tags from the rendered output.
- Inject `rel="nofollow"` (or `ugc`/`sponsored`) onto links without
  informing the publisher.
- Reject content server-side that the API accepted, returning a generic
  page or error instead of the expected content.

Without post-publish verification, operators cannot distinguish effective
backlinks from published-but-ineffective ones. HTTP-level success is
necessary but insufficient evidence of a working backlink.

## Guidance

### Pattern: Best-effort rendered-link verification

After publishing, fetch the published page URL, parse the HTML, and
check whether the expected anchor (target URL) appears as a dofollow
link. Classify the outcome into a standard taxonomy and attach it to
the publish record.

### Core implementation

**Verification function** (`publishing/_verify_html.py`):

```python
@dataclass
class RenderedLinkResult:
    found: bool          # anchor for target_url found in rendered HTML
    dofollow: bool | None  # whether the link has dofollow rel
    status_code: int     # HTTP status from fetch
    error: str | None    # error message, if any

def verify_rendered_link(
    published_url: str,
    target_url: str,
    timeout: int = 15,
) -> RenderedLinkResult:
    """
    Fetch published_url, parse HTML, and check whether target_url
    appears as a dofollow anchor. Returns RenderedLinkResult.
    """
```

Key design choices:

- **Stdlib only**: `urllib.request` for fetching, `html.parser.HTMLParser`
  for parsing. No external dependencies beyond the Python standard library.
- **URL normalization**: Both `published_url` and `target_url` are normalized
  before comparison — trailing slashes stripped, `www.` prefix handled,
  scheme lowered. Prevents false negatives from trivial URL differences.
- **Rel token-set matching**: The `rel` attribute is split on whitespace and
  checked for nofollow/ugc/sponsored tokens. Any of these present means
  the link is not truly dofollow.
- **Best-effort only**: Fetch failures (timeout, 4xx, 5xx, DNS errors) return
  `dofollow=None` rather than raising. The verification is an enrichment
  step — never a gate that blocks publish.

### Outcome taxonomy

The raw `RenderedLinkResult` is converted to a string taxonomy for
downstream consumers:

| Outcome | Condition | Meaning |
|---------|-----------|---------|
| `effective_backlink` | `found=True, dofollow=True` | Link renders as dofollow |
| `published_but_ineffective` | `found=True, dofollow=False` | Link exists but is nofollow |
| `needs_canary` | `found=False` or fetch failed | Published but unverifiable; needs human canary check |
| `failed` | Publish itself failed | Never reached verification stage |

### Integration pattern

The verification hooks into the publish dispatch pipeline as a
post-publish enrichment step:

```
dispatch(payload, mode, config)
  → adapter.publish(payload, mode, config)
  → _attach_backlink_outcome(adapter_result, payload, config)
  → carry_link_attr_verification(out, adapter_result._provider_meta)
  → return out
```

**`_registry_dispatch.py`** — post-publish hook:

```python
def _attach_backlink_outcome(
    adapter_result: AdapterResult,
    payload: dict[str, Any],
    config: Config,
) -> None:
    """Post-publish: verify rendered link and attach outcome."""
    if adapter_result.status not in ("published", "drafted"):
        adapter_result._provider_meta["backlink_outcome"] = "failed"
        return
    published_url = adapter_result.published_url
    target_url = payload.get("target_url")
    if not published_url or not target_url:
        adapter_result._provider_meta["backlink_outcome"] = "needs_canary"
        return
    result = verify_rendered_link(published_url, target_url)
    if result.dofollow is True and result.found:
        adapter_result._provider_meta["backlink_outcome"] = "effective_backlink"
    elif result.found and result.dofollow is False:
        adapter_result._provider_meta["backlink_outcome"] = "published_but_ineffective"
    else:
        adapter_result._provider_meta["backlink_outcome"] = "needs_canary"
```

**`base.py`** — carrier helper (shared across fresh and resume emit paths):

```python
def carry_link_attr_verification(
    out: dict[str, Any],
    source: dict[str, Any] | None,
) -> dict[str, Any]:
    """Copy backlink_outcome (and other verification keys) into out dict."""
    if source:
        for key in ("backlink_outcome", "link_attr_verification"):
            if key in source:
                out[key] = source[key]
    return out
```

### Reuse across inspection paths

The same `verify_rendered_link()` function serves both fresh-publish
verification and post-hoc inspection:

- **Fresh publish path** (`_registry_dispatch.py`): Called immediately
  after each publish, attaches `backlink_outcome` to the output row.
- **Canary target checks** (`cli/canary_targets.py`): The receipt dict
  carries `backlink_outcome` for each canary target, viewable in summary.
- **Recheck/probe** (`recheck/probe.py`): `probe_liveness()` calls
  `verify_rendered_link()` and returns `backlink_outcome` alongside
  other probe signals — reuses the same function signature and
  outcome classification.

## Why This Matters

1. **Closes the observability loop**: Operators can distinguish
   "published and effective" from "published but useless" without
   manually visiting each published page.

2. **Feeds the canary decay detection**: The recheck loop can detect
   platform drift — when a platform that was dofollow switches to
   nofollow, the probe catches it by comparing `backlink_outcome`
   across successive checks (fresh outcome  vs  rechecked outcome).

3. **Honest per-channel display**: The WebUI `/ce:health` card and
   channel dashboard show outcome badges, giving a true picture of
   channel quality rather than a binary "published / not published"
   view.

4. **No external dependencies**: Using stdlib for fetch and parse
   keeps the verification step zero-install, zero-config, and avoids
   drift between the verification logic and the publishing code.

## When to Apply

- **Any platform where the publish API is decoupled from rendering**:
  If the platform accepts content via API but renders it server-side
  (potentially transforming it), rendered-link verification adds value.

- **Zero-auth / public publish platforms**: These platforms have the
  weakest post-publish guarantees because there is no authenticated
  session to uphold content fidelity.

- **Canary-driven adapter lifecycle**: When `dofollow` must be confirmed
  via evidence rather than assumed from documentation. The verification
  outcome directly feeds the `dofollow` flag promotion path.

Avoid when:
- The platform API already returns a rendered snapshot with link data
  (e.g., Telegraph's page preview, Blogger's post render endpoint).
- The publish target is an owned domain with full render-path control.

## Examples

### Basic verification

```python
from backlink_publisher.publishing._verify_html import verify_rendered_link

result = verify_rendered_link(
    "https://example.telegraph.page/published-post",
    "https://my-target-site.com/article",
)
print(result)
# RenderedLinkResult(found=True, dofollow=True, status_code=200, error=None)
```

### Verification with short-TTL platform

```python
# A short-TTL platform has a time-limited publish window.
# The verification still runs — if it fails, outcome is "needs_canary"
# and the row is labeled as short-TTL for operator awareness.
result = verify_rendered_link(
    "https://rentry.co/abc123",
    "https://my-target-site.com/article",
)
# RenderedLinkResult(found=False, dofollow=None, status_code=404, error=None)
# → outcome == "needs_canary"
```

### WebUI outcome badge

In the settings dashboard, each channel card shows a colored badge:
- 🟢 **effective_backlink** — green badge
- 🟡 **published_but_ineffective** — amber badge
- ⚪ **needs_canary** — gray badge
- 🔴 **failed** — red badge

The badge is rendered from `backlink_outcome` stored in the publish
history row, read by `_get_latest_backlink_outcome()` in
`webui_app/binding_status.py`.

## Related

- [Dofollow canary verdict dropped at publish-output seam](../integration-issues/dofollow-canary-verdict-dropped-at-publish-output-seam-2026-05-25.md)
  — The `carry_link_attr_verification` helper was originally built for
  `link_attr_verification`; this pattern extends the same mechanism
  for `backlink_outcome`.
- `src/backlink_publisher/publishing/_verify_html.py` — Core implementation
- `src/backlink_publisher/publishing/_registry_dispatch.py` — Integration hook
- `src/backlink_publisher/publishing/adapters/base.py` — Carrier helper
