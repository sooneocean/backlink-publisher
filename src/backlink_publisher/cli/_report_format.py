"""Formatting, aggregation, and alarm helpers for report-anchors.

All pure functions extracted from report_anchors.py so that main() stays
thin and each helper is independently importable / testable.
"""

from __future__ import annotations

import collections
import json
from typing import Any

from backlink_publisher.anchor.metrics import (
    _ALARM_SAMPLE_MIN_PER_TARGET,
    compute_window_metrics,
    detect_breaches,
    filter_window,
    group_by_target_url,
    resolve_thresholds,
)
from backlink_publisher.anchor.profile import (
    ProfileState,
    recent_degradation_rate,
    recent_type_counts,
)
from backlink_publisher.config import ANCHOR_TYPES, AnchorAlarmConfig
from backlink_publisher.publishing import registry

# ── module-level constants (re-exported so report_anchors.py shims cleanly) ──

# Exit code when anchor distribution alarm fires (see report_anchors.py).
_EXIT_CODE_ALARM: int = 6

# Alarm threshold for systemic LLM rejection / pool exhaustion.
_DEGRADATION_ALARM_PCT: float = 10.0

# Below this sample size deviation numbers are statistically meaningless.
_RELIABLE_SAMPLE_MIN: int = 50

# How many of the most-repeated anchor texts to show in the report.
_TOP_TEXTS_N: int = 20


# ── internal helper ──────────────────────────────────────────────────────────


def _domain_label(main_domain: str) -> str:
    """Return bare domain for fallback detection (strips scheme + trailing slash)."""
    return main_domain.rstrip("/").removeprefix("https://").removeprefix("http://")


# ── JSONL-aggregate path ─────────────────────────────────────────────────────


def _build_report(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate anchor stats per main_domain from payload JSONL rows."""
    stats: dict[str, dict[str, Any]] = {}

    for row in rows:
        main_domain = row.get("main_domain", "").rstrip("/")
        if not main_domain:
            continue
        links = row.get("links", [])
        if not isinstance(links, list):
            continue

        if main_domain not in stats:
            stats[main_domain] = {
                "total_articles": 0,
                "anchors": collections.Counter(),
                "fallback_count": 0,
            }

        entry = stats[main_domain]
        entry["total_articles"] += 1
        fallback_label = _domain_label(main_domain)
        article_has_fallback = False

        for link in links:
            if not isinstance(link, dict):
                continue
            if link.get("kind") not in ("main_domain", "target"):
                continue
            anchor = link.get("anchor", "")
            if not anchor:
                continue
            entry["anchors"][anchor] += 1
            if anchor == fallback_label:
                article_has_fallback = True

        if article_has_fallback:
            entry["fallback_count"] += 1

    return stats


def _markdown_table(
    stats: dict[str, dict[str, Any]],
    top_n: int,
    tier_summary: dict[str, Any] | None = None,
) -> str:
    header = "| target | articles | distinct anchors | fallback % | top anchors |"
    sep = "|---|---|---|---|---|"
    rows = [header, sep]

    for domain in sorted(stats):
        s = stats[domain]
        total = s["total_articles"]
        counter: collections.Counter = s["anchors"]
        distinct = len(counter)
        fallback_pct = (
            f"{100 * s['fallback_count'] / total:.0f}%" if total else "—"
        )
        top = ", ".join(
            f"{kw!r} ({cnt})" for kw, cnt in counter.most_common(top_n)
        )
        rows.append(f"| {domain} | {total} | {distinct} | {fallback_pct} | {top} |")

    table = "\n".join(rows)
    if tier_summary is not None:
        table += "\n" + _tier_markdown(tier_summary)
    return table


def _json_output(
    stats: dict[str, dict[str, Any]],
    tier_summary: dict[str, Any] | None = None,
) -> str:
    out: dict[str, Any] = {
        domain: {
            "total_articles": s["total_articles"],
            "anchors": dict(s["anchors"]),
            "fallback_count": s["fallback_count"],
        }
        for domain, s in sorted(stats.items())
    }
    # Tier breakdown is added under a reserved key (no main_domain is
    # literally "_dofollow_tiers") so the per-domain top-level contract
    # stays backward-compatible for existing consumers. Plan 2026-05-25-001
    # Unit 3 (R3): JSONL-path tier segmentation.
    if tier_summary is not None:
        out["_dofollow_tiers"] = tier_summary
    return json.dumps(out, ensure_ascii=False, indent=2)


# ── dofollow-tier segmentation (Plan 2026-05-25-001 Unit 3 / R3) ─────────────


def _resolve_row_tier(row: dict[str, Any]) -> tuple[str, str | None]:
    """Return ``(tier, referral_value)`` for a payload row.

    Prefers the ``metadata`` mark stamped by plan-backlinks (Unit 2);
    falls back to a live registry join on ``row["platform"]`` for rows
    that predate the mark. Rows with no resolvable platform/status fall
    into the ``"unknown"`` tier.

    Note the deliberate precedence: the plan-time mark wins over the live
    registry. This means the report reflects the tier **as classified
    when the row was planned**, not the registry's current state — so if
    a platform is re-classified between plan and report, the older mark
    persists. This is intentional (the report describes what was planned);
    it is not a live registry read. ``register()`` remains the only writer
    of tier state — this function reads, never stores.
    """
    meta = row.get("metadata") or {}
    tier = meta.get("dofollow_tier")
    referral = meta.get("referral_value")
    platform = row.get("platform")
    if tier is None:
        status = registry.dofollow_status(platform) if platform else None
        if status is True:
            tier = "dofollow"
        elif status in (False, "uncertain"):
            tier = "nofollow-signal"
        else:
            tier = "unknown"
    if referral is None and platform:
        referral = registry.referral_value(platform)
    return tier, referral


def _count_qualifying_anchors(row: dict[str, Any]) -> int:
    """Count main_domain/target links carrying an anchor (mirrors
    ``_build_report``'s qualifying-anchor rule)."""
    n = 0
    links = row.get("links", [])
    if not isinstance(links, list):
        return 0
    for link in links:
        if not isinstance(link, dict):
            continue
        if link.get("kind") in ("main_domain", "target") and link.get("anchor"):
            n += 1
    return n


def _build_tier_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate article + anchor counts per dofollow tier, with a
    referral high/low sub-split inside the nofollow-signal bucket."""
    def _bucket() -> dict[str, int]:
        return {"articles": 0, "anchors": 0}

    summary: dict[str, Any] = {
        "dofollow": _bucket(),
        "nofollow-signal": {
            **_bucket(),
            "referral": {"high": _bucket(), "low": _bucket(), "unclassified": _bucket()},
        },
        "unknown": _bucket(),
    }
    for row in rows:
        tier, referral = _resolve_row_tier(row)
        anchors = _count_qualifying_anchors(row)
        bucket = summary.get(tier, summary["unknown"])
        bucket["articles"] += 1
        bucket["anchors"] += anchors
        if tier == "nofollow-signal":
            key = referral if referral in ("high", "low") else "unclassified"
            rb = summary["nofollow-signal"]["referral"][key]
            rb["articles"] += 1
            rb["anchors"] += anchors
    return summary


def _tier_markdown(tier_summary: dict[str, Any]) -> str:
    """Render the tier breakdown as a markdown section appended after the
    per-domain table."""
    total_articles = sum(
        tier_summary[t]["articles"] for t in ("dofollow", "nofollow-signal", "unknown")
    )

    def _pct(n: int) -> str:
        return f"{100 * n / total_articles:.0f}%" if total_articles else "—"

    lines = [
        "",
        "## Dofollow tier breakdown",
        "",
        "| tier | articles | anchors | article % |",
        "|---|---|---|---|",
    ]
    df = tier_summary["dofollow"]
    lines.append(f"| dofollow | {df['articles']} | {df['anchors']} | {_pct(df['articles'])} |")
    nf = tier_summary["nofollow-signal"]
    lines.append(
        f"| nofollow-signal | {nf['articles']} | {nf['anchors']} | {_pct(nf['articles'])} |"
    )
    for grade in ("high", "low", "unclassified"):
        rb = nf["referral"][grade]
        lines.append(
            f"| &nbsp;&nbsp;↳ referral={grade} | {rb['articles']} | {rb['anchors']} | "
            f"{_pct(rb['articles'])} |"
        )
    unk = tier_summary["unknown"]
    if unk["articles"]:
        lines.append(
            f"| unknown | {unk['articles']} | {unk['anchors']} | {_pct(unk['articles'])} |"
        )
    return "\n".join(lines)


# ── --from-profile path ──────────────────────────────────────────────────────


def _build_profile_report(
    profile: ProfileState,
    target_proportions: dict[str, float],
) -> dict[str, Any]:
    """Compile the report payload from a sliding-window ProfileState.

    Pure function — accepts an in-memory state and target proportions, returns
    a dict the formatter can render either as Markdown or JSON. Splitting the
    aggregation from the formatting keeps both forms in sync without
    duplicating the math.
    """
    total = len(profile.entries)
    type_counts = recent_type_counts(profile)
    deg_rate = recent_degradation_rate(profile)

    # Per-type deviation against the target proportions.
    type_stats: dict[str, dict[str, float]] = {}
    for t in ANCHOR_TYPES:
        count = type_counts.get(t, 0)
        actual = count / total if total > 0 else 0.0
        target = target_proportions.get(t, 0.0)
        type_stats[t] = {
            "count": count,
            "actual_pct": actual * 100,
            "target_pct": target * 100,
            "deviation_pp": (actual - target) * 100,
        }

    # url_category × anchor_type cross-tab. Defaultdict so missing combos
    # render as zero in the formatter without conditional plumbing here.
    cross: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for entry in profile.entries:
        cross[entry.url_category][entry.anchor_type] += 1

    # Top N most-repeated anchor texts — Success Criteria #2 observability.
    text_counter = collections.Counter(e.anchor_text for e in profile.entries)

    return {
        "main_domain": profile.main_domain,
        "total_entries": total,
        "type_stats": type_stats,
        "url_cat_cross": {k: dict(v) for k, v in cross.items()},
        "degradation_rate_pct": deg_rate * 100,
        "top_texts": text_counter.most_common(_TOP_TEXTS_N),
    }


def _compute_alarm(
    profile: ProfileState,
    alarm_cfg: AnchorAlarmConfig,
    main_domain: str,
) -> tuple[dict[str, Any], list[str]]:
    """Compute the per-target alarm block + stderr breach lines.

    Returns ``(alarm_dict, stderr_lines)``:

    - ``alarm_dict`` is the JSON-serializable structure embedded under the
      report's ``alarm`` key. Each entry maps a target_url (or the empty
      string for the pre-bump domain-rollup bucket) to its 30d/90d metrics,
      breach list, and applied thresholds.
    - ``stderr_lines`` is one human-readable line per breaching target;
      caller prints these to stderr.

    Breach detection runs only against the 90d window; 30d metrics are
    surfaced for visibility but never trigger a breach entry or exit code.
    """
    groups = group_by_target_url(profile)
    targets: dict[str, dict[str, Any]] = {}
    breach_lines: list[str] = []
    any_breach = False

    for target_url, entries in sorted(groups.items()):
        w30 = filter_window(entries, days=30)
        w90 = filter_window(entries, days=90)
        m30 = compute_window_metrics(w30)
        m90 = compute_window_metrics(w90)

        thresholds = resolve_thresholds(alarm_cfg, target_url, main_domain)
        breaches = detect_breaches(
            m90, thresholds, sample_floor=_ALARM_SAMPLE_MIN_PER_TARGET,
        )

        granularity = "domain-rollup" if target_url == "" else "url"
        target_label = target_url or "(pre-bump rollup)"

        targets[target_url] = {
            "target_url": target_url,
            "granularity": granularity,
            "metrics": {
                "30d": {
                    "entropy": m30.entropy,
                    "exact_ratio": m30.exact_ratio,
                    "top3_concentration_non_branded": m30.top_n_non_branded,
                    "sample_size": m30.sample_size,
                },
                "90d": {
                    "entropy": m90.entropy,
                    "exact_ratio": m90.exact_ratio,
                    "top3_concentration_non_branded": m90.top_n_non_branded,
                    "sample_size": m90.sample_size,
                },
            },
            "breaches": breaches,
            "thresholds_applied": {
                "entropy_floor": thresholds.entropy_floor,
                "exact_ratio_ceiling": thresholds.exact_ratio_ceiling,
                "top3_concentration_ceiling": thresholds.top3_concentration_ceiling,
            },
            "sample_floor_per_target": _ALARM_SAMPLE_MIN_PER_TARGET,
        }

        if breaches:
            any_breach = True
            top3_repr = (
                f"{m90.top_n_non_branded:.3f}"
                if m90.top_n_non_branded is not None
                else "n/a"
            )
            breach_lines.append(
                f"WARN [anchor_alarm] {target_label}: breached {','.join(breaches)} "
                f"in 90d (entropy={m90.entropy:.3f}, exact_ratio={m90.exact_ratio:.3f}, "
                f"top3_non_branded={top3_repr}; sample={m90.sample_size})"
            )

    return (
        {"targets": targets, "any_breach": any_breach},
        breach_lines,
    )


def _format_alarm_markdown(alarm_block: dict[str, Any]) -> str:
    """Render the alarm section appended to markdown output when breaches exist."""
    out: list[str] = []
    out.append("")
    out.append("## ⚠️ Anchor Distribution Alarm")
    out.append("")
    out.append(
        "These targets exceed the configured distribution thresholds in their "
        "90d window. Review the anchor strategy for each before publishing more "
        "to that destination — anchor over-optimization is the classic "
        "Penguin-era manual-action trigger."
    )
    out.append("")
    out.append("| Target | Breaches | Entropy (90d) | Exact ratio (90d) | Top-3 (90d) | Sample |")
    out.append("|---|---|---|---|---|---|")
    for target_url, target_data in alarm_block["targets"].items():
        if not target_data["breaches"]:
            continue
        label = target_url or "(pre-bump rollup)"
        m90 = target_data["metrics"]["90d"]
        top3 = m90["top3_concentration_non_branded"]
        top3_repr = f"{top3:.3f}" if top3 is not None else "n/a"
        out.append(
            f"| {label} | {', '.join(target_data['breaches'])} | "
            f"{m90['entropy']:.3f} | {m90['exact_ratio']:.3f} | "
            f"{top3_repr} | {m90['sample_size']} |"
        )
    return "\n".join(out)


def _format_profile_report_markdown(report: dict[str, Any]) -> str:
    """Render the profile report as a Markdown document."""
    out: list[str] = []
    out.append(f"# Anchor Profile Report: {report['main_domain']}")
    out.append("")
    out.append(f"Total entries (rolling window): **{report['total_entries']}**")

    if report["total_entries"] < _RELIABLE_SAMPLE_MIN:
        out.append("")
        out.append(
            f"⚠️ Sample size ({report['total_entries']}) is below "
            f"{_RELIABLE_SAMPLE_MIN} — deviation values are not yet reliable."
        )

    # Degradation rate — flagged with ⚠️ above the alarm threshold so the
    # operator sees the systemic-rejection signal at a glance.
    deg = report["degradation_rate_pct"]
    deg_marker = " ⚠️" if deg > _DEGRADATION_ALARM_PCT else ""
    out.append("")
    out.append(f"**Degradation Rate (rolling 100): {deg:.1f}%{deg_marker}**")
    if deg > _DEGRADATION_ALARM_PCT:
        out.append(
            f"> Degradation rate exceeds {_DEGRADATION_ALARM_PCT:.0f}% — investigate "
            "LLM provider rejections or typed-pool shortfalls."
        )

    # Anchor type distribution.
    out.append("")
    out.append("## Anchor Type Distribution")
    out.append("")
    out.append("| Type | Count | Actual % | Target % | Deviation (pp) |")
    out.append("|---|---|---|---|---|")
    for t in ANCHOR_TYPES:
        s = report["type_stats"][t]
        out.append(
            f"| {t} | {s['count']} | {s['actual_pct']:.1f}% | "
            f"{s['target_pct']:.1f}% | {s['deviation_pp']:+.1f} |"
        )

    # URL category × anchor type cross-tab.
    out.append("")
    out.append("## URL Category × Anchor Type")
    out.append("")
    cats = sorted(report["url_cat_cross"].keys())
    if cats:
        header = "| Category | " + " | ".join(ANCHOR_TYPES) + " | Total |"
        sep = "|---|" + "---|" * (len(ANCHOR_TYPES) + 1)
        out.append(header)
        out.append(sep)
        for cat in cats:
            cross = report["url_cat_cross"][cat]
            row = f"| {cat} |"
            cat_total = 0
            for t in ANCHOR_TYPES:
                c = cross.get(t, 0)
                cat_total += c
                row += f" {c} |"
            row += f" {cat_total} |"
            out.append(row)
    else:
        out.append("_(no entries)_")

    # Top repeated anchor texts.
    out.append("")
    out.append(f"## Top {_TOP_TEXTS_N} Most-Used Anchor Texts")
    out.append("")
    if report["top_texts"]:
        out.append("| Anchor Text | Count |")
        out.append("|---|---|")
        for text, count in report["top_texts"]:
            out.append(f"| {text} | {count} |")
    else:
        out.append("_(no entries)_")

    return "\n".join(out)


def _format_profile_report_json(report: dict[str, Any]) -> str:
    # Convert top_texts tuples to lists so the JSON is round-trippable.
    serializable = dict(report)
    serializable["top_texts"] = [list(item) for item in report["top_texts"]]
    return json.dumps(serializable, ensure_ascii=False, indent=2)
