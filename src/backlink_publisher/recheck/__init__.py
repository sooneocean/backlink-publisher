"""Post-publish backlink survival re-verification (Plan 2026-05-29-004).

The ``recheck-backlinks`` CLI re-verifies previously-published backlinks for
liveness, dofollow drift, and link/anchor tampering, then emits a
``link.rechecked`` lifecycle event time series to events.db. Decoupled from the
plan-007 history_store->events.db migration: this package writes its own event
kind and derives decay counts from that time series only — it does not touch
history_store or the articles projection columns.

Modules:

* ``verdicts``    — the 5-verdict taxonomy + the two load-bearing category sets.
* ``probe``       — the single shared liveness primitive + per-link verdict.
* ``selection``   — age-based candidate selection from ``publish.confirmed``.
* ``events_io``   — emit ``link.rechecked`` events + derive decay counts.
"""
