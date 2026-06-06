"""Wave 1 Unit 4: flip-or-kill enforcement for canary-pending channels.

Closes the P0 rot risk: a markdown deadline that nothing reads is fire-and-forget.
This test parses ``docs/discovery/canary-pending.md`` and FAILS once a channel's
deadline passes while it is still registered ``dofollow="uncertain"`` — forcing the
operator to either flip it to ``True`` (after an OUR-pipeline canary) or retire it.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

import backlink_publisher.publishing.adapters  # noqa: F401 — populate the registry
from backlink_publisher.publishing.registry import dofollow_status, registered_platforms

_TRACKER = Path(__file__).parents[1] / "docs" / "discovery" / "canary-pending.md"
_ROW_RE = re.compile(r"^\|\s*([a-z0-9_]+)\s*\|\s*([\d-]+)\s*\|\s*([\d-]+)\s*\|\s*(\w+)\s*\|$")


_HEADER_OR_SEP_RE = re.compile(r"^\|[\s\-|a-z]*\|$", re.IGNORECASE)


def _block_lines():
    text = _TRACKER.read_text(encoding="utf-8")
    block = text.split("<!-- canary-pending:begin -->", 1)[1].split("<!-- canary-pending:end -->", 1)[0]
    return [ln.strip() for ln in block.splitlines() if ln.strip().startswith("|")]


def _parse_rows():
    rows = []
    for line in _block_lines():
        m = _ROW_RE.match(line)
        if m:
            rows.append({
                "platform": m.group(1),
                "registered": m.group(2),
                "deadline": date.fromisoformat(m.group(3)),
                "status": m.group(4),
            })
    return rows


def test_no_malformed_rows_in_block():
    """Fail-open guard: every pipe-row is the header, the separator, or a valid data
    row. A typo (uppercase name, hyphen, bad date) would otherwise silently skip
    enforcement — defeating the flip-or-kill gate."""
    for line in _block_lines():
        is_header_or_sep = (
            "platform" in line.lower() and "deadline" in line.lower()
        ) or set(line) <= set("|- ")
        assert is_header_or_sep or _ROW_RE.match(line), (
            f"canary-pending.md has a malformed row that enforcement will silently "
            f"skip: {line!r}. Fix the format (| platform | YYYY-MM-DD | YYYY-MM-DD | status |)."
        )


def test_wave1_platforms_all_tracked():
    """The three channels this gate exists for must each have a row (guards a
    dropped/typo'd entry from silently escaping the deadline)."""
    tracked = {r["platform"] for r in _parse_rows()}
    for p in ("hackmd", "mataroa", "gitlabpages"):
        assert p in tracked, f"{p} is registered uncertain but missing from canary-pending.md"


def test_tracker_file_exists_and_parses():
    assert _TRACKER.exists(), f"missing tracker: {_TRACKER}"
    rows = _parse_rows()
    assert rows, "canary-pending tracker has no parseable rows"


@pytest.mark.parametrize("row", _parse_rows(), ids=lambda r: r["platform"])
def test_pending_channel_not_past_deadline(row):
    """A pending channel past its deadline while still 'uncertain' fails CI."""
    if row["status"] != "pending":
        return  # flipped / retired rows are checked for consistency below
    if dofollow_status(row["platform"]) != "uncertain":
        return  # already flipped/retired in the registry; row is just stale
    assert date.today() <= row["deadline"], (
        f"{row['platform']} is past its canary deadline ({row['deadline']}) but is "
        f"still registered dofollow=\"uncertain\". Run the OUR-pipeline canary and "
        f"either flip it to dofollow=True or retire it — see docs/discovery/canary-pending.md."
    )


@pytest.mark.parametrize("row", _parse_rows(), ids=lambda r: r["platform"])
def test_row_consistent_with_registry(row):
    """A 'flipped' row must be dofollow=True in the registry; 'pending' must be registered."""
    assert row["platform"] in registered_platforms(), (
        f"{row['platform']} is in the canary tracker but not registered"
    )
    if row["status"] == "flipped":
        assert dofollow_status(row["platform"]) is True, (
            f"{row['platform']} is marked 'flipped' but registry shows "
            f"{dofollow_status(row['platform'])!r}"
        )
