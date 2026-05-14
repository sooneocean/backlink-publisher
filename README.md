# backlink-publisher

A local-first, terminal-native backlink publishing pipeline for Blogger and Medium.  
Generates, validates, and publishes short backlink articles — fully pipe-friendly, cron-safe, and non-interactive.

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

`save_config` does **not** write the `[targets]` section back. Edit
`config.toml` by hand to add or update keyword pools.

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

`save_config` does not round-trip `[sites.*]`, `[anchor.proportions]`,
`[anchor_alarm]`, or `[llm.anchor_provider]` — edit `config.toml` by hand.

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

Publishing is API-first with a browser fallback for Medium.

| Platform | Primary | Fallback |
|---|---|---|
| **Blogger** | Blogger API v3 (OAuth2) | — |
| **Medium** | Medium API v1 (Integration Token) | Playwright browser automation |

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

| Problem | Solution |
|---|---|
| `Blogger OAuth not configured` | Add `[blogger.oauth]` to `~/.config/backlink-publisher/config.toml` |
| `No Blogger blog_id configured for domain` | Add the domain mapping under `[blogger]` in config.toml |
| `Blogger authentication failed (HTTP 401)` | Delete `~/.config/backlink-publisher/blogger-token.json` and re-run to re-authorize |
| `medium integration token not configured` | Add `[medium] integration_token = "..."` to config, or install Playwright as fallback |
| `Medium login expired` | Log in to Medium in the managed Chrome profile |
| `Medium CAPTCHA detected` | Solve CAPTCHA manually at medium.com, then retry |
| `Playwright is not installed` | Run `playwright install chromium` |
| `Medium selector changed` | Update selectors in `src/backlink_publisher/adapters/_medium_selectors.py` |
| Failed publish saved screenshot | Check `~/.cache/backlink-publisher/screenshots/` for error screenshots |

## Project Structure

```
backlink-publisher/
├── config.example.toml
├── pyproject.toml
├── README.md
├── src/backlink_publisher/
│   ├── __init__.py
│   ├── adapters/
│   │   ├── __init__.py          # dispatcher (publish, verify_adapter_setup)
│   │   ├── base.py              # AdapterResult dataclass
│   │   ├── blogger_api.py       # Blogger API v3 adapter
│   │   ├── medium_api.py        # Medium API v1 adapter
│   │   ├── medium_browser.py    # Playwright browser fallback
│   │   └── _medium_selectors.py # CSS selector constants
│   ├── cli/
│   │   ├── plan_backlinks.py
│   │   ├── validate_backlinks.py
│   │   └── publish_backlinks.py
│   ├── config.py
│   ├── schema.py
│   ├── errors.py
│   ├── jsonl.py
│   ├── linkcheck.py
│   ├── language_check.py
│   └── markdown_utils.py
├── tests/
│   ├── test_adapter_base.py
│   ├── test_adapter_blogger_api.py
│   ├── test_adapter_medium_api.py
│   ├── test_adapter_medium_browser.py
│   ├── test_adapter_dispatcher.py
│   ├── test_config.py
│   ├── test_markdown_render.py
│   ├── test_throttle.py
│   ├── test_plan_backlinks.py
│   ├── test_validate_backlinks.py
│   ├── test_publish_backlinks.py
│   └── test_edge_cases.py
└── fixtures/
    └── seed.jsonl
```
