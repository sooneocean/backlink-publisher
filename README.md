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
