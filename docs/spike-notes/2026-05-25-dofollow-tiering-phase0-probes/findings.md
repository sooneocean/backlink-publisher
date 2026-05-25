---
date: 2026-05-25
topic: dofollow-tiering-phase0-viability-probes
plan: docs/plans/2026-05-25-001-feat-dofollow-tiering-platform-expansion-plan.md
status: probes-complete-awaiting-operator-go-no-go
---

# Phase 0 Viability Probes — 6-Platform Expansion

Go/no-go gate for Plan 2026-05-25-001 Units 4–8. These probes resolve the
`Deferred to Implementation [Phase 0 go/no-go]` block in the plan.

## Method

**Read-only network reconnaissance only.** No posting, no account creation, no
form submission — those require operator credentials/decisions. Tooling: `curl`
(raw headers + HTML), WebSearch (qualitative status/ToS). Definitive dofollow
determination per platform is still deferred to the R4 two-phase canary loop
(`uncertain` register → canary publish → `verify_link_attributes` → amend);
Phase 0 only gathers the *preliminary* signal needed to decide whether to build.

## Per-platform findings

| Platform | Liveness | Cloudflare | Publish archetype (observed) | dofollow (preliminary) | Verdict |
|---|---|---|---|---|---|
| **livejournal** | XML-RPC `/interface/xmlrpc` **200**, homepage 200, `Server: nginx` | **None** | XML-RPC challenge-response API | Body links on a real post (`news.livejournal.com/166511`) are `rel="noopener noreferrer"` — **no `nofollow` token = dofollow** | **GO** (keystone) |
| **txt.fyi** | 200 | Yes (DYNAMIC; homepage **not** challenged) | **Pure HTML form** `POST edit.php`: `url` + `txt` + `nonce`(HMAC,ts) + `form_time`, **no captcha** | TBD via R4 canary | **GO** (raw-POST) |
| **justpaste.it** | 200, `Server: nginx` | None (homepage) | **JS SPA** — no native `<form>`/textarea in raw HTML; "enable JavaScript" | TBD | **CONDITIONAL** — needs XHR/API reverse-eng or browser |
| **teletype.in** | 200 | Yes | **JS SPA** (19KB, no native form) — account + JS editor archetype | TBD | **CONDITIONAL** — likely account/API, treat as Unit 6-style credential path |
| **jkforum.net** | `www`→301→`jkforum.net` **200**, 60KB | Yes (**no challenge to normal UA**) | Discuz forum (登入/論壇), browser-login recipe | Discuz default = nofollow on post/sig links | **HOLD / likely NO-GO** — see risk |
| **bloglovin.com** | Homepage **403 Cloudflare** to bots | Yes (blocking) | Rebranded→Activate 2018→abandoned 2021; no blog-post service | n/a | **NO-GO — effectively retired** |

## Resolution of plan's deferred Phase 0 questions

- **livejournal XML-RPC alive + dofollow?** Yes, alive (200, nginx, no CF). Preliminary dofollow positive (`noopener noreferrer`, no nofollow). **No OAuth / app-specific-password path exists** — auth is RFC-2617 challenge-response only, requiring `MD5(password)` (password-equivalent) at rest. → R15 mitigation **mandates a throwaway account** (secret is un-revocable except by password change). Confirms the deepening finding; no revocable-token alternative to fall back to.
- **anon trio raw POST vs Cloudflare?** **txt.fyi = clean go** (pure form, nonce+form_time, no captcha — the textbook case for Unit 4 `fetch_form→extract_hidden_fields→submit_form`). **justpaste.it + teletype.in = JS SPAs**, no native form → raw `http.post` insufficient; need XHR/API reverse-engineering or browser. The Unit 4 helper covers ≥1 platform (txt.fyi), satisfying its soft-gate.
- **bloglovin retired?** **Yes — effectively retired.** Rebranded to Activate (2018), abandoned (last update Dec 2021); homepage 403s bots via Cloudflare. → degrade 6→5 platforms (plan Unit 8 already anticipates this).
- **jkforum ToS + survival + IP ban?** Reachable (no CF challenge to normal UA). ToS prohibits 濫發廣告 (ad spam). **Reputational/legal red flag:** JKF 捷克論壇 has been publicly implicated in operating pornographic ads / facilitating sex transactions, with the operator arrested (自由時報, T客邦). Linking from a "bad-neighborhood" site is SEO-toxic. Combined with Discuz default-nofollow + forum deletion of promo posts, link-survival viability is poor. Survival probe (7-day) is moot if reputation alone disqualifies.

## Impact on plan units

- **Unit 4** (HTTP form helper): **build** — txt.fyi validates the helper end-to-end.
- **Unit 5** (forum login recipe): **defer/drop** unless operator accepts jkforum reputation risk. Its sole downstream consumer (Unit 8 jkforum) is HOLD.
- **Unit 6** (livejournal XML-RPC): **build** — keystone GO; throwaway account required.
- **Unit 7** (anon trio): build **txt.fyi** (composes Unit 4); justpaste.it/teletype.in conditional on archetype decision (raw-POST not viable as-is). teletype.in, if account-based, follows Unit 6 credential path (0o600 + atomic_write), not the credential-less form path.
- **Unit 8** (jkforum + bloglovin): **bloglovin → record retirement, 6→5 platforms.** jkforum → HOLD pending operator reputation call.

## Operator decisions required before Units 4–8 proceed

1. **livejournal**: confirm use of a **throwaway account** (password-equivalent secret, un-revocable). Build with that account, or skip the keystone?
2. **jkforum**: accept the reputation/"bad-neighborhood" risk + nofollow + deletion likelihood, or drop it (and with it Unit 5)?
3. **justpaste.it / teletype.in**: invest in JS/API reverse-engineering (or browser recipe), or scope them out for now and ship the dofollow keystone (livejournal) + clean raw-POST (txt.fyi) first?
4. **bloglovin**: confirm retirement → plan/docs note 6→5 platforms.
