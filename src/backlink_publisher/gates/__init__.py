"""Phase-0 falsification gates (plan 2026-06-01-005).

Cheap, read-only premise probes (G2/G3/G5) that each emit a single
``GO``/``KILL``/``INCONCLUSIVE``/``BLOCKED`` verdict + an evidence sample, so a
downstream "build the machine" brainstorm cannot enter ``/ce:plan`` before its
premise is validated (governance rule R16). The gates build no pipeline and
write no store — their only output is a verdict on stdout and a curated row in
``docs/ideation/gate-verdicts.md``.

The shared verdict contract lives in :mod:`backlink_publisher.gates.verdict`.
"""

from __future__ import annotations
