---
title: "Rendered-link verification: post-publish dofollow confirmation pattern"
module: backlink_publisher.publishing._verify_html
tags:
  - design-pattern
  - quality-assurance
  - adapter-contract
  - post-publish
  - dofollow-integrity
problem_type: best-practices
---

## Context

After publishing backlinks to public platforms (Telegraph, Rentry, PostEasy, BrewPage, Nonograph, HtmlDrop), there was no way to confirm the anchor actually rendered as a dofollow link. Platforms can silently strip links, inject `rel=nofollow`, or fail to render the post correctly — the publish adapter returns `"published"` but the backlink is dead on arrival. Without post-publish fetch-and-inspect, operators could not distinguish effective backlinks from published-but-useless ones.

## Guidance

**Verify every published page by fetching it and inspecting the rendered HTML for a dofollow backlink.** Make this a best-effort post-publish step that enriches the result metadata — never a gate that can fail the publish itself.

### Core verification function

```python
@dataclass
class RenderedLinkResult:
    effective: bool                      # True if target found as dofollow <a>
    failure_reason: str | None = None    # e.g. "nofollow", "fetch_failed:HTTP 403"

def verify_rendered_link(
    published_url: str,
    target_url: str,
    timeout: int = 30,
) -> RenderedLinkResult:
```

The function:
1. Fetches the published page with stdlib `urllib` (no external deps)
2. Parses HTML with `html.parser.HTMLParser`
3. Extracts all `<a href=... rel=...>` elements
4. Normalizes URLs (lowercase, strip scheme/www/trailing slash/fragment)
5. Checks if target URL appears in any anchor's href
6. Inspects `rel` for `nofollow`/`ugc`/`sponsored` tokens
7. Falls back to plain-text URL presence detection for platforms that don't use `<a>` tags

### Outcome taxonomy

Map the binary `RenderedLinkResult.effective` to a three-value outcome string stored in publish metadata:

```python
if vr.effective:
    outcome = "effective_backlink"      # found + dofollow
else:
    outcome = "published_but_ineffective"  # found but nofollow, or link missing
# on exception:
outcome = "failed"                      # verification itself threw
```

The three-value taxonomy feeds downstream consumers:
- **`effective_backlink`** → green badge in WebUI, counts toward canary health
- **`published_but_ineffective`** → amber badge, alerts operator to platform drift
- **`failed`** → grey badge (fetch transient, retry next cycle)

### Integration pattern

Post-publish, attach the outcome into `AdapterResult._provider_meta`:

```python
def _attach_backlink_outcome(result: AdapterResult, payload: dict[str, Any]) -> None:
    target_url = payload.get("target_url", "")
    published_url = result.published_url
    if result.status not in ("published", "drafted") or not published_url or not target_url:
        return
    try:
        vr = verify_rendered_link(published_url=published_url, target_url=target_url)
        outcome = "effective_backlink" if vr.effective else "published_but_ineffective"
    except Exception:
        outcome = "failed"
    meta = dict(result._provider_meta) if result._provider_meta else {}
    meta["backlink_outcome"] = outcome
    object.__setattr__(result, "_provider_meta", meta)
```

Propagate the outcome through the publish output via a shared helper:

```python
def carry_link_attr_verification(
    out: dict[str, Any], source: dict[str, Any] | None
) -> dict[str, Any]:
    if source:
        outcome = source.get("backlink_outcome")
        if outcome is not None:
            out["backlink_outcome"] = outcome
    return out
```

### Key design properties

| Property | Choice | Why |
|---|---|---|
| Dependency footprint | stdlib only | No install friction, works in airgapped contexts |
| Failure semantics | Best-effort, never fail publish | Verification is informational; a transient fetch error must not lose a published backlink |
| URL matching | Normalized substring (not exact) | Handles trailing slashes, www variants, protocol-relative URLs |
| Rel parsing | Token-set intersection against `{nofollow, ugc, sponsored}` | Covers compound rel values like `"nofollow ugc"` |
| Plain-text fallback | URL-in-page-text check | txt.fyi / pastebin-style platforms don't use anchor tags |
| Recheck vs. fresh | Same function called from both `_registry_dispatch.py` and `recheck/probe.py` | Single verification path, no drift between fresh publish and survival re-probe |

## Why This Matters

Publishing platforms are adversarial surfaces — they change rendering behavior without notice, retroactively add nofollow to old posts, or silently strip links during format conversion. A publish adapter returning `"published"` is a low-confidence signal. The rendered-link verification closes the loop, turning "the API accepted my post" into "a real visitor would see a dofollow link to the target."

For canary health tracking, the outcome feeds a decay signal: a platform whose posts increasingly land as `published_but_ineffective` triggers investigation before a full migration is needed. Without this pattern, operators ship backlinks blindly and discover platform degradation only when search-rank signals fail to appear weeks later.

## When to Apply

Apply this pattern to any system that publishes content to third-party platforms where:

- The platform may transform or strip content after submission (format conversion, sanitization, SEO policy enforcement)
- The platform's API response does not include a rendered preview
- The business value depends on specific HTML attributes surviving (rel, target, href integrity)
- You need to distinguish "post exists" from "link works" for operational dashboards

Do NOT apply when the platform returns a verifiable rendered snapshot in the API response, or when the verification cost (fetch latency, rate-limit consumption) exceeds the value of knowing.

## Examples

### Fresh publish — outcome attached to publish output

```python
# In dispatch(), after adapter.publish():
result = adapter.publish(payload, mode, config)
_attach_backlink_outcome(result, payload)

# Output JSONL row now carries:
# {"status": "published", "backlink_outcome": "effective_backlink", ...}
```

### Survival re-probe — same function reused

```python
# In recheck/probe.py:
vr = verify_rendered_link(post_url, target_url)
if vr.effective:
    out["backlink_outcome"] = "effective_backlink"
elif vr.failure_reason:
    out["backlink_outcome"] = "published_but_ineffective"
else:
    out["backlink_outcome"] = "failed"
```

### WebUI badge rendering

```python
# bind to BadgeOutcome via a lookup dict:
OUTCOME_LABEL = {
    "effective_backlink": ("success", "已生效 ✓"),
    "published_but_ineffective": ("warning", "已发布但链接失效 ⚠"),
    "failed": ("secondary", "验证失败"),
}
```
