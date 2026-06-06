"""The 4-state falsification-gate verdict contract (plan 2026-06-01-005, Unit 1).

Dependency-free by design (mirrors :mod:`backlink_publisher.recheck.verdicts`):
a gate engine builds a :class:`GateVerdict` from its measured evidence and the
CLI serializes it to one JSONL line on stdout, or curates it into the committed
``gate-verdicts.md`` ledger. The contract enforces the three discipline rules
the gates' whole value rests on:

* **No GO without confirmed evidence + a recorded threshold.** A gate that
  cannot confirm its premise returns ``INCONCLUSIVE``, never ``GO`` — and the
  *first* run per gate is a calibration pass (no threshold yet → ``INCONCLUSIVE``
  by construction). ``build_verdict`` coerces a premature GO/KILL down.
* **BLOCKED is Tier-2 only.** ``BLOCKED`` means "operator credentials
  unavailable" and is constructible only for a Tier-2 (credentialed) gate
  (G3/G4). A Tier-1 offline gate (G2/G5) that requests it is a programming error.
* **Untrusted remote strings are capped + escaped** before they can reach the
  committed Markdown ledger (a git-tracked decision surface an operator trusts).

Modelled deliberately as four states, not five: G5's "premise unverifiable by
re-fetch" saturation is a *terminal* ``INCONCLUSIVE`` (``terminal=True``), not a
new state — a singleton fifth state would be taxonomy for its own sake.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

# --- The four verdict states. ------------------------------------------------
GO: Final = "GO"
KILL: Final = "KILL"
INCONCLUSIVE: Final = "INCONCLUSIVE"
BLOCKED: Final = "BLOCKED"

#: Every verdict a gate can emit. A string outside this set is a closed-vocabulary
#: violation (``build_verdict`` raises) — never silently passed through.
VERDICTS: Final[frozenset[str]] = frozenset({GO, KILL, INCONCLUSIVE, BLOCKED})

#: Verdicts that require a confirmed evidence sample AND a recorded threshold to
#: reach. Requesting either on the first (calibration) run, or with an empty
#: sample, coerces to ``INCONCLUSIVE``.
_THRESHOLD_GATED: Final[frozenset[str]] = frozenset({GO, KILL})

#: Gate tiers. Tier-2 gates depend on operator-external credentials (GA4/GSC) and
#: are the only gates that may return ``BLOCKED``. Tier-1 gates are cheap offline
#: probes that can always at least measure (so they never legitimately "block").
TIER2_GATES: Final[frozenset[str]] = frozenset({"g3", "g4"})

#: Default cap for any single untrusted remote-derived evidence string, mirroring
#: the preflight ``X-Robots-Tag`` discipline (``_X_ROBOTS_MAX_LEN``).
EVIDENCE_MAX_LEN: Final = 256


def gate_tier(gate: str) -> int:
    """Return 2 for a credentialed gate (G3/G4), else 1 (offline probe)."""
    return 2 if gate.lower() in TIER2_GATES else 1


def cap_untrusted(value: object, *, limit: int = EVIDENCE_MAX_LEN) -> str:
    """Render an untrusted remote-derived value as a single safe ledger cell.

    Caps length and neutralises the characters that could break a Markdown table
    row or inject markup into the committed ``gate-verdicts.md`` decision surface:
    pipes, backticks, angle brackets, backslashes, and any control/newline char.
    A bad remote string becomes inert text, never a row-break or an injection.
    """
    text = "" if value is None else str(value)
    # Collapse every control char / newline / tab to a single space first.
    cleaned = "".join(" " if (ch < " " or ch == "\x7f") else ch for ch in text)
    for bad, repl in (("|", "\\|"), ("`", "'"), ("<", "&lt;"), (">", "&gt;")):
        cleaned = cleaned.replace(bad, repl)
    cleaned = cleaned.strip()
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1] + "…"
    return cleaned


@dataclass(frozen=True, slots=True)
class GateVerdict:
    """One gate's immutable verdict + its evidence sample.

    Build via :func:`build_verdict` (which applies the discipline rules) rather
    than constructing directly, so the "no GO without evidence", "BLOCKED is
    Tier-2 only", and untrusted-escaping invariants always hold.
    """

    gate: str
    tier: int
    state: str
    sample_n: int
    rate: float | None
    note: str
    evidence: tuple[str, ...] = field(default_factory=tuple)
    #: True when a Tier-1 gate has reached a *terminal* INCONCLUSIVE it should not
    #: keep resampling (e.g. G5 "premise unverifiable by re-fetch"). Advisory; the
    #: state is still ``INCONCLUSIVE``.
    terminal: bool = False

    def to_jsonl_dict(self) -> dict[str, object]:
        """The machine-readable verdict envelope emitted on stdout (one JSONL line)."""
        return {
            "gate": self.gate,
            "tier": self.tier,
            "verdict": self.state,
            "sample_n": self.sample_n,
            "rate": self.rate,
            "terminal": self.terminal,
            "note": self.note,
            "evidence": list(self.evidence),
        }

    def to_ledger_row(self, *, premise: str, date: str, downstream: str) -> str:
        """A single pre-escaped Markdown table row for ``gate-verdicts.md``.

        Columns: ``gate | tier | premise | verdict | rate/evidence | sample-n |
        date | downstream-blocked``. Every dynamic cell is run through
        :func:`cap_untrusted` so a hostile remote evidence string cannot break
        the table or inject markup.
        """
        rate_cell = "—" if self.rate is None else f"{self.rate:.2%}"
        evidence_cell = "; ".join(self.evidence) if self.evidence else self.note
        cells = [
            cap_untrusted(self.gate),
            f"T{self.tier}",
            cap_untrusted(premise),
            f"{self.state}{' (terminal)' if self.terminal else ''}",
            cap_untrusted(f"{rate_cell} {evidence_cell}".strip()),
            str(self.sample_n),
            cap_untrusted(date),
            cap_untrusted(downstream),
        ]
        return "| " + " | ".join(cells) + " |"


def build_verdict(
    gate: str,
    requested_state: str,
    *,
    sample_n: int,
    confirmed: bool,
    threshold_set: bool,
    rate: float | None = None,
    note: str = "",
    evidence: tuple[str, ...] = (),
    terminal: bool = False,
) -> GateVerdict:
    """Construct a :class:`GateVerdict`, applying the three discipline rules.

    Args:
        gate: short gate id (``"g2"``/``"g3"``/``"g5"``…); decides the tier.
        requested_state: the verdict the engine *wants* to emit. Must be in
            :data:`VERDICTS`.
        sample_n: how many items the probe actually measured.
        confirmed: whether the measured sample actually confirms the premise
            (e.g. enough readable pages, a non-empty referral signal). A GO/KILL
            requested with ``confirmed=False`` is coerced to ``INCONCLUSIVE``.
        threshold_set: whether a calibrated GO/KILL threshold has been recorded.
            On the first (calibration) run this is ``False`` → GO/KILL is coerced
            to ``INCONCLUSIVE`` regardless of the measured rate.
        rate / note / evidence / terminal: passed through (evidence is escaped).

    Raises:
        ValueError: if ``requested_state`` is outside the closed vocabulary, or
            ``BLOCKED`` is requested for a Tier-1 gate.
    """
    if requested_state not in VERDICTS:
        raise ValueError(
            f"unknown gate verdict {requested_state!r}; expected one of {sorted(VERDICTS)}"
        )
    tier = gate_tier(gate)
    if requested_state == BLOCKED and tier != 2:
        raise ValueError(
            f"BLOCKED is Tier-2 only; gate {gate!r} is Tier-{tier} (offline) and "
            "must measure rather than block"
        )

    state = requested_state
    # Discipline rule: GO/KILL need a confirmed sample AND a recorded threshold.
    # The first run has no threshold → calibration → INCONCLUSIVE.
    if state in _THRESHOLD_GATED and (sample_n <= 0 or not confirmed or not threshold_set):
        state = INCONCLUSIVE

    return GateVerdict(
        gate=gate,
        tier=tier,
        state=state,
        sample_n=sample_n,
        rate=rate,
        note=note,
        evidence=tuple(cap_untrusted(e) for e in evidence),
        terminal=terminal,
    )
