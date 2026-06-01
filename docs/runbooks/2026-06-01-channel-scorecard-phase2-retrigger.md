# Channel Scorecard — Phase 2 (GA4/GSC attribution) re-trigger gate

> Operations runbook for `docs/plans/2026-06-01-005-feat-per-channel-value-scorecard-plan.md`.
> Phase 1 (the read-only per-channel scorecard MVP) shipped. Phase 2 (GA4 referral +
> GSC discovery attribution + the HMAC-UTM tagging) is **deferred** and must not be
> built until this gate passes. This doc is how you decide.

## Why Phase 2 is parked

A Wave-0 read-only measurement (2026-06-01) showed there is **no corpus of real
owned-target placements to attribute yet**: a single owned money site, ~0.6% of
placements pointing at it, and the event store dominated by `example.com` test data.
Building the GA4/GSC pipelines now would attribute ~2 rows of real data, and the
HMAC-UTM secret baked into published URLs is **irreversible** (non-rotating by design).
So Phase 2 waits for real volume. See the plan's "Wave-0 Measurement Result" section.

## GO threshold (operator-tunable)

Build Phase 2 **only when all three hold**:

1. **Owned-target placement volume** — at least **~30 real, live placements** pointing
   at owned target sites (not `example.com`/test), and
2. **Channel spread** — those placements span **at least a few distinct channels**
   (a single-channel corpus can't rank channels), and
3. **GA4 referral is plausibly non-zero** — a one-off manual GA4 check on an owned
   property shows the channels actually drive *some* referral sessions (if real clicks
   are flat-zero, the GA4 axis measures nothing — discovery latency is the better
   signal, and even that argues for the GSC half only).

Numbers are a floor, not a promise — tune from the measured distribution.

## The re-trigger measurement (read-only, ~5 lines)

Run this against the operator's live config + `events.db`. It is **read-only**, never
prints third-party domains, and needs no credentials. Re-run it monthly (or after a
publishing push) and read the two headline numbers.

```python
import tomllib, json, sqlite3
from urllib.parse import urlsplit
from collections import Counter
CFG = "<config-dir>"  # default: ~/.config/backlink-publisher  (or $BACKLINK_PUBLISHER_CONFIG_DIR)

def reg(u):  # registrable domain of a URL/host
    h = (urlsplit(u if "//" in str(u) else "//" + str(u)).hostname or "").lower()
    return ".".join(h.split(".")[-2:]) if h.count(".") >= 1 else h

cfg = tomllib.load(open(f"{CFG}/config.toml", "rb"))
owned = {reg(k) for sec in ("targets", "sites") for k in cfg.get(sec, {})}
owned.discard("")

con = sqlite3.connect(f"file:{CFG}/events.db?mode=ro", uri=True)
# channel per live_url (authoritative platform lives in the confirmed-event payload)
plat = {a: p for a, p in con.execute(
    "SELECT a.live_url, json_extract(e.payload_json,'$.platform') FROM events e "
    "JOIN articles a ON e.article_id=a.article_id "
    "WHERE e.kind IN ('publish.confirmed','publish.unverified') "
    "AND a.live_url IS NOT NULL AND json_extract(e.payload_json,'$.platform') IS NOT NULL")}
owned_n = third = 0
chans = Counter()
for (tj,) in con.execute("SELECT target_urls_json FROM articles"):
    try: t = json.loads(tj or "[]")
    except Exception: t = []
    hosts = {reg(u) for u in (t if isinstance(t, list) else []) if isinstance(u, str)}
    if not hosts: continue
    if hosts & owned:
        owned_n += 1
        for u, ch in plat.items():
            if reg(u) in owned: chans[ch] += 1   # crude per-channel owned count
    else:
        third += 1

denom = owned_n + third
print(f"owned-target placements: {owned_n} / {denom} = {100*owned_n/denom:.1f}%  (owned sites: {len(owned)})")
print(f"channels with owned placements: {sorted(chans)}")
```

**Verdict:** below the GO threshold → stay DESCOPE (Phase 1 scorecard already covers
keep/prune on declared + liveness). At or above it **and** GA4 referral is non-zero →
proceed to Phase 2 (plan Units 1-3, 5, 6, 7), starting with the credential/config
foundation and the `attribute-traffic` GA4 verb. **Do not** ship the HMAC-UTM tagging
(Unit 5) until you have committed to building the GA4 read side — the secret is
permanent once it reaches published URLs.

## See also

- Plan: `docs/plans/2026-06-01-005-feat-per-channel-value-scorecard-plan.md`
- Phase 1 verb: `channel-scorecard` (and the `/ce:health` card)
- Sibling read-only advisories: `equity-ledger`, `cull-channels`, `canary-targets`
