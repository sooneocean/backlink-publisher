"""Operator remediation actions for backlink decay (Plan 2026-06-07-001 Phase A).

Provides the ``remediation-queue`` CLI and WebUI remediation panel with the
ability to ack / resolve / snooze detected dead backlinks, closing the loop
between observability (``recheck-backlinks``) and operator action.

Modules:

* ``actions`` — action constants + unresolved view derivation from events.db.
* ``events_io`` — emit and query ``remediation.event`` rows in events.db.
"""