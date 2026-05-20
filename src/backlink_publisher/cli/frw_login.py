"""`frw-login` CLI — interactive 0600 token writer for the FRW image-gen key.

Plan: docs/plans/2026-05-20-001-feat-banner-image-gen-plan.md
Unit 1 — operator-facing CLI that stashes the FRW (operator's
OpenAI-compatible LLM gateway) API key in
``~/.config/backlink-publisher/frw-token.json`` with 0600 perms.

Unlike ``velog-login`` and ``medium-login`` (which are aliases for
``bind-channel --channel <name>`` and drive a browser binding flow),
``frw-login`` accepts a raw API key via interactive prompt and writes
it through ``_util/secrets.write_frw_token`` — no browser is involved.

Behavior:
  * One-line banner to stderr (parity with velog / medium login).
  * Reads the key from ``getpass.getpass`` so it is never echoed.
  * Empty / whitespace-only input → ``UsageError`` (exit 1).
  * Existing token file is archived under a μs-precise suffix before
    the new key is written (rotation post-leak workflow).
  * Always re-resolves ``BACKLINK_PUBLISHER_CONFIG_DIR`` so sandboxed
    test / CI runs land in the right place.
"""

from __future__ import annotations

import getpass
import sys
from typing import Callable, Sequence

from backlink_publisher._util.errors import UsageError, handle_error
from backlink_publisher._util.secrets import frw_token_path, write_frw_token

_BANNER = "frw-login: write FRW image-gen API key to 0600 token file"


def main(
    argv: Sequence[str] | None = None,
    *,
    _input_provider: Callable[[str], str] | None = None,
) -> None:
    """Entry point for the ``frw-login`` console script.

    Args:
        argv: Reserved for future flags. Currently no flags are
            consumed — passing anything non-empty is silently
            ignored to keep the alias robust to future extensions.
        _input_provider: Test seam. When provided, called instead of
            ``getpass.getpass`` to read the key. Tests inject a lambda
            here to drive the CLI deterministically.
    """
    print(_BANNER, file=sys.stderr, flush=True)
    target = frw_token_path()
    print(
        f"Writing to: {target} (0600)",
        file=sys.stderr,
        flush=True,
    )

    prompt_fn = _input_provider if _input_provider is not None else getpass.getpass
    raw = prompt_fn("FRW API key: ")

    try:
        if not raw or not raw.strip():
            raise UsageError(
                "frw-login: empty API key — refusing to write an empty token "
                "file. Rerun and paste your key."
            )
        write_frw_token(raw)
    except UsageError as exc:
        handle_error(exc)
        return  # unreachable
    except ValueError as exc:
        # ``write_frw_token`` raises ValueError on empty stripped input
        # too — surface it as UsageError so the exit code is 1, not 2.
        handle_error(UsageError(f"frw-login: {exc}"))
        return  # unreachable

    print(
        "frw-login: token written. Verify with `cat <(stat -f '%Sp' "
        f"{target})` — should show -rw-------.",
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
