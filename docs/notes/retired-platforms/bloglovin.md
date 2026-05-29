# Bloglovin — Retired Platform

**Decision date:** 2026-05-25  
**Status:** NO-GO — retired, do not implement

## Evidence

- Rebranded to **Activate** in 2018; original blog-aggregation product abandoned.
- Service went dark in December 2021; blog-post discovery and reader features shut down.
- `https://www.bloglovin.com/` returns Cloudflare 403 as of 2026-05-25 probe.
- No blog-post publish API was ever exposed publicly.

## Decision

Bloglovin is no longer a viable backlink channel. No adapter will be implemented.
Operator spike findings: `docs/spike-notes/2026-05-25-dofollow-tiering-phase0-probes/findings.md`

## If reconsidered

Require: live homepage returning 200, documented publish API, and a successful
pipeline canary before re-opening.
