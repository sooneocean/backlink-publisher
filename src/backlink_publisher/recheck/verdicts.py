"""The 5-verdict recheck taxonomy and its two load-bearing category sets.

Dependency-free by design (mirrors ``events.kinds``): importable by the probe,
selection, events_io, and CLI modules without dragging in sqlite or Flask.

The verdicts map a re-verification outcome onto exactly one of five strings,
which travel in ``link.rechecked`` payloads as ``payload["verdict"]``:

* ``alive``         — page readable, target backlink present and dofollow.
* ``host_gone``     — page not readable (non-200 / fetch error). Deterministic dead.
* ``link_stripped`` — page readable (200) but the target backlink is absent.
                      Deterministic dead.
* ``dofollow_lost`` — backlink present but its own ``rel`` now strips weight
                      (nofollow/ugc/sponsored). An *advisory* contract-drift
                      signal, NOT a death — never trips ``--fail-on-dead`` and
                      cross-checked against the channel's manifest dofollow truth.
* ``probe_error``   — timeout / invalid URL / unexpected exception. Non-fatal,
                      indeterminate; does NOT advance the age cursor (so a
                      persistently-unreachable link stays selectable).

Two category sets carry all v1 behavior — deliberately only two (a singleton
``DEGRADED``/``UNKNOWN`` set would be taxonomy for its own sake; dofollow_lost
is "not in DETERMINISTIC_DEAD" and probe_error is "not in DEFINITIVE"):

* ``DETERMINISTIC_DEAD`` — the ``--fail-on-dead`` trigger set (R13).
* ``DEFINITIVE``         — verdicts that advance the age cursor (everything
                           except ``probe_error``); excluding probe_error keeps
                           unreachable links eligible for re-probe (D3/D5).
"""

from __future__ import annotations

from typing import Final

ALIVE: Final = "alive"
HOST_GONE: Final = "host_gone"
LINK_STRIPPED: Final = "link_stripped"
DOFOLLOW_LOST: Final = "dofollow_lost"
PROBE_ERROR: Final = "probe_error"

#: Every verdict the recheck pipeline can produce.
VERDICTS: Final[frozenset[str]] = frozenset(
    {ALIVE, HOST_GONE, LINK_STRIPPED, DOFOLLOW_LOST, PROBE_ERROR}
)

#: Verdicts that count as a confirmed dead backlink — the ``--fail-on-dead``
#: trigger set (R13). dofollow_lost is degradation, not death; probe_error is
#: indeterminate — neither belongs here.
DETERMINISTIC_DEAD: Final[frozenset[str]] = frozenset({HOST_GONE, LINK_STRIPPED})

#: Verdicts that advance the age cursor (``last_definitive_at``). probe_error is
#: excluded so a persistently-unreachable link is not "refreshed" out of
#: re-selection — the load-bearing protection against silent false-negatives (D3).
DEFINITIVE: Final[frozenset[str]] = frozenset(
    {ALIVE, HOST_GONE, LINK_STRIPPED, DOFOLLOW_LOST}
)


def is_deterministic_dead(verdict: str) -> bool:
    """True if ``verdict`` is a confirmed dead backlink (trips ``--fail-on-dead``)."""
    return verdict in DETERMINISTIC_DEAD


def advances_age_cursor(verdict: str) -> bool:
    """True if ``verdict`` should advance ``last_definitive_at``.

    Every verdict except ``probe_error`` is definitive. An unknown string also
    returns False (treated as non-definitive) so a future verdict can't silently
    advance the cursor before it is added to ``DEFINITIVE``.
    """
    return verdict in DEFINITIVE
