# Discovery Batch — 2026-06-01 NO-GO ledger

Eight candidates ruled out in the 2026-06-01 discovery run
(`docs/discovery/2026-06-01-run.md`). Recorded here so future runs dedup against
them instead of re-probing. Each has a documented reopen condition.

| Candidate | Status | Reason (probe evidence) | Reopen condition |
|---|---|---|---|
| **gitbook.com** | NO-GO | dofollow + indexed, but **public publishing is paid-tier only** (free = private). Violates free-public-posting requirement. | A free public-output tier returns. |
| **bearblog.dev** | NO-GO | Free accounts quarantined: **noindex + nofollow by default** until reputation earned; founder-documented anti-SEO-spam stance; recent mass de-index. | Only if a non-quarantined free path yields dofollow+indexed (unlikely by design). |
| **svbtle.com** | NO-GO | **Paid only ($7/mo)** AND injects `rel="nofollow"` on author outbound links (verified 2/23, link `verysimpleblogging.com` ×3 nofollow). | Free tier + dofollow — neither plausible. |
| **scrapbox.io** (Cosense) | NO-GO | JS-SPA: outbound `rel` not server-rendered (unverifiable); partial indexation; cookie-session writes = heavy adapter; non-blog fit. | A canary proves dofollow + reliable indexation via headless render. |
| **paste.ee** | NO-GO | Paste body is a client-rendered SPA (302 → `pastee.dev`, 0 bytes server HTML); weak/noindex; **redundant** with rentry/txtfyi/telegraph. | Server-rendered indexed output appears. |
| **weebly.com** | NO-GO | Free `*.weebly.com` subdomain: low DA, forced footer, **deindexation-prone** (2025 thin-content wave); heavy browser+account adapter; unverified dofollow. | N/A — classic Web2.0 PBN layer, no ROI. |
| **sites.google.com** | NO-GO | Apex DA is a mirage: per-`/view/` content weakly indexed, outbound links **redirect-wrapped** (dofollow unconfirmable), heavy browser+account adapter. | New Google Sites stops wrapping links AND per-site indexation proven. |
| **gitlab.com Snippets** | NO-GO | GitLab-only content **barely indexed by Google** (issues #4650/#24289) → near-zero SEO value. (GitLab **Pages** is a separate GO — see discovery run.) | Google indexation of snippets materially improves. |

Probe method + full per-candidate evidence: `docs/discovery/2026-06-01-run.md`.
