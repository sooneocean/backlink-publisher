"""Tests for save_config's three new managed roots — Plan 2026-05-20-003 Unit A.2.

Locks the round-trip semantics for ``[ghpages]`` / ``[hashnode]`` / ``[writeas]``
after they join ``[blogger]`` / ``[medium]`` / ``[targets]`` as managed roots
in ``_SAVE_CONFIG_KNOWN_ROOTS``. Three behaviors are non-negotiable:

  1. Round-trip works: a section on disk → ``load_config`` → ``save_config``
     → same section reappears on disk (was a P0 regression risk before A.2
     added emission code — the preservation pass drops depth-1 headings of
     known roots, so without emission the operator's routing fields would
     be lost on every save).
  2. Operator-added depth-2 subsections (e.g. ``[ghpages.routing]``) are
     preserved verbatim under taxonomy branch (c) — they're under a managed
     root but the writer didn't emit them on this call.
  3. The three new sections **do not carry PATs/tokens** — those live in
     separate 0600 sidecar JSON files (``ghpages-token.json`` etc.) per
     SEC-3 of Plan 2026-05-19-006. The TOML blocks carry only routing
     fields (``repo`` / ``publication_id`` / ``collection_alias`` etc.).

Plan: docs/plans/2026-05-20-003-feat-portfolio-roundtrip-spike-quality-plan.md
Requirements: R-A1 (no loss across save_config), R-A3 (channels join managed
roots), R-A4 (file-mode invariants survive — covered by the A.1 canary, not
re-asserted here).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backlink_publisher.config import (
    Config,
    GhpagesConfig,
    HashnodeConfig,
    WriteAsConfig,
    load_config,
    save_config,
)


# ─── Round-trip without channel kwargs ───────────────────────────────────────


def test_ghpages_block_survives_save_when_only_on_disk(tmp_path: Path) -> None:
    """load_config picks up [ghpages] from disk → save_config emits it back
    from cfg.ghpages even when no ghpages_config kwarg is passed. Regression
    guard: before A.2 added emission code, ghpages was unmanaged → preserved
    verbatim. After A.2 it's managed, so emission must round-trip the section
    or operator routing config is lost on every save.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[ghpages]\n'
        'repo = "operator/site-repo"\n'
        'branch = "main"\n'
        'path_template = "blog/{date}-{slug}.md"\n',
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    save_config(cfg, path=config_path)

    text = config_path.read_text(encoding="utf-8")
    assert "[ghpages]" in text, "A.2 regression: [ghpages] dropped on save"
    assert 'operator/site-repo' in text
    assert '"main"' in text
    assert '"blog/{date}-{slug}.md"' in text


def test_hashnode_block_survives_save_when_only_on_disk(tmp_path: Path) -> None:
    """Same regression guard as ghpages — [hashnode] must round-trip without
    requiring an explicit kwarg."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[hashnode]\n'
        'publication_id = "abc-123-pub-id"\n'
        'host = "blog.example.com"\n',
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    save_config(cfg, path=config_path)

    text = config_path.read_text(encoding="utf-8")
    assert "[hashnode]" in text, "A.2 regression: [hashnode] dropped on save"
    assert "abc-123-pub-id" in text
    assert "blog.example.com" in text


def test_writeas_block_survives_save_when_only_on_disk(tmp_path: Path) -> None:
    """Same regression guard — [writeas] must round-trip."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[writeas]\n'
        'collection_alias = "operator-handle"\n'
        'api_base = "https://write.as/api"\n',
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    save_config(cfg, path=config_path)

    text = config_path.read_text(encoding="utf-8")
    assert "[writeas]" in text, "A.2 regression: [writeas] dropped on save"
    assert "operator-handle" in text


# ─── Channel kwargs overwrite cfg.<channel> ─────────────────────────────────


def test_ghpages_config_kwarg_overrides_existing(tmp_path: Path) -> None:
    """save_config(cfg, ghpages_config=GhpagesConfig(repo='new')) replaces
    the on-disk [ghpages] block with the kwarg-supplied content. Mirrors the
    medium_token / blogger_client_id three-state pattern."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[ghpages]\n'
        'repo = "old/repo"\n'
        'branch = "gh-pages"\n'
        'path_template = "_posts/{date}-{slug}.md"\n',
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    save_config(
        cfg,
        path=config_path,
        ghpages_config=GhpagesConfig(
            repo="new/repo", branch="main", path_template="content/{slug}.md",
        ),
    )

    text = config_path.read_text(encoding="utf-8")
    assert "new/repo" in text and "old/repo" not in text
    assert '"main"' in text
    assert "content/{slug}.md" in text


# ─── Branch (c): operator-added depth-2 subsection preserved ────────────────


def test_operator_depth2_subsection_under_ghpages_preserved(
    tmp_path: Path,
) -> None:
    """An operator-added ``[ghpages.experimental]`` block under the managed
    root must survive verbatim under taxonomy branch (c) — the writer didn't
    emit it on this call, so ``_preserve_unknown_sections`` copies it through.
    Empty ``known_subsections`` for the new channels is the design choice
    that enables this.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[ghpages]\n'
        'repo = "operator/site"\n'
        'branch = "main"\n'
        'path_template = "_posts/{date}-{slug}.md"\n'
        '\n'
        '[ghpages.experimental]\n'
        'draft_branch = "drafts"\n'
        'unlisted_path = "_drafts/{slug}.md"\n',
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    save_config(cfg, path=config_path)

    text = config_path.read_text(encoding="utf-8")
    # Both depth-1 and depth-2 blocks survive
    assert "[ghpages]" in text
    assert "[ghpages.experimental]" in text, (
        "Branch (c): operator-added depth-2 subsection lost — empty "
        "known_subsections for ghpages should fall through to verbatim "
        "preservation"
    )
    assert "drafts" in text and "_drafts/{slug}.md" in text


# ─── Branch (d): existing managed roots not disturbed by channel emission ───


def test_mixed_write_does_not_disturb_blogger_medium_or_targets(
    tmp_path: Path,
) -> None:
    """save_config(cfg, blogger_client_id=..., ghpages_config=...) must not
    lose ``[medium]``, ``[targets.X]``, or any other managed root. Regression
    guard against ordering bugs in the lines.append sequence introduced by
    A.2."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[blogger.oauth]\n'
        'client_id     = "old-id"\n'
        'client_secret = "old-sec"\n'
        '\n'
        '[medium]\n'
        'integration_token = "med-tok"\n'
        '\n'
        '[targets."https://example.com"]\n'
        'anchor_keywords = ["alpha", "beta"]\n'
        '\n'
        '[ghpages]\n'
        'repo = "old/repo"\n'
        'branch = "gh-pages"\n'
        'path_template = "_posts/{date}-{slug}.md"\n',
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    save_config(
        cfg,
        path=config_path,
        blogger_client_id="new-id",
        blogger_client_secret="new-sec",
        ghpages_config=GhpagesConfig(
            repo="new/repo", branch="main", path_template="_posts/{slug}.md",
        ),
    )

    text = config_path.read_text(encoding="utf-8")
    # New values landed
    assert "new-id" in text and "new-sec" in text and "old-id" not in text
    assert "new/repo" in text
    # Other managed roots survived
    assert "med-tok" in text
    assert "alpha" in text and "beta" in text
    assert '[targets."https://example.com"]' in text


# ─── Idempotency: load → save → save again → identical ──────────────────────


def test_round_trip_is_idempotent_for_all_three_channels(tmp_path: Path) -> None:
    """A file with all three channels and a blogger block round-trips byte-
    identically across two consecutive saves. Catches accidental field
    reordering or default-value drift introduced by A.2's emission code.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[ghpages]\n'
        'repo = "operator/site"\n'
        'branch = "main"\n'
        'path_template = "_posts/{date}-{slug}.md"\n'
        '\n'
        '[hashnode]\n'
        'publication_id = "pub-xyz"\n'
        'host = ""\n'
        '\n'
        '[writeas]\n'
        'collection_alias = "feed"\n'
        'api_base = "https://write.as/api"\n',
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    save_config(cfg, path=config_path)
    first_save = config_path.read_text(encoding="utf-8")

    save_config(load_config(config_path), path=config_path)
    second_save = config_path.read_text(encoding="utf-8")

    assert first_save == second_save, (
        "A.2 emission is not idempotent — second save diverged from first. "
        "Likely cause: field order or default value not matching the loader's "
        "expectations."
    )


# ─── Reverted PR #108 channels remain unmanaged → verbatim preserved ────────


def test_devto_section_still_unmanaged_after_a2(tmp_path: Path) -> None:
    """The reverted Phase 4 ``[devto]`` channel was never added to
    ``_SAVE_CONFIG_KNOWN_ROOTS`` (see Plan 2026-05-20-003 §Scope Boundaries
    and the binding_status._DOFOLLOW_BY_CHANNEL map). An operator who edits
    ``[devto]`` into config.toml should see it preserved verbatim under
    branch (d), not silently dropped by A.2's known-roots expansion.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[devto]\n'
        'api_key_placeholder = "operator-edit-only"\n'
        'organization_id = ""\n',
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    save_config(cfg, path=config_path)

    text = config_path.read_text(encoding="utf-8")
    assert "[devto]" in text, (
        "Branch (d): unmanaged [devto] dropped by A.2's preservation pass"
    )
    assert "operator-edit-only" in text


# ─── Channel sections do NOT carry PATs (SEC-3 audit) ───────────────────────


def test_emitted_channel_blocks_carry_only_routing_fields(tmp_path: Path) -> None:
    """The three new managed roots emit ONLY routing fields. Tokens/PATs MUST
    live in sidecar 0600 JSON files (ghpages-token.json etc.) per SEC-3 of
    Plan 2026-05-19-006. This test locks the writer against accidentally
    learning to emit a ``pat`` / ``token`` / ``api_key`` field — a regression
    here would spread credentials into config.toml + every snapshot.
    """
    config_path = tmp_path / "config.toml"
    save_config(
        Config(),
        path=config_path,
        ghpages_config=GhpagesConfig(repo="o/r"),
        hashnode_config=HashnodeConfig(publication_id="pub"),
        writeas_config=WriteAsConfig(collection_alias="c"),
    )

    text = config_path.read_text(encoding="utf-8")

    # Sections present
    assert "[ghpages]" in text
    assert "[hashnode]" in text
    assert "[writeas]" in text

    # Field allow-list per channel — no credentials anywhere
    forbidden_field_names = ("pat", "token", "api_key", "client_secret",
                              "access_token", "refresh_token")
    for chan in ("[ghpages]", "[hashnode]", "[writeas]"):
        # Extract the block body until the next heading
        idx = text.find(chan)
        nxt = text.find("\n[", idx + 1)
        body = text[idx : (nxt if nxt != -1 else len(text))]
        for forbidden in forbidden_field_names:
            assert f"{forbidden} =" not in body, (
                f"SEC-3 violation: {chan} block emitted a {forbidden!r} field. "
                f"Tokens live in 0600 sidecar JSON files, not config.toml."
            )
