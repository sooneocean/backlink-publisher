"""The single shared liveness primitive + the CLI-facing per-link probe.

``probe_liveness`` is the one liveness engine used by BOTH the ``recheck-backlinks``
CLI and the WebUI manual recheck (``webui_app/services/recheck.py`` routes its
``verify_fn`` here) — so the two surfaces can never give contradictory liveness
judgments about the same URL (Plan 2026-05-29-004 U2 / origin R1).

It wraps :func:`inspect_target_anchor` (SSRF-guarded preflight opener; reads the
target anchor's own ``rel``) and maps the outcome onto the 5-verdict taxonomy.
Never raises.

Verdict mapping:

* page not readable + reason ``http_404``/``http_410`` -> ``host_gone`` (deterministic dead)
* page not readable, any other reason (5xx, 403, 429, timeout, network, ssrf, empty) -> ``probe_error``
  (transient / anti-bot / indeterminate — NOT a death; never trips --fail-on-dead, never advances the cursor)
* page readable, no target_url to inspect -> ``alive`` (liveness only)
* page readable, target anchor absent -> ``link_stripped`` (deterministic dead)
* page readable, target anchor present + nofollow + channel is dofollow -> ``dofollow_lost`` (drift)
* otherwise -> ``alive``

Anchor-text drift is best-effort: only evaluated when a baseline anchor is
supplied AND the live anchor text could be captured; it is recorded as metadata
(``anchor_drift`` / ``reason``) and never changes the liveness verdict (R3).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from backlink_publisher.content._preflight_fetch import fetch_target
from backlink_publisher.publishing.adapters import link_attr_verifier
from backlink_publisher.publishing.registry import dofollow_status
from backlink_publisher.recheck import indexability, verdicts

log = logging.getLogger(__name__)

#: Non-200 HTTP reasons that are deterministic "the page is gone" signals.
#: Everything else (5xx, 403/429 anti-bot, timeouts, network, ssrf) is treated
#: as indeterminate (``probe_error``) so a transient blip never false-positives
#: a dead link (anti-bot windowed-budget caution, medium-liveness-probe spike).
_DETERMINISTIC_DEAD_REASONS = frozenset({"http_404", "http_410"})


def _probe_indexability(
    live_url: str,
    timeout: float,
    fetch_fn: Callable[..., Any],
) -> tuple[str, str | None]:
    """Second-fetch ``live_url`` and classify its source-page indexability.

    Reads the SAME ``PreflightFacts.noindex`` fact that
    :func:`cli.canary_targets._classify` consumes (via ``fetch_target``) — recheck
    and canary are a single source by construction, no parallel detector to drift.
    Never raises; any fetch failure ⇒ ``unknown`` (fail-open). The tri-state map
    itself lives in :func:`recheck.indexability.classify_indexability`.
    """
    fetch = fetch_fn or fetch_target
    try:
        facts = fetch(live_url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — never-raise; indeterminate => unknown
        log.warning("indexability probe error url=%s: %s", live_url, exc)
        return indexability.UNKNOWN, None
    return indexability.classify_indexability(facts)


def _norm(text: str) -> str:
    """Whitespace-collapsed, case-folded form for anchor-text comparison."""
    return " ".join(text.split()).casefold()


def probe_liveness(
    live_url: str,
    target_url: str,
    *,
    platform: str | None = None,
    baseline_anchor: str | None = None,
    timeout: float = 10.0,
    inspect_fn: Callable[..., dict[str, Any]] | None = None,
    fetch_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Re-verify a single backlink. Returns a verdict dict; never raises.

    Return shape::

        {
            "verdict": str,                  # one of recheck.verdicts.VERDICTS
            "reason": str | None,            # taxonomy / drift note
            "target_rel": str | None,
            "expected_nofollow": bool,       # nofollow that is the channel's norm
            "anchor_baseline_missing": bool, # no baseline to compare anchor text
            "anchor_drift": bool,            # anchor text changed vs baseline
            "indexability": str,             # ok / blocked / unknown (orthogonal)
            "indexability_reason": str|None, # closed vocab when blocked
        }

    ``indexability`` is orthogonal contract-drift metadata (like ``anchor_drift``)
    read via a second ``fetch_target`` call for any page we successfully read; it
    NEVER changes the liveness verdict. ``fetch_fn`` is injectable for tests.
    """
    inspect = inspect_fn or link_attr_verifier.inspect_target_anchor
    out: dict[str, Any] = {
        "verdict": None,
        "reason": None,
        "target_rel": None,
        "expected_nofollow": False,
        "anchor_baseline_missing": False,
        "anchor_drift": False,
        "indexability": indexability.UNKNOWN,
        "indexability_reason": None,
    }
    target = (target_url or "").strip()

    try:
        res = inspect(
            live_url,
            target,
            timeout=timeout,
            capture_anchor_text=bool(baseline_anchor),
        )
    except Exception as exc:  # noqa: BLE001 — never-raise; structured probe_error
        log.warning("recheck probe error url=%s: %s", live_url, exc)
        out["verdict"] = verdicts.PROBE_ERROR
        out["reason"] = f"probe_exception:{exc.__class__.__name__}"
        return out

    reason = res.get("reason")

    if not res.get("page_readable"):
        # Unreadable/dead page: skip the second fetch entirely; indexability of a
        # page we never read stays the default ``unknown``.
        if reason in _DETERMINISTIC_DEAD_REASONS:
            out["verdict"] = verdicts.HOST_GONE
        else:
            out["verdict"] = verdicts.PROBE_ERROR
        out["reason"] = reason
        return out

    # Page was successfully read — compute the orthogonal indexability axis once,
    # before any verdict-specific early return, so every readable-page outcome
    # (alive / link_stripped / dofollow_lost) carries it. Never changes verdict.
    out["indexability"], out["indexability_reason"] = _probe_indexability(
        live_url, timeout, fetch_fn
    )

    if not target:
        # No backlink target to inspect — can only confirm the page is live.
        out["verdict"] = verdicts.ALIVE
        return out

    if not res.get("target_anchor_found"):
        # A malformed/uncanonicalizable target is indeterminate, not a confirmed
        # stripped link — never let it count as deterministic dead (correctness).
        if reason == "target_uncanonicalizable":
            out["verdict"] = verdicts.PROBE_ERROR
            out["reason"] = reason
        else:
            out["verdict"] = verdicts.LINK_STRIPPED
            out["reason"] = "target_anchor_absent"
        return out

    out["target_rel"] = res.get("target_rel")

    # Anchor-text drift (best-effort; recorded, never changes the verdict — R3).
    if baseline_anchor:
        live_text = res.get("target_anchor_text")
        if live_text is not None and _norm(live_text) != _norm(baseline_anchor):
            out["anchor_drift"] = True
            out["reason"] = "anchor_text_changed"
    else:
        out["anchor_baseline_missing"] = True

    if res.get("target_is_nofollow"):
        # Only alarm when the channel is KNOWN dofollow; False/"uncertain"/None
        # means expected-nofollow or unverifiable — not drift (D6).
        if (dofollow_status(platform) if platform else None) is True:
            out["verdict"] = verdicts.DOFOLLOW_LOST
            out["reason"] = "rel_nofollow"
            return out
        out["expected_nofollow"] = True

    out["verdict"] = verdicts.ALIVE

    # Wave 4 zero-auth MVP: best-effort rendered-link outcome orthogonal to the
    # liveness verdict. Runs only when both URLs are available (no-op for
    # unreachable pages / missing targets). Never changes the verdict.
    if live_url and target and res.get("page_readable"):
        try:
            from backlink_publisher.publishing._verify_html import verify_rendered_link
            vr = verify_rendered_link(published_url=live_url, target_url=target)
            if vr.effective:
                out["backlink_outcome"] = "effective_backlink"
            else:
                out["backlink_outcome"] = "published_but_ineffective"
        except Exception:  # noqa: BLE001 — best-effort; never fails the recheck
            out["backlink_outcome"] = "failed"
    return out


def recheck_link(
    record: dict[str, Any],
    *,
    probe: bool,
    timeout: float = 10.0,
    inspect_fn: Callable[..., dict[str, Any]] | None = None,
    fetch_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Produce a recheck result for one candidate ``record``.

    ``record`` carries ``live_url``, ``target_url``, ``host``, ``article_id``,
    ``platform``, optional ``baseline_anchor`` and ``published_age_days``.

    When ``probe`` is False this is a **zero-network dry preview**: it returns
    the candidate's identity + ``will_probe: True`` without any HTTP call. When
    ``probe`` is True it runs :func:`probe_liveness` and merges the verdict in.
    """
    base = {
        "live_url": record.get("live_url"),
        "target_url": (record.get("target_url") or None),
        "host": record.get("host"),
        "article_id": record.get("article_id"),
        "platform": record.get("platform"),
    }
    if not probe:
        return {
            **base,
            "will_probe": True,
            "published_age_days": record.get("published_age_days"),
        }
    verdict = probe_liveness(
        record.get("live_url") or "",
        record.get("target_url") or "",
        platform=record.get("platform"),
        baseline_anchor=record.get("baseline_anchor"),
        timeout=timeout,
        inspect_fn=inspect_fn,
        fetch_fn=fetch_fn,
    )
    return {**base, **verdict}
