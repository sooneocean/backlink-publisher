"""Canary for save_config's 5-branch section taxonomy and file-mode invariants.

This file is the single named witness for the post-PR-#99 taxonomy locked in
``src/backlink_publisher/config/writer.py:286-304`` and summarised in
``AGENTS.md`` lines 61-67:

  (a) Emitted on every call: ``[blogger]``, ``[medium]``, one
      ``[targets."<domain>"]`` per resolved domain in the emit set.
  (b) Emitted conditionally: ``[blogger.oauth]`` only when at least one
      credential field is non-empty.
  (c) Depth-2 subsections under managed roots NOT emitted on this call
      (``[medium.oauth]``, ``[medium.browser]``, operator-added
      ``[targets.X]`` / ``[blogger.X]`` / ``[medium.X]``, dormant
      ``[blogger.oauth]``) — preserved verbatim.
  (d) Unmanaged top-level sections (``[sites.*]``, ``[anchor.*]``,
      ``[anchor_alarm]`` / ``[[anchor_alarm.override]]``, ``[llm.*]``,
      arbitrary operator-added tables) — preserved verbatim when they
      carry key=value data.
  (e) Pure-placeholder sections (header + comments only, no data) — never
      *emitted* by the writer ab initio (a fresh ``save_config`` of an empty
      ``Config`` produces only ``[blogger]`` and ``[medium]``). Placeholder
      sections already on disk are preserved verbatim by the same pass as
      branches (c)/(d); branch (e) is about emission, not deletion.

The earlier coverage is excellent but scattered: ``test_config_roundtrip.py``
locks the example.toml round-trip; ``test_config_safety_net.py`` exercises
``_preserve_unknown_sections`` byte-exactly; ``test_config_managed_root_-
subsection_roundtrip.py`` (PR #99) locks branch (c) for ``[targets.X]``,
``[blogger.oauth]``, ``[medium.oauth]``. None of them name the 5 branches
explicitly, and none assert the file-mode invariants (``0o600`` on the
config file, ``0o700`` on the parent dir and snapshot dir). This file is the
canary: one test per branch + four file-mode assertions, so a regression
that breaks the taxonomy fires a test whose name matches the branch.

Plan: docs/plans/2026-05-20-003-feat-portfolio-roundtrip-spike-quality-plan.md
Unit: A.1 (Requirements R-A1, R-A2, R-A4).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from backlink_publisher.config import Config, load_config, save_config


def _has_section_heading(text: str, heading: str) -> bool:
    """True iff ``text`` contains a line starting with ``heading``."""
    return any(line.strip().startswith(heading) for line in text.splitlines())


# ─── Branch (a): always-emitted top-level tables ─────────────────────────────


def test_branch_a_blogger_medium_targets_emitted_every_call(tmp_path: Path) -> None:
    """``[blogger]`` and ``[medium]`` are written on every call, even when the
    in-memory Config carries no blog ids / token. ``[targets."<domain>"]`` is
    emitted for each domain in the resolved emit set, which on a first save is
    driven by the kwargs to ``save_config`` (the Config field on its own does
    not seed the emit set because ``save_config`` defaults to the existing
    on-disk values when the kwarg is ``None``).
    """
    config_path = tmp_path / "config.toml"
    save_config(
        Config(blogger_blog_ids={"https://example.com": "111"}),
        path=config_path,
        target_anchor_keywords={"https://example.com": ["a", "b"]},
    )

    text = config_path.read_text(encoding="utf-8")
    assert _has_section_heading(text, "[blogger]"), "Branch (a): [blogger] missing"
    assert _has_section_heading(text, "[medium]"), "Branch (a): [medium] missing"
    assert _has_section_heading(text, '[targets."https://example.com"]'), (
        "Branch (a): [targets.<domain>] missing"
    )


# ─── Branch (b): conditional [blogger.oauth] emission ────────────────────────


def test_branch_b_blogger_oauth_emitted_only_with_credentials(tmp_path: Path) -> None:
    """``[blogger.oauth]`` appears on disk only when at least one credential
    field is non-empty. With both fields blank the section is not emitted by
    the writer (preserved-on-disk paths are tested separately under branch c).
    """
    config_path = tmp_path / "config.toml"
    cfg_empty = Config(blogger_blog_ids={"https://example.com": "111"})
    save_config(cfg_empty, path=config_path)
    assert not _has_section_heading(
        config_path.read_text(encoding="utf-8"), "[blogger.oauth]"
    ), "Branch (b): [blogger.oauth] emitted with empty credentials"

    # With credentials, the section appears.
    config_path.unlink()
    save_config(
        cfg_empty,
        path=config_path,
        blogger_client_id="id-xyz",
        blogger_client_secret="sec-xyz",
    )
    text = config_path.read_text(encoding="utf-8")
    assert _has_section_heading(text, "[blogger.oauth]"), (
        "Branch (b): [blogger.oauth] missing when credentials present"
    )
    assert "id-xyz" in text and "sec-xyz" in text


# ─── Branch (c): depth-2 subsections preserved verbatim ──────────────────────


def test_branch_c_depth2_subsections_under_managed_roots_preserved(
    tmp_path: Path,
) -> None:
    """Operator-added depth-2 subsections under managed roots
    (``[medium.oauth]``, operator-added ``[blogger.X]`` / ``[medium.X]``)
    survive a save_config call that does not emit them.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[medium]\n'
        'integration_token = "tok"\n'
        '\n'
        '[medium.oauth]\n'
        'access_token = "tok-from-oauth"\n'
        'refresh_token = "rtok"\n'
        '\n'
        '[medium.browser]\n'
        'user_data_dir = "/tmp/foo"\n',
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    save_config(cfg, path=config_path)

    text = config_path.read_text(encoding="utf-8")
    assert "[medium.oauth]" in text, "Branch (c): [medium.oauth] dropped"
    assert "tok-from-oauth" in text
    assert "[medium.browser]" in text, "Branch (c): [medium.browser] dropped"
    assert "/tmp/foo" in text


# ─── Branch (d): unmanaged sections with key=value data preserved ────────────


@pytest.mark.parametrize(
    "heading,body",
    [
        ('[sites."https://example.com".url_categories]', 'home = "https://example.com/"'),
        ('[anchor_alarm]', 'entropy_floor = 1.5'),
        ('[anchor.proportions]', 'branded = 0.55'),
        (
            '[llm.anchor_provider]',
            'base_url = "https://api.openai.com/v1"\n'
            'model = "gpt-4o-mini"\n'
            'api_key = "sk-test-fixture-key-not-a-real-secret"',
        ),
    ],
)
def test_branch_d_unmanaged_with_keyvalue_data_preserved(
    tmp_path: Path, heading: str, body: str,
) -> None:
    """Unmanaged top-level sections carrying key=value data survive verbatim.

    Covers the four section families called out by the previously-stale
    CLAUDE.md caveat (``[sites.*]``, ``[anchor_alarm]``, ``[anchor.proportions]``,
    ``[llm.anchor_provider]``). Per AGENTS.md taxonomy branch (d), each is
    preserved as long as it carries actual data.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        f'{heading}\n'
        f'{body}\n',
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    save_config(cfg, path=config_path)

    text = config_path.read_text(encoding="utf-8")
    assert heading in text, f"Branch (d): {heading} dropped after save_config"
    assert body in text, f"Branch (d): body for {heading} dropped after save_config"


# ─── Branch (e): pure-placeholder sections intentionally dropped ─────────────


def test_branch_e_writer_does_not_emit_placeholder_sections_from_scratch(
    tmp_path: Path,
) -> None:
    """Branch (e) — corrected.

    The writer never *emits* placeholder sections ab initio. Given an empty
    ``Config``, ``save_config`` produces exactly ``[blogger]``, ``[medium]``,
    and nothing else: no ``[sites.*]``, no ``[anchor_alarm]``,
    no ``[anchor.proportions]``, no ``[llm.anchor_provider]``.

    Important nuance the original writer.py:302-304 docstring understated:
    ``_preserve_unknown_sections`` (writer.py:170-188) **does** copy unmanaged
    placeholder sections verbatim when they already exist on disk. A.1's
    canary surfaced this drift; the docstring is corrected in the same PR.
    Branch (e) therefore only governs initial emission, not on-disk
    preservation — which is itself governed by branches (c) and (d).
    """
    config_path = tmp_path / "config.toml"
    save_config(Config(), path=config_path)

    text = config_path.read_text(encoding="utf-8")
    # The writer emits [blogger] and [medium] unconditionally.
    assert _has_section_heading(text, "[blogger]")
    assert _has_section_heading(text, "[medium]")
    # And nothing else from the placeholder families.
    for absent in ("[sites.", "[anchor_alarm]", "[anchor.proportions]", "[llm."):
        assert absent not in text, (
            f"Branch (e): writer unexpectedly emitted '{absent}' from an "
            f"empty Config — expected only [blogger] / [medium] on a fresh save"
        )


# ─── File-mode invariants (R-A4) ─────────────────────────────────────────────


def _file_mode(path: Path) -> int:
    """Return the file-mode bits (octal-comparable int) of ``path``."""
    return stat.S_IMODE(path.stat().st_mode)


def test_config_file_mode_is_0o600_after_save(tmp_path: Path) -> None:
    """``save_config`` rewrites ``config.toml`` via ``_atomic_write_text`` which
    chmod's the tempfile to ``0o600`` before rename. Regression guard against
    the tempfile-default-umask leak (group/world-readable secrets)."""
    config_path = tmp_path / "config.toml"
    save_config(Config(), path=config_path)
    assert _file_mode(config_path) == 0o600, (
        f"R-A4: config.toml mode is {oct(_file_mode(config_path))}, expected 0o600 — "
        "secrets in [blogger.oauth] / [medium] would be group/world-readable"
    )


def test_config_parent_dir_mode_is_0o700_after_save(tmp_path: Path) -> None:
    """``save_config`` chmods the parent directory to ``0o700`` so other users
    on the box cannot list the config file even when the file itself is 0o600.
    """
    config_dir = tmp_path / "isolated-config"
    config_path = config_dir / "config.toml"
    save_config(Config(), path=config_path)
    assert _file_mode(config_dir) == 0o700, (
        f"R-A4: parent dir mode is {oct(_file_mode(config_dir))}, expected 0o700"
    )


def test_config_file_mode_survives_caller_umask(tmp_path: Path) -> None:
    """A caller-set umask of 0o022 (default-friendly) must not cause the
    rewritten config to land at 0o644 (group/world-readable). The atomic-write
    path explicitly chmods to 0o600 between tempfile write and rename.
    """
    config_path = tmp_path / "config.toml"
    previous_umask = os.umask(0o022)
    try:
        save_config(Config(), path=config_path)
    finally:
        os.umask(previous_umask)
    assert _file_mode(config_path) == 0o600, (
        f"R-A4: under umask 0o022 the file landed at "
        f"{oct(_file_mode(config_path))} (expected 0o600)"
    )


def test_snapshot_file_mode_is_0o600_and_dir_is_0o700(tmp_path: Path) -> None:
    """``_snapshot_config`` writes pre-save snapshots to ``.config-history/``
    and chmods each snapshot to ``0o600`` inside a ``0o700`` directory.
    Regression guard: post-PR-#99 the snapshot pipeline now persists
    credential subsections, so the snapshot's file-mode is a credential
    safety boundary, not just an audit nicety (see AGENTS.md line 71).
    """
    config_path = tmp_path / "config.toml"
    # First save creates the file but does not snapshot (snapshot is taken
    # before overwrite, so the second save is what triggers _snapshot_config).
    save_config(
        Config(blogger_blog_ids={"https://example.com": "111"}),
        path=config_path,
    )
    save_config(
        Config(blogger_blog_ids={"https://example.com": "222"}),
        path=config_path,
    )

    snapshot_dir = config_path.parent / ".config-history"
    assert snapshot_dir.exists(), (
        "R-A4: .config-history/ was not created by _snapshot_config"
    )
    assert _file_mode(snapshot_dir) == 0o700, (
        f"R-A4: .config-history/ mode is {oct(_file_mode(snapshot_dir))}, "
        "expected 0o700"
    )

    snapshots = sorted(snapshot_dir.glob("*.toml"))
    assert snapshots, "R-A4: no snapshot file found in .config-history/"
    for snap in snapshots:
        assert _file_mode(snap) == 0o600, (
            f"R-A4: snapshot {snap.name} mode is {oct(_file_mode(snap))}, "
            "expected 0o600 — credential subsections persist into snapshots, "
            "so this is a leak surface"
        )


# ─── Integration: branches do not interfere ──────────────────────────────────


def test_mixed_branches_in_one_file_round_trip_cleanly(tmp_path: Path) -> None:
    """A realistic config with all five branches active in one file round-trips
    cleanly: managed roots get rewritten, branch (c)/(d) sections survive
    verbatim, branch (e) on-disk placeholders are preserved by the same pass
    (branch (e) only governs initial emission) — and the file-mode invariants
    still hold after the write.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[blogger.oauth]\n'
        'client_id     = "cid"\n'
        'client_secret = "sec"\n'
        '\n'
        '[medium]\n'
        'integration_token = "tok"\n'
        '\n'
        '[medium.oauth]\n'
        'access_token = "atk"\n'
        '\n'
        '[anchor_alarm]\n'
        'entropy_floor = 1.5\n'
        '\n'
        '[future_feature]\n'
        '# pure placeholder\n',
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    save_config(cfg, path=config_path)

    text = config_path.read_text(encoding="utf-8")

    # Branches (a), (b): managed top-level rewritten.
    assert "[blogger]" in text and "[medium]" in text
    assert "[blogger.oauth]" in text and "cid" in text

    # Branch (c): unemitted depth-2 preserved.
    assert "[medium.oauth]" in text and "atk" in text

    # Branch (d): unmanaged keyed section preserved.
    assert "[anchor_alarm]" in text and "entropy_floor" in text

    # Branch (e) — corrected: pure-placeholder is preserved verbatim by
    # _preserve_unknown_sections, not dropped. The writer simply never emits
    # placeholder sections ab initio (see
    # ``test_branch_e_writer_does_not_emit_placeholder_sections_from_scratch``).
    assert "[future_feature]" in text

    # R-A4: file-mode invariants survive the mixed write.
    assert _file_mode(config_path) == 0o600
    assert _file_mode(config_path.parent) == 0o700
