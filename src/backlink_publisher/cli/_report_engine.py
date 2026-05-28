"""Pure report-anchors engine — no process-global side effects.

Thin-WebUI Phase 2 Unit 7 (plan ``2026-05-27-004``). Extracted from
``cli/report_anchors.py`` so the CLI shell and the in-process ``PipelineAPI``
bridge share one reporting kernel. Follows the ``validate/engine.py`` precedent.

Models both structural paths:

- **Profile path** (``--from-profile``): loads an anchor profile for
  ``main_domain``, builds per-target metrics + alarm, formats the output
  document. Can set ``outcome.alarm_breach`` / ``exit_code=6`` (advisory —
  the document is always populated so the caller can still print it).
- **Stdin-aggregate path**: builds a raw stats report from JSONL payload
  rows. Structurally incapable of computing the anchor alarm (``links[]``
  lacks ``anchor_type``); ``alarm_breach`` is always False.

Output is a formatted **document** (markdown or JSON string), NOT JSONL rows.
Caller prints the document to stdout (H3 — caller owns I/O).

MUST NOT write to ``sys.stdout`` / ``sys.stderr`` (H3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import backlink_publisher.publishing.adapters  # noqa: F401  populate registry
from backlink_publisher.anchor.profile import load_profile
from backlink_publisher.config import Config

from ._report_format import (
    _EXIT_CODE_ALARM,
    _build_profile_report,
    _build_report,
    _build_tier_summary,
    _compute_alarm,
    _format_alarm_markdown,
    _format_profile_report_json,
    _format_profile_report_markdown,
    _json_output,
    _markdown_table,
)


@dataclass
class ReportOutcome:
    """Result of building an anchor-text report.

    - ``document``: formatted output (markdown or JSON string) — caller prints
      to stdout. Always populated even when ``alarm_breach`` is True.
    - ``alarm_breach``: True if the distribution alarm fired (profile path only).
    - ``breach_count``: number of per-target warning lines.
    - ``breach_lines``: caller prints each line to stderr.
    - ``exit_code``: 6 if alarm, 0 otherwise (advisory — document still valid).
    """

    document: str = ""
    alarm_breach: bool = False
    breach_count: int = 0
    breach_lines: list[str] = field(default_factory=list)
    exit_code: int = 0


def report_from_profile(
    main_domain: str,
    config: Config,
    *,
    as_json: bool = True,
) -> ReportOutcome:
    """Build a profile-based anchor distribution report.

    Reads the anchor profile for ``main_domain`` from disk, computes per-target
    metrics over 30d / 90d windows using ``config.anchor_proportions`` and
    ``config.anchor_alarm``, and formats the result.

    If any target's 90d window breaches configured thresholds:
    - ``outcome.alarm_breach`` is True
    - ``outcome.exit_code`` is 6 (advisory — document is still populated)
    - ``outcome.breach_lines`` contains one warning string per breaching target

    Not strictly pure (reads disk via ``load_profile``), but owns no
    stdout/stderr (H3 — caller owns I/O).
    """
    profile = load_profile(main_domain)
    report = _build_profile_report(profile, config.anchor_proportions)
    alarm_block, breach_lines = _compute_alarm(
        profile, config.anchor_alarm, main_domain,
    )
    report["alarm"] = alarm_block

    if as_json:
        document = _format_profile_report_json(report)
    else:
        parts = [_format_profile_report_markdown(report)]
        if alarm_block.get("any_breach"):
            parts.append(_format_alarm_markdown(alarm_block))
        document = "\n".join(parts)

    alarm_breach = bool(alarm_block.get("any_breach"))
    return ReportOutcome(
        document=document,
        alarm_breach=alarm_breach,
        breach_count=len(breach_lines),
        breach_lines=list(breach_lines),
        exit_code=_EXIT_CODE_ALARM if alarm_breach else 0,
    )


def report_from_rows(
    rows: list[dict[str, Any]],
    *,
    as_json: bool = False,
    top_anchors: int = 5,
) -> ReportOutcome:
    """Build a raw aggregate report from JSONL payload rows.

    Structurally incapable of computing the anchor distribution alarm (the JSONL
    ``links[]`` array lacks ``anchor_type``). ``alarm_breach`` is always False.

    The caller (shell or bridge) is responsible for printing the NOTE about this
    limitation to stderr — the engine owns no stderr.
    """
    stats = _build_report(rows)
    tier_summary = _build_tier_summary(rows)
    if as_json:
        document = _json_output(stats, tier_summary=tier_summary)
    else:
        document = _markdown_table(stats, top_n=top_anchors, tier_summary=tier_summary)
    return ReportOutcome(document=document, exit_code=0)
