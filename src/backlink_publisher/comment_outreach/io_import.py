"""``comment import`` — validate and ingest externally-provided CommentTarget JSONL.

This is the *only* entry path for social-platform targets (``discover`` handles public
blog / forum URLs by fetching them). Reading is lenient: malformed JSON / non-dict lines
are skipped by ``read_jsonl(strict=False)`` (which emits its own ``WARN`` on stderr) and
schema-invalid rows are skipped here with an always-on ``RECON`` reason. Valid
``CommentTarget`` rows flow to stdout unchanged.

No fetching, no network: social targets keep ``comment_open=null``. ``import`` is a pure
validating filter, so the process always exits 0 — zero valid rows is a legitimate (if
unhelpful) result, surfaced by the end-of-run RECON summary, not an error.
"""

from __future__ import annotations

from typing import Any, TextIO

from backlink_publisher._util.jsonl import read_jsonl, write_jsonl
from backlink_publisher._util.logger import PipelineLogger
from backlink_publisher.comment_outreach import schema

import_logger = PipelineLogger("comment-import")


def import_targets(source: TextIO | None = None, dest: TextIO | None = None) -> dict[str, int]:
    """Validate CommentTarget JSONL from *source*, writing valid rows to *dest*.

    *source* / *dest* default to stdin / stdout. Returns a counts dict
    ``{"valid", "rejected"}`` where ``rejected`` is the number of schema-invalid rows
    skipped here (malformed-JSON / non-dict lines are dropped earlier by the reader and
    reported by it separately). Never raises on bad input rows.
    """
    valid: list[dict[str, Any]] = []
    rejected = 0
    for idx, row in enumerate(read_jsonl(source, strict=False), start=1):
        errors = schema.validate_comment_target(row)
        if errors:
            rejected += 1
            import_logger.recon(
                "comment_import_skip",
                row=idx,
                id=row.get("id"),
                reasons=errors,
            )
            continue
        valid.append(row)

    write_jsonl(valid, dest)
    import_logger.recon("comment_import_summary", valid=len(valid), rejected=rejected)
    return {"valid": len(valid), "rejected": rejected}
