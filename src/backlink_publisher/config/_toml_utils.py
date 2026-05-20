"""TOML heading lexer, section preservation engine, and TOML string formatters.

Extracted from ``writer.py`` in the Unit 2 monolith decomposition.
Re-exported via ``writer.py`` and ``config/__init__.py`` for backward compatibility.
"""

from __future__ import annotations

import re


_SAVE_CONFIG_KNOWN_ROOTS: frozenset[str] = frozenset(
    {"blogger", "medium", "targets", "ghpages", "hashnode", "writeas"}
)

_TOML_HEADING_RE = re.compile(
    r"""
    ^\s*\[\[?           # opening [ or [[
    \s*
    (?:
        "([^"]+)"       # quoted root
        |
        ([^.\]\s"]+)    # bare root (no dots, brackets, whitespace)
    )
    """,
    re.VERBOSE,
)

_TOML_HEADING_PATH_RE = re.compile(
    r"""
    ^\s*\[\[?           # opening [ or [[
    \s*
    (?:
        "([^"]+)"       # quoted root -> group 1
        |
        ([^.\]\s"]+)    # bare root -> group 2
    )
    (?:                 # optional second segment after a dot
        \.
        (?:
            ("[^"]+")   # quoted sub WITH surrounding quotes -> group 3
            |
            ([^.\]\s"]+)# bare sub -> group 4
        )
    )?
    """,
    re.VERBOSE,
)


def _canon_subsection_key(segment: str) -> str:
    return segment


def _toml_heading_root(line: str) -> str | None:
    m = _TOML_HEADING_RE.match(line)
    if not m:
        return None
    return m.group(1) or m.group(2)


def _toml_heading_path(line: str) -> tuple[str, str | None] | None:
    m = _TOML_HEADING_PATH_RE.match(line)
    if not m:
        return None
    root = m.group(1) or m.group(2)
    sub_raw = m.group(3) or m.group(4)
    if sub_raw is None:
        return (root, None)
    return (root, _canon_subsection_key(sub_raw))


def _preserve_unknown_sections(
    raw_text: str,
    known_roots: frozenset[str],
    known_subsections: frozenset[tuple[str, str]],
) -> str:
    out: list[str] = []
    keep_current = False
    for line in raw_text.splitlines():
        path = _toml_heading_path(line)
        if path is not None:
            root, sub = path
            if root in known_roots:
                keep_current = sub is not None and (root, sub) not in known_subsections
            else:
                keep_current = True
            if keep_current:
                out.append(line)
        elif keep_current:
            out.append(line)
    return ("\n".join(out) + "\n") if out else ""


def _toml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_list(values: list[str]) -> str:
    if not values:
        return "[]"
    return "[" + ", ".join(_toml_str(v) for v in values) + "]"
