"""G3 — referer render-path audit + GA4 referral intake (Tier-2). Plan 005 Unit 3.

Two halves answer one premise — *does any channel ever deliver a real referral
session, and do our render paths even preserve ``referer``?*

* **Static render-path audit (in-repo, deterministic, no network).** Enumerates
  every operator anchor render path and measures whether it strips ``referer``
  by actually rendering through the real :func:`_format_anchor_html` and reading
  the emitted ``rel``. **This half alone can KILL:** if the majority of paths
  strip ``referer``, attribution is structurally blind regardless of GA4.
* **GA4 referral intake (operator-external).** Zero GA4/GSC code lives in-repo;
  the operator runs ``gsearch-radar`` and supplies a strictly-typed referral
  count + window. It is a GO-*confirmation*, never a verdict prerequisite — so
  G3 never stalls forever waiting on external evidence. If Tier-2 credentials are
  unavailable, the gate returns ``BLOCKED`` (parked), not INCONCLUSIVE.
"""

from __future__ import annotations

from dataclasses import dataclass

from backlink_publisher._util.markdown import _format_anchor_html
from backlink_publisher.gates import verdict as gv

#: Ground-truth inventory of operator anchor render paths and the ``rel`` each
#: passes to ``_format_anchor_html`` — verified against the call sites in
#: ``_util/markdown.py`` (render_zh_short_article, default rel) and
#: ``content/themed_gen.py`` (work-themed, ``rel="noopener"``). Add a row when a
#: new render path is introduced (don't undercount — the audit's value is total
#: coverage of where operator anchors are emitted).
_RENDER_PATHS: tuple[tuple[str, str], ...] = (
    ("zh_short_default", "noopener noreferrer"),  # render_zh_short_article
    ("work_themed", "noopener"),                  # themed_gen main/list/work
)


@dataclass(frozen=True, slots=True)
class RenderPathFact:
    name: str
    rel: str
    strips_referer: bool


@dataclass(frozen=True, slots=True)
class ReferralEvidence:
    """Operator-supplied GA4 referral signal for the owned site (strictly typed)."""

    sessions: int
    window: str


def _strips_referer(rel: str) -> bool:
    """Measure (not assume) whether a render path strips ``referer``.

    Renders a probe anchor through the real ``_format_anchor_html`` with this
    path's ``rel`` and checks the emitted attribute — so the audit tracks the
    actual renderer, not a hard-coded claim that could drift.
    """
    html = _format_anchor_html("https://probe.example/", "anchor", rel=rel)
    return "noreferrer" in html


def audit_render_paths() -> list[RenderPathFact]:
    """Measure each render path's referer policy by rendering through the real builder."""
    return [
        RenderPathFact(name=name, rel=rel, strips_referer=_strips_referer(rel))
        for name, rel in _RENDER_PATHS
    ]


def assess_g3(
    *,
    referral: ReferralEvidence | None,
    credentials_available: bool,
    strip_threshold: float | None = None,
) -> gv.GateVerdict:
    """G3 verdict. ``strip_threshold`` ``None`` = calibration run → INCONCLUSIVE.

    Decision order (the static audit can terminate on its own):

    1. majority of paths strip ``referer`` (``strip_fraction >= strip_threshold``)
       → **KILL** — attribution structurally blind regardless of GA4.
    2. else Tier-2 credentials unavailable → **BLOCKED** (Program B parked).
    3. else no referral evidence supplied → **INCONCLUSIVE** (operator must run
       ``gsearch-radar``).
    4. else ``sessions > 0`` → **GO** (a real referral exists and paths preserve it).
    5. else (``sessions == 0``) → **KILL** (no referral despite preservable paths).
    """
    audit = audit_render_paths()
    total = len(audit)
    stripped = sum(1 for p in audit if p.strips_referer)
    strip_fraction = (stripped / total) if total else 0.0
    threshold_set = strip_threshold is not None
    _preserved = [p.name for p in audit if not p.strips_referer]
    evidence = (
        f"render_paths={total}",
        f"strip_referer={stripped}/{total}",
        "preserving=" + (",".join(_preserved) if _preserved else "none"),
        f"referral_sessions={referral.sessions}" if referral else "referral_evidence=absent",
    )
    note = "referer render-path audit + GA4 referral"

    def _v(state: str, *, confirmed: bool) -> gv.GateVerdict:
        return gv.build_verdict(
            "g3", state, sample_n=total, confirmed=confirmed,
            threshold_set=threshold_set, rate=strip_fraction, note=note, evidence=evidence,
        )

    # 1. Static audit alone can KILL (majority strip → structurally blind).
    if threshold_set and strip_fraction >= strip_threshold:
        return _v(gv.KILL, confirmed=True)
    # 2. Tier-2 credentials gate (parked, not deferred). BLOCKED bypasses the
    #    threshold coercion in build_verdict (it is not threshold-gated).
    if not credentials_available:
        return _v(gv.BLOCKED, confirmed=False)
    # 3. Paths can preserve referer → need the referral signal to decide.
    if referral is None:
        return _v(gv.INCONCLUSIVE, confirmed=False)
    if referral.sessions > 0:
        return _v(gv.GO, confirmed=True)
    return _v(gv.KILL, confirmed=True)
