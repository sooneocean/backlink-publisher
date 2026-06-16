"""Shared helpers for publish-backlinks CLI.

Re-export module for backward compatibility with test patches.
Functions are now implemented in specialized modules:

- _publish_verify: _check_row_reachability, _do_verify
- _publish_canary: _canary_gate
- _publish_throttle: _load_throttle_config, _do_sleep, _sleep_with_throttle, _medium_throttle_sleep
- _publish_checkpoint: _try_update_ckpt_failed, _build_failure_row, _build_skip_row
- _publish_banner: _make_banner_emit, _error_class, _record_publish_failure, _record_publish_path
- _publish_reconciler: _run_reconciler, _write_reconciler_report
- _publish_epilogue: _publish_epilogue
- _publish_lease: _gate_banner_sentinel, _release_acquired_leases, _acquire_publish_leases, _maybe_emit_gate_banner, _check_token_drift

This module re-exports all symbols so existing imports and test patches continue to work.
"""

from __future__ import annotations

# Re-export for backward compatibility (tests patch these at backlink_publisher.cli._publish_helpers.X)
# Functions extracted to specialized modules
from backlink_publisher.cli._publish_verify import (
    _check_row_reachability,
    _do_verify,
)
# check_url and ThreadPoolExecutor are patched by tests; re-export from the producer module
# to keep patches working at backlink_publisher.cli._publish_helpers.check_url
from backlink_publisher.linkcheck.http import check_url  # noqa: F401
from concurrent.futures import ThreadPoolExecutor  # noqa: F401
from backlink_publisher.linkcheck.verify import verify_published  # noqa: F401  # Patched in tests
# Re-export time module for backward compatibility with test patches
import time as time  # noqa: F401  # Tests patch backlink_publisher.cli._publish_helpers.time.sleep
from backlink_publisher.cli._publish_canary import _canary_gate
from backlink_publisher.cli._publish_throttle import (
    _load_throttle_config,
    _do_sleep,
    _sleep_with_throttle,
    _medium_throttle_sleep,
)
from backlink_publisher.cli._publish_checkpoint import (
    _try_update_ckpt_failed,
    _build_failure_row,
    _build_skip_row,
)
from backlink_publisher.cli._publish_banner import (
    _make_banner_emit,
    _error_class,
    _record_publish_failure,
    _record_publish_path,
)
from backlink_publisher.cli._publish_reconciler import (
    _run_reconciler,
    _write_reconciler_report,
)
from backlink_publisher.cli._publish_epilogue import _publish_epilogue
from backlink_publisher.cli._publish_lease import (
    _gate_banner_sentinel,
    _release_acquired_leases,
    _acquire_publish_leases,
    _maybe_emit_gate_banner,
    _check_token_drift,
)
# Re-export from _publish_cli for backward compatibility
from backlink_publisher.cli._publish_cli import (
    _build_parser,
    _handle_auth_expired,
    _handle_checkpoint_ops,
)

__all__ = [
    "_check_row_reachability",
    "_do_verify",
    "check_url",
    "ThreadPoolExecutor",
    "_canary_gate",
    "_load_throttle_config",
    "_do_sleep",
    "_sleep_with_throttle",
    "_medium_throttle_sleep",
    "_try_update_ckpt_failed",
    "_build_failure_row",
    "_build_skip_row",
    "_make_banner_emit",
    "_error_class",
    "_record_publish_failure",
    "_record_publish_path",
    "_run_reconciler",
    "_write_reconciler_report",
    "_publish_epilogue",
    "_gate_banner_sentinel",
    "_release_acquired_leases",
    "_acquire_publish_leases",
    "_maybe_emit_gate_banner",
    "_check_token_drift",
    # Re-exported from _publish_cli
    "_build_parser",
    "_handle_auth_expired",
    "_handle_checkpoint_ops",
    # Re-exported modules for backward compatibility with test patches
    "time",  # Tests patch backlink_publisher.cli._publish_helpers.time.sleep
    "verify_published",  # Tests patch backlink_publisher.cli._publish_helpers.verify_published
]