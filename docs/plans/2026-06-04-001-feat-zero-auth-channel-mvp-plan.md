---
title: "feat: Zero-Auth Backlink Channel MVP — implementation plan"
type: feat
status: active
date: 2026-06-04
origin: dev/specs/2026-06-04_1_zero-auth-backlink-channel-mvp/requirements.md
claims:
  paths:
    - src/backlink_publisher/publishing/registry.py
    - src/backlink_publisher/publishing/_manifest_types.py
    - src/backlink_publisher/publishing/_manifests.py
    - src/backlink_publisher/publishing/adapters/__init__.py
    - src/backlink_publisher/publishing/adapters/base.py
    - src/backlink_publisher/cli/plan_backlinks/core.py
    - src/backlink_publisher/cli/validate_backlinks.py
    - src/backlink_publisher/cli/publish_backlinks/__init__.py
    - src/backlink_publisher/cli/_publish_helpers.py
    - src/backlink_publisher/publishing/adapters/telegraph_api.py
    - src/backlink_publisher/publishing/adapters/rentry.py
    - src/backlink_publisher/publishing/adapters/txtfyi.py
    - webui_store/channel_status.py
    - webui_app/templates/settings.html
    - webui_app/templates/_settings_channel_binding.html
    - webui_app/routes/settings.py
---

# Zero-Auth Backlink Channel MVP — Implementation Plan

## Overview

Converge the backlink-publisher MVP from "publish to as many platforms as possible" to a focused zero-auth pipeline: only no-login platforms with rendered-link verification produce a counted backlink. This plan covers:

1. **FA-A** — Channel auth-type classification (R9–R12)
2. **FA-B** — Rendered-link verification (R13–R16)
3. **FA-C** — First-wave zero-auth adapters (R17–R20)
4. **FA-D** — WebUI channel-status honesty (R21–R22)
5. **FA-E** — Canary / recheck integration (R23–R24)

### Resolved Design Decisions

| Question | Decision |
|---|---|
| Q1: PostEasy/BrewPage autopilot? | **Autopilot** — probe + implement in one wave |
| Q2: Short-TTL platforms (BrewPage 30d, HtmlDrop 7d)? | **Include with TTL labeling** |
| Q3: Live canary for Notesh/HtmlDrop? | **Yes** — real test posts OK |
| Q4: txt.fyi retention? | **`visibility="hidden"`** — UI hidden, config preserved |

---

## Wave 0 — Foundation (R9, R10, R13)

### R9 — `zero_auth_platforms()` in `publishing/registry.py`

Add a `zero_auth_platforms()` function that returns all registered platforms whose `auth_type == "anon"`. Currently this set is `{telegraph, rentry, txtfyi}` — derived from the existing `_AUTH_TYPE_BY_PLATFORM` dict.

**Files:** `src/backlink_publisher/publishing/registry.py`

**Changes:**
1. Export `zero_auth_platforms()` from `registry.py` — calls `auth_platforms_by_type("anon")` (already exists as `platforms_by_auth_type`)
2. Add a public alias `available_zero_auth_platforms()` that additionally filters by `visibility != "retired"` (so `active_platforms() ∩ zero_auth_platforms()`)

**Success criteria:** `zero_auth_platforms()` returns `{"telegraph", "rentry", "txtfyi"}` pre-R12, then `{"telegraph", "rentry"}` post-R12 (txtfyi hidden). Already-exported functions unchanged.

---

### R10 — `--zero-auth` CLI flag

Add a uniform `--zero-auth` flag to `plan-backlinks`, `validate-backlinks`, and `publish-backlinks` that constrains platform operations to zero-auth platforms only.

**Files:**
- `src/backlink_publisher/cli/plan_backlinks/core.py` — `_build_parser()`: add `--zero-auth` flag (default False)
- `src/backlink_publisher/cli/validate_backlinks.py` — `_build_parser()`: same
- `src/backlink_publisher/cli/publish_backlinks/__init__.py` — `_build_parser()`: same
- `src/backlink_publisher/cli/_publish_helpers.py` — `resolve_platforms()`: accept `--zero-auth` override

**Design:**
- Store a `set[str]` of zero-auth platform names in a shared location (or inline the call to `zero_auth_platforms()` at each command entry point)
- When `--zero-auth` is passed, the CLI **overrides** `--platform` if both are given (last-wins OR document that `--zero-auth` narrows `--platform`). Preferred: `--zero-auth` is an additive constraint — if both `--platform blogger` and `--zero-auth` are given, the intersect is used (but this is an error-mostly-unlikely path; simple override is fine).
- `plan-backlinks`: `--default-platform` is narrowed if `--zero-auth` is given
- `validate-backlinks`: the `--platform` choices list is filtered to zero-auth platforms
- `publish-backlinks`: `--platform` choices narrowed

**Success criteria:** `python -m backlink_publisher plan-backlinks --zero-auth < seeds.jsonl` uses only telegraph/rentry. Same for validate and publish.

---

### R13 — `RenderedLinkVerifier` module

Create `publishing/_verify_html.py` — a lightweight HTML fetcher + `<a>` parser that checks whether a published page:
1. Contains an `<a>` element with `href` matching the target URL
2. Has no `rel="nofollow|ugc|sponsored"` on that `<a>`

**File:** `src/backlink_publisher/publishing/_verify_html.py`

**Parameters:**
- `verify_rendered_link(url: str, target_url: str, timeout: int = 30) -> RenderedLinkResult`

**Output dataclass:**
```python
@dataclass
class RenderedLinkResult:
    url_accessible: bool          # page loaded without 4xx/5xx/timeout
    link_found: bool              # <a href="...target_url..."> exists
    link_followable: bool         # no rel=nofollow|ugc|sponsored on the <a>
    effective: bool               # url_accessible AND link_found AND link_followable
    failure_reason: str | None    # "page_unreachable" / "link_missing" / "link_nofollow" / None
```

**Implementation notes:**
- Use `requests` with a timeout, user-agent from existing patterns (`backlink_publisher._util.url` or content fetch)
- Parse with `html.parser` (stdlib) — no new dependency
- Match `target_url` by checking if the `<a href>` contains the target domain/path (exact match OR domain-match — prefer exact match with trailing-slash normalization)
- Follow redirects (status_redirects) up to 5 hops
- Classify failure reason:
  - `page_unreachable`: 4xx/5xx/timeout/DNS failure
  - `link_missing`: page loaded 200 but no `<a href>` containing target
  - `link_nofollow`: `<a>` found but `rel` contains nofollow|ugc|sponsored
  - `<a>` found with dofollow → `effective=True`

**SSRF safety:** Use existing `_util.net_safety` guards (same as content fetch). Only fetch URLs that are:
- HTTPS (reject plain HTTP)
- Not loopback/private IPs
- Resolve to public IPs

**Success criteria:** Unit tests with known-good HTML (has dofollow link), known-bad HTML (nofollow link), known-missing HTML (no link), and HTTP error pages. Integration test `pytest -m real_content_fetch` hits a real telegraph page.

---

## Wave 1 — Adapters + Classification (R11, R12, R17, R18)

### R11 — Annotate existing zero-auth platforms

Update `adapters/__init__.py` register() calls for telegraph, rentry, txtfyi to carry the auth-type classification explicitly via the manifest or an inline annotation.

**Design:** The `_AUTH_TYPE_BY_PLATFORM` dict in `registry.py` already classifies these as `"anon"`. No code change needed unless we want to surface this in manifests (nice-to-have: add an `auth_type` field to `UiMeta` or a separate annotation). For MVP scope, rely on the existing `_AUTH_TYPE_BY_PLATFORM`.

**However:** txt.fyi currently has `dofollow="uncertain"` — this plan adds `rendered_link_status: str` to `RegistryEntry` (see below). After Wave 2 verification, set it to `"ineffective"` for txt.fyi.

**Changes:**
1. Add `rendered_link_status: str | None = None` field to `RegistryEntry` in `registry.py`
2. Add accessor `rendered_link_status(platform: str) -> str | None` exported from `registry.py`
3. Set `rendered_link_status="ineffective"` for txt.fyi at registration time (or via a post-registration annotation)

**Rationale:** `rendered_link_status` on `RegistryEntry` is the single source of truth. The CLI and WebUI read it from here, not from a second `_RENDERED_LINK_STATUS` dict.

---

### R12 — txt.fyi `visibility="hidden"`

Change txt.fyi's visibility from the current default (`"active"`) to `"hidden"` in its register() call.

**File:** `src/backlink_publisher/publishing/adapters/__init__.py`

**Change:** Add `visibility="hidden"` to the txtfyi `register(...)` call.

**Success criteria:** `active_platforms()` no longer includes `"txtfyi"`. `registered_platforms()` still includes it. Existing txtfyi config sections round-trip through `save_config`. The txtfyi card no longer appears in the WebUI settings page.

---

### R17 — PostEasy adapter (first-wave, autopilot)

**Discovery:** Run a channel-probe against `posteasy.io` (or the actual domain) to determine:
1. HTTP reachability matrix (REST API? Form POST? Login required?)
2. Rendered `<a>` behavior (does a published page contain a dofollow anchor?)
3. Available publishing surface (API key? Anonymous form? Token?)

**Implementation:** Based on probe findings, write `publishing/adapters/posteasy.py`:

```python
from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.publishing.registry import Publisher

class PostEasyAdapter(Publisher):
    @classmethod
    def available(cls, config) -> bool:
        return True  # no auth needed

    def publish(self, payload, mode, config) -> AdapterResult:
        # Form POST or API call to create a post
        # Extract the published URL from the response
        # Return AdapterResult with draft_url/published_url
        ...
```

**Registration** in `adapters/__init__.py`:
```python
from .posteasy import PostEasyAdapter
register("posteasy", PostEasyAdapter, dofollow="uncertain",
         rationale="First-wave zero-auth platform; rendered-link verification determines actual dofollow status. Probationary MVP inclusion pending live evidence.",
         visibility="experimental")
```

**Success criteria:** `PostEasyAdapter` publishes a post, returns a `published_url`. `pytest -m real_content_fetch` confirms the rendered page contains a dofollow link.

---

### R18 — BrewPage adapter (first-wave, autopilot)

Same pattern as R17, against `brewpage.work` or actual domain.

**File:** `publishing/adapters/brewpage.py`

**Registration:**
```python
from .brewpage import BrewPageAdapter
register("brewpage", BrewPageAdapter, dofollow="uncertain",
         rationale="First-wave zero-auth platform; 30-day TTL noted. Rendered-link verification determines actual dofollow status.",
         visibility="experimental")
```

**TTL note:** BrewPage has a 30-day post TTL. The adapter carries `AdapterResult(post_publish_delay_seconds=1)` but the TTL is documented (not enforced by the publish path — canary/recheck detects decay).

**Success criteria:** Same as R17.

---

## Wave 2 — Verification Integration (R14, R15)

### R14 — Post-publish verification plug

After each adapter's `publish()` returns `AdapterResult` with a `published_url`, run `verify_rendered_link(published_url, target_url)` and annotate the result.

**Design:** Modify the dispatch flow in `registry.py:dispatch()` OR the publish loop in `cli/publish_backlinks/__init__.py`:

1. **Option A (recommended):** Add a `post_publish_verify` hook in `registry.py:dispatch()`. After `adapter.publish()` succeeds and returns a `published_url`, call `verify_rendered_link(published_url, target_url)` and write the result into the `AdapterResult._provider_meta["rendered_link_result"]`.

2. This way the verification is baked into every publish path (CLI and WebUI) transparently.

**Implementation detail in `registry.py:dispatch()`:**
```python
from .._verify_html import verify_rendered_link

# Inside dispatch(), after adapter.publish() succeeds:
if result.published_url and payload.get("target_url"):
    rlr = verify_rendered_link(result.published_url, payload["target_url"])
    result._provider_meta["rendered_link_result"] = rlr
```

**Changes propagate:**
- `AdapterResult` already has `carry_link_attr_verification()` which looks at `_provider_meta["link_attr_verification"]` — we need a parallel path for `rendered_link_result` OR extend `link_attr_verification` to include rendered-link fields.
- `AdapterResult.to_publish_output()` must include `rendered_link_result` in the output dict.

**Success criteria:** After publish, the output JSONL row contains `rendered_link_result: {effective: bool, failure_reason: str | null}`.

---

### R15 — Result taxonomy

Define the four-tier result in the publish output and WebUI surface:

| Term | Condition | Icon/Color |
|---|---|---|
| `effective_backlink` | rendered_link_result.effective == True | ✅ green |
| `published_but_ineffective` | publish succeeded AND rendered_link_result.effective == False | ⚠️ amber |
| `needs_canary` | dofollow="uncertain" AND no verification result yet | 🔍 blue |
| `failed` | publish returned error (no URL) | ❌ red |

**Implementation:** Add a `_classify_backlink_outcome(result, rlr)` function in a shared location (e.g., a new `publishing/_outcome.py` or inline in `_verify_html.py`).

**File:** `src/backlink_publisher/publishing/_outcome.py`

```python
BacklinkOutcome = Literal["effective_backlink", "published_but_ineffective", "needs_canary", "failed"]

def classify_backlink_outcome(publish_result: AdapterResult) -> BacklinkOutcome:
    """Classify the backlink outcome based on publish result + rendered link result."""
    if publish_result.error:
        return "failed"
    rlr = publish_result._provider_meta.get("rendered_link_result")
    if rlr is None:
        return "needs_canary"
    if rlr.effective:
        return "effective_backlink"
    return "published_but_ineffective"
```

**Success criteria:** Each publish row in stdout JSONL carries a `backlink_outcome` field.

---

## Wave 3 — Canary Gate (R19, R20)

### R19 — Notesh canary-gated adapter

**Note:** Notesh (`notesh.io`?) — verify actual domain during channel-probe.

**Discovery phase:**
1. HTTP reachability matrix
2. Rendered `<a>` behavior
3. Registration form / publishing surface

**Canary gate flow:**
1. `channel-probe` script runs against Notesh
2. If probe shows a rendered backlink surface → register as `dofollow="uncertain"` with `visibility="experimental"`
3. The first publish triggers `verify_rendered_link` (Wave 2 infrastructure)
4. If the rendered link is verified as effective → subsequent publishes can be marked `effective_backlink`
5. If the rendered link is ineffective → platform stays as `published_but_ineffective`

**File:** `publishing/adapters/notesh.py`

**Registration:** `register("notesh", NoteshAdapter, dofollow="uncertain", ..., visibility="experimental")`

**Success criteria:** Notesh publishes a post. The rendered link result drives the outcome classification.

---

### R20 — HtmlDrop canary-gated adapter

Same pattern as R19, against `htmldrop.com` or actual domain. 7-day anonymous TTL noted.

**File:** `publishing/adapters/htmldrop.py`

**Registration:** `register("htmldrop", HtmlDropAdapter, dofollow="uncertain", ..., visibility="experimental")`

---

## Wave 4 — WebUI Channel-Status Honesty (R21, R22)

### R21 — Per-channel backlink status in settings UI

Add a rendered backlink status column/card section to the settings page, next to each channel card.

**Design:**
- Each channel card in `_settings_channel_binding.html` or the settings template shows the `backlink_outcome` state
- The state is read from a new WebUI endpoint or passed through template context
- States: `effective_backlink` / `published_but_ineffective` / `needs_canary` / `failed` (+ `unused` if no publishes yet)

**Files:**
- `webui_app/templates/_settings_channel_binding.html` — add a status badge section
- `webui_app/routes/settings.py` — add per-channel backlink status to the template context
- `webui_store/channel_status.py` — optionally extend to store per-channel `backlink_outcome`

**Implementation:**
1. Add `backlink_outcome` field to `channel_status_store` (or create a separate `backlink_status_store`)
2. Route `GET /settings` reads per-channel backlink status from the store and passes to template
3. Template renders a colored badge: green (effective), amber (ineffective), blue (needs canary), red (failed), gray (unused)

**Success criteria:** Settings page shows per-platform backlink status. An amber badge for txt.fyi post-verification.

---

### R22 — Health surface `/ce:health` integration

Add a zero-auth health summary card to the `/ce:health` dashboard showing:
- Number of zero-auth platforms registered
- Number that pass rendered-link verification
- Number that failed

**Files:**
- `webui_app/routes/health.py` (or wherever `/ce:health` is served)
- `webui_app/templates/_health_zero_auth_card.html` (new partial)

**Success criteria:** Health page shows a "Zero-Auth Channels" card with counts.

---

## Wave 5 — Canary / Recheck Integration (R23, R24)

### R23 — Canary-targets integration

Add zero-auth platforms to the canary-targets rotation. The canary-targets CLI (`cli/canary_targets.py`) currently checks dofollow status for seeded canary posts. Extend it to:

1. Accept zero-auth platforms in its config/the platform list
2. Run `verify_rendered_link` against the seeded canary URL
3. Record the result in `canary-health.json`

**Design:** The canary-targets system already has a per-platform config in `[canary.<platform>]` TOML sections. Add `canary.posteasy`, `canary.brewpage`, etc. seeded posts manually or programmatically after Wave 1.

**Changes:**
- `cli/canary_targets.py`: use `_verify_html.verify_rendered_link()` instead of (or in addition to) the existing link attribute check
- Record `rendered_link_effective` in the canary output

**Success criteria:** `canary-targets` runs against a zero-auth platform and reports `rendered_link_effective: true/false`.

---

### R24 — Recheck-backlinks integration

The recheck-backlinks CLI (`cli/recheck_backlinks.py`) re-probes published backlinks for liveness/dofollow-drift. Add rendered-link verification to the recheck flow:

1. After the existing `_verify_url_status` / link check, run `verify_rendered_link`
2. Emit both `link.rechecked` and a rendered-link event
3. The `/ce:health` decay banner triggers if `rendered_link.effective` flips from True to False

**Changes:**
- `cli/recheck_backlinks.py`: add `_probe_rendered_link()` step in the per-backlink loop
- Emit `rendered_link_changed` event schema

**Success criteria:** `recheck-backlinks --probe` against a known-effective platform that went dead reports the change.

---

## Rollout Sequence

```
Wave 0 (R9, R10, R13) — Foundation    ─┐
                                       │
Wave 1 (R11, R12, R17, R18) — Adapters ├─ Parallelizable within each wave
                                       │
Wave 2 (R14, R15) — Verification       ─┘
                                       │
Wave 3 (R19, R20) — Canary Gate        ─┐ Sequential: needs Wave 2 infra
                                       │
Wave 4 (R21, R22) — WebUI              ─┘
                                       │
Wave 5 (R23, R24) — Integration        ─ Sequential: needs Wave 1-2 infra
```

**Within a wave**, tasks are independent and can be delegated in parallel.

---

## Files Changed (Complete List)

| File | Change |
|---|---|
| `src/backlink_publisher/publishing/registry.py` | Add `zero_auth_platforms()`, `rendered_link_status` field to `RegistryEntry`, `rendered_link_status()` accessor, post-publish verify hook in `dispatch()` |
| `src/backlink_publisher/publishing/_manifest_types.py` | No change needed (existing UiMeta/BindDescriptor/Policy cover this scope) |
| `src/backlink_publisher/publishing/_manifests.py` | Add POSTEASY_MANIFEST, BREWPAGE_MANIFEST, NOTESH_MANIFEST, HTMLDROP_MANIFEST |
| `src/backlink_publisher/publishing/adapters/__init__.py` | Register PostEasy, BrewPage, Notesh, HtmlDrop. Update txtfyi register() with `visibility="hidden"`. Import new adapters. |
| `src/backlink_publisher/publishing/adapters/base.py` | Add `rendered_link_result` field to `AdapterResult._provider_meta` if not already extensible |
| `src/backlink_publisher/publishing/adapters/posteasy.py` | New — PostEasy zero-auth adapter |
| `src/backlink_publisher/publishing/adapters/brewpage.py` | New — BrewPage zero-auth adapter |
| `src/backlink_publisher/publishing/adapters/notesh.py` | New — Notesh canary-gated adapter |
| `src/backlink_publisher/publishing/adapters/htmldrop.py` | New — HtmlDrop canary-gated adapter |
| `src/backlink_publisher/publishing/_verify_html.py` | New — RenderedLinkVerifier module |
| `src/backlink_publisher/publishing/_outcome.py` | New — BacklinkOutcome classifier |
| `src/backlink_publisher/cli/plan_backlinks/core.py` | Add `--zero-auth` flag |
| `src/backlink_publisher/cli/validate_backlinks.py` | Add `--zero-auth` flag |
| `src/backlink_publisher/cli/publish_backlinks/__init__.py` | Add `--zero-auth` flag, post-publish verify integration |
| `src/backlink_publisher/cli/_publish_helpers.py` | `resolve_platforms()` zero-auth support |
| `src/backlink_publisher/cli/canary_targets.py` | Rendered-link verification in canary probe |
| `src/backlink_publisher/cli/recheck_backlinks.py` | Rendered-link verification in recheck |
| `webui_store/channel_status.py` | Extend to track `backlink_outcome` per channel |
| `webui_app/routes/settings.py` | Pass per-channel backlink status to template context |
| `webui_app/routes/health.py` | Add zero-auth health card route |
| `webui_app/templates/settings.html` | Add backlink status badges to channel cards |
| `webui_app/templates/_settings_channel_binding.html` | Render backlink outcome badge |
| `webui_app/templates/_health_zero_auth_card.html` | New — zero-auth health partial |
| `tests/test_zero_auth_registry.py` | New — registry functions |
| `tests/test_zero_auth_cli_flags.py` | New — `--zero-auth` flag tests |
| `tests/test_verify_html.py` | New — RenderedLinkVerifier unit tests |
| `tests/test_outcome_classification.py` | New — BacklinOutcome taxonomy tests |
| `tests/test_posteasy_adapter.py` | New — PostEasy adapter tests |
| `tests/test_brewpage_adapter.py` | New — BrewPage adapter tests |
| `tests/test_zero_auth_webui.py` | New — WebUI status badge tests |

---

## Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| PostEasy/BrewPage API surface differs from channel-probe expectations | Medium | Probe first (within same wave), adapt implementation to findings |
| Rendered-link verification blocks on slow pages | Low | 30s timeout, non-blocking (verification is advisory, not a gate in v1) |
| Notesh/HtmlDrop change their rendered behavior between canary and publish | Low | Recheck detects drift; published_but_ineffective is honest about it |
| WebUI badge data model diverges from backend truth | Medium | Single source of truth: `channel_status_store.backlink_outcome` read by route → template |
| Short TTL (BrewPage 30d, HtmlDrop 7d) causes high churn in health surface | Low | Accept as known limitation; expired posts appear as `published_but_ineffective` in recheck |
| txt.fyi users confused by disappearance from WebUI | Low | visibility="hidden" preserves config; no functional breakage |

---

## Test Plan

| Test File | What It Verifies |
|---|---|
| `tests/test_zero_auth_registry.py` | `zero_auth_platforms()` returns correct set; `rendered_link_status()` accessor works |
| `tests/test_zero_auth_cli_flags.py` | `--zero-auth` flag appears in all three CLI parsers; filtering works |
| `tests/test_verify_html.py` | `verify_rendered_link()` with known-good HTML (dofollow link), nofollow, missing link, HTTP errors |
| `tests/test_outcome_classification.py` | All four `BacklinkOutcome` values produced correctly from `AdapterResult` |
| `tests/test_posteasy_adapter.py` | Mock HTTP response → `AdapterResult` with published_url |
| `tests/test_brewpage_adapter.py` | Same |
| `tests/test_zero_auth_webui.py` | WebUI route returns `backlink_outcome` in template context |

**Integration check:** `python -m pytest tests/test_zero_auth_registry.py -v --tb=short`

---

## Evidence & Verification Plan

| Requirement | Hard evidence |
|---|---|
| R9: zero_auth_platforms() | `python -c "from backlink_publisher.publishing.registry import zero_auth_platforms; print(zero_auth_platforms())"` |
| R10: --zero-auth CLI | `plan-backlinks --help` shows flag; `plan-backlinks --zero-auth < seeds.jsonl` filters to zero-auth platforms |
| R12: txt.fyi hidden | `active_platforms()` no longer includes txtfyi; settings page card gone |
| R13: verify_rendered_link | Unit tests pass; integration test with real telegraph page |
| R17, R18: PostEasy/BrewPage | Adapter returns `published_url`; probe confirms rendered backlink |
| R21: WebUI badge | Settings page shows per-channel backlink outcome badge |

---

## Appendix: Key Code Patterns

### Registry entry after changes

```python
@dataclass(frozen=True)
class RegistryEntry:
    publishers: list[type[Publisher] | Publisher]
    dofollow: _DofollowStatus
    rationale: str | None = None
    referral_value: _ReferralValue | None = None
    ui: UiMeta | None = None
    visibility: Visibility = "active"
    rendered_link_status: str | None = None  # NEW
```

### Register call for first-wave adapter

```python
from .posteasy import PostEasyAdapter

register("posteasy", PostEasyAdapter, dofollow="uncertain",
         rationale="First-wave zero-auth platform; rendered-link verification determines actual dofollow status. Probationary MVP inclusion pending live evidence.",
         visibility="experimental",
         ui=UiMeta(display_name="PostEasy", domain="posteasy.io",
                   category="anonymous-paste", icon="bi-file-earmark-text"),
         policy=Policy(throttle_band=(30, 60), retry_id="default"))
```

### RenderedLinkResult taxonomy

```python
@dataclass
class RenderedLinkResult:
    url_accessible: bool
    link_found: bool
    link_followable: bool
    effective: bool  # url_accessible AND link_found AND link_followable
    failure_reason: str | None

    @property
    def outcome(self) -> str:
        if not self.url_accessible:
            return "page_unreachable"
        if not self.link_found:
            return "link_missing"
        if not self.link_followable:
            return "link_nofollow"
        return "effective"
```
