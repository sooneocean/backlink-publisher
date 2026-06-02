"""``canonical-expand`` — fan out canonical-mode rows to canonical-supporting platforms.

A pure JSONL-to-JSONL transformation step in the pipeline:

    seeds.jsonl | plan-backlinks | validate-backlinks | canonical-expand | publish-backlinks

For each input row with ``canonical_mode: true``:
  - Expands to one output row per canonical-supporting platform (devto, blogger,
    ghpages, notion — all have documented canonical_url support).
  - Injects ``seo.canonical_url`` from the row itself or from ``--canonical-url``.
  - Removes the ``canonical_mode`` sentinel (consumed by this step).

Rows WITHOUT ``canonical_mode: true`` are emitted unchanged (pass-through).

Per-row opt-in is intentional: canonical mode republishes the content with a
canonical back-link, which is correct for main-site articles but would pull
external pages off SERP if applied globally (Plan 003 key decision).
"""

from __future__ import annotations

import copy
import json
import sys
from typing import Any

#: Platforms with documented native canonical_url support.
#: velog: no originalArticleURL equivalent (explicitly documented in adapter).
#: telegraph: ignores canonical_url by design (explicitly documented).
CANONICAL_PLATFORMS: frozenset[str] = frozenset(
    {"devto", "blogger", "ghpages", "notion"}
)


def expand_canonical_row(
    row: dict[str, Any],
    *,
    default_canonical_url: str | None = None,
) -> list[dict[str, Any]]:
    """Return expanded platform rows for a canonical-mode row, or ``[row]`` unchanged.

    When ``row["canonical_mode"]`` is truthy, returns one row per platform in
    ``CANONICAL_PLATFORMS``, each with ``channel`` set and ``seo.canonical_url``
    injected.  The ``canonical_mode`` key is removed from output rows.

    When ``canonical_mode`` is absent or falsy, returns the original row in a
    single-element list (pass-through).
    """
    if not row.get("canonical_mode"):
        return [row]

    canonical_url: str | None = (row.get("seo") or {}).get(
        "canonical_url"
    ) or default_canonical_url

    rows: list[dict[str, Any]] = []
    for platform in sorted(CANONICAL_PLATFORMS):
        out = copy.deepcopy(row)
        out.pop("canonical_mode", None)
        out["channel"] = platform
        seo: dict[str, Any] = out.setdefault("seo", {})
        if canonical_url:
            seo["canonical_url"] = canonical_url
        rows.append(out)
    return rows


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="canonical-expand",
        description=(
            "Fan out canonical-mode rows to all canonical-supporting platforms. "
            "stdin: JSONL — one JSON object per line. "
            "stdout: JSONL — one row per canonical platform for expanded rows, "
            "pass-through for others. stderr: diagnostics. Always exits 0."
        ),
    )
    parser.add_argument(
        "--canonical-url",
        metavar="URL",
        default=None,
        help=(
            "Default canonical URL to inject into seo.canonical_url when the "
            "input row does not carry one. Per-row seo.canonical_url takes priority."
        ),
    )
    args = parser.parse_args(argv)

    expanded_count = 0
    passthrough_count = 0

    for lineno, raw in enumerate(sys.stdin, 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(
                f"canonical-expand: line {lineno}: invalid JSON — {exc}",
                file=sys.stderr,
                flush=True,
            )
            sys.exit(1)
        if not isinstance(row, dict):
            print(
                f"canonical-expand: line {lineno}: expected JSON object, got {type(row).__name__}",
                file=sys.stderr,
                flush=True,
            )
            sys.exit(1)

        is_canonical = bool(row.get("canonical_mode"))
        out_rows = expand_canonical_row(row, default_canonical_url=args.canonical_url)
        for out_row in out_rows:
            print(json.dumps(out_row, ensure_ascii=False), flush=True)

        if is_canonical:
            expanded_count += len(out_rows)
        else:
            passthrough_count += 1

    print(
        f"canonical-expand: {expanded_count} rows expanded "
        f"({expanded_count // len(CANONICAL_PLATFORMS) if expanded_count else 0} source rows × "
        f"{len(CANONICAL_PLATFORMS)} platforms), "
        f"{passthrough_count} passed through",
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
