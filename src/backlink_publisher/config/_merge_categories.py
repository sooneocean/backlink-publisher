"""In-place TOML merge for ``[sites."<main>".url_categories]`` blocks.

Extracted from ``writer.py`` in the Unit 2 monolith decomposition.
"""

from __future__ import annotations

from pathlib import Path

from backlink_publisher._util.errors import InputValidationError
from ._config_io import _resolve_config_dir, _snapshot_config, _atomic_write_text
from ._toml_utils import _toml_str


def _find_section(lines: list[str], header: str) -> tuple[int, int]:
    """Return (start_idx, end_idx) of the TOML section matching ``header``.

    ``start_idx`` is the line index of the header; ``end_idx`` is the first
    line index of the next sibling section (or len(lines) if none). Returns
    (-1, -1) when the section is absent.
    """
    for i, line in enumerate(lines):
        if line.strip() == header:
            end = len(lines)
            for j in range(i + 1, len(lines)):
                sj = lines[j].strip()
                if sj.startswith("[") and sj.endswith("]") and not sj.startswith("[["):
                    end = j
                    break
            return i, end
    return -1, -1


def _append_section(lines: list[str], header: str, additions: dict[str, str]) -> str:
    """Append a new TOML section with ``additions`` and return the joined text."""
    if lines and lines[-1].strip() != "":
        lines.append("")
    lines.append(header)
    for k in sorted(additions):
        lines.append(f"{k} = {_toml_str(additions[k])}")
    lines.append("")
    return "\n".join(lines)


def _update_section(
    lines: list[str],
    start: int,
    end: int,
    additions: dict[str, str],
) -> str:
    """Merge ``additions`` into an existing section and return the joined text."""
    section_body = lines[start + 1 : end]
    pending = dict(additions)
    new_body: list[str] = []
    for body_line in section_body:
        stripped = body_line.strip()
        if not stripped or stripped.startswith("#"):
            new_body.append(body_line)
            continue
        if "=" not in body_line:
            new_body.append(body_line)
            continue
        key_part = body_line.split("=", 1)[0].strip()
        if key_part in pending:
            new_body.append(f"{key_part} = {_toml_str(pending.pop(key_part))}")
        else:
            new_body.append(body_line)
    trailing_blanks: list[str] = []
    while new_body and new_body[-1].strip() == "":
        trailing_blanks.append(new_body.pop())
    for k in sorted(pending):
        new_body.append(f"{k} = {_toml_str(pending[k])}")
    new_body.extend(trailing_blanks)
    new_lines = lines[: start + 1] + new_body + lines[end:]
    return "\n".join(new_lines)


def merge_site_url_categories(
    main_url: str,
    additions: dict[str, str],
    *,
    path: Path | None = None,
) -> None:
    if not additions:
        return

    config_path = path or (_resolve_config_dir() / "config.toml")
    config_path.parent.mkdir(parents=True, exist_ok=True)

    raw = config_path.read_text(encoding="utf-8") if config_path.exists() else ""

    if any(ch in main_url for ch in ("\n", "\r", "\x00")):
        raise InputValidationError(
            f"main_url contains a control character: {main_url!r}"
        )

    domain_key = main_url.rstrip("/")
    section_header = f'[sites."{domain_key}".url_categories]'
    lines = raw.splitlines() if raw else []

    start, end = _find_section(lines, section_header)
    if start == -1:
        new_text = _append_section(lines, section_header, additions)
    else:
        new_text = _update_section(lines, start, end, additions)

    if raw and not new_text.endswith("\n"):
        new_text += "\n"
    elif not raw:
        new_text += "\n"

    if config_path.exists():
        _snapshot_config(config_path)
    _atomic_write_text(config_path, new_text)
