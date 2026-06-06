# backlink-publisher

A local-first, terminal-native backlink publishing pipeline.
Generates, validates, and publishes short backlink articles across **20+ platforms** (Blogger, Medium, Telegraph, Velog, Substack, dev.to, Notion, GitHub/GitLab Pages, and more) — fully pipe-friendly, cron-safe, and non-interactive.

Adding a new platform is a single `register("x", XAdapter)` line — the CLI, schema, throttle gating, and tier matrix all read the adapter registry dynamically (see [AGENTS.md → Adding a new publisher adapter](AGENTS.md#adding-a-new-publisher-adapter)). A Flask **WebUI** (`python webui.py`) wraps the same pipeline for operators who prefer a browser to a terminal.

## Workspace Layout

This is the canonical project repository. It lives under a parent workspace directory that is **not** itself a git repo. Sibling directories named `bp-<topic>` (e.g. `bp-events-u4`, `bp-ko-html`) are temporary `git worktree` checkouts of this same repository on parallel feature branches — they share `.git/` with this main checkout. The convention is one `bp-<topic>` worktree per active feature branch; remove the worktree (`git worktree remove ../bp-<topic>`) when the branch lands. See `AGENTS.md` for the contributor workflow.

## Quick Start

```bash
# Install
pip install -e .

# Run the full pipeline (dry-run)
cat seeds.jsonl \
  | plan-backlinks \
  | validate-backlinks \
  | publish-backlinks --platform medium --mode draft --dry-run
```

## Prerequisites

| Requirement | Details |
|---|---|
| **Python** | >= 3.11 |
| **Chromium** | Only for Medium browser fallback: `playwright install chromium` |

> **No Node.js required.** Publishing uses the Blogger API v3 and Medium API directly. Chrome/Playwright is only needed as a fallback when no Medium Integration Token is configured.

## First-Run Setup

```bash
# 1. Install the package and dependencies
pip install -e .

# 2. Copy and edit the config file
cp config.example.toml ~/.config/backlink-publisher/config.toml
# Edit: set Blogger blog_id map, OAuth credentials, optional Medium token

# 3. (Optional) Install Chromium for Medium browser fallback
playwright install chromium
#    Then log in to Medium once in the Playwright-managed profile:
#    open ~/.config/backlink-publisher/chrome-profile-default/
```

## Pipeline Commands

### 1. plan-backlinks

Reads seed JSONL from stdin or `--input`, generates one article payload per row.

```bash
cat seeds.jsonl | plan-backlinks
cat seeds.jsonl | plan-backlinks -i /dev/stdin
```

**Input schema (seed):**

```json
{
  "target_url": "https://example.com/article",
  "main_domain": "https://example.com",
  "language": "en",
  "platform": "medium",
  "url_mode": "A",
  "publish_mode": "draft",
  "topic": "optional string",
  "seed_keywords": ["optional", "strings"]
}
```

| Field | Required | Values |
|---|---|---|
| `target_url` | yes | Valid HTTPS URL |
| `main_domain` | yes | Valid HTTPS URL |
| `language` | yes | `en`, `zh-CN`, `ru` |
| `platform` | yes | `medium`, `blogger` |
| `url_mode` | yes | `A` (main only), `B` (main+category), `C` (main+detail) |
| `publish_mode` | yes | `draft`, `publish` |
| `topic` | no | String |
| `seed_keywords` | no | String array |

**Output schema:**

```json
{
  "id": "sha256-truncated-16hex",
  "platform": "medium",
  "language": "en",
  "publish_mode": "draft",
  "target_url": "https://example.com/article",
  "main_domain": "https://example.com",
  "url_mode": "A",
  "title": "Exploring example.com: A Comprehensive Guide",
  "slug": "exploring-example-com-a-comprehensive-guide",
  "excerpt": "...",
  "tags": ["backlink", "reference", ...],
  "content_markdown": "# Title\n\n...",
  "links": [
    { "url": "...", "anchor": "...", "kind": "main_domain", "required": true }
  ],
  "seo": {
    "title": "...",
    "description": "...",
    "canonical_url": "..."
  }
}
```

- Articles are 100–200 words.
- 6–8 links per article (main_domain + target + mode-specific + supporting).
- `main_domain` appears naturally in the body, not at the start or end.
- Supports Simplified Chinese, English, and Russian.

### 2. validate-backlinks

Reads planned JSONL, validates schema + URLs, enriches with a `validation` block.

```bash
cat planned.jsonl | validate-backlinks
cat planned.jsonl | validate-backlinks --no-check-urls   # skip HTTP checks
```

**Validations performed:**

- All required output fields present with correct types
- 6–8 links per payload
- `target_url` and all link URLs reachable (HTTP 200/301/302)
- `main_domain` appears in `content_markdown`
- Title is non-empty
- SEO block complete
- Language roughly matches content (heuristic)
- `platform=linkedin` rejected (exit code 2)

**Output schema:** adds `validation` block:

```json
{
  "...all input fields...": {},
  "validation": {
    "status": "passed",
    "checked_at": "2026-05-11T12:00:00+00:00",
    "warnings": []
  }
}
```

### 3. publish-backlinks

Reads validated JSONL and publishes via API-first adapters with browser fallback.

```bash
# Medium (dry-run)
cat validated.jsonl | publish-backlinks --platform medium --mode draft --dry-run

# Medium (publish for real — uses API token or browser fallback)
cat validated.jsonl | publish-backlinks --platform medium --mode publish

# Blogger (draft — uses Blogger API v3)
cat validated.jsonl | publish-backlinks --platform blogger --mode draft

# Per-row platform (omit --platform)
cat validated.jsonl | publish-backlinks --mode draft
```

| Flag | Default | Description |
|---|---|---|
| `--platform` | per-row | Override platform for all rows |
| `--mode` | `draft` | `draft` or `publish` |
| `--dry-run` | off | Print command plan, don't execute |
| `--input`, `-i` | stdin | Input file path |

**Medium throttle:** Between consecutive Medium posts, the pipeline sleeps a random
60–300 seconds to avoid rate-limiting. Override with env vars:

```bash
MEDIUM_THROTTLE_MIN=30 MEDIUM_THROTTLE_MAX=90 publish-backlinks ...
```

## SEO Anchor Keywords

Generated articles place two backlinks pointing at each target site's
`main_domain`. Without configuration, both anchor texts default to the bare
domain (e.g. `your-site.com`) — a near-zero SEO signal. To improve keyword
relevance, configure a per-target keyword pool in `~/.config/backlink-publisher/config.toml`:

```toml
[targets."https://your-site.com"]
anchor_keywords = [
  "your-site",                  # branded
  "comprehensive content hub",  # head term
  "in-depth resource guide",    # long-tail
  "curated reference library",
  "expert tutorials",
]
```

**Selection strategy.** For each article, two distinct keywords are picked
deterministically using `keywords[(position + offset) % len(keywords)]`, where
`offset` is `0/1/2` for `url_mode` `A/B/C`. Same article configuration always
yields the same anchor distribution; varying `url_mode` across articles rotates
which keyword anchors which slot, producing natural distribution. Recommended
pool size: **5–10 keywords**, mixing branded terms, head terms, and long-tail
phrases.

**All anchored references** in the article (excerpt, body paragraphs, density
fallback paragraph, references section) use the configured keywords.

**Fallback.** If `anchor_keywords` is missing or an empty list, the renderer
falls back to the bare domain label and emits a single `WARN` per article so
the operator notices the missed SEO opportunity. Articles still publish
normally.

**New-tab behaviour.** All `<a>` tags in the rendered HTML include
`target="_blank" rel="noopener"` so backlinks open in a new tab without
exposing the opener window. (Note: Medium's renderer may strip these
attributes — behaviour on Medium is best-effort.)

`save_config` rewrites `[targets."<domain>"]` blocks from the resolved
`Config` (the `target_anchor_keywords` and `target_three_url` kwargs follow
the three-state `None` / `{}` / non-empty contract). Operator-added
`[targets.X]` subsections — where `X` is not a managed domain — are
preserved verbatim on save.

## Work-Themed Backlinks (Three-URL Form)

Recommended for new projects (Plan 2026-05-13-004). Each generated article
carries **three** backlinks pointing at the same target site:

1. **`main_url`** — the brand-weight anchor (drawn from `branded_pool`).
2. **`list_url`** — the discovery surface (anchor drawn 70% from `partial_pool`,
   30% from `exact_pool`).
3. **`work_url`** — one URL per article, anchor synthesised from the scraped
   `<title>` via the `work_anchor_templates` (default templates: `{title}`,
   `{title} 详情`, `{title} 推荐`, `{title} 介绍`).

Anchor positions across the three paragraphs are permuted by a per-article
seed (six possible orderings) so the link layout doesn't form a stable
"main first / work last" fingerprint. All anchors render as
`<a target="_blank" rel="noopener">` — **no `nofollow`** so dofollow weight
transfers in full. The post-publish verifier (`link_attr_verifier`) flags
any platform-injected `rel="nofollow"` so silent demotion (Medium and
similar) surfaces in the publish report.

### Configuring via WebUI

Open `/sites` in the WebUI to fill the three-URL form. The form uses CSRF
tokens and the page is bound to `127.0.0.1` by default — set
`BACKLINK_PUBLISHER_ALLOW_NETWORK=1` to bind to a non-loopback address
(only do this on a trusted network). The save button persists the
configuration via the same `save_config` that `[blogger.oauth]` uses, so
existing credentials, the legacy `[sites.*]` block, and any operator-added
depth-2 subsections under managed roots (e.g. `[medium.oauth]`,
`[medium.browser]`, `[targets.X]`) are preserved verbatim.

### Configuring via `config.toml`

Equivalent TOML form:

```toml
[targets."https://your-site.com"]
main_url = "https://your-site.com/"
list_url = "https://your-site.com/list"
work_urls = ["https://your-site.com/work/1", "https://your-site.com/work/2"]
branded_pool = ["Your Site", "Your Site Hub"]
partial_pool = ["site hub partial keyword"]
exact_pool = ["site keyword"]
# work_anchor_templates = ["{title}", "{title} 详情", "{title} 推荐", "{title} 介绍"]
# list_path_blocklist = ["/tag/", "/category/", "/page/"]
# insecure_tls = false
```

When `work_urls` is empty, the planner discovers candidates by fetching
`/sitemap.xml` (recursing one level into `<sitemapindex>`), falling back
to scraping `<a href>` elements off `list_url` with a default nav-path
blocklist (`/tag/`, `/category/`, `/page/`, `/author/`, `/about`,
`/contact`, `/search`, `/feed`).

### Dual-path coexistence (no migration pressure)

Sites that already have a `[sites."<domain>"]` block continue to use the
zh-CN short-form scheduler (next section). Adding a `[targets."<domain>"]`
three-URL block for the same domain just routes that domain through the
work-themed planner instead — both paths are kept alive. A single INFO
log notes the coexistence so you can decide when (or whether) to migrate.

## zh-CN Short-Form Anchor Profile Scheduler

The default path above (`[targets."<domain>"].anchor_keywords`) drives en/ru
articles and any zh-CN target that hasn't opted into the scheduler. zh-CN
targets can opt into a richer **anchor profile scheduler** that:

- generates 150–200-character short articles (1 main link to home + 1–2
  secondary links to non-home pages) instead of the long-form 6–8-link layout
- enforces a sliding-window distribution against a Safe SEO target
  (Branded 55% / Partial 25% / Exact 10% / LSI 10%) across the four anchor
  type buckets
- picks each secondary link's URL category from `{hot, animate, category,
  topic}` so a single article never repeats the same target page twice

### Default mode: LLM-free runtime

The 51acgs.com block in `config.example.toml` is pre-sized for **no LLM at
runtime**: 126 hand-picked candidates across 20 `(url_category, anchor_type)`
cells, the heavy-use `home/branded` cell padded to 15 entries to comfortably
outlast the 20-entry text-dedup window. A 500-article × 3-seed simulation
produces zero LLM fallback calls and lands within 1 pp of every target
proportion.

To enable, uncomment the `[sites."https://51acgs.com".url_categories]` and
`[sites."https://51acgs.com".anchor_pools.*]` blocks in `config.example.toml`
(or copy them to your `~/.config/backlink-publisher/config.toml`). The
scheduler engages automatically for any zh-CN seed row whose `main_domain`
has these blocks configured; rows without v2 config fall through to the
legacy long-form path with zero behavior change.

To extend the scheduler to another site, mirror the 51acgs.com structure:

1. List the site's `url_categories` (must include `home` plus at least one
   non-home category).
2. Fill `[sites."<site>".anchor_pools.<category>.<type>]` for every
   `(url_category, anchor_type)` cell you want covered — minimum 3
   candidates per cell, **≥12 in `home/branded`** to keep the scheduler
   out of the degrade path.
3. Run `pytest tests/test_config_example_pool.py` after editing — the
   regression tests run a 500-article simulation against your pool and
   fail if any cell would trigger a degrade.

### Optional mode: hybrid with LLM fallback

If you want to thin some cells and let an LLM generate candidates on
demand, uncomment the `[llm.anchor_provider]` block:

```toml
[llm.anchor_provider]
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
timeout_s = 30
```

Provide the API key via the `BACKLINK_LLM_API_KEY` env var (preferred) or
`api_key = "sk-..."` in the same block (which requires `chmod 600
config.toml` — a warning is emitted otherwise). `base_url` must be
`https://`.

Before promoting hybrid config to production, validate the provider's
rejection rate against your content shape:

```bash
python scripts/llm_rejection_spike.py
# exit 0 = rejection rate < 20%, exit 1 = above threshold
```

Adult-content sites should expect significant rejection rates from
mainstream providers — `scripts/llm_rejection_spike.py` makes that
measurement reproducible so the trade-off is explicit rather than
discovered during a production batch.

### Observability

After at least 50 articles, inspect the per-site anchor profile:

```bash
report-anchors --from-profile https://51acgs.com
```

The report shows the rolling type distribution vs. target, a
`url_category × anchor_type` cross-tab, and degradation rate flagged
with ⚠️ when above 10%. JSON output is available via `--json` for
scripting.

`[anchor.proportions]`, `[anchor_alarm]`, and `[llm.anchor_provider]` are
operator-edit-only and preserved verbatim by `save_config` (unmanaged
roots). See the **Managing SEO keywords** section above for the full
`save_config` taxonomy and credential-lifecycle notes.

### Anchor Distribution Visibility

`report-anchors --from-profile` also surfaces three per-target-URL
distribution metrics over rolling 30d and 90d windows:

- **Shannon entropy** of normalized anchor-text distribution
- **Exact-match ratio** — fraction with `anchor_type == "exact"`
- **Top-3 concentration** over non-branded anchors only

When the 90d window for any target crosses a configured threshold,
`report-anchors` emits a structured `alarm` block in JSON output, a
`WARN [anchor_alarm]` line per breaching target on stderr, and exits
with code **6** so cron wrappers can alert without ambiguity.

```bash
report-anchors --from-profile https://example.com
# Exit 0  → no breach
# Exit 6  → at least one target breached in the 90d window
```

This is **detection, not prevention**. The publish path does not consult
the alarm — the operator is the deciding agent. When a target breaches:
pause publishing to that URL, rotate its anchor strategy, and re-run
after another batch of articles. The thresholds are a conservative
lower-bound approximation of Google's SpamBrain signal — false-positives
trigger a 5-minute review; false-negatives risk a Penguin penalty, so
defaults favor an earlier warning (sample-size floor 20 per target vs.
the 50-entry floor used for domain-level metrics).

Override thresholds via `[anchor_alarm]` in `config.toml`. See
`config.example.toml` for the full schema including per-domain and
per-URL overrides.

Note: the operator-facing aliases `report-anchors` (without
`--from-profile`) and `cat payloads.jsonl | report-anchors` continue to
report type distribution from the JSONL stream but **do not** compute
the distribution alarm — that path lacks the `anchor_type` field needed
for exact-ratio. A stderr hint surfaces this on every invocation so the
zero exit code is not mistaken for "no breach detected".

## Publisher Adapters

Publishing is API-first with a browser fallback where no API exists. **20+ platforms** are registered in `publishing/adapters/__init__.py`; the table below groups them by link equity. Adding one more is a single `register(...)` call — see [AGENTS.md → Adding a new publisher adapter](AGENTS.md#adding-a-new-publisher-adapter).

Each registration declares a `dofollow` verdict — `True` / `False` / `"uncertain"` — plus a `referral_value` (`high` / `low`). `"uncertain"` means a third-party probe saw dofollow but **our own** publish-path canary hasn't confirmed it yet; `canary-targets` re-fetches live posts to settle these verdicts over time.

| Platform | dofollow | Transport | Auth |
|---|---|---|---|
| **Blogger** | ✅ dofollow | Blogger API v3 | OAuth2 token |
| **Medium** | ✅ dofollow | Medium API v1 + Playwright fallback | Integration token / browser |
| **Telegraph** | ✅ dofollow | telegra.ph API | anonymous |
| **Velog** | ✅ dofollow | internal GraphQL `writePost` | cookie jar (30-day) |
| **GitHub Pages** | ✅ dofollow | GitHub Contents API (`*.github.io`) | Bearer PAT |
| **WordPress.com** | ❔ uncertain | WordPress.com REST v1.1 | OAuth2 token |
| **Substack** | ❔ uncertain | internal publish API | cookie jar |
| **Hatena** | ❔ uncertain | AtomPub | API key |
| **HackMD** | ❔ uncertain | HackMD API | API token |
| **Mataroa** | ❔ uncertain | Mataroa API | API key |
| **GitLab Pages** | ❔ uncertain | GitLab Repository Files API (`*.gitlab.io`) | PAT (PRIVATE-TOKEN) |
| **Rentry / txt.fyi** | ❔ uncertain | anonymous paste POST | none |
| **Hashnode / Write.as** | ❔ uncertain | API (retiring) | API token |
| **dev.to / Notion** | ⛔ nofollow | API | API token |
| **LinkedIn¹ / Tumblr / LiveJournal / Mastodon** | ⛔ nofollow | API / cookie jar | per-platform |

¹ LinkedIn is registered `visibility="experimental"`.

> nofollow platforms are kept for **referral traffic, topical relevance, and indexation speed** — `referral_value` records why. Use `equity-ledger` / `plan-gap` to steer fresh links toward the dofollow tier.

### Blogger Setup

1. Create a Google Cloud project and enable the **Blogger API v3**.
2. Create OAuth2 credentials (Desktop app) and download client JSON.
3. Add credentials to `~/.config/backlink-publisher/config.toml`:

```toml
[blogger.oauth]
client_id     = "..."
client_secret = "..."

[blogger]
"https://your-site.com" = "your-blog-id"
```

4. Run any Blogger publish — a browser window opens once for OAuth authorization.
   The token is saved automatically for future runs.

### Velog Setup

velog.io has no official API; we publish via its internal `v2.velog.io/graphql`
GraphQL endpoint using a cookie jar from social login.

1. Install Playwright (required once):

```bash
pip install playwright && playwright install chromium
```

2. Run the login command (opens a headed Chromium window):

```bash
velog-login
```

3. Complete social login (Google / GitHub / Facebook) in the browser.
   Credentials are saved to `~/.config/backlink-publisher/velog-cookies.json` (0600).

4. Publish:

```bash
cat seeds.jsonl | plan-backlinks | validate-backlinks \
  | publish-backlinks --platform velog --mode publish
```

**Notes:**
- Cookie TTL: access_token 24 h (auto-refreshed); refresh_token **30 days**.
  Re-run `velog-login` once per 30 days.
- Phase 1 cap: **5 posts/day** until 2026-06-02, then 30/day.
- Cross-machine: daily cap is per-machine. Coordinate manually if using multiple machines.
- See `docs/operations/velog-login.md` for full operator guide.

### Medium Setup

**Option A — Integration Token (preferred):**

1. Generate a token at `medium.com/me/settings/security → Integration tokens`.
2. Add to config:

```toml
[medium]
integration_token = "your-token"
```

**Option B — Browser fallback (no token needed):**

```bash
playwright install chromium
# Launch the managed profile once and log in to Medium:
# The profile is at ~/.config/backlink-publisher/chrome-profile-default/
```

The pipeline automatically uses the browser if no token is configured.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Usage error (bad CLI flags) |
| `2` | Input validation error (schema, link count, bad URLs) |
| `3` | Dependency error (missing config, OAuth not set up, Playwright not installed) |
| `4` | External service error (API error, login expired, CAPTCHA) |
| `5` | Unexpected internal error |
| `6` | Anchor distribution alarm — `report-anchors --from-profile` detected at least one target's 90d window exceeds the configured threshold. Output is otherwise valid; treat as a warning that requires operator action. |

## Output Contract

- **stdout** — structured JSONL only (on success)
- **stderr** — diagnostic messages only (on failure)
- **exit code** — 0 on success, non-zero on failure
- No human-readable "Done" or "Success" messages in any mode

## Example Pipeline

```bash
# Step 1: generate seeds
cat > seeds.jsonl <<'EOF'
{"target_url":"https://example.com/article","main_domain":"https://example.com","language":"en","platform":"medium","url_mode":"A","publish_mode":"draft","topic":"Web Development"}
{"target_url":"https://blog.example.org/posts/guide","main_domain":"https://blog.example.org","language":"zh-CN","platform":"blogger","url_mode":"C","publish_mode":"publish","topic":"Python最佳实践"}
EOF

# Step 2: full pipeline (dry-run)
cat seeds.jsonl | plan-backlinks | validate-backlinks | publish-backlinks --mode draft --dry-run

# Step 3: full pipeline (Blogger, publish)
cat seeds.jsonl | plan-backlinks | validate-backlinks | publish-backlinks --platform blogger --mode publish
```

## Troubleshooting

If a publish fails with `channel 'X' credentials expired`, open Settings (`/settings`) and click **重新绑定** on the affected channel card. A headed browser opens for you to log in; the badge transitions `绑定中…` → `已绑定 ✓` when the storage_state file is written and `mark_bound` records the bind. CLI alternative: `bind-channel --channel <velog|medium|blogger>`. See `AGENTS.md → Binding a channel` for the full lifecycle.

| Problem | Solution |
|---|---|
| `Blogger OAuth not configured` | Add `[blogger.oauth]` to `~/.config/backlink-publisher/config.toml` |
| `No Blogger blog_id configured for domain` | Add the domain mapping under `[blogger]` in config.toml |
| `channel 'blogger' credentials expired` (exit 3) | Open `/settings` → Blogger → 重新绑定, **or** run `bind-channel --channel blogger` |
| `channel 'medium' credentials expired` (exit 3) | Open `/settings` → Medium → 重新绑定, **or** run `bind-channel --channel medium` |
| `medium integration token not configured` | Add `[medium] integration_token = "..."` to config, or install Playwright as fallback |
| `Medium login expired` | Log in to Medium in the managed Chrome profile |
| `Medium CAPTCHA detected` | Solve CAPTCHA manually at medium.com, then retry |
| `Playwright is not installed` | Run `playwright install chromium` |
| `Medium selector changed` | Update selectors in `src/backlink_publisher/adapters/_medium_selectors.py` |
| Failed publish saved screenshot | Check `~/.cache/backlink-publisher/screenshots/` for error screenshots |

## CLI command reference

23 console entrypoints are declared in `pyproject.toml` `[project.scripts]`. The pipeline core is `plan-backlinks → validate-backlinks → publish-backlinks`; the rest are read-only analysis, channel-binding, and re-verification helpers:

| Command | Role |
|---|---|
| `plan-backlinks` | Generate article payloads from seed JSONL |
| `validate-backlinks` | Validate + enrich payloads |
| `publish-backlinks` | Publish via platform adapters |
| `report-anchors` | Anchor-profile + distribution alarm |
| `footprint` | Link-footprint analysis |
| `equity-ledger` | Per-target backlink scorecard (read-only) |
| `plan-gap` | Deficit-driven re-plan from the ledger (read-only) |
| `audit-state` | Dual-state divergence auditor (read-only) |
| `preflight-targets` | Destination-page health check before publish |
| `cull-channels` / `channel-scorecard` | Channel-quality advisories (read-only) |
| `canary-targets` | Re-fetch dofollow posts to confirm links survive (advisory) |
| `recheck-backlinks` / `recheck-overlay` | Re-verify published links + overlay deltas |
| `bind-channel` | Bind a destination to a publishing channel |
| `velog-login` / `medium-login` / `frw-login` | Interactive per-platform login helpers |
| `generate-backlink-text` | LLM-assisted content drafting |
| `comment` | Comment-outreach driver |
| `gate-probe` | Probe gate/throttle decisions |
| `plan-check` / `plan-gap` | Plan-doc drift validator + coverage gap |
| `phase0-seal` | Phase 0 seal operations |

## Project Structure

```
backlink-publisher/                 # canonical git repo (this dir)
├── config.example.toml
├── pyproject.toml
├── README.md / README.zh.md
├── AGENTS.md                        # authoritative contributor guide
├── webui.py                         # Flask WebUI launcher (:8888)
├── src/backlink_publisher/
│   ├── cli/                         # 23 console entrypoints
│   │   └── plan_backlinks/          # plan-backlinks decomposed into a package
│   ├── publishing/
│   │   ├── adapters/__init__.py     # adapter registry — register("x", …)
│   │   ├── registry.py              # dynamic platform lookup
│   │   ├── browser_publish/         # Playwright fallbacks
│   │   └── reliability/             # throttle / circuit-breaker
│   ├── anchor/                      # anchor-keyword scheduler + alarm
│   ├── content/                     # article rendering
│   ├── linkcheck/                   # URL reachability + SSRF guard
│   ├── canary/ · ledger/ · gap/     # equity-ledger / plan-gap engines
│   ├── audit/ · recheck/ · scorecard/
│   ├── config/ · schema.py · http.py · _util/
│   └── llm/                         # optional LLM anchor provider
├── webui_app/                       # Flask app (routes + services/)
├── webui_store/                     # WebUI state singletons
├── tests/                           # pytest suite (PYTHONHASHSEED=0)
├── monolith_budget.toml             # radon SLOC ceilings
├── complexity_budget.toml           # radon cyclomatic-complexity ceilings
└── fixtures/
```

## For contributors

Adding a new publishing platform (WordPress, Substack, Telegraph, …) is one `register("x", XAdapter)` call away from being reachable through the CLI and schema layers. See [AGENTS.md → Adding a new publisher adapter](AGENTS.md#adding-a-new-publisher-adapter) for the five-step recipe (subclass / implement / register / config / deps / test) that cites `BloggerAPIAdapter` at every step.

For broader project conventions (`docs/solutions/` lesson curation, monolith SLOC budget, worktree auto-cleanup), the rest of [AGENTS.md](AGENTS.md) is the source of truth.

## Developer Tooling (Experimental)

> **Not part of the publishing pipeline.** These tools use [Webwright](https://github.com/microsoft/Webwright) (LLM-driven Playwright) to accelerate local development tasks only.

**Prerequisites:** `pip install -e ".[dev-webwright]"` and an `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`) in your environment.

**Scaffold a new platform adapter** — explore a platform's login + post flow and get a Playwright script draft:

```bash
make scaffold PLATFORM=devto LOGIN_URL=https://dev.to/enter
# Output: docs/spikes/scaffold-devto-<date>/  (gitignored)
# Review the draft, then promote to src/.../adapters/ following AGENTS.md § adapter recipe
```

**Diagnose a bind-channel failure** — reproduce and document a login flow failure with screenshots:

```bash
make diagnose CHANNEL=velog
# Output: docs/diagnostics/velog-<date>/  (gitignored)
# Read summary.txt and screenshots to identify the root cause
```
