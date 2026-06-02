"""Input parsing + deterministic validation for generate-backlink-text.

Extracted from ``generate_backlink_text.py`` so that module stays focused on
CLI/argparse + LLM orchestration. Holds candidate parsing (``_read_candidates``),
per-record field validation (``_validate_candidate`` / ``_make_rejected``),
output-text validation (``_validate_generated_text`` and its ``_get_host`` /
``_count_words`` / ``_is_refusal`` helpers), output emission (``_emit_records``),
and the corrective-reprompt hint map (``_make_correction_hint``). All pure and
deterministic — no argparse, no LLM, no network. Behaviour preserved verbatim;
``generate_backlink_text`` re-exports the test-referenced names.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any
from urllib.parse import urlparse

from backlink_publisher._util.errors import InputValidationError
from backlink_publisher._util.jsonl import write_jsonl
from backlink_publisher._util.url import safe_urlparse, validate_https_url


_REQUIRED_FIELDS = ("target_url", "anchor_text", "mode")
_DEFAULT_MAX_INPUT_BYTES = 2_000_000
_DEFAULT_MAX_RECORDS = 200

# ── Output validation constants ────────────────────────────────────────────────

#: Markdown link ``[text](url)`` — used for link extraction and extra-link stripping.
_MARKDOWN_LINK_RE = re.compile(r"\[([^\[\]]*)\]\(([^()]*)\)")

#: Per-mode (min, max) word-count bounds enforced on validated model output.
_MODE_WORD_BOUNDS: dict[str, tuple[int, int]] = {
    "article": (200, 400),
    "comment": (30, 80),
}

#: Control + bidi-override chars that must not appear in model output.
#: Explicit ``\uXXXX`` Python string escapes — avoids literal-Unicode ambiguity
#: (same principle as ``llm/client.py:_PROMPT_UNSAFE_CHARS``).
#: Allows tab (0x09), LF (0x0a), CR (0x0d) — normal in Markdown.
_OUTPUT_UNSAFE_RE = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u2028-\u202e\u2066-\u2069]"
)

#: Common LLM refusal substrings (lowercase, checked against ``text.lower()``).
_REFUSAL_PHRASES: tuple[str, ...] = (
    "i cannot", "i'm unable", "i am unable", "i'm not able",
    "as an ai", "as a language model", "i cannot assist",
    "i'm sorry, but i", "cannot fulfill", "cannot help with",
    "i apologize, but i", "i must decline",
)


def _read_candidates(
    raw_text: str,
    *,
    max_input_bytes: int = _DEFAULT_MAX_INPUT_BYTES,
    max_records: int = _DEFAULT_MAX_RECORDS,
) -> list[dict]:
    """Parse and return a list of candidate dicts from raw input text.

    Accepts JSON object (single record), JSON array, or JSONL.  Enforces
    ``max_input_bytes`` before parsing (fail-closed, R5) and ``max_records``
    after (fail-closed, R5).  Empty input → ``[]`` (R5b).

    Raises:
        InputValidationError: if the raw byte length exceeds ``max_input_bytes``
            or the record count exceeds ``max_records``.
    """
    raw_bytes = raw_text.encode("utf-8") if isinstance(raw_text, str) else raw_text
    if len(raw_bytes) > max_input_bytes:
        raise InputValidationError(
            f"generate-backlink-text: input exceeds --max-input-bytes "
            f"({max_input_bytes:,} bytes); refusing to parse"
        )

    text = raw_text.strip() if isinstance(raw_text, str) else raw_text.decode("utf-8").strip()
    if not text:
        return []

    # Try single JSON object or JSON array first.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            candidates: list[dict] = [parsed]
        elif isinstance(parsed, list):
            candidates = [r for r in parsed if isinstance(r, dict)]
        else:
            candidates = []
    except json.JSONDecodeError:
        # Fall back to JSONL (one record per line, skip malformed — strict=False).
        candidates = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    candidates.append(obj)
            except json.JSONDecodeError:
                pass  # skip malformed lines

    if len(candidates) > max_records:
        raise InputValidationError(
            f"generate-backlink-text: input has {len(candidates)} records, "
            f"exceeds --max-records ({max_records}); refusing to process"
        )

    return candidates


# ── Per-record field validation ───────────────────────────────────────────────


def _validate_candidate(rec: dict) -> dict:
    """Return a validated + normalised candidate or a ``rejected`` marker.

    Never raises — invalid records become ``{"status": "rejected", ...}`` so
    the batch continues (R4b, R13).

    Required fields: ``target_url`` (https-scheme, urlparse-safe), ``anchor_text``
    (non-empty string), ``mode`` (non-empty string; unsupported values produce a
    per-record rejection at generation time, not here).
    """
    # Check all required fields are present and non-empty strings.
    for field in _REQUIRED_FIELDS:
        val = rec.get(field)
        if not isinstance(val, str) or not val.strip():
            return _make_rejected(rec, "invalid_record")

    # Gate target_url: must be https.
    # safe_urlparse inside validate_https_url catches malformed IPv6 etc. and
    # returns None without raising, so we check that first to distinguish a
    # truly malformed URL (invalid_record) from a non-https scheme (bad_target_url_scheme).
    validated_url = validate_https_url(rec["target_url"])
    if validated_url is None:
        if safe_urlparse(rec["target_url"]) is None:
            return _make_rejected(rec, "invalid_record")
        return _make_rejected(rec, "bad_target_url_scheme")

    return {
        "target_url": validated_url,
        "anchor_text": rec["anchor_text"].strip(),
        "mode": rec["mode"].strip(),
        # Carry any extra fields the operator included (pass-through).
        **{
            k: v
            for k, v in rec.items()
            if k not in _REQUIRED_FIELDS
        },
    }


def _make_rejected(rec: dict, reason: str) -> dict:
    """Build a per-record rejected output row (R13, R14)."""
    out: dict[str, Any] = {"status": "rejected", "rejection_reason": reason}
    # Carry the original fields so the operator can trace the source row.
    for field in _REQUIRED_FIELDS:
        if field in rec:
            out[field] = rec[field]
    return out


# ── Output emission ───────────────────────────────────────────────────────────


# ── Output validation helpers ─────────────────────────────────────────────────


def _get_host(url: str) -> str | None:
    """Extract hostname from a URL, guarding ``urlparse`` ValueError (malformed IPv6)."""
    try:
        return urlparse(url).hostname
    except ValueError:
        return None


def _count_words(text: str) -> int:
    """Count words in generated Markdown text, stripping link/formatting syntax.

    For Latin scripts: space-separated tokens.
    For CJK scripts (Chinese/Korean): each 2 characters \u2248 1 word
    (conventional CJK readability estimate; Chinese words average 2 chars).
    Mixed text uses whichever count is larger so a short English comment with
    a few CJK characters is still measured by space-split.
    """
    clean = _MARKDOWN_LINK_RE.sub(r"\1", text)
    clean = re.sub(r"[*_`#]+", "", clean)
    latin_words = len(clean.split())
    # CJK Unified Ideographs + Hangul Syllables
    cjk_chars = sum(
        1 for c in clean
        if "\u4e00" <= c <= "\u9fff" or "\uac00" <= c <= "\ud7af"
    )
    if cjk_chars > 5:
        return max(latin_words, cjk_chars // 2)
    return latin_words


def _is_refusal(text: str) -> bool:
    """Return True if the text contains a common LLM refusal phrase."""
    lower = text.lower()
    return any(phrase in lower for phrase in _REFUSAL_PHRASES)


def _validate_generated_text(
    text: str,
    *,
    target_url: str,
    anchor_text: str,
    mode: str,
    language: str = "",
) -> dict:
    """Validate LLM-generated Markdown text deterministically. Never raises.

    Check order:
    1. Refusal phrasing → ``"llm_refusal"``
    2. Control/bidi chars in output → ``"unsafe_chars"``
    3. Extract and canonicalize Markdown links from the text.
    4. Strip extra-domain links (host ≠ ``target_url`` host, incl. userinfo
       confusion like ``target.com@evil.com`` and protocol-relative
       ``//evil.com``); record stripped count as advisory flag.
    5. No link to ``target_url`` host remains → ``"missing_link"``
    6. ``anchor_text`` not in surviving link text (case/whitespace-normalized)
       → ``"missing_anchor"``
    7. Per-mode word count outside bounds → ``"length_out_of_bounds"``
    8. Language advisory (never rejects): mismatch → ``language_flag`` set.

    Returns:
        ``{"ok": True, "text": cleaned_text, "stripped_extra_links": int,
          "language_flag": str | None}``
        or
        ``{"ok": False, "reason": "<rejection_reason>"}``
    """
    # 1. Refusal detection.
    if _is_refusal(text):
        return {"ok": False, "reason": "llm_refusal"}

    # 2. Unsafe chars in output.
    if _OUTPUT_UNSAFE_RE.search(text):
        return {"ok": False, "reason": "unsafe_chars"}

    # 3. Parse all Markdown links from the text.
    all_links = _MARKDOWN_LINK_RE.findall(text)  # list of (link_text, url)

    # 4. Strip extra-domain links; canonicalize hosts via urlparse.hostname.
    #    urlparse.hostname correctly demotes userinfo:
    #      urlparse("https://target.com@evil.com/p").hostname == "evil.com"
    #    and returns "evil.com" for "//evil.com/p" (scheme='', netloc='evil.com').
    target_host = _get_host(target_url)
    target_links: list[tuple[str, str]] = []
    cleaned_text = text
    stripped_count = 0

    for link_text, link_url in all_links:
        link_host = _get_host(link_url)
        if link_host is None or link_host != target_host:
            # Replace the full Markdown link with just its visible text.
            full_link = f"[{link_text}]({link_url})"
            cleaned_text = cleaned_text.replace(full_link, link_text, 1)
            stripped_count += 1
        else:
            target_links.append((link_text, link_url))

    # 5. Target link presence.
    if not target_links:
        return {"ok": False, "reason": "missing_link"}

    # 6. Anchor text check (case/whitespace-normalized substring match).
    norm_anchor = " ".join(anchor_text.lower().split())
    link_has_anchor = any(
        norm_anchor in " ".join(lt.lower().split())
        for lt, _ in target_links
    )
    if not link_has_anchor:
        return {"ok": False, "reason": "missing_anchor"}

    # 7. Per-mode word count (after extra-link stripping).
    if mode in _MODE_WORD_BOUNDS:
        lo, hi = _MODE_WORD_BOUNDS[mode]
        wc = _count_words(cleaned_text)
        if not lo <= wc <= hi:
            return {"ok": False, "reason": "length_out_of_bounds"}

    # 8. Language advisory — never rejects.
    language_flag: str | None = None
    if language:
        from backlink_publisher.linkcheck.language import (
            detect_language_from_markdown,
            language_matches,
        )
        detected = detect_language_from_markdown(cleaned_text)
        if not language_matches(detected, language):
            language_flag = detected

    return {
        "ok": True,
        "text": cleaned_text,
        "stripped_extra_links": stripped_count,
        "language_flag": language_flag,
    }


def _emit_records(
    records: list[dict], output_format: str, file=None
) -> None:
    """Emit output records in the chosen format (JSONL or JSON array)."""
    dest = file or sys.stdout
    if output_format == "json":
        json.dump(records, dest, ensure_ascii=False, indent=2)
        dest.write("\n")
    else:  # jsonl (default)
        write_jsonl(records, dest)


# ── Corrective re-prompt ──────────────────────────────────────────────────────


#: Human-readable correction hints keyed by validation failure reason.
#: Appended to the re-prompt so the model knows exactly what to fix.
_CORRECTION_HINTS: dict[str, str] = {
    "missing_link": (
        "Your previous response did not include a Markdown hyperlink to the "
        "target URL. Add exactly one link formatted as [anchor text](URL)."
    ),
    "missing_anchor": (
        "Your previous response did not use the required anchor text as the "
        "link text. The link must be formatted as [anchor text](URL) where "
        "'anchor text' is the exact anchor text provided."
    ),
    "length_out_of_bounds": (
        "Your previous response was outside the required word count. "
        "Adjust the length to fit within the specified bounds."
    ),
    "unsafe_chars": (
        "Your previous response contained disallowed control or bidirectional "
        "override characters. Please use only standard printable text."
    ),
}


def _make_correction_hint(reason: str) -> str | None:
    """Return a corrective instruction for the LLM based on validation failure reason.

    Returns ``None`` for reasons where re-prompting is unlikely to help
    (``llm_refusal``) so the orchestrator can skip the re-prompt.
    """
    return _CORRECTION_HINTS.get(reason)
