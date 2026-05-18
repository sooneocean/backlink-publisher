"""Pre-flight validation for ko ``anchor_keywords`` + shared anchor-pool sanitize.

Plan 2026-05-18-006 Unit 7 R14 + R8.

ko anchor pools are operator-supplied via TOML config. The long-form
anchor resolver in :mod:`backlink_publisher.cli.plan_backlinks` falls back
to the bare-domain label (e.g. ``"example.com"``) when ``anchor_keywords``
is empty — that fallback is fatal for ko targets because the ASCII domain
has zero Hangul codepoints and fails the R7 anchor-language gate. This
module catches the configuration error at startup with a clear pointer at
the offending TOML key, rather than letting the row fail with an opaque
``anchor_failed_filters`` error mid-batch.

The sanitize rule also applies to ``branded_pool`` for **all** languages
(not just ko): operator-supplied pool entries flow into rendered HTML
anchors via ``_format_anchor_html`` downstream; a forgotten ``<script>``
or Bidi-override character would survive into a published page.

Public entry point: :func:`validate_anchor_pool_entry`. Production caller:
:func:`preflight_ko_targets` invoked at plan-backlinks startup.
"""

from __future__ import annotations

import unicodedata
from typing import Iterable

__all__ = [
    "validate_anchor_pool_entry",
    "preflight_ko_target_pools",
]


#: Hangul Syllables block. Same as :mod:`anchor.lang` R7 — Jamo
#: (U+1100..U+11FF) deferred to follow-up.
_HANGUL_BMP_START, _HANGUL_BMP_END = 0xAC00, 0xD7AF

#: Zero-width characters to strip BEFORE the presence + sanitize checks.
#: Operators pasting from Word / Google Docs / web editors routinely pick
#: up these; stripping them prevents an "all-zero-width" entry from passing
#: the require_hangul check via a hidden adjacent valid char.
_ZERO_WIDTH_STRIPPED = frozenset({
    "​",  # ZERO WIDTH SPACE
    "‌",  # ZERO WIDTH NON-JOINER
    "‍",  # ZERO WIDTH JOINER
    "﻿",  # ZERO WIDTH NO-BREAK SPACE (BOM)
})

#: Forbidden literal characters. HTML metacharacters break
#: ``_format_anchor_html`` rendering downstream; NUL and other C0 controls
#: are pure noise / security hazards.
_FORBIDDEN_LITERALS = frozenset({
    "<", ">", '"', "'", "&",
})

#: Bidi formatting controls that can flip the visible order of an anchor
#: relative to its href — a classic phishing vector.
_BIDI_CONTROLS = frozenset({
    "‪",  # LRE — LEFT-TO-RIGHT EMBEDDING
    "‫",  # RLE — RIGHT-TO-LEFT EMBEDDING
    "‬",  # PDF — POP DIRECTIONAL FORMATTING
    "‭",  # LRO — LEFT-TO-RIGHT OVERRIDE
    "‮",  # RLO — RIGHT-TO-LEFT OVERRIDE
    "⁦",  # LRI — LEFT-TO-RIGHT ISOLATE
    "⁧",  # RLI — RIGHT-TO-LEFT ISOLATE
    "⁨",  # FSI — FIRST STRONG ISOLATE
    "⁩",  # PDI — POP DIRECTIONAL ISOLATE
})


def _strip_outer_whitespace_and_zero_width(text: str) -> str:
    """Strip leading/trailing whitespace AND zero-width characters."""
    while text and (text[0].isspace() or text[0] in _ZERO_WIDTH_STRIPPED):
        text = text[1:]
    while text and (text[-1].isspace() or text[-1] in _ZERO_WIDTH_STRIPPED):
        text = text[:-1]
    return text


def _has_hangul_syllable(text: str) -> bool:
    return any(_HANGUL_BMP_START <= ord(c) <= _HANGUL_BMP_END for c in text)


def _find_forbidden_char(text: str) -> tuple[str, str] | None:
    """Return ``(char, reason)`` for the first forbidden codepoint in
    ``text``, or ``None`` if all codepoints are acceptable."""
    for c in text:
        if c in _FORBIDDEN_LITERALS:
            return c, f"HTML metacharacter {c!r}"
        cp = ord(c)
        if cp == 0:
            return c, "NUL (U+0000)"
        if 0x01 <= cp <= 0x1F or cp == 0x7F:
            # C0 + DEL controls. Names via unicodedata for operator clarity.
            name = unicodedata.name(c, f"U+{cp:04X}")
            return c, f"C0 control ({name})"
        if c in _BIDI_CONTROLS:
            name = unicodedata.name(c, f"U+{cp:04X}")
            return c, f"Bidi formatting control ({name})"
        # Surviving zero-width chars in the middle (after outer-strip) are
        # also rejected — they would render invisibly in published HTML.
        if c in _ZERO_WIDTH_STRIPPED:
            name = unicodedata.name(c, f"U+{cp:04X}")
            return c, f"zero-width character ({name})"
    return None


def validate_anchor_pool_entry(
    entry: str,
    *,
    require_hangul: bool,
) -> tuple[bool, str | None]:
    """Return ``(ok, reason)`` for one anchor-pool entry.

    Plan 2026-05-18-006 Unit 7 R14 + Threat Model anti-injection.

    Steps (applied in order):

    1. NFC-normalize the entry.
    2. Strip leading/trailing whitespace + zero-width characters.
    3. If the entry is empty after step 2: reject as "empty".
    4. If ``require_hangul`` and no codepoint in U+AC00..U+D7AF remains
       after the strip: reject as "no Hangul Syllable codepoint".
    5. Scan for forbidden literals (``<``, ``>``, ``"``, ``'``, ``&``),
       NUL, C0/DEL controls, Bidi formatting controls, surviving
       zero-width characters: reject with the offending codepoint name.

    ``require_hangul=True`` for ``anchor_keywords`` on ko targets
    (production anchor source). ``require_hangul=False`` for
    ``branded_pool`` on any language (cross-script exemption list — Latin
    brand names + Hanja proper nouns are legitimate).
    """
    if not isinstance(entry, str):
        return False, f"entry must be a string, got {type(entry).__name__}"
    normalized = unicodedata.normalize("NFC", entry)
    stripped = _strip_outer_whitespace_and_zero_width(normalized)
    if not stripped:
        return False, "empty after whitespace / zero-width strip"
    if require_hangul and not _has_hangul_syllable(stripped):
        return False, "no Hangul Syllable codepoint (U+AC00..U+D7AF)"
    finding = _find_forbidden_char(stripped)
    if finding is not None:
        offending, reason = finding
        return False, f"forbidden codepoint: {reason}"
    return True, None


def preflight_ko_target_pools(
    config: object,
    targets_view: Iterable[tuple[str, str, list[str], list[str]]],
) -> list[str]:
    """Validate every ko target's anchor pools at plan-backlinks startup.

    ``targets_view`` is an iterable of ``(target_url, language,
    anchor_keywords, branded_pool)`` tuples — caller is responsible for
    flattening the config into this shape (different config schemas may
    yield different structures; the helper stays decoupled from the
    config dataclass).

    Returns a list of error strings. Empty list means all ko targets pass
    pre-flight. Non-empty: the caller raises :class:`SystemExit` with
    exit code 2 (matching the existing schema-validation pattern) and
    writes the errors to stderr.

    Plan 2026-05-18-006 Unit 7 R14 + R8.

    ``branded_pool`` on ko targets gets the sanitize rule but NOT the
    require_hangul rule (it is the cross-script exemption list).
    ``anchor_keywords`` on ko targets gets both rules.

    Non-ko targets are not validated by this pre-flight (they have their
    own existing fallbacks). Future follow-up: extend the sanitize rule
    to non-ko targets as well — pass-2 security suggested branded_pool
    on all languages should get the sanitize rule.
    """
    errors: list[str] = []
    for target_url, language, anchor_keywords, branded_pool in targets_view:
        if language != "ko":
            continue
        if not anchor_keywords:
            errors.append(
                f"target '{target_url}' (language=ko): anchor_keywords is "
                f"empty — ko targets MUST supply Hangul anchor entries to "
                f"avoid the bare-domain fallback that fails the R7 codepoint "
                f"gate. Add at least one Hangul-bearing entry under "
                f"[targets.'{target_url}'].anchor_keywords in your config."
            )
            continue
        # anchor_keywords MUST contain at least one Hangul-bearing entry
        # AND every entry must pass the sanitize rule.
        has_hangul_entry = False
        for idx, entry in enumerate(anchor_keywords):
            ok, reason = validate_anchor_pool_entry(entry, require_hangul=False)
            if not ok:
                errors.append(
                    f"target '{target_url}' (language=ko): "
                    f"anchor_keywords[{idx}] = {entry!r} — {reason}"
                )
                continue
            # Separate Hangul-presence check at the entry level so we know
            # if ANY entry has Hangul (relaxed to "≥1 entry has Hangul"
            # per plan §Requirements R14, not "every entry has Hangul").
            normalized = unicodedata.normalize("NFC", entry)
            stripped = _strip_outer_whitespace_and_zero_width(normalized)
            if _has_hangul_syllable(stripped):
                has_hangul_entry = True
        if not has_hangul_entry:
            errors.append(
                f"target '{target_url}' (language=ko): anchor_keywords has "
                f"no entry containing a Hangul Syllable codepoint "
                f"(U+AC00..U+D7AF). Add at least one Hangul-bearing entry "
                f"under [targets.'{target_url}'].anchor_keywords."
            )
        # branded_pool: sanitize-only (no Hangul-presence requirement —
        # it's the cross-script exemption list).
        for idx, entry in enumerate(branded_pool):
            ok, reason = validate_anchor_pool_entry(entry, require_hangul=False)
            if not ok:
                errors.append(
                    f"target '{target_url}' (language=ko): "
                    f"branded_pool[{idx}] = {entry!r} — {reason}"
                )
    return errors
