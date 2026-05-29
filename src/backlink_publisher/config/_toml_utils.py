"""TOML heading lexer, section preservation engine, and TOML string formatters.

Extracted from ``writer.py`` in the Unit 2 monolith decomposition.
Re-exported via ``writer.py`` and ``config/__init__.py`` for backward compatibility.
"""

from __future__ import annotations

import re

from .types import DEFAULT_WORK_TEMPLATES, ThreeUrlConfig


_SAVE_CONFIG_KNOWN_ROOTS: frozenset[str] = frozenset(
    {"blogger", "medium", "targets", "ghpages", "mastodon"}
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


def _emit_target_section(
    domain: str,
    kws_by_domain: dict[str, list[str]],
    probe_queries_by_domain: dict[str, list[str]],
    brand_aliases_by_domain: dict[str, list[str]],
    three_url_by_domain: dict[str, ThreeUrlConfig],
) -> list[str]:
    """Render the ``[targets.<domain>]`` block lines for one domain.

    Extracted from ``save_config`` so the writer stays under its cyclomatic-
    complexity ceiling — the per-domain conditional emission (anchor_keywords /
    probe_queries / brand_aliases / three-URL fields) lives here.
    """
    lines = [f"[targets.{_toml_str(domain)}]"]
    if domain in kws_by_domain:
        lines.append(f"anchor_keywords = {_toml_list(kws_by_domain[domain])}")
    if domain in probe_queries_by_domain:
        lines.append(
            f"probe_queries = {_toml_list(probe_queries_by_domain[domain])}"
        )
    if domain in brand_aliases_by_domain:
        lines.append(
            f"brand_aliases = {_toml_list(brand_aliases_by_domain[domain])}"
        )
    if domain in three_url_by_domain:
        tu = three_url_by_domain[domain]
        lines.append(f"main_url = {_toml_str(tu.main_url)}")
        lines.append(f"list_url = {_toml_str(tu.list_url)}")
        lines.append(f"work_urls = {_toml_list(tu.work_urls)}")
        lines.append(f"branded_pool = {_toml_list(tu.branded_pool)}")
        lines.append(f"partial_pool = {_toml_list(tu.partial_pool)}")
        lines.append(f"exact_pool = {_toml_list(tu.exact_pool)}")
        if tu.work_anchor_templates != list(DEFAULT_WORK_TEMPLATES):
            lines.append(
                f"work_anchor_templates = {_toml_list(tu.work_anchor_templates)}"
            )
        if tu.list_path_blocklist is not None:
            lines.append(
                f"list_path_blocklist = {_toml_list(tu.list_path_blocklist)}"
            )
        if tu.insecure_tls:
            lines.append("insecure_tls = true")
    return lines
