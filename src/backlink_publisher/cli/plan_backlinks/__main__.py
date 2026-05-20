"""Package entry-point: forward to the same main() used by the
pyproject console script.

Before the plan_backlinks decomposition (PR 60da49a), this module was a
single file and `python -m backlink_publisher.cli.plan_backlinks` invoked
its module-level `if __name__ == "__main__": main()` guard. After
decomposition into a package, `python -m <package>` looks here instead.
Leaving it empty silently dropped all output for callers that still use
the `-m` form (CI's plan/validate/publish steps + webui_app's
`_rewrite_cli_cmd`)."""

from . import main

raise SystemExit(main() or 0)
