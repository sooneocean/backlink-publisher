# Retired Platforms

Platforms evaluated and ruled out. One file per platform.

| Platform | Decision date | Reason |
|---|---|---|
| [Bloglovin](bloglovin.md) | 2026-05-25 | Service shut down Dec 2021; Cloudflare 403 |
| [GitBook](2026-06-01-discovery-batch.md) | 2026-06-01 | Public publishing is paid-tier only |
| [Bear Blog](2026-06-01-discovery-batch.md) | 2026-06-01 | Free posts quarantined noindex+nofollow (anti-SEO) |
| [Svbtle](2026-06-01-discovery-batch.md) | 2026-06-01 | Paid-only + injects nofollow on outbound links |
| [Scrapbox/Cosense](2026-06-01-discovery-batch.md) | 2026-06-01 | JS-SPA, dofollow unverifiable, heavy adapter |
| [paste.ee](2026-06-01-discovery-batch.md) | 2026-06-01 | SPA noindex + redundant with rentry/txtfyi |
| [Weebly](2026-06-01-discovery-batch.md) | 2026-06-01 | Free subdomain PBN, deindex-prone, heavy adapter |
| [Google Sites](2026-06-01-discovery-batch.md) | 2026-06-01 | Apex-DA mirage, redirect-wrapped links, heavy |
| [GitLab Snippets](2026-06-01-discovery-batch.md) | 2026-06-01 | GitLab-only content barely indexed by Google |

## Adding an entry

Create `<platform>.md` with: decision date, evidence (probes, HTTP status, API availability),
decision rationale, and conditions that would warrant re-evaluation.
