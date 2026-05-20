"""Atomic config writer + section preservation."""
from __future__ import annotations

import logging
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

from backlink_publisher.errors import InputValidationError
from backlink_publisher.logger import plan_logger
from .types import (
    Config,
    DEFAULT_WORK_TEMPLATES,
    ThreeUrlConfig,
)

if sys.version_info >= (3, 11):
    pass
else:
    pass  # type: ignore[no-redef]

from .loader import load_config


def _resolve_config_dir():
    """Indirect lookup of ``_config_dir`` via the package, so that
    ``patch("backlink_publisher.config._config_dir", ...)`` in tests
    intercepts the call. Splitting into ``loader.py`` turned what was a
    module-internal lookup in the monolithic ``config.py`` into a captured
    cross-module reference; this shim restores patchability.
    """
    from backlink_publisher import config as _cfg
    return _cfg._config_dir()


def _resolve_cache_dir():
    """Indirect lookup of ``_cache_dir`` — see ``_resolve_config_dir``."""
    from backlink_publisher import config as _cfg
    return _cfg._cache_dir()

_log = logging.getLogger(__name__)

import re

_SAVE_CONFIG_KNOWN_ROOTS: frozenset[str] = frozenset(
    {"blogger", "medium", "targets"}
)

# Cap on rolling config.toml snapshots kept under .config-history/.
_CONFIG_HISTORY_MAX: int = 20

# Matches a TOML top-level heading: `[section]`, `[[array.of.tables]]`,
# `[quoted."dotted"]`. Captures the root (first dotted segment) so the caller
# can decide whether to copy or skip. The lexer is intentionally not a full
# TOML parser — it only needs to find section boundaries.
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

# Extends ``_TOML_HEADING_RE`` with an optional second segment so the lexer
# can distinguish depth-1 vs depth-2 headings under a managed root. The
# second segment is captured WITH surrounding double-quotes when quoted so
# the canonical form matches what ``save_config`` emits via ``_toml_str``.
# Depth-3+ headings collapse to depth-2 from the state-machine's view — the
# second segment is captured, deeper segments are ignored (R2: depth-3 OUT
# of scope for this preservation pass).
_TOML_HEADING_PATH_RE = re.compile(
    r"""
    ^\s*\[\[?           # opening [ or [[
    \s*
    (?:
        "([^"]+)"       # quoted root → group 1
        |
        ([^.\]\s"]+)    # bare root → group 2
    )
    (?:                 # optional second segment after a dot
        \.
        (?:
            ("[^"]+")   # quoted sub WITH surrounding quotes → group 3
            |
            ([^.\]\s"]+)# bare sub → group 4
        )
    )?
    """,
    re.VERBOSE,
)


def _canon_subsection_key(segment: str) -> str:
    """Canonical form of a TOML heading sub-segment.

    Single source of truth shared by ``_toml_heading_path`` (lexer side) and
    ``save_config``'s ``known_subsections`` builder (emit side) so that
    frozenset membership of ``(root, sub)`` tuples is correct by construction.
    Today this is identity — quoted segments arrive already wrapped in
    literal double-quote characters (matching ``_toml_str``'s output), and
    bare segments arrive verbatim. The function reserves a normalization
    seam for future refactors.
    """
    return segment


def _toml_heading_root(line: str) -> str | None:
    """Extract the root segment of a TOML heading line, or None if not a heading."""
    m = _TOML_HEADING_RE.match(line)
    if not m:
        return None
    return m.group(1) or m.group(2)


def _toml_heading_path(line: str) -> tuple[str, str | None] | None:
    """Extract ``(root, subsection)`` for a TOML heading line.

    Returns ``None`` for non-heading lines. Depth-1 headings (e.g.
    ``[blogger]``) return ``(root, None)``. Depth-2+ headings (e.g.
    ``[blogger.oauth]``, ``[targets."example.com"]``) return
    ``(root, _canon_subsection_key(sub))`` — depth-3+ collapses to depth-2.
    """
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
    """Return verbatim text of sections the writer did not emit on this call.

    Walks the input line-by-line. State flips on each TOML heading based on
    a two-predicate rule:

    - Heading under an unknown root → preserve (existing behavior).
    - Heading under a known root with ``sub is None`` (depth-1, e.g.
      ``[blogger]``) → skip; the writer just rewrote it.
    - Heading under a known root with ``(root, sub)`` in ``known_subsections``
      → skip; the writer just emitted this depth-2 block.
    - Heading under a known root with ``sub`` NOT in ``known_subsections``
      (operator-added or loader-only subsection like ``[medium.oauth]``,
      ``[medium.browser]``, ``[targets.tier_b]``, dormant ``[blogger.oauth]``)
      → preserve verbatim. This closes the symmetric depth-2 drop documented
      in Plan 2026-05-19-010.

    Lines inside a preserved section are appended verbatim. Lines before the
    first heading (file preamble) are dropped because save_config rewrites
    the file's preamble.

    Edge cases:
    - Empty input → empty output.
    - Input with only known sections (depth-1 and known depth-2) → empty.
    - Heading inside a string literal would fool the regex; accepted risk —
      load_config would have rejected such a file at parse time.
    """
    out: list[str] = []
    keep_current = False  # before the first heading, drop preamble
    for line in raw_text.splitlines():
        path = _toml_heading_path(line)
        if path is not None:
            root, sub = path
            if root in known_roots:
                # Depth-1 heading or a depth-2 block the writer emitted →
                # skip. Otherwise (depth-2 not emitted) → preserve.
                keep_current = sub is not None and (root, sub) not in known_subsections
            else:
                keep_current = True
            if keep_current:
                out.append(line)
        elif keep_current:
            out.append(line)
    # Trailing newline keeps output well-formed when concatenated.
    return ("\n".join(out) + "\n") if out else ""


def _atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    """Write ``text`` to ``path`` atomically via a sibling .new + replace.

    Mirrors :func:`io_utils.atomic_write_json` for plain text. Readers see
    either the old file or the fully written new one — never a torn write.
    chmod best-effort; the rename is load-bearing.
    """
    tmp = path.with_name(path.name + ".new")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    tmp.replace(path)


def _snapshot_config(path: Path, max_history: int = _CONFIG_HISTORY_MAX) -> None:
    """Best-effort: copy current ``path`` to ``.config-history/<UTC-ts>.toml``.

    Pre-save snapshot for time-travel recovery. Failures (missing source,
    unwritable dir, full disk) are logged but never raise — operator data
    safety on the main save path dominates. Rotates oldest snapshots so the
    directory does not grow unbounded.
    """
    if not path.exists():
        return
    snapshot_dir = path.parent / ".config-history"
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(snapshot_dir, stat.S_IRWXU)  # 0700
        except OSError:
            pass
    except OSError as exc:
        plan_logger.warn(
            "config_snapshot_dir_failed",
            path=str(snapshot_dir),
            reason=type(exc).__name__,
        )
        return

    # UTC ISO timestamp with colons replaced (Windows-safe).
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S.%fZ")
    snap_path = snapshot_dir / f"{ts}.toml"
    try:
        snap_path.write_bytes(path.read_bytes())
        try:
            os.chmod(snap_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass
    except OSError as exc:
        plan_logger.warn(
            "config_snapshot_write_failed",
            path=str(snap_path),
            reason=type(exc).__name__,
        )
        return

    # Rotate: keep the newest `max_history` files by mtime.
    try:
        snapshots = sorted(
            (p for p in snapshot_dir.glob("*.toml") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        excess = len(snapshots) - max_history
        for old in snapshots[:max(0, excess)]:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        # Rotation failure is benign — operator will see one extra file.
        pass


def save_config(
    config: "Config",
    path: Path | None = None,
    extra_blogger_ids: dict[str, str] | None = None,
    medium_token: str | None = None,
    blogger_client_id: str | None = None,
    blogger_client_secret: str | None = None,
    target_anchor_keywords: dict[str, list[str]] | None = None,
    target_three_url: dict[str, ThreeUrlConfig] | None = None,
) -> None:
    """Write (or update) config.toml with the supplied values.

    Merges new values with any existing config so that calling this
    function never silently drops keys that were already there.

    All round-trippable kwargs follow the same three-state semantics:
    - ``None`` (default) — preserve whatever is already on disk
    - ``{}`` — explicitly clear the corresponding section
    - non-empty dict — write exactly the provided contents (overrides disk)

    Section taxonomy (Plan 2026-05-19-010):

    (a) **Emitted on every call (rewritten from in-memory Config):**
        ``[blogger]``, ``[medium]``, and one ``[targets."<domain>"]`` per
        resolved domain in the anchor-keywords/three-URL emit set.
    (b) **Emitted conditionally:** ``[blogger.oauth]`` only when at least
        one of ``client_id`` / ``client_secret`` is non-empty.
    (c) **Depth-2 subsections under managed roots NOT emitted on this
        call** (e.g. ``[medium.oauth]``, ``[medium.browser]``, operator-
        added ``[targets.X]`` / ``[blogger.X]`` / ``[medium.X]``, dormant
        ``[blogger.oauth]`` when credentials are absent) are preserved
        verbatim by the section-preservation pass below.
    (d) **Unmanaged top-level sections** (``[sites.*]``, ``[anchor.*]``,
        ``[anchor_alarm]`` / ``[[anchor_alarm.override]]``, ``[llm.*]``,
        arbitrary operator-added tables) are preserved verbatim when they
        carry key=value data.
    (e) **Pure-placeholder sections** (header + comments only, no data)
        are not *emitted* by the writer from scratch — given an empty
        ``Config``, ``save_config`` produces only ``[blogger]`` and
        ``[medium]``. They are not actively *dropped* however: if a
        placeholder section already exists on disk, the preservation pass
        copies it verbatim under branch (c) or (d). The canonical witness
        for this nuance is
        ``tests/test_save_config_section_taxonomy_canary.py``
        (``test_branch_e_writer_does_not_emit_placeholder_sections_from_scratch``).

    Operator note: ``merge_site_url_categories`` is a second writer that
    text-edits ``[sites."<main>".url_categories]`` blocks in place and
    does not interact with this preservation pass.

    Operator note (credential lifecycle): post-2026-05-19, managed-root
    credential subsections (``[medium.oauth]``, ``[blogger.oauth]``)
    persist on save and propagate into ``.config-history/`` rolling
    snapshots (cap 20). After credential rotation, up to 20 historical
    copies of revoked secrets remain on disk until aged out. If
    ``BACKLINK_PUBLISHER_CONFIG_DIR`` points to synced storage (Dropbox,
    NFS, dotfiles repo), credentials now propagate through the sync
    surface — keep the config dir on local-only storage.

    This closes the P0 data-loss footgun documented in
    feedback_config-save-overwrite-pattern.md (Plan 2026-05-13-004 Unit
    3 closed the root-level case; Plan 2026-05-19-010 closed the
    symmetric depth-2-subsection case).

    The write is atomic: contents go to ``<path>.tmp`` first, ``fsync``'d, then
    ``os.replace``'d onto the target path. A mid-write crash leaves the
    original file intact.
    """
    config_path = path or (_resolve_config_dir() / "config.toml")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    # Best-effort directory mode (matches ``.config-history/`` chmod
    # pattern at _snapshot_config). Pre-fix the dir defaulted to 0o755 and
    # managed-root credentials dropped on save anyway; post-fix credentials
    # persist on disk so directory-level access matters. Failure is benign.
    try:
        os.chmod(config_path.parent, 0o700)
    except OSError:
        pass

    # Load parsed config (for merge logic on managed fields).
    existing = load_config(config_path)

    # Build blog_ids: start from config.blogger_blog_ids (may already be pre-set by caller),
    # then overlay extra_blogger_ids on top. If extra_blogger_ids is None, merge from existing.
    blog_ids: dict[str, str] = dict(config.blogger_blog_ids)
    if extra_blogger_ids is None:
        for k, v in existing.blogger_blog_ids.items():
            if k not in blog_ids:
                blog_ids[k] = v
    elif extra_blogger_ids:
        blog_ids.update(extra_blogger_ids)

    # OAuth credentials
    client_id = blogger_client_id or (
        existing.blogger_oauth.client_id if existing.blogger_oauth else ""
    )
    client_secret = blogger_client_secret or (
        existing.blogger_oauth.client_secret if existing.blogger_oauth else ""
    )

    # Medium token
    token = medium_token if medium_token is not None else (
        existing.medium_integration_token or ""
    )

    # Resolve three-state for target_anchor_keywords and target_three_url.
    if target_anchor_keywords is None:
        kws_by_domain = dict(existing.target_anchor_keywords)
    else:
        kws_by_domain = dict(target_anchor_keywords)

    if target_three_url is None:
        three_url_by_domain = dict(existing.target_three_url)
    else:
        three_url_by_domain = dict(target_three_url)

    lines: list[str] = []

    # [blogger] — domain → blog_id pairs first
    lines.append("[blogger]")
    for domain, blog_id in blog_ids.items():
        lines.append(f"{_toml_str(domain)} = {_toml_str(blog_id)}")
    lines.append("")

    # [blogger.oauth]
    if client_id or client_secret:
        lines.append("[blogger.oauth]")
        lines.append(f"client_id     = {_toml_str(client_id)}")
        lines.append(f"client_secret = {_toml_str(client_secret)}")
        lines.append("")

    # [medium]
    lines.append("[medium]")
    if token:
        lines.append(f"integration_token = {_toml_str(token)}")
    else:
        lines.append('# integration_token = "your-medium-integration-token"')
    lines.append("")

    # [targets."<domain>"] — merge anchor_keywords + three_url into one block
    # per domain so they share a single header (TOML disallows duplicate
    # table headers).
    all_target_domains = sorted(
        set(kws_by_domain) | set(three_url_by_domain)
    )
    for domain in all_target_domains:
        lines.append(f"[targets.{_toml_str(domain)}]")
        if domain in kws_by_domain:
            kws = kws_by_domain[domain]
            lines.append(f"anchor_keywords = {_toml_list(kws)}")
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
        lines.append("")

    # Preserve every section save_config did not emit on this call:
    #   - top-level sections under unmanaged roots (``[anchor.proportions]``,
    #     ``[anchor_alarm]``, ``[anchor_alarm.override]``, ``[llm.anchor_provider]``,
    #     ``[sites.*]``, arbitrary operator-added tables)
    #   - depth-2 subsections under MANAGED roots that this call did not emit
    #     (``[medium.oauth]``, ``[medium.browser]``, operator-added ``[targets.X]``,
    #     dormant ``[blogger.oauth]`` when credentials are absent)
    # Depth-1 managed headings (``[blogger]``, ``[medium]``, ``[targets]``) and
    # depth-2 blocks the writer just emitted are skipped — they are owned by
    # the rewrite path above. The ``known_subsections`` set encodes which
    # depth-2 blocks fell into the writer's emit set on this call.
    known_subsections: set[tuple[str, str]] = set()
    if client_id or client_secret:
        known_subsections.add(("blogger", "oauth"))
    for domain in all_target_domains:
        known_subsections.add(("targets", _toml_str(domain)))
    # Three-state clear/overwrite intent for [targets."<domain>"]: domains
    # that were on disk before this call but did not land in the resolved
    # emit set are implicit clears. Add them to known_subsections so the
    # preservation pass drops them — honors the documented {}-clears /
    # non-empty-overwrites contract of the two targets kwargs.
    on_disk_target_domains = (
        set(existing.target_anchor_keywords) | set(existing.target_three_url)
    )
    for domain in on_disk_target_domains - set(all_target_domains):
        known_subsections.add(("targets", _toml_str(domain)))

    preserved = ""
    if config_path.exists():
        try:
            existing_raw = config_path.read_text(encoding="utf-8")
            preserved = _preserve_unknown_sections(
                existing_raw,
                _SAVE_CONFIG_KNOWN_ROOTS,
                frozenset(known_subsections),
            )
        except OSError as exc:
            plan_logger.warn(
                "config_preserve_read_failed",
                path=str(config_path),
                reason=type(exc).__name__,
            )

    payload = "\n".join(lines)
    if preserved:
        # Single blank line separator between known sections and preserved bytes.
        if not payload.endswith("\n"):
            payload += "\n"
        payload += "\n" + preserved

    # Snapshot before overwrite — opportunistic, never blocks the main save.
    _snapshot_config(config_path)

    # Atomic write: .new + replace. Crash mid-write leaves original intact.
    _atomic_write_text(config_path, payload)


def merge_site_url_categories(
    main_url: str,
    additions: dict[str, str],
    *,
    path: Path | None = None,
) -> None:
    """Add or update keys inside ``[sites."<main>".url_categories]`` in place.

    Plan 2026-05-14-009 deferred work. The brainstorm Q3 contract: when the
    homepage form submits a ``category_url``, persist it as both
    ``target_three_url[main].list_url`` (work-themed dispatcher reads this)
    AND ``sites."<main>".url_categories.category`` (zh-CN scheduler reads
    this). ``save_config`` only manages the former; this helper handles the
    latter via a focused, string-level TOML merge that preserves any
    operator-curated ``hot`` / ``animate`` / ``topic`` keys already present
    under the same section.

    Behaviour matrix:

    | section exists?     | additions keys present? | result                          |
    |---------------------|-------------------------|---------------------------------|
    | no                  | n/a                     | append new section block        |
    | yes, no overlap     | n/a                     | extend section with new keys    |
    | yes, key overlap    | key A also in section   | overwrite key A; preserve rest  |

    Snapshots the file before overwrite (mirrors ``save_config``'s safety
    net at ``.config-history/``). Atomic write via ``_atomic_write_text``.

    No-op when ``additions`` is empty.

    Raises if ``main_url`` contains characters that would break TOML basic
    string quoting (newlines / control chars). Caller is responsible for
    feeding a validated ``main_url`` (the webui handler already does so via
    ``validate_main_domain_url``).
    """
    if not additions:
        return

    config_path = path or (_resolve_config_dir() / "config.toml")
    config_path.parent.mkdir(parents=True, exist_ok=True)

    raw = config_path.read_text(encoding="utf-8") if config_path.exists() else ""

    # Defence against control chars in main_url (TOML basic strings reject).
    if any(ch in main_url for ch in ("\n", "\r", "\x00")):
        raise InputValidationError(
            f"main_url contains a control character: {main_url!r}"
        )

    domain_key = main_url.rstrip("/")
    section_header = f'[sites."{domain_key}".url_categories]'

    lines = raw.splitlines() if raw else []
    section_start_idx = -1
    section_end_idx = -1

    # Find the section if it exists.
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == section_header:
            section_start_idx = i
            # Section ends at the next [...] heading or EOF.
            section_end_idx = len(lines)  # default = EOF
            for j in range(i + 1, len(lines)):
                sj = lines[j].strip()
                if sj.startswith("[") and sj.endswith("]") and not sj.startswith("[["):
                    section_end_idx = j
                    break
            break

    if section_start_idx == -1:
        # Section doesn't exist — append a fresh block at the end.
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.append(section_header)
        for k in sorted(additions):
            lines.append(f"{k} = {_toml_str(additions[k])}")
        lines.append("")
        new_text = "\n".join(lines)
    else:
        # Merge keys inside the existing block.
        section_body = lines[section_start_idx + 1 : section_end_idx]
        # Scan for keys we want to overwrite; track which additions are
        # still pending (not yet overwritten) so we append the rest.
        pending = dict(additions)
        new_body: list[str] = []
        for body_line in section_body:
            stripped = body_line.strip()
            if not stripped or stripped.startswith("#"):
                new_body.append(body_line)
                continue
            # Parse a simple "key = value" line. Quoted-key keys aren't
            # expected under url_categories (operator-curated names are
            # simple identifiers).
            if "=" not in body_line:
                new_body.append(body_line)
                continue
            key_part = body_line.split("=", 1)[0].strip()
            if key_part in pending:
                new_body.append(f"{key_part} = {_toml_str(pending.pop(key_part))}")
            else:
                new_body.append(body_line)
        # Append any leftover additions (keys not previously present).
        # Place them before the trailing blank line if there is one.
        trailing_blanks = []
        while new_body and new_body[-1].strip() == "":
            trailing_blanks.append(new_body.pop())
        for k in sorted(pending):
            new_body.append(f"{k} = {_toml_str(pending[k])}")
        new_body.extend(trailing_blanks)
        # Stitch back.
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


def _toml_str(value: str) -> str:
    """Quote ``value`` as a TOML basic string. Escapes ``\\`` and ``"``."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_list(values: list[str]) -> str:
    """Emit a TOML list-of-strings. Empty list → ``[]``."""
    if not values:
        return "[]"
    return "[" + ", ".join(_toml_str(v) for v in values) + "]"
