# JustPaste.it — Retired

**Decision date:** 2026-06-06
**Evidence:** Playwright browser inspection — URLs render as plain text, NOT clickable `<a>` tags. HTTP 200 all UAs, no login wall, but the platform does not generate HTML anchor elements for pasted URLs.
**Decision rationale:** No backlink surface exists. URLs are displayed as raw text in the rendered page. Even if Google can crawl the text, there is no anchor tag to carry link equity. Cannot produce dofollow backlinks.
**Reopen conditions:** If JustPaste.it adds auto-linking of pasted URLs with clickable `<a>` tags (without nofollow). Check when: evaluate if platform changelog mentions this feature.
