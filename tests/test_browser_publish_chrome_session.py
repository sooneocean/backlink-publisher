"""Tests for browser_publish.chrome_session — Plan 2026-05-21-001 Unit 1.

Mocks subprocess, HTTP, and Playwright surfaces — CI never launches Chrome.
Coverage targets:
  - shared path helpers (incl. per-channel BACKLINK_PUBLISHER_BIND_CHANNEL)
  - listener identity verification (lsof + ps wiring)
  - profile permission enforcement
  - PID file atomic write / read / unlink
  - reap_orphan_publish_chrome (PID reuse defence)
  - ChromeAttachSession attach vs launch lifecycle, teardown
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher._util.errors import DependencyError
from backlink_publisher.publishing.browser_publish import (
    BrowserPublishRecipe,
    ChromeAttachSession,
    ChromeSessionError,
)
from backlink_publisher.publishing.browser_publish import chrome_session as cs


# ---------------------------------------------------------------------------
# Shared path helpers
# ---------------------------------------------------------------------------


class TestPathHelpers:
    def test_profile_dir_default(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR", raising=False)
        monkeypatch.delenv("BACKLINK_PUBLISHER_BIND_CHANNEL", raising=False)
        result = cs._chrome_profile_dir()
        assert result == tmp_path / "real-chrome-profile"

    def test_profile_dir_env_override_bypasses_channel_split(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR", str(tmp_path / "custom"))
        monkeypatch.setenv("BACKLINK_PUBLISHER_BIND_CHANNEL", "telegraph")
        assert cs._chrome_profile_dir() == tmp_path / "custom"

    def test_profile_dir_per_channel(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR", raising=False)
        monkeypatch.setenv("BACKLINK_PUBLISHER_BIND_CHANNEL", "velog")
        assert cs._chrome_profile_dir() == tmp_path / "real-chrome-profile" / "velog"

    def test_profile_dir_channel_isolates_two_channels(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR", raising=False)
        monkeypatch.setenv("BACKLINK_PUBLISHER_BIND_CHANNEL", "telegraph")
        a = cs._chrome_profile_dir()
        monkeypatch.setenv("BACKLINK_PUBLISHER_BIND_CHANNEL", "velog")
        b = cs._chrome_profile_dir()
        assert a != b

    @pytest.mark.parametrize("bad", ["../etc", "channel/with/slash", "with space", "with;semi", "UPPER"])
    def test_profile_dir_channel_whitelist_rejects(self, monkeypatch, tmp_path, bad):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR", raising=False)
        monkeypatch.setenv("BACKLINK_PUBLISHER_BIND_CHANNEL", bad)
        with pytest.raises(ChromeSessionError, match="chrome_invalid_bind_channel"):
            cs._chrome_profile_dir()

    def test_port_default(self, monkeypatch):
        monkeypatch.delenv("BACKLINK_PUBLISHER_REAL_CHROME_PORT", raising=False)
        assert cs._chrome_port() == 9222

    def test_port_env_override(self, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_REAL_CHROME_PORT", "9999")
        assert cs._chrome_port() == 9999

    def test_port_invalid_raises(self, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_REAL_CHROME_PORT", "not-a-number")
        with pytest.raises(ChromeSessionError, match="chrome_cdp_unavailable"):
            cs._chrome_port()

    @pytest.mark.parametrize("bad", ["0", "65536", "-1"])
    def test_port_out_of_range_raises(self, monkeypatch, bad):
        monkeypatch.setenv("BACKLINK_PUBLISHER_REAL_CHROME_PORT", bad)
        with pytest.raises(ChromeSessionError, match="chrome_cdp_unavailable"):
            cs._chrome_port()

    def test_chrome_binary_env_override_missing(self, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_REAL_CHROME_BIN", "/nonexistent/chrome")
        assert cs._chrome_binary() is None


# ---------------------------------------------------------------------------
# Listener identity verification
# ---------------------------------------------------------------------------


class TestListenerIdentity:
    def test_verified_when_chrome_bin_and_profile_in_cmdline(self):
        chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        profile = Path("/tmp/spike-profile")
        cmdline = (
            f"{chrome_bin} --remote-debugging-port=9222 "
            f"--user-data-dir={profile} about:blank"
        )
        with (
            patch.object(cs, "_lsof_listener_pid", return_value=12345),
            patch.object(cs, "_ps_command", return_value=cmdline),
        ):
            verified, reason = cs._verify_listener_is_chrome(9222, chrome_bin, profile)
        assert verified is True
        assert reason == "ok"

    def test_unverified_when_no_listener(self):
        with patch.object(cs, "_lsof_listener_pid", return_value=None):
            verified, reason = cs._verify_listener_is_chrome(
                9222, "/x/chrome", Path("/tmp/p")
            )
        assert verified is False
        assert reason == "lsof_no_listener"

    def test_unverified_when_chrome_bin_absent_from_cmdline(self):
        with (
            patch.object(cs, "_lsof_listener_pid", return_value=12345),
            patch.object(cs, "_ps_command", return_value="/usr/bin/python imposter.py"),
        ):
            verified, reason = cs._verify_listener_is_chrome(
                9222, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", Path("/tmp/p")
            )
        assert verified is False
        assert reason == "cmdline_missing_chrome_bin"

    def test_unverified_when_profile_path_absent_from_cmdline(self):
        chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        cmdline = f"{chrome_bin} --remote-debugging-port=9222 --user-data-dir=/other/profile about:blank"
        with (
            patch.object(cs, "_lsof_listener_pid", return_value=12345),
            patch.object(cs, "_ps_command", return_value=cmdline),
        ):
            verified, reason = cs._verify_listener_is_chrome(
                9222, chrome_bin, Path("/tmp/spike-profile")
            )
        assert verified is False
        assert reason == "cmdline_missing_profile_path"


# ---------------------------------------------------------------------------
# Profile permissions
# ---------------------------------------------------------------------------


class TestEnsureProfilePerms:
    def test_creates_dir_and_chmods(self, tmp_path):
        profile = tmp_path / "profile"
        # Don't pre-create — _ensure_profile_perms handles mkdir.
        cs._ensure_profile_perms(profile)
        assert profile.exists()
        assert stat.S_IMODE(profile.stat().st_mode) == 0o700

    def test_tightens_loose_perms(self, tmp_path):
        profile = tmp_path / "profile"
        profile.mkdir()
        os.chmod(profile, 0o755)
        cs._ensure_profile_perms(profile)
        assert stat.S_IMODE(profile.stat().st_mode) == 0o700

    def test_raises_chrome_profile_unsafe_perms_when_owner_mismatch(self, tmp_path):
        profile = tmp_path / "profile"
        profile.mkdir()
        with patch.object(cs.os, "geteuid", return_value=os.geteuid() + 1):
            with pytest.raises(ChromeSessionError, match="chrome_profile_unsafe_perms"):
                cs._ensure_profile_perms(profile)


# ---------------------------------------------------------------------------
# PID file
# ---------------------------------------------------------------------------


class TestPidFile:
    def test_write_then_read_roundtrips(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cs._write_pid_file(54321, 1700000000.123)
        record = cs._read_pid_file()
        assert record == {"pid": 54321, "start_time": 1700000000.123}

    def test_pid_file_is_0600(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cs._write_pid_file(11, 0.0)
        mode = stat.S_IMODE(cs._pid_file_path().stat().st_mode)
        assert mode == 0o600

    def test_unlink_missing_is_noop(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cs._unlink_pid_file()  # should not raise

    def test_read_missing_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        assert cs._read_pid_file() is None


# ---------------------------------------------------------------------------
# Orphan reap
# ---------------------------------------------------------------------------


class TestReapOrphan:
    def test_noop_when_no_pid_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        result = cs.reap_orphan_publish_chrome()
        assert result["action"] == "noop"

    def test_cleans_when_pid_dead(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cs._write_pid_file(99999999, 0.0)

        def fake_kill(pid, sig):
            raise ProcessLookupError()

        with patch.object(cs.os, "kill", side_effect=fake_kill):
            result = cs.reap_orphan_publish_chrome()
        assert result["action"] == "cleaned_stale"
        assert result["reason"] == "pid_gone"
        assert cs._pid_file_path().exists() is False

    def test_refuses_to_signal_pid_reused_by_unrelated_process(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cs._write_pid_file(12345, 0.0)

        kills: list[tuple[int, int]] = []

        def fake_kill(pid, sig):
            if sig == 0:
                return None  # alive
            kills.append((pid, sig))

        with (
            patch.object(cs.os, "kill", side_effect=fake_kill),
            patch.object(cs, "_chrome_binary", return_value="/x/chrome"),
            patch.object(cs, "_ps_command", return_value="/usr/bin/python imposter.py"),
        ):
            result = cs.reap_orphan_publish_chrome()
        assert result["action"] == "cleaned_stale"
        assert result["reason"] == "pid_reused_by_unrelated_process"
        assert kills == []  # we MUST NOT have signaled it
        assert cs._pid_file_path().exists() is False

    def test_reaps_when_pid_matches_chrome_with_profile(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("BACKLINK_PUBLISHER_BIND_CHANNEL", raising=False)
        monkeypatch.delenv("BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR", raising=False)
        cs._write_pid_file(54321, 0.0)
        chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        profile = tmp_path / "real-chrome-profile"
        cmdline = f"{chrome_bin} --remote-debugging-port=9222 --user-data-dir={profile} about:blank"

        signaled: list[tuple[int, int]] = []

        def fake_kill(pid, sig):
            if sig == 0:
                return None
            signaled.append((pid, sig))

        with (
            patch.object(cs.os, "kill", side_effect=fake_kill),
            patch.object(cs, "_chrome_binary", return_value=chrome_bin),
            patch.object(cs, "_ps_command", return_value=cmdline),
        ):
            result = cs.reap_orphan_publish_chrome()
        assert result["action"] == "reaped"
        assert result["pid"] == 54321
        assert signaled == [(54321, cs.signal_SIGTERM())]
        assert cs._pid_file_path().exists() is False


# ---------------------------------------------------------------------------
# BrowserPublishRecipe
# ---------------------------------------------------------------------------


class TestBrowserPublishRecipe:
    def test_frozen_dataclass(self):
        recipe = BrowserPublishRecipe(
            channel="hashnode",
            compose_url="https://hashnode.com/new",
            publish_flow=lambda page, payload: "https://hashnode.com/p/123",
        )
        assert recipe.channel == "hashnode"
        with pytest.raises(Exception):  # FrozenInstanceError or dataclass error
            recipe.channel = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ChromeAttachSession lifecycle
# ---------------------------------------------------------------------------


def _stub_playwright_factory(page_to_return):
    """Build a stub for ``sync_playwright()`` returning ``page_to_return``."""
    pw_cm = MagicMock()
    pw = MagicMock()
    browser = MagicMock()
    context = MagicMock()
    context.pages = [page_to_return]
    browser.contexts = [context]
    browser.new_context.return_value = context
    pw.chromium.connect_over_cdp.return_value = browser
    pw_cm.__enter__.return_value = pw
    pw_cm.__exit__.return_value = None
    return lambda: pw_cm, pw_cm, browser


class TestChromeAttachSession:
    @pytest.fixture
    def stub_page(self):
        return MagicMock(name="playwright_page")

    @pytest.fixture
    def chrome_bin(self):
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

    @pytest.fixture
    def profile(self, tmp_path):
        p = tmp_path / "profile"
        return p

    def test_launch_mode_writes_pid_file_and_owns(
        self, monkeypatch, tmp_path, stub_page, chrome_bin, profile
    ):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        proc = MagicMock(pid=10001)
        popen = MagicMock(return_value=proc)
        # CDP probe: first call (existence check) → None; later (post-launch) → real version
        version_responses = iter([None, {"webSocketDebuggerUrl": "ws://127.0.0.1:9999/devtools/browser/abc"}])
        version_probe = MagicMock(side_effect=lambda base: next(version_responses))
        factory, pw_cm, browser = _stub_playwright_factory(stub_page)

        session = ChromeAttachSession(
            "hashnode",
            port=9999,
            profile_dir=profile,
            chrome_bin=chrome_bin,
            popen=popen,
            playwright_factory=factory,
            version_probe=version_probe,
        )
        with session as page:
            assert page is stub_page
            assert session._owned is True
            assert cs._read_pid_file() == {"pid": 10001, "start_time": pytest.approx(
                cs._read_pid_file()["start_time"], abs=2.0
            )}
            popen.assert_called_once()
            launch_args = popen.call_args[0][0]
            assert chrome_bin in launch_args
            assert f"--user-data-dir={profile}" in launch_args
            assert "--remote-allow-origins=*" in launch_args
            assert popen.call_args[1]["start_new_session"] is True

        # Teardown: terminate + wait called, no killpg used.
        proc.terminate.assert_called_once()
        proc.wait.assert_called()
        assert cs._pid_file_path().exists() is False

    def test_attach_mode_refuses_without_opt_in(
        self, monkeypatch, tmp_path, chrome_bin, profile
    ):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("BACKLINK_PUBLISHER_REAL_CHROME_ATTACH", raising=False)
        version_probe = MagicMock(return_value={"webSocketDebuggerUrl": "ws://x"})

        session = ChromeAttachSession(
            "hashnode",
            port=9999,
            profile_dir=profile,
            chrome_bin=chrome_bin,
            popen=MagicMock(),
            version_probe=version_probe,
        )
        with pytest.raises(ChromeSessionError, match="chrome_cdp_unavailable"):
            session.__enter__()

    def test_attach_mode_rejects_unverified_listener(
        self, monkeypatch, tmp_path, chrome_bin, profile
    ):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("BACKLINK_PUBLISHER_REAL_CHROME_ATTACH", "1")
        version_probe = MagicMock(return_value={"webSocketDebuggerUrl": "ws://x"})
        verifier = MagicMock(return_value=(False, "cmdline_missing_chrome_bin"))

        session = ChromeAttachSession(
            "hashnode",
            port=9999,
            profile_dir=profile,
            chrome_bin=chrome_bin,
            popen=MagicMock(),
            version_probe=version_probe,
            verify_listener=verifier,
        )
        with pytest.raises(ChromeSessionError, match="chrome_cdp_foreign_listener"):
            session.__enter__()

    def test_attach_mode_passes_verification_and_does_not_own(
        self, monkeypatch, tmp_path, stub_page, chrome_bin, profile
    ):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("BACKLINK_PUBLISHER_REAL_CHROME_ATTACH", "1")
        version_probe = MagicMock(return_value={"webSocketDebuggerUrl": "ws://x"})
        verifier = MagicMock(return_value=(True, "ok"))
        factory, _, _ = _stub_playwright_factory(stub_page)
        popen = MagicMock()

        session = ChromeAttachSession(
            "hashnode",
            port=9999,
            profile_dir=profile,
            chrome_bin=chrome_bin,
            popen=popen,
            playwright_factory=factory,
            version_probe=version_probe,
            verify_listener=verifier,
        )
        with session as page:
            assert page is stub_page
            assert session._owned is False
            popen.assert_not_called()

        # No PID file written; nothing to unlink (popen never called).
        assert session._proc is None

    def test_chrome_not_available_raises(
        self, monkeypatch, tmp_path, profile
    ):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("BACKLINK_PUBLISHER_REAL_CHROME_BIN", "/nonexistent/chrome")
        session = ChromeAttachSession(
            "hashnode",
            port=9999,
            profile_dir=profile,
            chrome_bin=None,  # force binary discovery
            popen=MagicMock(),
            version_probe=MagicMock(return_value=None),
        )
        with pytest.raises(ChromeSessionError, match="chrome_not_available"):
            session.__enter__()

    def test_launch_failure_when_version_never_appears(
        self, monkeypatch, tmp_path, chrome_bin, profile
    ):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        # Shrink the connect-timeout so the test doesn't take 10s.
        monkeypatch.setattr(cs, "_CONNECT_TIMEOUT_S", 0.1)
        proc = MagicMock(pid=22222)
        popen = MagicMock(return_value=proc)
        version_probe = MagicMock(return_value=None)  # never ready

        session = ChromeAttachSession(
            "hashnode",
            port=9999,
            profile_dir=profile,
            chrome_bin=chrome_bin,
            popen=popen,
            version_probe=version_probe,
        )
        with pytest.raises(ChromeSessionError, match="chrome_launch_failed"):
            session.__enter__()
        # Failed launch must have torn down the popen'd proc + cleaned PID file.
        proc.terminate.assert_called()
        assert cs._pid_file_path().exists() is False

    def test_teardown_uses_terminate_not_killpg(
        self, monkeypatch, tmp_path, stub_page, chrome_bin, profile
    ):
        """Probe 1 finding: terminate() suffices; killpg EPERMs on macOS."""
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        proc = MagicMock(pid=33333)
        popen = MagicMock(return_value=proc)
        version_responses = iter([None, {"webSocketDebuggerUrl": "ws://x"}])
        version_probe = MagicMock(side_effect=lambda base: next(version_responses))
        factory, _, _ = _stub_playwright_factory(stub_page)

        with patch.object(cs.os, "killpg") as killpg_spy:
            session = ChromeAttachSession(
                "hashnode",
                port=9999,
                profile_dir=profile,
                chrome_bin=chrome_bin,
                popen=popen,
                playwright_factory=factory,
                version_probe=version_probe,
            )
            with session:
                pass
            killpg_spy.assert_not_called()
        proc.terminate.assert_called_once()
        proc.wait.assert_called()

    def test_propagates_exception_from_within_with_block(
        self, monkeypatch, tmp_path, stub_page, chrome_bin, profile
    ):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        proc = MagicMock(pid=44444)
        popen = MagicMock(return_value=proc)
        version_responses = iter([None, {"webSocketDebuggerUrl": "ws://x"}])
        version_probe = MagicMock(side_effect=lambda base: next(version_responses))
        factory, _, _ = _stub_playwright_factory(stub_page)

        session = ChromeAttachSession(
            "hashnode",
            port=9999,
            profile_dir=profile,
            chrome_bin=chrome_bin,
            popen=popen,
            playwright_factory=factory,
            version_probe=version_probe,
        )
        with pytest.raises(ValueError, match="recipe error"):
            with session:
                raise ValueError("recipe error")
        # Still tore down.
        proc.terminate.assert_called_once()


# ---------------------------------------------------------------------------
# Shared module is single-source for bind path helpers
# ---------------------------------------------------------------------------


class TestBindReusesShared:
    """Bind chrome_backend.py must re-export shared helpers, not duplicate logic."""

    def test_bind_uses_shared_profile_dir(self, monkeypatch, tmp_path):
        from backlink_publisher.cli._bind.chrome_backend import _chrome_profile_dir as bind_pd

        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR", raising=False)
        monkeypatch.setenv("BACKLINK_PUBLISHER_BIND_CHANNEL", "telegraph")
        # bind path now reflects per-channel split — confirming shared source.
        assert bind_pd() == tmp_path / "real-chrome-profile" / "telegraph"

    def test_bind_translates_session_error_to_launch_error(self, monkeypatch):
        """ChromeSessionError from shared must surface as ChromeLaunchError in bind."""
        from backlink_publisher.cli._bind.chrome_backend import _chrome_port as bind_port
        from backlink_publisher.cli._bind.driver import ChromeLaunchError

        monkeypatch.setenv("BACKLINK_PUBLISHER_REAL_CHROME_PORT", "not-numeric")
        with pytest.raises(ChromeLaunchError, match="chrome_cdp_unavailable"):
            bind_port()
