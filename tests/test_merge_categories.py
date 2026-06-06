"""Characterization tests for ``config/_merge_categories.py``.

The module does line-based TOML surgery on ``[sites."<main>".url_categories]``
blocks — exactly the fiddly, regression-prone code that had no direct test
coverage. These pin the observed behavior (pure helpers + the public
``merge_site_url_categories`` writer) so a future refactor can't silently
change config-merge semantics.

Added 2026-06-01 (no production code change).
"""

from __future__ import annotations

import tomllib

import pytest

from backlink_publisher._util.errors import InputValidationError
from backlink_publisher.config._merge_categories import (
    _append_section,
    _find_section,
    _update_section,
    merge_site_url_categories,
)

HEADER = '[sites."https://x.com".url_categories]'


# ── _find_section ────────────────────────────────────────────────────────────

class TestFindSection:
    def test_absent_returns_minus_one(self):
        assert _find_section(["[other]", "k = 1"], HEADER) == (-1, -1)

    def test_found_at_eof_end_is_len(self):
        lines = ["[other]", "a = 1", HEADER, "home = \"x\""]
        start, end = _find_section(lines, HEADER)
        assert start == 2
        assert end == len(lines)

    def test_sibling_section_terminates(self):
        lines = [HEADER, "home = \"x\"", "[next]", "z = 1"]
        assert _find_section(lines, HEADER) == (0, 2)

    def test_array_of_tables_does_not_terminate(self):
        # A ``[[...]]`` line is NOT a sibling boundary (line 27 guard).
        lines = [HEADER, "home = \"x\"", "[[item]]", "k = 1", "[real]"]
        start, end = _find_section(lines, HEADER)
        assert start == 0
        assert end == 4  # stops at "[real]", skipping "[[item]]"


# ── _append_section ──────────────────────────────────────────────────────────

class TestAppendSection:
    def test_keys_emitted_sorted_and_quoted(self):
        text = _append_section([], HEADER, {"hot": "/hot", "animate": "/a"})
        body = text.splitlines()
        assert body[0] == HEADER
        # sorted: animate before hot
        assert body[1] == 'animate = "/a"'
        assert body[2] == 'hot = "/hot"'

    def test_inserts_blank_separator_when_prev_not_blank(self):
        text = _append_section(["[other]", "a = 1"], HEADER, {"home": "/"})
        lines = text.splitlines()
        assert lines[2] == ""  # blank inserted before header
        assert lines[3] == HEADER

    def test_no_double_blank_when_prev_already_blank(self):
        text = _append_section(["[other]", "a = 1", ""], HEADER, {"home": "/"})
        lines = text.splitlines()
        assert lines[3] == HEADER  # no extra blank added


# ── _update_section ──────────────────────────────────────────────────────────

class TestUpdateSection:
    def test_existing_key_replaced_in_place(self):
        lines = [HEADER, 'home = "/old"', ""]
        out = _update_section(lines, 0, 3, {"home": "/new"})
        assert 'home = "/new"' in out.splitlines()
        assert 'home = "/old"' not in out

    def test_new_key_appended_before_trailing_blanks(self):
        lines = [HEADER, 'home = "/"', "", "[after]"]
        out = _update_section(lines, 0, 3, {"hot": "/hot"})
        olines = out.splitlines()
        # new key sits before the blank that precedes [after]
        assert olines.index('hot = "/hot"') < olines.index("[after]")
        assert olines[olines.index("[after]") - 1] == ""

    def test_comments_and_blank_lines_preserved(self):
        lines = [HEADER, "# keep me", 'home = "/"', ""]
        out = _update_section(lines, 0, 3, {"home": "/"})
        assert "# keep me" in out.splitlines()


# ── merge_site_url_categories (public writer) ────────────────────────────────

class TestMergeWriter:
    def test_empty_additions_is_noop_no_file(self, tmp_path):
        cfg = tmp_path / "config.toml"
        merge_site_url_categories("https://x.com", {}, path=cfg)
        assert not cfg.exists()

    def test_creates_new_file_parseable(self, tmp_path):
        cfg = tmp_path / "config.toml"
        merge_site_url_categories(
            "https://x.com", {"home": "/", "hot": "/hot"}, path=cfg
        )
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        assert data["sites"]["https://x.com"]["url_categories"] == {
            "home": "/",
            "hot": "/hot",
        }
        assert cfg.read_text(encoding="utf-8").endswith("\n")

    def test_trailing_slash_stripped_from_domain_key(self, tmp_path):
        cfg = tmp_path / "config.toml"
        merge_site_url_categories("https://x.com/", {"home": "/"}, path=cfg)
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        assert "https://x.com" in data["sites"]
        assert "https://x.com/" not in data["sites"]

    def test_preserves_unrelated_section(self, tmp_path):
        cfg = tmp_path / "config.toml"
        cfg.write_text('[medium]\nintegration_token = "tok"\n', encoding="utf-8")
        merge_site_url_categories("https://x.com", {"home": "/"}, path=cfg)
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        assert data["medium"]["integration_token"] == "tok"
        assert data["sites"]["https://x.com"]["url_categories"]["home"] == "/"

    def test_update_merges_without_duplicating(self, tmp_path):
        cfg = tmp_path / "config.toml"
        merge_site_url_categories("https://x.com", {"home": "/old"}, path=cfg)
        merge_site_url_categories(
            "https://x.com", {"home": "/new", "hot": "/hot"}, path=cfg
        )
        text = cfg.read_text(encoding="utf-8")
        data = tomllib.loads(text)
        cats = data["sites"]["https://x.com"]["url_categories"]
        assert cats == {"home": "/new", "hot": "/hot"}
        # in-place replace, not append → only one home line
        assert text.count("home =") == 1

    def test_control_char_in_main_url_rejected(self, tmp_path):
        cfg = tmp_path / "config.toml"
        with pytest.raises(InputValidationError):
            merge_site_url_categories("https://x\n.com", {"home": "/"}, path=cfg)

    def test_snapshot_written_when_file_existed(self, tmp_path):
        cfg = tmp_path / "config.toml"
        cfg.write_text("[medium]\n", encoding="utf-8")
        merge_site_url_categories("https://x.com", {"home": "/"}, path=cfg)
        assert (tmp_path / ".config-history").exists()

    def test_value_with_quotes_roundtrips(self, tmp_path):
        cfg = tmp_path / "config.toml"
        merge_site_url_categories(
            "https://x.com", {"home": 'a "q" b'}, path=cfg
        )
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        assert data["sites"]["https://x.com"]["url_categories"]["home"] == 'a "q" b'
