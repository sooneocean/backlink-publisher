---
title: "feat: SSRF-harden channel_probe.py (R14) + notes.io adapter stub (wave-3)"
type: feat
status: active
date: 2026-06-02
deepened: 2026-06-02
origin: docs/brainstorms/2026-06-01-channel-discovery-funnel-requirements.md
discovery: docs/discovery/2026-06-01-run.md
claims: {}
---

# feat: SSRF-harden channel_probe.py + notes.io form-post adapter (wave-3)

## Overview

Two self-contained units from the channel-discovery-funnel brainstorm:

**Unit 1 (R14):** `scripts/channel_probe.py` is the only fetch path in the repo
that does NOT route through `net_safety`. It uses raw `requests.get(allow_redirects=True)`.
Before the funnel can ever batch-drive it on machine-sourced URLs it must adopt
the same SSRF guard the production pipeline uses: reject RFC1918 / loopback /
link-local / cloud-metadata resolutions and re-validate every redirect hop.

**Unit 2 (wave-3, notes.io):** The 2026-06-01 discovery run confirmed notes.io
(notes.io) as a GO: `dofollow=12/0 server-rendered`, anonymous posting (no account),
`referral_value=low`, form-post archetype (reuses txtfyi pattern). Low adapter cost;
the txtfyi adapter is the direct template. Ships `dofollow="uncertain"` pending
our-pipeline canary.

## Requirements Trace

**Unit 1 (SSRF hardening)**
- R14a. The `_probe()` function must validate each candidate URL against
  `net_safety._check_url_for_ssrf` before any HTTP fetch.
- R14b. Every redirect hop must be re-validated (disable `allow_redirects=True`;
  follow manually, checking each intermediate URL).
- R14c. A per-run fetch budget is enforced (max N URLs × M UAs = hard stop).
- R14d. The SPA headless-render path (if/when added) must enforce the same host gate.
- R14e. Tests: SSRF-blocked URL returns `Hit(status=None, error="ssrf-blocked")`; a
  redirect to an RFC1918 address is blocked at the redirect hop.

**Unit 2 (notes.io adapter)**
- R-N1. `register("notesio", NotesioFormPostAdapter, dofollow="uncertain", ...)` — 
  adapter file `src/backlink_publisher/publishing/adapters/notesio_api.py`.
- R-N2. Anonymous posting via `POST https://notes.io/` form submission
  (reuses `http_form_post.py` archetype).
- R-N3. Carries `rationale=` ≥80 chars + `referral_value="low"`.
- R-N4. Manifest: standard fields, `dofollow_mechanism="form-post-anonymous"`.
- R-N5. `verify_adapter_setup()` offline check (no network; checks form endpoint is
  configured).
- R-N6. No credential required for anonymous form-post (unlike devto/hackmd).
- R-N7. Canary-pending entry added to `docs/discovery/canary-pending.md`.
- R-N8. No `cli/*.py` / `schema.py` edits (`test_r9_extension_readiness.py`).

## Scope Boundaries
- **Not in scope:** decided-store (R8), family enumeration (R1), wave-2 adapters
  (Qiita/Zenn), ReadtheDocs adapter (complex Sphinx build).
- **SSRF hardening** covers only `scripts/channel_probe.py`, not the production
  pipeline (which already routes through `net_safety`).

## Implementation Units

### Unit 1: SSRF-harden channel_probe.py

**Goal:** Wrap every `requests.get()` call in `_probe()` with net_safety validation;
follow redirects manually; add per-run fetch budget.

**Files:**
- Modify: `scripts/channel_probe.py`
- Add: `tests/test_channel_probe_ssrf.py`

**Approach:**
```python
# In _probe(), before requests.get:
from backlink_publisher._util import net_safety
try:
    net_safety._check_url_for_ssrf(url)
except Exception as exc:
    return Hit(ua=ua_key, status=None, error=f"ssrf-blocked: {exc}")

# Follow redirects manually (allow_redirects=False):
session = requests.Session()
resp = session.get(url, ..., allow_redirects=False)
while resp.is_redirect:
    next_url = resp.headers.get("Location", "")
    try:
        net_safety._check_url_for_ssrf(next_url)
    except Exception as exc:
        return Hit(ua=ua_key, status=None, error=f"ssrf-redirect-blocked: {next_url}: {exc}")
    resp = session.get(next_url, ..., allow_redirects=False)
```

**Patterns to follow:**
- `src/backlink_publisher/content/_preflight_fetch.py` — production SSRF gate pattern
- `src/backlink_publisher/_util/net_safety.py` — `_check_url_for_ssrf` function

**Test scenarios:**
- SSRF-blocked host (169.254.169.254, 192.168.1.1) → `Hit.error="ssrf-blocked:..."`
- Redirect to RFC1918 host → `Hit.error="ssrf-redirect-blocked:..."`
- Normal public host → probe proceeds normally (mock requests)

**Verification:** `pytest tests/test_channel_probe_ssrf.py -v` passes.

---

### Unit 2: notes.io form-post adapter

**Goal:** Add `NotesioFormPostAdapter` to the registry; anonymous form-post, no credential.

**Files:**
- Add: `src/backlink_publisher/publishing/adapters/notesio_api.py`
- Modify: `src/backlink_publisher/publishing/adapters/__init__.py`
- Modify: `docs/discovery/canary-pending.md`
- Add: `tests/test_adapter_notesio.py`

**Approach:**
Follow `txtfyi_api.py` pattern exactly — anonymous HTTP form post:
```python
POST https://notes.io/
Content-Type: application/x-www-form-urlencoded
Body: text=<content>&token=<none-or-empty>
Response: redirect to published URL
```

**Manifest:**
```python
NOTESIO_MANIFEST = {
    "display_name": "notes.io",
    "platform_url": "https://notes.io/",
    "dofollow_mechanism": "form-post-anonymous",
    "requires_account": False,
    "content_format": "plaintext",
    "max_length": 50000,
}
```

**Rationale (≥80 chars):**
```
notes.io: dofollow confirmed (12/0) on 3rd-party posts (2026-06-01 discovery run);
server-rendered, anonymous form-post, no credential required. OUR canary pending.
```

**Test scenarios:**
- Happy path: `adapter.publish(...)` → mocked form POST → 302 → returns `published_url`
- Network error → `ExternalServiceError`
- Credential test: `verify_adapter_setup()` → offline, always OK (no credential needed)
- Token never appears in error messages (R9 — trivial here, no token)

**Verification:** `pytest tests/test_adapter_notesio.py tests/test_adapter_dofollow_gate.py tests/test_r9_extension_readiness.py -v` passes.

## Post-Deploy Monitoring & Validation
- After merge, run `python scripts/channel_probe.py 169.254.169.254` — must return
  `ssrf-blocked` error, not a real probe result.
- Run `publish-backlinks` with `--platform notesio` on a test seed to confirm the
  form-post path works end-to-end (advisory, requires network).
- `canary-pending.md` table: notesio row must appear with status=`pending` and a
  deadline ≤ 2026-09-01.
