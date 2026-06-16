---
kind: canary-pending-tracker
enforced_by: tests/test_canary_pending_deadline.py
---

# Canary-Pending Channels — flip-or-kill tracker

Channels registered `dofollow="uncertain"` are **excluded** from the evergreen
`canary-targets` cohort (which is `dofollow_status(p) is True`). They graduate to
`dofollow=True` only after an **OUR-pipeline** canary confirms our own placed link
renders dofollow on our own published post (for GitLab Pages: additionally that the
`*.gitlab.io` page is `index,follow`).

This file is the **flip-or-kill** tracker. A test (`test_canary_pending_deadline.py`)
parses the table and **fails CI** once a channel's `deadline` passes while it is still
registered `"uncertain"` — so the flip cannot silently rot the way 8+ prior
`"uncertain"` channels did. To clear a row: run the canary, then either flip the
`register()` call to `dofollow=True` (set status `flipped`) or retire the channel
(set status `retired`).

## Close-out procedure (per channel)

1. Bind a dedicated throwaway account/token (`0o600` via the bind flow).
2. Publish an OUR canary post through the pipeline.
3. Run `verify_link_attributes` / `inspect_target_anchor` on the published URL.
4. If our placed link is dofollow (GitLab: AND the page is `index,follow`):
   flip `register(...)` from `dofollow="uncertain"` to `dofollow=True`, drop the
   `rationale=`/`referral_value=` kwargs, add a `[canary.<platform>]` config entry,
   and set the row's status to `flipped`.
5. Otherwise retire (record in `docs/notes/retired-platforms/`) and set `retired`.

<!-- canary-pending:begin -->
| platform | registered | deadline | status |
|---|---|---|---|
| hackmd | 2026-06-01 | 2026-07-31 | pending |
| mataroa | 2026-06-01 | 2026-07-31 | pending |
| gitlabpages | 2026-06-01 | 2026-07-31 | pending |
<!-- canary-pending:end -->
