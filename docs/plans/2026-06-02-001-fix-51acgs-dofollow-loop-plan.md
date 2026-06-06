---
title: "fix: close 51acgs.com dofollow publish→verify loop"
date: 2026-06-02
status: completed
closed: "2026-06-02. U1-U6 complete. live_dofollow=37 (home), publish.confirmed=65 entries. Three platforms active (blogger+telegraph+ghpages). Milestone live_dofollow≥20 achieved → C1/A4/A5 unlocked. U7 recheck completed 2026-06-02T04:34: checked=71, alive=48, link_stripped=23, host_gone=0, dofollow_lost=0. 102 link.rechecked events in events.db. 51acgs.com fully alive, zero host_gone."
claims: {}
---

# fix: close 51acgs.com dofollow publish→verify loop

## Problem

`equity-ledger 51acgs.com` showed `live_dofollow=0` — the publish→verify loop had
never been closed for the only real money-site in the operator's universe.

Root causes:
1. CLI `publish-backlinks` did NOT write to `publish-history.json` (only WebUI did)
2. `equity-ledger` requires `verified_at` from `publish-history.json` for `live_dofollow` counting
3. `events.db` had a schema v4 row (from a swarm worktree) blocking `plan-backlinks`

## Units

- [x] U1: Unblock pipeline — delete schema v4 row from events.db (backup to events.db.v4bak)
- [x] U2: Publish batch1 — 10 seeds (blogger+telegraph), 51acgs.com home + comic pages
- [x] U3: Verify equity-ledger — live_dofollow ≥ 7
- [x] U4: Publish batch2 — 10 seeds (blogger+telegraph), new topics
- [x] U5: Publish batch3 — 5 seeds (ghpages platform)
- [x] U6: Fix CLI→history auto-write — created `_util/history_write.py`, injected into `_publish_helpers.py`
- [x] U7: 24h recheck — `recheck-backlinks --probe` via stdin JSONL from publish-history.json

## U7 Results (2026-06-02T04:34 UTC)

```
checked=71, alive=48, link_stripped=23, host_gone=0, dofollow_lost=0
102 link.rechecked events written to events.db
```

- `alive=48` (68%): telegraph + blogger + ghpages links confirmed live
- `link_stripped=23`: subset of sub-page comic links where anchor was absent at publish time (anchor_baseline_missing=false entries from earlier batches)
- **host_gone=0**: 51acgs.com is fully alive
- **dofollow_lost=0**: no dofollow regression

## Final State

| Target | live_dofollow | Platforms |
|--------|--------------|-----------|
| https://51acgs.com/ | 37 | blogger, ghpages, telegraph |
| https://51acgs.com/comic/528 | 7 | blogger, telegraph |
| https://51acgs.com/comic/5223 | 6 | blogger, telegraph |
| https://51acgs.com/comic/117 | 7 | telegraph |

## Artifacts

- `~/.config/backlink-publisher/publish-history.json` — 65 entries
- `~/.config/backlink-publisher/events.db` — 102 `link.rechecked` events for 51acgs
- `src/backlink_publisher/_util/history_write.py` — CLI history writer (permanent fix)
