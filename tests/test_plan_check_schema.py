"""Unit tests for cli/_plan_check_schema.py.

All tested functions are pure-parse (no git, no network, no DB).
Tests cover _parse_frontmatter, _grandfathered, _validate_sha_format,
_validate_claims_schema, _check_filename_date_lock, and _read_plan_text.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from backlink_publisher.cli._plan_check_schema import (
    ClaimsBlock,
    PlanClaimsFrontmatterSchemaError,
    PlanClaimsFilenameDateMismatch,
    PlanClaimsGlobUnsupported,
    PlanClaimsMissingOnPostCutoff,
    _GRANDFATHER_CUTOFF,
    _check_filename_date_lock,
    _grandfathered,
    _parse_frontmatter,
    _read_plan_text,
    _validate_claims_schema,
    _validate_sha_format,
)


# ── _read_plan_text ───────────────────────────────────────────────────────────


class TestReadPlanText:
    def test_reads_plain_utf8(self, tmp_path: Path) -> None:
        f = tmp_path / "plan.md"
        f.write_text("---\nfoo: bar\n---\n", encoding="utf-8")
        assert _read_plan_text(f) == "---\nfoo: bar\n---\n"

    def test_strips_bom(self, tmp_path: Path) -> None:
        f = tmp_path / "plan.md"
        f.write_bytes(b"\xef\xbb\xbf---\nfoo: bar\n---\n")
        text = _read_plan_text(f)
        assert text.startswith("---")
        assert "﻿" not in text

    def test_non_utf8_raises_schema_error(self, tmp_path: Path) -> None:
        f = tmp_path / "plan.md"
        f.write_bytes(b"---\nfoo: \x80\x81\n---\n")
        with pytest.raises(PlanClaimsFrontmatterSchemaError, match="not valid UTF-8"):
            _read_plan_text(f)


# ── _parse_frontmatter ────────────────────────────────────────────────────────


class TestParseFrontmatter:
    def _valid(self, extra: str = "") -> str:
        return f"---\ndate: 2026-05-25\n{extra}---\nbody\n"

    def test_valid_returns_dict(self) -> None:
        result = _parse_frontmatter(self._valid())
        assert isinstance(result, dict)
        assert "date" in result

    def test_no_leading_fence_raises(self) -> None:
        with pytest.raises(PlanClaimsFrontmatterSchemaError, match="missing YAML frontmatter"):
            _parse_frontmatter("date: 2026-05-25\n")

    def test_missing_closing_fence_raises(self) -> None:
        with pytest.raises(PlanClaimsFrontmatterSchemaError, match="missing closing"):
            _parse_frontmatter("---\ndate: 2026-05-25\n")

    def test_empty_frontmatter_raises(self) -> None:
        with pytest.raises(PlanClaimsFrontmatterSchemaError, match="empty"):
            _parse_frontmatter("---\n---\n")

    def test_non_dict_frontmatter_raises(self) -> None:
        with pytest.raises(PlanClaimsFrontmatterSchemaError, match="top-level mapping"):
            _parse_frontmatter("---\n- item\n---\n")

    def test_invalid_yaml_raises(self) -> None:
        with pytest.raises(PlanClaimsFrontmatterSchemaError, match="not valid YAML"):
            _parse_frontmatter("---\n: :\n---\n")

    def test_preserves_all_fields(self) -> None:
        text = "---\ndate: 2026-05-25\nclaims: {}\ntitle: My Plan\n---\nbody\n"
        fm = _parse_frontmatter(text)
        assert fm["title"] == "My Plan"
        assert fm["claims"] == {}

    def test_crlf_after_opening_fence(self) -> None:
        text = "---\r\ndate: 2026-05-25\r\n---\r\n"
        fm = _parse_frontmatter(text)
        assert "date" in fm

    def test_yaml_date_becomes_date_object(self) -> None:
        fm = _parse_frontmatter(self._valid())
        assert isinstance(fm["date"], dt.date)


# ── _grandfathered ────────────────────────────────────────────────────────────


class TestGrandfathered:
    def test_date_before_cutoff_is_grandfathered(self) -> None:
        before = _GRANDFATHER_CUTOFF - dt.timedelta(days=1)
        assert _grandfathered({"date": before}) is True

    def test_date_on_cutoff_is_not_grandfathered(self) -> None:
        assert _grandfathered({"date": _GRANDFATHER_CUTOFF}) is False

    def test_date_after_cutoff_is_not_grandfathered(self) -> None:
        after = _GRANDFATHER_CUTOFF + dt.timedelta(days=1)
        assert _grandfathered({"date": after}) is False

    def test_missing_date_field_raises(self) -> None:
        with pytest.raises(PlanClaimsFrontmatterSchemaError, match="missing required `date:`"):
            _grandfathered({})

    def test_string_date_raises(self) -> None:
        with pytest.raises(PlanClaimsFrontmatterSchemaError, match="must be ISO-8601"):
            _grandfathered({"date": "2026-05-19"})

    def test_int_date_raises(self) -> None:
        with pytest.raises(PlanClaimsFrontmatterSchemaError, match="must be ISO-8601"):
            _grandfathered({"date": 20260519})

    def test_datetime_object_coerced_to_date(self) -> None:
        before = dt.datetime(2026, 5, 19, 12, 0, 0)
        assert _grandfathered({"date": before}) is True

    def test_cutoff_constant_is_may_20_2026(self) -> None:
        assert _GRANDFATHER_CUTOFF == dt.date(2026, 5, 20)


# ── _validate_sha_format ──────────────────────────────────────────────────────


class TestValidateShaFormat:
    def test_valid_7_char_short_sha(self) -> None:
        assert _validate_sha_format("a1b2c3d") is True

    def test_valid_40_char_full_sha(self) -> None:
        assert _validate_sha_format("a" * 40) is True

    def test_valid_mixed_length(self) -> None:
        assert _validate_sha_format("deadbeef123") is True

    def test_uppercase_rejected(self) -> None:
        assert _validate_sha_format("DEADBEEF123") is False

    def test_mixed_case_rejected(self) -> None:
        assert _validate_sha_format("Deadbeef") is False

    def test_too_short_6_chars(self) -> None:
        assert _validate_sha_format("a1b2c3") is False

    def test_too_long_41_chars(self) -> None:
        assert _validate_sha_format("a" * 41) is False

    def test_empty_string(self) -> None:
        assert _validate_sha_format("") is False

    def test_non_hex_chars(self) -> None:
        assert _validate_sha_format("g1234567") is False

    def test_non_string_returns_false(self) -> None:
        assert _validate_sha_format(None) is False  # type: ignore[arg-type]
        assert _validate_sha_format(12345) is False  # type: ignore[arg-type]

    def test_whitespace_rejected(self) -> None:
        assert _validate_sha_format("abc def1") is False


# ── _validate_claims_schema ───────────────────────────────────────────────────


class TestValidateClaimsSchema:
    def test_missing_claims_raises_missing(self) -> None:
        with pytest.raises(PlanClaimsMissingOnPostCutoff, match="requires a ``claims:``"):
            _validate_claims_schema({"date": dt.date(2026, 5, 25)})

    def test_empty_dict_is_explicit_optout(self) -> None:
        result = _validate_claims_schema({"claims": {}})
        assert result.is_explicit_optout is True
        assert result.paths == []
        assert result.shas == []

    def test_null_claims_is_explicit_optout(self) -> None:
        result = _validate_claims_schema({"claims": None})
        assert result.is_explicit_optout is True

    def test_unknown_key_raises(self) -> None:
        with pytest.raises(PlanClaimsFrontmatterSchemaError, match="unknown key"):
            _validate_claims_schema({"claims": {"paths": [], "bogus": []}})

    def test_claims_not_dict_raises(self) -> None:
        with pytest.raises(PlanClaimsFrontmatterSchemaError, match="must be a mapping"):
            _validate_claims_schema({"claims": ["a", "b"]})

    def test_paths_not_list_raises(self) -> None:
        with pytest.raises(PlanClaimsFrontmatterSchemaError, match="must be a list"):
            _validate_claims_schema({"claims": {"paths": "src/foo.py"}})

    def test_shas_not_list_raises(self) -> None:
        with pytest.raises(PlanClaimsFrontmatterSchemaError, match="must be a list"):
            _validate_claims_schema({"claims": {"shas": "abc1234"}})

    def test_path_entry_not_string_raises(self) -> None:
        with pytest.raises(PlanClaimsFrontmatterSchemaError, match="must be strings"):
            _validate_claims_schema({"claims": {"paths": [42]}})

    def test_glob_star_in_path_raises(self) -> None:
        with pytest.raises(PlanClaimsGlobUnsupported, match="glob character"):
            _validate_claims_schema({"claims": {"paths": ["src/*.py"]}})

    def test_glob_question_in_path_raises(self) -> None:
        with pytest.raises(PlanClaimsGlobUnsupported):
            _validate_claims_schema({"claims": {"paths": ["src/foo?.py"]}})

    def test_glob_bracket_in_path_raises(self) -> None:
        with pytest.raises(PlanClaimsGlobUnsupported):
            _validate_claims_schema({"claims": {"paths": ["src/[abc].py"]}})

    def test_sha_entry_not_string_raises(self) -> None:
        with pytest.raises(PlanClaimsFrontmatterSchemaError, match="must be strings"):
            _validate_claims_schema({"claims": {"shas": [123]}})

    def test_invalid_sha_format_raises(self) -> None:
        with pytest.raises(PlanClaimsFrontmatterSchemaError, match="not a valid sha"):
            _validate_claims_schema({"claims": {"shas": ["DEADBEEF"]}})

    def test_valid_paths_only(self) -> None:
        result = _validate_claims_schema(
            {"claims": {"paths": ["src/foo.py", "src/bar.py"]}}
        )
        assert result.paths == ["src/foo.py", "src/bar.py"]
        assert result.shas == []
        assert result.is_explicit_optout is False

    def test_valid_shas_only(self) -> None:
        result = _validate_claims_schema(
            {"claims": {"shas": ["abc1234", "deadbeef123"]}}
        )
        assert result.shas == ["abc1234", "deadbeef123"]
        assert result.paths == []
        assert result.is_explicit_optout is False

    def test_valid_paths_and_shas(self) -> None:
        result = _validate_claims_schema(
            {"claims": {"paths": ["src/x.py"], "shas": ["abc1234"]}}
        )
        assert result.paths == ["src/x.py"]
        assert result.shas == ["abc1234"]

    def test_empty_paths_and_empty_shas_is_optout(self) -> None:
        result = _validate_claims_schema({"claims": {"paths": [], "shas": []}})
        assert result.is_explicit_optout is True

    def test_valid_40_char_sha_accepted(self) -> None:
        sha = "a" * 40
        result = _validate_claims_schema({"claims": {"shas": [sha]}})
        assert result.shas == [sha]


# ── _check_filename_date_lock ─────────────────────────────────────────────────


class TestCheckFilenameDateLock:
    def _fm(self, d: dt.date) -> dict:
        return {"date": d}

    def test_matching_date_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "2026-05-25-001-my-plan.md"
        f.touch()
        _check_filename_date_lock(f, self._fm(dt.date(2026, 5, 25)))

    def test_date_mismatch_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "2026-05-25-001-my-plan.md"
        f.touch()
        with pytest.raises(PlanClaimsFilenameDateMismatch, match="disagrees with"):
            _check_filename_date_lock(f, self._fm(dt.date(2026, 5, 26)))

    def test_no_date_prefix_in_filename_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "my-plan-without-date.md"
        f.touch()
        with pytest.raises(PlanClaimsFilenameDateMismatch, match="does not match required"):
            _check_filename_date_lock(f, self._fm(dt.date(2026, 5, 25)))

    def test_bad_date_type_in_fm_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "2026-05-25-001-my-plan.md"
        f.touch()
        with pytest.raises(PlanClaimsFrontmatterSchemaError, match="must be ISO-8601"):
            _check_filename_date_lock(f, {"date": "2026-05-25"})

    def test_datetime_in_fm_coerced(self, tmp_path: Path) -> None:
        f = tmp_path / "2026-05-25-001-my-plan.md"
        f.touch()
        _check_filename_date_lock(f, {"date": dt.datetime(2026, 5, 25, 10, 0, 0)})

    def test_missing_date_in_fm_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "2026-05-25-001-my-plan.md"
        f.touch()
        with pytest.raises(PlanClaimsFrontmatterSchemaError, match="must be ISO-8601"):
            _check_filename_date_lock(f, {})

    def test_filename_without_sequence_number_accepted(self, tmp_path: Path) -> None:
        f = tmp_path / "2026-05-25-my-plan.md"
        f.touch()
        _check_filename_date_lock(f, self._fm(dt.date(2026, 5, 25)))


# ── ClaimsBlock dataclass ─────────────────────────────────────────────────────


class TestClaimsBlock:
    def test_default_construction(self) -> None:
        cb = ClaimsBlock()
        assert cb.paths == []
        assert cb.shas == []
        assert cb.is_explicit_optout is False

    def test_frozen(self) -> None:
        cb = ClaimsBlock(paths=["src/x.py"])
        with pytest.raises((AttributeError, TypeError)):
            cb.paths = []  # type: ignore[misc]

    def test_optout_flag(self) -> None:
        cb = ClaimsBlock(is_explicit_optout=True)
        assert cb.is_explicit_optout is True
