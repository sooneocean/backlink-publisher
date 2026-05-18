#!/usr/bin/env python3
"""Telegraph Phase 0 recheck — T+7 / T+14 / T+21 rel + indexation pass.

Reads ``run_output/results.json`` (produced by ``publish_batch.py``) and
for every published page:

1. Re-fetches the page HTML.
2. Extracts the ``rel`` and ``target`` of the anchor that points at the
   target_url (the one that matters for backlink purposes).
3. Optionally hits ``https://www.google.com/search?q=site:telegra.ph/<slug>``
   to probe indexation (no API key; HTML scrape; result is a soft signal
   that may produce false negatives if Google shows a CAPTCHA — operator
   must spot-check by hand on indexation day).

Output: a markdown table fragment named ``recheck-<day>.md`` in
``run_output/``. Operator pastes it into the report under the corresponding
checkpoint column.

Usage::

    python scripts/telegraph_spike/recheck.py --day t7
    python scripts/telegraph_spike/recheck.py --day t14 --check-indexation
    python scripts/telegraph_spike/recheck.py --day t21
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

import requests

DEFAULT_RESULTS = Path(__file__).resolve().parent / "run_output" / "results.json"
DEFAULT_MANIFEST = Path(__file__).resolve().parent / "results-manifest.json"


def load_pages(results_path: Path,
               manifest_path: Path) -> tuple[list[dict], str]:
    """Prefer gitignored run_output/results.json (richest, includes
    link_attrs from publish time); fall back to the committed sanitized
    manifest so remote scheduled agents can re-run the checkpoints
    without needing the operator's local artifacts."""
    if results_path.exists():
        return json.loads(results_path.read_text()), str(results_path)
    if manifest_path.exists():
        m = json.loads(manifest_path.read_text())
        return m.get("pages", []), str(manifest_path)
    raise FileNotFoundError(
        f"neither {results_path} nor {manifest_path} found. "
        "publish_batch.py needs to have run, OR results-manifest.json "
        "needs to be committed to the repo for remote agents.")


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        a = dict(attrs)
        self.anchors.append({"href": a.get("href"), "rel": a.get("rel"),
                             "target": a.get("target")})


def find_target_anchor(html: str, target_url: str) -> dict:
    p = _AnchorParser()
    p.feed(html)
    norm = target_url.rstrip("/")
    for a in p.anchors:
        if (a.get("href") or "").rstrip("/").startswith(norm):
            return a
    return {"href": None, "rel": None, "target": None}


def fetch_page(url: str) -> str:
    r = requests.get(url, timeout=15,
                     headers={"User-Agent": "telegraph-phase0-recheck/1.0"})
    r.raise_for_status()
    return r.text


def probe_indexation(slug: str) -> str:
    """Soft probe via Google search. Returns 'yes' / 'no' / 'captcha'.

    No API access — HTML scrape with all the caveats. The operator should
    cross-check by hand on the 6/01 review day; this is a fast first pass."""
    q = f"site:telegra.ph/{slug}"
    try:
        r = requests.get(
            "https://www.google.com/search",
            params={"q": q},
            timeout=15,
            headers={"User-Agent":
                     "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/120.0 Safari/537.36"})
    except Exception as e:
        return f"error: {e!r}"
    text = r.text.lower()
    if "did not match any documents" in text or "no results found" in text:
        return "no"
    if "unusual traffic" in text or "/sorry/" in r.url:
        return "captcha"
    # Google's results page typically embeds the URL in an h3 → a chain
    if slug.lower() in text:
        return "yes"
    return "uncertain"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--day", required=True, choices=["t7", "t14", "t21"],
                   help="Checkpoint label")
    p.add_argument("--results", type=Path, default=DEFAULT_RESULTS,
                   help="Path to publish_batch.py results.json (preferred)")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST,
                   help="Committed fallback manifest if results.json absent")
    p.add_argument("--target-url", default="https://51acgs.com",
                   help="Target URL used in the batch run (anchor matcher)")
    p.add_argument("--check-indexation", action="store_true",
                   help="On T+14 also probe Google for indexation (soft "
                        "signal — operator must cross-check manually)")
    p.add_argument("--inter-call-delay", type=float, default=2.0)
    args = p.parse_args()

    try:
        records, source = load_pages(args.results, args.manifest)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"rechecking {len(records)} pages @ {args.day} (source: {source})")

    rows: list[str] = []
    rel_col = f"rel_{args.day}"
    idx_col = f"indexed_{args.day}" if args.check_indexation else None
    header = ["#", "URL", "group", "velocity", rel_col, "target",
              "fetch_status"]
    if idx_col:
        header.append(idx_col)
    rows.append("| " + " | ".join(header) + " |")
    rows.append("|" + "|".join(["---"] * len(header)) + "|")

    summary = {"total": len(records), "dofollow_retained": 0,
               "nofollow_introduced": 0, "fetch_failed": 0, "indexed": 0,
               "not_indexed": 0, "captcha": 0}

    for rec in records:
        url = rec["url"]
        slug = rec["slug"]
        try:
            html = fetch_page(url)
            anchor = find_target_anchor(html, args.target_url)
            rel = anchor.get("rel")
            target = anchor.get("target")
            rel_str = "null" if rel is None else str(rel)
            tgt_str = target or "null"
            status = "ok"
            if rel is None:
                summary["dofollow_retained"] += 1
            else:
                summary["nofollow_introduced"] += 1
        except Exception as exc:
            rel_str = "?"
            tgt_str = "?"
            status = f"error: {exc!r}"
            summary["fetch_failed"] += 1

        cells = [str(rec["idx"]), url, rec["group"], rec["velocity"] or "—",
                 rel_str, tgt_str, status]
        if idx_col:
            probe = probe_indexation(slug)
            if probe == "yes":
                summary["indexed"] += 1
            elif probe == "no":
                summary["not_indexed"] += 1
            elif probe == "captcha":
                summary["captcha"] += 1
            cells.append(probe)

        rows.append("| " + " | ".join(cells) + " |")
        print(f"  #{rec['idx']:2} {rec['group']} {rec['velocity'] or '   ':3}  "
              f"{rel_col}={rel_str:9}  target={tgt_str}"
              + (f"  indexation={cells[-1]}" if idx_col else ""))
        time.sleep(args.inter_call_delay)

    out_dir = args.results.parent  # run_output/
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"recheck-{args.day}.md"
    out.write_text("\n".join(rows) + "\n")

    print(f"\nresults table → {out}")
    print(f"\nsummary @ {args.day}:")
    for k, v in summary.items():
        print(f"  {k:25} = {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
