---
title: "feat: Automated Setup Wizard + Watch Service + Scoring Engine (Wave 1)"
type: feat
status: active
date: 2026-06-06
origin: docs/brainstorms/2026-06-06-automated-backlink-dashboard-wizard-requirements.md
claims: {}  # opt-out: net-new wave artifacts have no origin/main-reachable SHA yet
---

# Wave 1 — Setup Wizard + Watch Service + Scoring Engine

## Summary

Extend the existing backlink-publisher WebUI with three capabilities:

1. **Setup Wizard** (`/wizard`) — guided multi-step onboarding for new operators
2. **Watch Service** (APScheduler job) — polls seed sources, detects new URLs, enqueues publish tasks
3. **Scoring Engine** — computes and persists scores per publish event

All three extend existing infrastructure (Flask WebUI, `webui_store` JSON stores, APScheduler, PipelineAPI). No new frameworks, no external services, no major refactoring.

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Score storage | New `score_store` JSON file | Simpler than events.db projector; consistent with existing `history_store`/`queue_store` pattern |
| Polling interval | Configurable, default 6h | Conservative; good enough for backlink publishing latency |
| Watch/queue separation | Watch enqueues → existing queue worker executes | Reuses all rate-limit/retry/AuthExpiredError handling |
| Coverage check | equity-ledger engine when available, else history_store fallback | Avoids subprocess calls; pure Python import |
| Channel selection | Pure engine function, no I/O | Testable without mocking stores |
| Auto-start | Wizard completion triggers scheduler start | Immediate feedback; operator sees "system active" |

## Layer Map

```
webui_store/                           ← 3 new JSON stores
  seen_urls_store.py                   ←  seen_urls.json
  score_store.py                       ←  score-store.json
  wizard_config_store.py               ←  wizard-config.json

webui_app/services/
  watch_service.py                     ←  pure engine: poll → detect → select → enqueue

webui_app/scheduler.py                 ←  added watch_service APScheduler job

webui_app/routes/
  wizard.py                            ←  /wizard blueprint (multi-step form)

webui_app/templates/
  wizard.html                          ←  wizard container (JS-driven steps)

webui_app/static/js/
  wizard.js                            ←  wizard module (step navigation + API calls)
```

## Implementation Units

### Unit 1: New JSON Stores (webui_store/)

Three new `JsonStore` subclasses following the existing pattern.

#### 1a. `seen_urls_store.py`

```
SeenUrlsStore(JsonStore)
  key: url_hash (SHA256 of normalized URL, first 16 hex chars)
  value: {
    url: str,
    url_hash: str,
    source_type: "sitemap" | "manual" | "bookmark",
    source_origin: str,           # the sitemap URL, or "manual", or bookmark file path
    discovered_at: str (ISO 8601),
    last_seen_at: str (ISO 8601),
    coverage: {                    # channel → status map
      "<channel>": "pending" | "published" | "failed" | "skipped"
    }
  }
  methods:
    - mark_seen(url, source_type, source_origin) → record
    - is_new(url) → bool
    - get_by_source(source_type) → list[records]
    - update_coverage(url_hash, channel, status) → void
    - get_uncovered() → list[records]    # no channel has "published" status
```

**Persistence**: `wizard-config.json` in `webui_store` directory.

#### 1b. `score_store.py`

```
ScoreStore(JsonStore)
  key: f"{target_url_hash}:{channel}"   (composite key)
  value: {
    score_id: str,
    target_url: str,
    target_url_hash: str,
    channel: str,
    platform_weight: float,
    dofollow_multiplier: float,
    survival_bonus: float,
    score: float,                        # computed: base(1) * platform_weight * dofollow_multiplier * survival_bonus
    base_score: float,                   # always 1.0 in v1
    published_at: str (ISO 8601),
    rechecked_at: str (ISO 8601) or None,
    status: "initial" | "survival_confirmed" | "survival_lost"
  }
  methods:
    - record_publish(target_url, channel, platform_weight, dofollow_multiplier) → score_id
    - update_survival(score_id, alive: bool) → updated score
    - get_total_score() → float
    - get_channel_breakdown() → dict[channel → {count, total}]
    - get_recent(limit=50) → list
    - backfill_from_history(history_store) → int (count of backfilled)
```

**Persistence**: `score-store.json`.

#### 1c. `wizard_config_store.py`

```
WizardConfigStore(JsonStore)
  key: "wizard_config" (singleton)
  value: {
    completed: bool,
    completed_at: str (ISO 8601) or None,
    skipped: bool,
    wizard_version: str,
    seed_sources: [
      {
        id: str (uuid),
        type: "sitemap" | "manual" | "bookmark",
        value: str,               # URL for sitemap, text for manual, file path for bookmark
        label: str or None,
        created_at: str (ISO 8601),
        enabled: bool
      }
    ],
    channels: [                   # channels configured during wizard
      {
        channel: str,
        bound: bool,
        daily_cap: int,
        dofollow_preference: bool,
        language_whitelist: list[str]
      }
    ],
    automation_rules: {
      polling_interval_seconds: int,   # default 21600 (6h)
      default_daily_cap: int,          # default 10
      max_daily_publish: int,          # default 50
      language_filter: list[str]       # empty = all
    }
  }
  methods:
    - is_completed() → bool
    - mark_completed(config) → void
    - mark_skipped() → void
    - get_seed_sources() → list
    - add_seed_source(source) → void
    - get_automation_rules() → dict
```

#### 1d. Registration

- Add `_LazyStore` bindings to `webui_store/__init__.py`
- Add properties to `WebUIStores` in `registry.py`
- Import the new stores wherever needed via `current_app.extensions['webui_stores']`

### Unit 2: Watch Service Engine (webui_app/services/watch_service.py)

A pure-Python engine that implements seed source polling, URL detection, coverage checking, channel selection, and queue enqueue.

```
class WatchService:
    def __init__(self, seen_urls_store, history_store, queue_store, equity_ledger_engine=None):
        self.seen = seen_urls_store
        self.history = history_store
        self.queue = queue_store
        self.ledger = equity_ledger_engine or self._fallback_coverage

    def poll_all_sources(self, seed_sources: list) -> PollResult:
        """Poll all configured seed sources, return new URLs found."""
        ...

    def poll_sitemap(self, url: str) -> list[str]:
        """Fetch sitemap XML, extract all <loc> URLs."""
        ...

    def poll_manual_list(self, text: str) -> list[str]:
        """Parse newline-separated URLs from manual target list."""
        ...

    def poll_bookmark_file(self, file_path: str) -> list[str]:
        """Parse HTML bookmark file, extract HREF URLs."""
        ...

    def detect_new_urls(self, candidates: list[ParsedUrl]) -> list[NewUrl]:
        """Diff candidates against seen_urls_store; return only truly new URLs."""
        ...

    def check_coverage(self, target_url: str, channels: list[str]) -> dict[str, bool]:
        """Return per-channel coverage status. Equity-ledger first, then history_store fallback."""
        ...

    def select_best_channel(self, target_url: str, channels: list[ChannelInfo]) -> ChannelInfo | None:
        """Score each active channel by priority rules. Return best or None."""
        ...

    def enqueue_publish(self, target_url: str, channel: str, seed_source_info: dict) -> str:
        """Build seed dict and push to queue_store. Return queue item ID."""
        ...

    def run_once(self, config: WizardConfig) -> RunReport:
        """Full cycle: poll all sources → detect new → check coverage → select channels → enqueue.
        Called by the APScheduler job."""
        ...
```

**Channel Selection Algorithm (R7):**
1. Filter: only channels with `bound=True` in wizard config
2. Filter: channel status is not `expired` (check `channel_status_store`)
3. Filter: language whitelist matches target language (if configured)
4. Priority sort:
   - Tier 1: dofollow=True, daily_cap not exhausted
   - Tier 2: dofollow=True, daily_cap exhausted (skip)
   - Tier 3: dofollow="uncertain", daily_cap not exhausted
   - Tier 4: dofollow=False, daily_cap not exhausted
   - Tier 5: any whose daily quota is exhausted
5. If no channel qualifies → log uncovered with reason in `RunReport.uncovered`
6. For multiple qualifying channels, pick the one with fewest total publishes today (load balancing)

**RunReport** (returned by `run_once`):
```
{
  polled_sources: int,
  urls_found: int,
  new_urls: int,
  already_covered: int,
  enqueued: int,
  uncovered: [{url, reason}],
  errors: [{source, error}]
}
```

### Unit 3: APScheduler Integration (webui_app/scheduler.py)

Add a second APScheduler job alongside the existing queue processor.

**Changes to `webui_app/scheduler.py`:**

```python
# Add new import
from webui_app.services.watch_service import WatchService

# At module level, after existing queue job registration:
def _register_watch_job(scheduler):
    """Register the watch_service polling job."""
    scheduler.add_job(
        _run_watch_cycle,
        'interval',
        seconds=21600,  # 6 hours default
        id='watch_service',
        name='Seed source polling',
        misfire_grace_time=3600,
        replace_existing=True,
        next_run_time=None  # Don't run until wizard completes
    )

def _run_watch_cycle():
    """Execute one watch cycle. Called by APScheduler."""
    with app.app_context():
        stores = current_app.extensions['webui_stores']
        config = stores.wizard_config_store.get('wizard_config', {})
        if not config.get('completed'):
            return  # Not configured yet
        service = WatchService(
            seen_urls_store=stores.seen_urls_store,
            history_store=stores.history_store,
            queue_store=stores.queue_store
        )
        report = service.run_once(config)
        app.logger.info(f"Watch cycle complete: {report}")
        return report

# In start_scheduler(): also call _register_watch_job(scheduler)
# On wizard completion: trigger immediate first run via scheduler.modify_job(next_run_time=datetime.now())
```

**Wizard completion triggers immediate first watch cycle** — the operator sees instant feedback rather than waiting up to 6 hours.

### Unit 4: Setup Wizard WebUI (webui_app/routes/wizard.py)

New Flask blueprint for the multi-step wizard at `/wizard`.

```
wizard_bp = Blueprint('wizard', __name__, template_folder='../templates')

GET /wizard
  - If wizard_config_store.is_completed(): redirect to /
  - Render wizard.html with registered platforms list, binding status

POST /wizard/step/seed-sources
  - Accept: sitemap URL, manual target text, bookmark file upload
  - Validate URLs, parse bookmark file
  - Store seed sources in wizard_config_store

POST /wizard/step/channels
  - Accept: selected platforms list
  - For each selected platform: render binding iframe/modal (reuse existing binding flow)
  - Return binding status per channel
  - Store channel configs in wizard_config_store

POST /wizard/step/rules
  - Accept: polling interval, daily caps, language filter, dofollow preference
  - Store in wizard_config_store.automation_rules

POST /wizard/step/launch
  - Mark wizard_config_store.completed = True
  - Start watch service job (modify_job next_run_time=now)
  - Return {"status": "active"} → JS redirects to /

GET /api/wizard/status
  - Return {"completed": bool, "skipped": bool}
```

**CSRF**: The wizard blueprint participates in the existing `_global_csrf_guard`. All POST routes need the CSRF token.

### Unit 5: Wizard Frontend (webui_app/templates/wizard.html + static/js/wizard.js)

**Template: `wizard.html`**
```
{% extends 'base.html' %}
{% block title %}Setup Wizard — Backlink Publisher{% endblock %}
{% block content %}
<div id="wizard-app" class="container py-4">
  <div id="wizard-steps" data-current-step="1">
    <!-- Step indicators rendered server-side or built by JS -->
    <!-- Content area swapped by JS on step navigation -->
  </div>
</div>
{% endblock %}
{% block page_data %}
<script>window.__wizardData = {{ wizard_bootstrap | tojson }}</script>
{% endblock %}
{% block page_module %}
<script type="module" src="{{ url_for('static', filename='js/wizard.js', v=asset_version) }}"></script>
{% endblock %}
```

**JS Module: `wizard.js`**
```javascript
import { fetchJson, postJson, readCsrf } from './lib/api.js';
import { qs, delegate } from './lib/dom.js';

// State
let currentStep = 1;
const totalSteps = 6;
const wizardData = window.__wizardData;  // read once, discard

// Step renderers
function renderWelcome() { ... }
function renderSeedSources() { ... }
function renderChannels() { ... }
function renderRules() { ... }
function renderReview() { ... }
function renderLaunch() { ... }

// Navigation
function goToStep(n) { ... }
async function submitStep(data) { ... }

// Event delegation
delegate('#wizard-app', 'click', '[data-wizard-action]', handler);
```

**Step breakdown (6 steps):**
1. **Welcome** — explain wizard purpose, "Get Started" button
2. **Seed Sources** — form with: sitemap URL input, manual targets textarea, bookmark file upload
3. **Channels** — list available platforms with checkboxes + current binding status. Each checked channel goes through binding flow inline
4. **Automation Rules** — polling interval slider, per-channel daily cap inputs, language whitelist
5. **Review** — summary of all configured settings, editable sections
6. **Launch** — "Start Automation" button → POST /wizard/step/launch → redirect to /

### Unit 6: System Active Indicator (base.html modification)

Add status indicator to `base.html` in a visible area (e.g., navbar):

```html
<!-- In base.html, after existing nav elements -->
{% if wizard_config.completed %}
<span id="system-status" class="badge bg-success ms-2" title="Automation active">
  <i class="bi bi-check-circle-fill"></i> System Active
</span>
{% else %}
<span id="system-status" class="badge bg-secondary ms-2" title="Setup incomplete">
  <i class="bi bi-slash-circle"></i> Not Configured
</span>
{% endif %}
```

The `wizard_config` variable is injected via a context processor or `_render()` helper. If the scheduler is stopped or the watch job fails, the badge should transition via JS polling `/api/wizard/status`.

### Unit 7: Scoring Engine + Backfill

#### 7a. On-Publish Scoring Hook

Modify the queue processor (`scheduler.py` `_process_queue_job` or the success handler) to call scoring after each successful publish:

```python
def _score_after_publish(publish_result, channel, target_url):
    """Called after successful publish. Records score."""
    stores = current_app.extensions['webui_stores']
    platform_weight = _get_platform_weight(channel)
    dofollow_mult = _get_dofollow_multiplier(channel)
    score_id = stores.score_store.record_publish(
        target_url=target_url,
        channel=channel,
        platform_weight=platform_weight,
        dofollow_multiplier=dofollow_mult
    )
    return score_id
```

#### 7b. Survival Bonus (Recheck Hook)

The existing recheck pipeline (`recheck-backlinks`) needs a hook to update scores when survival is confirmed. Add a call in the recheck success handler:

```python
# In recheck processing, after confirming link still alive:
stores.score_store.update_survival(score_id, alive=True)
```

#### 7c. Backfill

Expose a CLI or API endpoint `/api/scoring/backfill` that:
1. Reads all successful publishes from `history_store`
2. For each, computes score and stores in `score_store` (if not already present)
3. Returns count of backfilled records

Also run backfill automatically on first wizard completion.

### Unit 8: Tests

#### 8a. `tests/test_wave1_wizard.py`
- `test_wizard_config_store_crud` — save/load/complete/skip
- `test_wizard_seed_sources_validate` — valid URL, invalid URL, empty
- `test_wizard_channel_config` — channel selection persistence
- `test_wizard_routing` — GET /wizard, POST steps, redirect when completed
- `test_wizard_csrf_opt_out` — test with CSRF_ENABLED=False

#### 8b. `tests/test_wave1_watch_service.py`
- `test_detect_new_urls` — empty seen, new URL, duplicate URL
- `test_check_coverage_equity_ledger` — mock equity-ledger data
- `test_check_coverage_history_fallback` — fallback when no ledger
- `test_select_best_channel` — dofollow priority, language filter, cap respect
- `test_select_best_channel_no_qualifying` — all expired or capped
- `test_poll_sitemap` — mock HTTP response, extract URLs
- `test_enqueue_publish` — verify queue_store item shape
- `test_run_once_full_cycle` — integration: poll → detect → select → enqueue
- `test_run_once_no_new_urls` — nothing new → empty report

#### 8c. `tests/test_wave1_scoring.py`
- `test_score_computation` — verify formula: base×weight×dofollow×survival
- `test_score_store_crud` — record, read, aggregate
- `test_survival_bonus_update` — initial 1.0 → confirmed 1.2
- `test_channel_breakdown` — per-channel aggregation
- `test_backfill_from_history` — scan history store, compute scores
- `test_backfill_idempotent` — running twice doesn't double-count

## File Change Summary

| File | Action | Lines (est.) |
|---|---|---|
| `webui_store/seen_urls_store.py` | CREATE | 60 |
| `webui_store/score_store.py` | CREATE | 90 |
| `webui_store/wizard_config_store.py` | CREATE | 70 |
| `webui_store/__init__.py` | MODIFY | +15 |
| `webui_store/registry.py` | MODIFY | +15 |
| `webui_app/services/watch_service.py` | CREATE | 250 |
| `webui_app/scheduler.py` | MODIFY | +40 |
| `webui_app/routes/wizard.py` | CREATE | 180 |
| `webui_app/templates/wizard.html` | CREATE | 60 |
| `webui_app/static/js/wizard.js` | CREATE | 250 |
| `webui_app/templates/base.html` | MODIFY | +5 |
| `tests/test_wave1_wizard.py` | CREATE | 120 |
| `tests/test_wave1_watch_service.py` | CREATE | 200 |
| `tests/test_wave1_scoring.py` | CREATE | 150 |

## Dependencies

- Existing `webui_store` infrastructure (JsonStore, _LazyStore, WebUIStores)
- Existing APScheduler (`BackgroundScheduler`)
- Existing `PipelineAPI` / `queue_store` / queue processor
- Existing `channel_status_store`
- Existing history_store for backfill
- `backlink_publisher.cli.gap.engine.plan_gap` (for equity-ledger integration, optional)
- `backlink_publisher.cli.equity_ledger.aggregate.build_ledger` (for coverage check)
- Python stdlib: `hashlib`, `xml.etree.ElementTree`, `urllib.request`, `json`, `uuid`

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Sitemap polling fails on large sitemaps | Set reasonable timeout (30s); log error and skip source |
| Queue grows faster than worker can consume | Already rate-limited by existing queue processor; watch respects daily caps |
| Scoring backfill runs on every restart | Track last backfill timestamp in score_store; run only once |
| Wizard state partially saved on crash | Each step POST is atomic to the JSON store; re-entering wizard resumes from completed steps |
| Equity-ledger data stale for coverage check | Fallback to history_store; document latency expectation |

## Success Criteria

- [ ] An operator can complete the wizard from `/wizard` with a sitemap URL + one channel bound under 5 minutes
- [ ] After wizard completion, the "System Active" badge appears on all pages
- [ ] New URLs added to manual target list appear in the queue within the polling interval
- [ ] Scoring records match published events 1:1 (reconciliation)
- [ ] All existing tests continue to pass
- [ ] No changes to `src/backlink_publisher/cli/` or `schema.py`
