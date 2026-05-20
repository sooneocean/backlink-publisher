"""In-place TOML merge for ``[sites."<main>".url_categories]`` blocks.

Extracted from ``writer.py`` in the Unit 2 monolith decomposition.
"""

from __future__ import annotations

from pathlib import Path

from backlink_publisher._util.errors import InputValidationError
from ._config_io import _resolve_config_dir, _snapshot_config, _atomic_write_text
from ._toml_utils import _toml_str


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
    section_start_idx = -1
    section_end_idx = -1

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == section_header:
            section_start_idx = i
            section_end_idx = len(lines)
            for j in range(i + 1, len(lines)):
                sj = lines[j].strip()
                if sj.startswith("[") and sj.endswith("]") and not sj.startswith("[["):
                    section_end_idx = j
                    break
            break

    if section_start_idx == -1:
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.append(section_header)
        for k in sorted(additions):
            lines.append(f"{k} = {_toml_str(additions[k])}")
        lines.append("")
        new_text = "\n".join(lines)
    else:
        section_body = lines[section_start_idx + 1 : section_end_idx]
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
        trailing_blanks = []
        while new_body and new_body[-1].strip() == "":
            trailing_blanks.append(new_body.pop())
        for k in sorted(pending):
            new_body.append(f"{k} = {_toml_str(pending[k])}")
        new_body.extend(trailing_blanks)
        new_lines = (
            lines[: section_start_idx + 1]
            + new_body
            + lines[section_end_idx:]
        )
        new_text = "\n".join(new_lines)

    if raw and not new_text.endswith("\n"):
        new_text += "\n"
    elif not raw:
        new_text += "\n"

    if config_path.exists():
        _snapshot_config(config_path)
    _atomic_write_text(config_path, new_text)
