"""Tests for SEC-1: credential redaction in config_history snapshots.

The redaction function replaces credential values in TOML content
with "****" before writing config history snapshots, preventing
credential leakage through rolling snapshots.
"""
from __future__ import annotations

from pathlib import Path


# =========================================================================
# RED tests for _redact_toml_credential_values
# =========================================================================

def test_redact_client_id_values(tmp_path: Path) -> None:
    """SEC-1: client_id value is redacted in [blogger.oauth] section."""
    from backlink_publisher.config._config_io import _redact_toml_credential_values

    toml = '''[blogger]
"domain.com" = "12345"

[blogger.oauth]
client_id     = "abc-123-secret"
client_secret = "super-secret-xyz"
'''
    result = _redact_toml_credential_values(toml)
    assert '"abc-123-secret"' not in result, \
        f"client_id value leaked: {result!r}"
    assert '"super-secret-xyz"' not in result, \
        f"client_secret value leaked: {result!r}"
    # Structure preserved
    assert "[blogger.oauth]" in result
    assert '"domain.com" = "12345"' in result


def test_redact_medium_integration_token(tmp_path: Path) -> None:
    """SEC-1: integration_token is redacted (backward compat)."""
    from backlink_publisher.config._config_io import _redact_toml_credential_values

    toml = '''[medium]
integration_token = "tok_live_abc123xyz"
'''
    result = _redact_toml_credential_values(toml)
    assert "tok_live_abc123xyz" not in result, \
        f"integration_token leaked: {result!r}"
    assert '"****"' in result, f"Expected redacted value: {result!r}"
    assert "[medium]" in result


def test_redact_llm_api_key(tmp_path: Path) -> None:
    """SEC-1: llm anchor_provider api_key is redacted."""
    from backlink_publisher.config._config_io import _redact_toml_credential_values

    toml = '''[llm]

[llm.anchor_provider]
provider = "openai"
api_key = "sk-proj-abcdef123456"
'''
    result = _redact_toml_credential_values(toml)
    assert "sk-proj-abcdef123456" not in result, \
        f"api_key leaked: {result!r}"
    assert '"****"' in result


def test_redact_does_not_touch_non_credential_lines(tmp_path: Path) -> None:
    """SEC-1: non-credential values are preserved as-is."""
    from backlink_publisher.config._config_io import _redact_toml_credential_values

    toml = '''[blogger]
"x.com" = "111111"
"y.com" = "222222"

[medium]
# integration_token is commented out
'''
    result = _redact_toml_credential_values(toml)
    assert '"111111"' in result
    assert '"222222"' in result
    # The comment referencing integration_token is preserved (no key=value to redact).
    assert "# integration_token is commented out" in result


def test_redact_no_credentials_empty_safe(tmp_path: Path) -> None:
    """SEC-1: file with no credential keys is returned unchanged."""
    from backlink_publisher.config._config_io import _redact_toml_credential_values

    toml = '''[blogger]
"x.com" = "111111"

[targets."example.com"]
main_url = "https://example.com"
'''
    result = _redact_toml_credential_values(toml)
    assert result == toml, f"Redaction altered non-credential file: {result!r}"


def test_redact_preserves_toml_comments(tmp_path: Path) -> None:
    """SEC-1: inline comments after credential values survive redaction."""
    from backlink_publisher.config._config_io import _redact_toml_credential_values

    toml = '''[blogger.oauth]
client_id     = "abc-123"  # from the dev portal
client_secret = "xyz-789"  # rotate quarterly
'''
    result = _redact_toml_credential_values(toml)
    assert "# from the dev portal" in result, \
        f"Comment stripped: {result!r}"
    assert "# rotate quarterly" in result, \
        f"Comment stripped: {result!r}"
    assert '"abc-123"' not in result
    assert '"xyz-789"' not in result


def test_redact_medium_oauth(tmp_path: Path) -> None:
    """SEC-1: [medium.oauth] client_id/client_secret are redacted."""
    from backlink_publisher.config._config_io import _redact_toml_credential_values

    toml = '''[medium.oauth]
client_id     = "medium-app-123"
client_secret = "medium-secret-456"
'''
    result = _redact_toml_credential_values(toml)
    assert "medium-app-123" not in result
    assert "medium-secret-456" not in result
    assert '"****"' in result


# =========================================================================
# Integration: _snapshot_config writes redacted content
# =========================================================================

def test_snapshot_config_redacts_credentials(tmp_path: Path) -> None:
    """SEC-1: _snapshot_config writes a snapshot with redacted credentials."""
    from backlink_publisher.config._config_io import _snapshot_config

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        '[blogger]\n"x.com" = "111"\n\n'
        '[blogger.oauth]\n'
        'client_id     = "secret-abc"\n'
        'client_secret = "secret-xyz"\n',
        encoding="utf-8",
    )

    _snapshot_config(cfg_path, max_history=3)

    history_dir = tmp_path / ".config-history"
    assert history_dir.is_dir()
    snapshots = sorted(history_dir.glob("*.toml"))
    assert len(snapshots) >= 1

    snap_text = snapshots[0].read_text(encoding="utf-8")
    # Redacted
    assert "secret-abc" not in snap_text
    assert "secret-xyz" not in snap_text
    # Structure preserved
    assert "[blogger.oauth]" in snap_text
    assert '"x.com" = "111"' in snap_text
    # Redacted markers present
    assert '"****"' in snap_text


def test_snapshot_config_preserves_non_credential_file(tmp_path: Path) -> None:
    """SEC-1: _snapshot_config preserves files without credential keys."""
    from backlink_publisher.config._config_io import _snapshot_config

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        '[blogger]\n"x.com" = "111"\n',
        encoding="utf-8",
    )

    _snapshot_config(cfg_path, max_history=3)

    history_dir = tmp_path / ".config-history"
    snapshots = sorted(history_dir.glob("*.toml"))
    snap_text = snapshots[0].read_text(encoding="utf-8")
    assert snap_text == cfg_path.read_text(encoding="utf-8")


def test_snapshot_config_missing_file_does_not_crash(tmp_path: Path) -> None:
    """SEC-1: _snapshot_config handles missing source file gracefully."""
    from backlink_publisher.config._config_io import _snapshot_config

    cfg_path = tmp_path / "config.toml"
    # Do NOT create the file.
    # Should not raise.
    _snapshot_config(cfg_path, max_history=3)

    history_dir = tmp_path / ".config-history"
    # No file to snapshot, so no history dir.
    assert not history_dir.exists() or len(list(history_dir.iterdir())) == 0


def test_rotate_snapshots_with_content(tmp_path: Path) -> None:
    """SEC-1: rotate_snapshots accepts optional pre-processed content."""
    from backlink_publisher.persistence.safe_write import rotate_snapshots

    source = tmp_path / "source.toml"
    source.write_text('key = "original"\n', encoding="utf-8")
    history_dir = tmp_path / ".history"

    # Pass redacted content instead of reading from path.
    rotate_snapshots(
        source,
        history_dir,
        file_suffix=".toml",
        max_history=3,
        content='key = "****"\n',
    )

    snapshots = sorted(history_dir.glob("*.toml"))
    assert len(snapshots) == 1
    assert snapshots[0].read_text(encoding="utf-8") == 'key = "****"\n'
