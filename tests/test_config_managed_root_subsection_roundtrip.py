"""Regression tests for managed-root depth-2 subsection preservation.

Plan 2026-05-19-010 Unit 1. Closes the actively-biting ``[medium.oauth]`` /
``[medium.browser]`` drop documented in Plan 2026-05-18-013 and the latent
symmetric ``[targets.X]`` / ``[blogger.X]`` drop.

All assertions are positive-shape per R9 — see
``docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md``
for the historical incident this file's shape choice is reacting to.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from backlink_publisher.config import (
    Config,
    MediumOAuthConfig,
    load_config,
    save_config,
)
from backlink_publisher.config._toml_utils import (
    _canon_subsection_key,
    _toml_heading_path,
    _toml_str,
)


# ─── helpers ────────────────────────────────────────────────────────────────

def _between(text: str, header: str, until_headers: tuple[str, ...] = ()) -> str:
    """Return file slice from ``header`` (inclusive) up to the next top-level
    heading (or one of ``until_headers``, or EOF). The slice excludes the
    terminating heading and excludes trailing blank padding.
    """
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == header)
    except StopIteration as exc:
        raise AssertionError(
            f"header {header!r} not present in file. Body was:\n{text}"
        ) from exc
    end = len(lines)
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if not until_headers or stripped in until_headers or True:
                end = i
                break
    return "\n".join(lines[start:end]).rstrip()


# ─── R7: operator-added [targets.X] subsection ──────────────────────────────


def test_targets_operator_added_subsection_survives_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "config.toml"
    src.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[targets.tier_b]\n'
        'quota = 5\n'
        'weight = 0.8\n',
        encoding="utf-8",
    )

    cfg = load_config(src)
    save_config(cfg, path=src)
    text = src.read_text(encoding="utf-8")

    block = _between(text, "[targets.tier_b]")
    assert "[targets.tier_b]" in block
    assert "quota = 5" in block
    assert "weight = 0.8" in block


def test_multiple_targets_subsections_all_survive(tmp_path: Path) -> None:
    src = tmp_path / "config.toml"
    src.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[targets.tier_a]\n'
        'quota = 3\n'
        '\n'
        '[targets.tier_b]\n'
        'quota = 5\n'
        '\n'
        '[targets.shadow_pool]\n'
        'enabled = true\n',
        encoding="utf-8",
    )

    cfg = load_config(src)
    save_config(cfg, path=src)
    text = src.read_text(encoding="utf-8")

    assert "quota = 3" in _between(text, "[targets.tier_a]")
    assert "quota = 5" in _between(text, "[targets.tier_b]")
    assert "enabled = true" in _between(text, "[targets.shadow_pool]")


def test_interleaved_managed_and_operator_added_targets(tmp_path: Path) -> None:
    """Managed ``[targets."<domain>"]`` (rewritten) coexists with operator-added
    ``[targets.custom_tier]`` (preserved verbatim) in the same file.
    """
    src = tmp_path / "config.toml"
    src.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[targets."domain.example.com"]\n'
        'anchor_keywords = ["k1", "k2"]\n'
        '\n'
        '[targets.custom_tier]\n'
        'threshold = 0.5\n',
        encoding="utf-8",
    )

    cfg = load_config(src)
    assert cfg.target_anchor_keywords.get("domain.example.com") == ["k1", "k2"], (
        "Loader must populate target_anchor_keywords for the managed domain — "
        "fixture precondition for the test."
    )

    save_config(cfg, path=src)
    text = src.read_text(encoding="utf-8")

    # Managed block re-emitted from Config (anchor_keywords round-tripped).
    managed = _between(text, '[targets."domain.example.com"]')
    assert "anchor_keywords" in managed
    assert "k1" in managed and "k2" in managed

    # Operator-added block preserved verbatim.
    custom = _between(text, "[targets.custom_tier]")
    assert "threshold = 0.5" in custom


def test_targets_subsection_with_comments_preserved(tmp_path: Path) -> None:
    src = tmp_path / "config.toml"
    src.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[targets.tier_b]\n'
        '# operator note: experimental tier\n'
        'quota = 5\n'
        '# todo: tune\n'
        'weight = 0.8\n',
        encoding="utf-8",
    )

    cfg = load_config(src)
    save_config(cfg, path=src)
    text = src.read_text(encoding="utf-8")

    block = _between(text, "[targets.tier_b]")
    assert "# operator note: experimental tier" in block
    assert "# todo: tune" in block
    assert "quota = 5" in block
    assert "weight = 0.8" in block


# ─── R8a: [medium.oauth] + [medium.browser] (the actively-biting cases) ──────


def test_medium_oauth_survives_roundtrip(tmp_path: Path) -> None:
    """``[medium.oauth]`` with non-empty credentials must round-trip.

    Loader gate at ``loader.py`` requires BOTH ``client_id`` AND
    ``client_secret`` to populate ``Config.medium_oauth``; the fixture
    supplies both, and the assertion checks the value after the second
    load (not just file-byte presence) — defends against a regression
    where the section text survives but the values get mangled.
    """
    src = tmp_path / "config.toml"
    src.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[medium]\n'
        '\n'
        '[medium.oauth]\n'
        'client_id = "medium-client-id-fixture"\n'
        'client_secret = "medium-client-secret-fixture"\n',
        encoding="utf-8",
    )

    cfg_before = load_config(src)
    assert cfg_before.medium_oauth == MediumOAuthConfig(
        client_id="medium-client-id-fixture",
        client_secret="medium-client-secret-fixture",
    ), "Loader fixture precondition: Config.medium_oauth populated."

    save_config(cfg_before, path=src)
    cfg_after = load_config(src)
    assert cfg_after.medium_oauth == cfg_before.medium_oauth

    text = src.read_text(encoding="utf-8")
    assert "[medium.oauth]" in text
    assert "medium-client-id-fixture" in text
    assert "medium-client-secret-fixture" in text


def test_medium_browser_user_data_dir_survives_roundtrip(tmp_path: Path) -> None:
    """``[medium.browser]`` ``user_data_dir`` must round-trip.

    Anti-spurious-pass guard: the fixture path is explicitly NOT the
    loader's fallback ``_resolve_config_dir() / "chrome-profile-default"``.
    A uuid suffix makes the value unforgeable so any test that accidentally
    passes by reading the fallback would still fail this assertion.
    """
    custom_path = f"/tmp/bp-medium-profile-{uuid.uuid4()}"
    src = tmp_path / "config.toml"
    src.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[medium]\n'
        '\n'
        '[medium.browser]\n'
        f'user_data_dir = "{custom_path}"\n',
        encoding="utf-8",
    )

    cfg_before = load_config(src)
    assert str(cfg_before.medium_user_data_dir) == custom_path, (
        "Loader fixture precondition: Config.medium_user_data_dir matches "
        "operator-supplied path, not the chrome-profile-default fallback."
    )

    save_config(cfg_before, path=src)
    cfg_after = load_config(src)
    assert str(cfg_after.medium_user_data_dir) == custom_path

    text = src.read_text(encoding="utf-8")
    assert "[medium.browser]" in text
    assert custom_path in text


# ─── R8b: [blogger.oauth] conditional-emit gate ─────────────────────────────


def test_blogger_oauth_only_client_id_no_secret_block_survives(tmp_path: Path) -> None:
    """``[blogger.oauth]`` with only ``client_id`` (no ``client_secret``).

    The loader gate requires BOTH fields to populate ``Config.blogger_oauth``;
    when one is missing, the loader returns ``None``. ``save_config``'s emit
    gate ``if client_id or client_secret`` then also evaluates False (both
    are empty in the resolved Config). The on-disk ``[blogger.oauth]`` block
    must therefore survive the round-trip verbatim — preservation, not
    re-emit. This exercises R3's "in-static-emit-set vs actually-emitted-on-
    this-call" distinction.
    """
    src = tmp_path / "config.toml"
    src.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[blogger.oauth]\n'
        'client_id = "leftover-id"\n',
        encoding="utf-8",
    )

    cfg_before = load_config(src)
    assert cfg_before.blogger_oauth is None, (
        "Loader fixture precondition: client_secret missing → blogger_oauth=None."
    )

    save_config(cfg_before, path=src)
    text = src.read_text(encoding="utf-8")

    block = _between(text, "[blogger.oauth]")
    assert "client_id" in block
    assert "leftover-id" in block


# ─── Canonical-match contract: lexer output == writer emit form ────────────


def test_canonical_subsection_key_matches_writer_emit_form() -> None:
    """The frozenset membership check in ``_preserve_unknown_sections``
    requires that ``_toml_heading_path`` returns the SAME canonical form
    ``save_config`` writes into ``known_subsections`` via ``_toml_str``.
    Lock the contract by construction: lexing a heading the writer
    emitted must produce a tuple that matches the writer's add-to-set
    call, for every domain shape the writer may emit.
    """
    for domain in (
        "example.com",
        "domain.example.com",
        "https://example.com",
        "a.b.c.d",
        "domain-with-dashes.example.com",
    ):
        emitted = f"[targets.{_toml_str(domain)}]"
        lexed = _toml_heading_path(emitted)
        assert lexed == ("targets", _toml_str(domain)), (
            f"Canonical mismatch for {domain!r}: lexer={lexed!r} "
            f"emit_form={('targets', _toml_str(domain))!r}"
        )


# ─── Three-state × preservation interaction (not covered by Plan) ───────────


def test_explicit_clear_drops_on_disk_target_subsection(tmp_path: Path) -> None:
    """``target_anchor_keywords={}`` clears the on-disk ``[targets."<d>"]``
    block even after the depth-2-preservation extension. Locks the
    interaction between the documented ``{}``-clears contract and the new
    preservation pass — the on-disk-minus-emit-set computation in
    ``save_config`` must add cleared domains to ``known_subsections`` so
    the preservation pass drops them.
    """
    src = tmp_path / "config.toml"
    src.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[targets."domain.example.com"]\n'
        'anchor_keywords = ["k1", "k2"]\n',
        encoding="utf-8",
    )
    cfg = load_config(src)
    assert cfg.target_anchor_keywords.get("domain.example.com") == ["k1", "k2"]

    save_config(cfg, path=src, target_anchor_keywords={})
    cfg_after = load_config(src)
    assert cfg_after.target_anchor_keywords == {}


def test_none_preserves_anchor_keywords_when_three_url_overwritten(
    tmp_path: Path,
) -> None:
    """``target_anchor_keywords=None`` (preserve) must keep on-disk
    ``[targets."<d>"]`` even when ``target_three_url`` overwrites a
    different domain — the three-state contract is per-kwarg, not
    per-section. Locks the interaction reviewers flagged where a future
    refactor could clear the wrong domain.
    """
    from backlink_publisher.config import ThreeUrlConfig

    src = tmp_path / "config.toml"
    src.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[targets."old.example.com"]\n'
        'anchor_keywords = ["legacy"]\n',
        encoding="utf-8",
    )
    cfg = load_config(src)
    assert cfg.target_anchor_keywords.get("old.example.com") == ["legacy"]

    three_url_cfg = ThreeUrlConfig(
        main_url="https://new.example.com/",
        list_url="https://new.example.com/list",
        branded_pool=["new brand"],
        partial_pool=["new partial"],
        exact_pool=["new exact"],
    )
    save_config(
        cfg,
        path=src,
        target_three_url={"new.example.com": three_url_cfg},
    )
    cfg_after = load_config(src)
    assert cfg_after.target_anchor_keywords.get("old.example.com") == ["legacy"]


# ─── Anti-regression: existing characterization tests still pass ────────────
# (See tests/test_config_roundtrip.py:
#   - test_save_config_inplace_preserves_all_sections
#   - test_save_config_inplace_preserves_sections_with_keyvalue_data
#   - test_save_config_preserves_unknown_top_level_section
#  and tests/test_config_v2_pools.py:
#   - test_save_config_preserves_v2_fields_verbatim
# All four must continue to pass unmodified; the suite-level CI run guards
# that contract — no per-file duplicate assertion needed here.)
