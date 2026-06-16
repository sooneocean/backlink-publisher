"""Package entry-point: forward to the same main() used by the
pyproject console script.

Before the publish_backlinks decomposition (plan 2026-06-02-001), this
module was a single file and `python -m backlink_publisher.cli.publish_backlinks`
invoked its module-level `if __name__ == "__main__": main()` guard. After
decomposition into a package, `python -m <package>` looks here instead.
Leaving it empty silently drops all output for callers that still use
the `-m` form (CI steps + webui_app._rewrite_cli_cmd)."""

from . import main

raise SystemExit(main() or 0)
