# Dofollow Platform Shortlist

The authoritative list of publishing channels that pass PageRank (dofollow), for
operators building a link-equity strategy. Derived live from the adapter registry —
a channel is **confirmed dofollow** only when `dofollow_status(p) is True`, which
happens after an OUR-pipeline canary confirms our own placed link renders dofollow
(not merely a third-party spot-check). Channels at `"uncertain"` are **not** here yet.

Query it yourself:

```python
import backlink_publisher.publishing.adapters  # populate the registry
from backlink_publisher.publishing.registry import registered_platforms, dofollow_status
confirmed = [p for p in registered_platforms() if dofollow_status(p) is True]
```

## Confirmed dofollow (`dofollow=True`)

| Platform | Mechanism | Notes |
|---|---|---|
| `blogger` | Blogger API (OAuth) | High DA, operator-owned blog |
| `ghpages` | GitHub Pages (Contents API) | Operator-owned static HTML, DA 100 |
| `medium` | API / browser fallback | High DA |
| `telegraph` | Telegraph API | Instant pages |
| `velog` | velog GraphQL (cookies) | KR dev platform |

## Canary-pending (`dofollow="uncertain"` — NOT yet equity-passing)

These have a plausible dofollow signal (third-party check or operator-controlled rel)
but have **not** cleared an OUR-pipeline canary, so they are excluded from the evergreen
`canary-targets` cohort and from the confirmed list above. The flip-or-kill deadline is
tracked in [`docs/discovery/canary-pending.md`](../discovery/canary-pending.md) and
enforced by `tests/test_canary_pending_deadline.py`.

| Platform | Signal | Added |
|---|---|---|
| `hackmd` | 3rd-party: 188 anchors / 0 nofollow, index,follow | Wave 1 (2026-06-01) |
| `mataroa` | 3rd-party: 6/0 nofollow, `site:` fresh | Wave 1 (2026-06-01) |
| `gitlabpages` | rel operator-controlled; `*.gitlab.io` index partial + async publish | Wave 1 (2026-06-01) |
| `hashnode`, `substack`, `hatena`, `rentry`, `txtfyi`, `wordpresscom`, `writeas` | various 3rd-party checks | pre-Wave 1 |

When a channel's canary confirms, flip its `register()` call to `dofollow=True`, drop
the `rationale=`/`referral_value=` kwargs, and move it to the confirmed table above.

## Out of scope (confirmed nofollow)

`devto`, `linkedin`, `livejournal`, `mastodon`, `notion`, `tumblr` are registered
`dofollow=False` — kept for referral/entity value, not equity. See
`publishing/adapters/_nofollow_rationales.py`.
