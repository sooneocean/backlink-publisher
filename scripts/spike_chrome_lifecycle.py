"""Plan 2026-05-21-001 Unit 0 spike — Chrome lifecycle pre-Unit-1 validation.

Throwaway. Empirically validates four lifecycle assumptions before Unit 1
commits ChromeAttachSession contract. Outputs feed `docs/refs/2026-05-21-
chrome-lifecycle-spike.md`. Delete (or archive to docs/solutions/research/)
after Unit 1 lands.

Four probes (each independent; selectable via subcommand):

  1  process-group teardown — does start_new_session=True + killpg() actually
     reap renderer/GPU/network helpers + release SQLite profile lock fast?
  2  CDP listener identity — can lsof + ps reliably attribute the port-9222
     listener to our chrome PID + executable, cross-OS?
  3  profile permission — does chmod 0700 on profile dir hold under macOS
     SIP/sandbox; what failure modes surface?
  4  per-channel profile env var — does the env-keyed channel split that
     plan D3 assumes actually exist on this branch / main? (negative
     finding on main: see CHROME-PROFILE-DIR section.)

Usage:
    # Activate the project venv first (needs requests + websocket-client
    # only for probe 1's existence-check side; probes 2-4 are stdlib).
    python scripts/spike_chrome_lifecycle.py 1   # spawn + teardown loop
    python scripts/spike_chrome_lifecycle.py 2 --port 9222
    python scripts/spike_chrome_lifecycle.py 3
    python scripts/spike_chrome_lifecycle.py 4

    # Run all four sequentially:
    python scripts/spike_chrome_lifecycle.py all

Every probe prints structured JSONL lines to stdout. Human-readable
diagnostics go to stderr. Capture stdout to feed the report:

    python scripts/spike_chrome_lifecycle.py all \\
        > /tmp/spike-chrome-lifecycle.jsonl 2> /tmp/spike-chrome-lifecycle.log
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


# ---------------------------------------------------------------------------
# Reused fragments mirrored from cli/_bind/chrome_backend.py so the spike
# stays runnable without project imports (intentional — keeps it throwaway).
# ---------------------------------------------------------------------------


def discover_chrome_binary() -> str | None:
    """Mirror of chrome_backend._chrome_binary() for the spike."""
    raw = os.environ.get("BACKLINK_PUBLISHER_REAL_CHROME_BIN")
    if raw:
        path = Path(raw).expanduser()
        return str(path) if path.exists() else None

    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def emit(event: str, **fields) -> None:
    """Single-line JSON to stdout for the report to consume."""
    payload = {"event": event, "ts": time.time(), **fields}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def diag(msg: str) -> None:
    """Human-readable note to stderr (not consumed by the report)."""
    sys.stderr.write(f"[spike] {msg}\n")
    sys.stderr.flush()


def free_port() -> int:
    """Bind to 0, read what kernel assigned, close — race-y but adequate for spike."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def temp_profile_dir(prefix: str = "spike-chrome-"):
    base = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield base
    finally:
        try:
            shutil.rmtree(base, ignore_errors=True)
        except Exception:
            pass


def wait_for_cdp(port: int, timeout_s: float = 10.0) -> bool:
    """Poll /json/version until 200 or timeout."""
    import urllib.request

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=1.0
            ) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def chrome_pids_under(parent_pid: int) -> list[int]:
    """All descendant PIDs of parent_pid via pgrep -P (macOS + Linux compat)."""
    out: list[int] = []
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(parent_pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.strip().splitlines():
            try:
                out.append(int(line.strip()))
            except ValueError:
                continue
    except Exception:
        pass
    return out


def all_descendants(parent_pid: int, depth: int = 5) -> list[int]:
    """BFS PID tree via pgrep -P. Caps recursion to avoid runaway."""
    seen: set[int] = set()
    frontier = [parent_pid]
    levels = 0
    while frontier and levels < depth:
        next_frontier: list[int] = []
        for pid in frontier:
            for child in chrome_pids_under(pid):
                if child in seen:
                    continue
                seen.add(child)
                next_frontier.append(child)
        frontier = next_frontier
        levels += 1
    return sorted(seen)


def pgid_of(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return None


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# ---------------------------------------------------------------------------
# Probe 1: process-group teardown
# ---------------------------------------------------------------------------


def probe_1_teardown(iterations: int = 5) -> None:
    """Spawn Chrome, observe helper tree, tear down via killpg, measure lock release."""
    chrome_bin = discover_chrome_binary()
    if not chrome_bin:
        emit("probe1_skip", reason="chrome_not_available")
        diag("Chrome binary not found — probe 1 skipped.")
        return

    emit("probe1_start", chrome_bin=chrome_bin, iterations=iterations)
    diag(f"probe 1 begin — {iterations} iterations against {chrome_bin}")

    with temp_profile_dir("spike-probe1-") as profile:
        for i in range(iterations):
            port = free_port()
            args = [
                chrome_bin,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ]
            t_spawn = time.monotonic()
            proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            ready = wait_for_cdp(port)
            t_ready = time.monotonic()
            descendants_before = all_descendants(proc.pid)
            pgid = pgid_of(proc.pid)
            emit(
                "probe1_iter_ready",
                iter=i,
                pid=proc.pid,
                pgid=pgid,
                port=port,
                cdp_ready=ready,
                spawn_to_ready_s=round(t_ready - t_spawn, 3),
                descendant_count=len(descendants_before),
                descendants=descendants_before[:20],
            )

            # Variant A: proc.terminate() only (mirrors main's current code)
            t_term = time.monotonic()
            try:
                proc.terminate()
            except Exception as exc:
                emit("probe1_iter_terminate_err", iter=i, err=repr(exc))
            time.sleep(2.0)
            still_alive_after_terminate = [
                pid for pid in descendants_before if pid_alive(pid)
            ]
            parent_alive = pid_alive(proc.pid)
            emit(
                "probe1_iter_after_terminate",
                iter=i,
                parent_alive=parent_alive,
                descendants_alive=len(still_alive_after_terminate),
                descendants_alive_pids=still_alive_after_terminate[:20],
                wait_s=round(time.monotonic() - t_term, 3),
            )

            # Variant B: killpg if anything survives
            if pgid is not None and (parent_alive or still_alive_after_terminate):
                try:
                    os.killpg(pgid, signal.SIGTERM)
                    emit("probe1_iter_killpg_sigterm", iter=i, pgid=pgid)
                except (ProcessLookupError, PermissionError) as exc:
                    emit("probe1_iter_killpg_sigterm_err", iter=i, err=repr(exc))
                # Wait up to 5s for graceful shutdown
                t_killwait = time.monotonic()
                while time.monotonic() - t_killwait < 5.0:
                    if not pid_alive(proc.pid) and not any(
                        pid_alive(pid) for pid in descendants_before
                    ):
                        break
                    time.sleep(0.1)
                still_alive_after_sigterm = [
                    pid for pid in descendants_before if pid_alive(pid)
                ]
                emit(
                    "probe1_iter_after_sigterm",
                    iter=i,
                    parent_alive=pid_alive(proc.pid),
                    descendants_alive=len(still_alive_after_sigterm),
                    wait_s=round(time.monotonic() - t_killwait, 3),
                )

                if pid_alive(proc.pid) or still_alive_after_sigterm:
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                        emit("probe1_iter_killpg_sigkill", iter=i, pgid=pgid)
                    except (ProcessLookupError, PermissionError) as exc:
                        emit("probe1_iter_killpg_sigkill_err", iter=i, err=repr(exc))
                    time.sleep(0.5)
                    final_alive = [pid for pid in descendants_before if pid_alive(pid)]
                    emit(
                        "probe1_iter_after_sigkill",
                        iter=i,
                        parent_alive=pid_alive(proc.pid),
                        descendants_alive=len(final_alive),
                    )

            # Reap zombie if any
            try:
                proc.wait(timeout=1.0)
            except Exception:
                pass

            # Test: how fast can a fresh chrome reuse the profile after teardown?
            t_reuse_start = time.monotonic()
            port2 = free_port()
            reuse_args = [
                chrome_bin,
                f"--remote-debugging-port={port2}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ]
            reuse_proc = subprocess.Popen(
                reuse_args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            reuse_ready = wait_for_cdp(port2, timeout_s=15.0)
            emit(
                "probe1_iter_profile_reuse",
                iter=i,
                reuse_pid=reuse_proc.pid,
                reuse_port=port2,
                cdp_ready=reuse_ready,
                reuse_spawn_to_ready_s=round(time.monotonic() - t_reuse_start, 3),
            )
            # Tear down reuse chrome
            reuse_pgid = pgid_of(reuse_proc.pid)
            if reuse_pgid is not None:
                try:
                    os.killpg(reuse_pgid, signal.SIGTERM)
                    time.sleep(1.0)
                    if pid_alive(reuse_proc.pid):
                        os.killpg(reuse_pgid, signal.SIGKILL)
                except Exception:
                    pass
            try:
                reuse_proc.wait(timeout=2.0)
            except Exception:
                pass

    emit("probe1_done")


# ---------------------------------------------------------------------------
# Probe 2: CDP listener identity verification
# ---------------------------------------------------------------------------


def lsof_listener_pid(port: int) -> int | None:
    """Parse `lsof -i:<port> -sTCP:LISTEN -Fp` to extract PID."""
    try:
        result = subprocess.run(
            ["lsof", "-iTCP:" + str(port), "-sTCP:LISTEN", "-Fp", "-n", "-P"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        emit("probe2_lsof_missing")
        return None
    for line in result.stdout.splitlines():
        if line.startswith("p"):
            try:
                return int(line[1:])
            except ValueError:
                continue
    return None


def ps_describe(pid: int) -> dict:
    """`ps -o comm=,command= -p <pid>` shape, plus best-effort exe path on Linux."""
    info: dict = {"pid": pid}
    try:
        result = subprocess.run(
            ["ps", "-o", "comm=,command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        line = result.stdout.strip()
        if line:
            parts = line.split(None, 1)
            info["comm"] = parts[0] if parts else None
            info["command"] = parts[1] if len(parts) > 1 else None
    except Exception as exc:
        info["ps_err"] = repr(exc)

    # /proc/<pid>/exe — Linux only, ignored on macOS
    proc_exe = Path(f"/proc/{pid}/exe")
    if proc_exe.exists():
        try:
            info["exe"] = os.readlink(proc_exe)
        except OSError as exc:
            info["exe_err"] = repr(exc)

    return info


def probe_2_listener_identity() -> None:
    """Spawn Chrome, verify listener via lsof+ps, compare against expected binary."""
    chrome_bin = discover_chrome_binary()
    if not chrome_bin:
        emit("probe2_skip", reason="chrome_not_available")
        return

    port = free_port()
    emit("probe2_start", chrome_bin=chrome_bin, port=port)

    with temp_profile_dir("spike-probe2-") as profile:
        args = [
            chrome_bin,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ]
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        ready = wait_for_cdp(port)
        emit("probe2_chrome_ready", pid=proc.pid, cdp_ready=ready)

        # Wait briefly for socket to be in LISTEN state (CDP /json/version
        # response often precedes the kernel's TIME_WAIT cleanup of any
        # previous listener).
        time.sleep(0.5)

        listener_pid = lsof_listener_pid(port)
        emit("probe2_lsof_listener_pid", listener_pid=listener_pid, expected_pid=proc.pid)

        if listener_pid is not None:
            desc = ps_describe(listener_pid)
            emit("probe2_listener_describe", **desc)
            chrome_basename = Path(chrome_bin).name
            comm = desc.get("comm") or ""
            command = desc.get("command") or ""
            emit(
                "probe2_listener_attribution",
                comm=comm,
                command=command,
                chrome_basename=chrome_basename,
                expected_path=chrome_bin,
                comm_matches=chrome_basename in comm,
                command_contains_chrome_bin=chrome_bin in command,
                command_contains_profile=str(profile) in command,
            )

        # Probe negative case: another listener on a different port should NOT
        # be attributed to chrome.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as ghost:
            ghost.bind(("127.0.0.1", 0))
            ghost.listen(1)
            ghost_port = ghost.getsockname()[1]
            ghost_listener = lsof_listener_pid(ghost_port)
            ghost_desc = ps_describe(ghost_listener) if ghost_listener else {}
            emit(
                "probe2_ghost_listener",
                ghost_port=ghost_port,
                ghost_pid=ghost_listener,
                ghost_describe=ghost_desc,
                same_as_chrome=ghost_listener == proc.pid,
            )

        # Teardown
        pgid = pgid_of(proc.pid)
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGTERM)
                time.sleep(1.0)
                if pid_alive(proc.pid):
                    os.killpg(pgid, signal.SIGKILL)
            except Exception:
                pass
        try:
            proc.wait(timeout=3.0)
        except Exception:
            pass

    emit("probe2_done")


# ---------------------------------------------------------------------------
# Probe 3: profile permission verification
# ---------------------------------------------------------------------------


def probe_3_profile_perms() -> None:
    """Stat profile dir + chmod 0700 + observe fail modes."""
    with temp_profile_dir("spike-probe3-") as profile:
        emit("probe3_start", profile=str(profile))
        st = profile.stat()
        emit(
            "probe3_initial",
            mode_octal=oct(stat.S_IMODE(st.st_mode)),
            uid=st.st_uid,
            gid=st.st_gid,
        )

        try:
            os.chmod(profile, 0o700)
            chmod_err = None
        except OSError as exc:
            chmod_err = repr(exc)
        st_after = profile.stat()
        emit(
            "probe3_after_chmod_0700",
            mode_octal=oct(stat.S_IMODE(st_after.st_mode)),
            chmod_err=chmod_err,
            applied=stat.S_IMODE(st_after.st_mode) == 0o700,
        )

        # Test: subdir creation under 0o700 profile
        subdir = profile / "Default"
        try:
            subdir.mkdir(parents=True, exist_ok=True)
            subdir_err = None
        except OSError as exc:
            subdir_err = repr(exc)
        emit(
            "probe3_subdir_create",
            path=str(subdir),
            success=subdir.exists(),
            err=subdir_err,
        )

        # Test: file write under 0o700 profile
        sentinel = profile / "Default" / "Cookies"
        try:
            sentinel.write_text("test")
            write_err = None
        except OSError as exc:
            write_err = repr(exc)
        emit(
            "probe3_file_write",
            path=str(sentinel),
            success=sentinel.exists(),
            err=write_err,
        )

        # Test: chmod to overly-permissive 0o755 should also succeed and
        # produce an empirical baseline for "drift detection" feature ideas.
        try:
            os.chmod(profile, 0o755)
            chmod_755_err = None
        except OSError as exc:
            chmod_755_err = repr(exc)
        emit(
            "probe3_after_chmod_0755",
            mode_octal=oct(stat.S_IMODE(profile.stat().st_mode)),
            chmod_err=chmod_755_err,
        )

    emit("probe3_done")


# ---------------------------------------------------------------------------
# Probe 4: per-channel profile env var behavior
# ---------------------------------------------------------------------------


def probe_4_per_channel_profile() -> None:
    """Observe whether _chrome_profile_dir honors BACKLINK_PUBLISHER_BIND_CHANNEL."""
    emit("probe4_start")

    # Import the real chrome_backend to test what it ACTUALLY does on this
    # branch (vs what the plan assumes). Adds project src/ to sys.path.
    project_root = Path(__file__).resolve().parents[1]
    src_path = project_root / "src"
    if src_path.is_dir() and str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
    try:
        from backlink_publisher.cli._bind.chrome_backend import _chrome_profile_dir
    except Exception as exc:
        emit("probe4_import_failed", err=repr(exc))
        return

    saved_env = {
        k: os.environ.get(k)
        for k in (
            "BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR",
            "BACKLINK_PUBLISHER_BIND_CHANNEL",
            "BACKLINK_PUBLISHER_CONFIG_DIR",
        )
    }
    try:
        with temp_profile_dir("spike-probe4-config-") as fake_config_dir:
            os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = str(fake_config_dir)
            # Strip any user override of profile dir so we observe the default path.
            os.environ.pop("BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR", None)

            # Baseline: no BACKLINK_PUBLISHER_BIND_CHANNEL set.
            os.environ.pop("BACKLINK_PUBLISHER_BIND_CHANNEL", None)
            baseline_profile = _chrome_profile_dir()
            emit(
                "probe4_baseline",
                profile=str(baseline_profile),
                contains_channel_segment=False,
            )

            # Channel A
            os.environ["BACKLINK_PUBLISHER_BIND_CHANNEL"] = "telegraph"
            profile_a = _chrome_profile_dir()
            emit(
                "probe4_channel_a",
                channel="telegraph",
                profile=str(profile_a),
                differs_from_baseline=str(profile_a) != str(baseline_profile),
                contains_channel_segment="telegraph" in str(profile_a),
            )

            # Channel B
            os.environ["BACKLINK_PUBLISHER_BIND_CHANNEL"] = "velog"
            profile_b = _chrome_profile_dir()
            emit(
                "probe4_channel_b",
                channel="velog",
                profile=str(profile_b),
                differs_from_baseline=str(profile_b) != str(baseline_profile),
                differs_from_channel_a=str(profile_b) != str(profile_a),
                contains_channel_segment="velog" in str(profile_b),
            )

            # Plan D3 assumption: <config_dir>/real-chrome-profile/<channel>/
            expected_a = fake_config_dir / "real-chrome-profile" / "telegraph"
            emit(
                "probe4_plan_d3_assumption",
                expected_telegraph=str(expected_a),
                actual_telegraph=str(profile_a),
                matches=str(profile_a) == str(expected_a),
            )
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    emit("probe4_done")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


PROBES = {
    "1": probe_1_teardown,
    "2": probe_2_listener_identity,
    "3": probe_3_profile_perms,
    "4": probe_4_per_channel_profile,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("probe", choices=list(PROBES) + ["all"])
    parser.add_argument("--iterations", type=int, default=5,
                        help="probe 1: how many spawn/teardown cycles (default 5)")
    parser.add_argument("--port", type=int, default=None,
                        help="(unused — kept for forward compat)")
    args = parser.parse_args(argv)

    emit(
        "spike_start",
        plan="2026-05-21-001",
        unit="0",
        platform=sys.platform,
        python=sys.version.split()[0],
        chrome_bin=discover_chrome_binary(),
    )

    targets = [args.probe] if args.probe != "all" else ["1", "2", "3", "4"]
    for key in targets:
        fn = PROBES[key]
        diag(f"=== running probe {key}: {fn.__name__} ===")
        try:
            if key == "1":
                fn(iterations=args.iterations)
            else:
                fn()
        except Exception as exc:
            emit(f"probe{key}_exception", err=repr(exc))
            diag(f"probe {key} raised: {exc!r}")

    emit("spike_done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
