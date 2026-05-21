#!/usr/bin/env python3
"""Backlink Publisher WebUI launcher — Plan 2026-05-18-001 Unit 3.

Thin entry. The Flask app, routes, helpers, scheduler, and Jinja2
templates all live in ``webui_app/`` (Plan Unit 3 + 4 refactor).
State persistence lives in ``webui_store/`` (Plan Unit 2).

Run with ``python webui.py``. The default bind is loopback only;
override via ``BIND_HOST=...`` plus
``BACKLINK_PUBLISHER_ALLOW_NETWORK=1`` for non-loopback hosts.
"""

from __future__ import annotations

import os

from webui_app import create_app
from webui_app.helpers import _resolve_bind_host, _wire_content_fetch_ttl_from_env


# Module-level app instance — required so ``from webui import app`` works
# (legacy tests, WSGI servers, debug tooling).
app = create_app()


# ─── Legacy re-export shim — Plan Unit 3 ────────────────────────────────────
# Tests + external scripts patch helpers via ``webui.<name>``. After
# Unit 3 split, the canonical home is ``webui_app.helpers``. Re-exporting
# here keeps existing patch points working without forcing a tests/ sweep.
# When patching has migrated (next major version), this block can shrink.
from webui_app.helpers import (  # noqa: E402
    _WORK_THEMED_RUNS,
    _calc_next_available,
    _check_csrf_or_abort,
    _check_localhost,
    _draft_tab_extra,
    _ensure_csrf_token,
    _get_blogger_token_status,
    _load_incomplete_run,
    _load_schedule_settings,
    _oauth_callback_uri,
    _parse_lines,
    _parse_publish_results,
    _persist_three_tier_config,
    _render,
    _settings_context,
    _validate_webui_run_id,
    run_pipe,
)
from webui_app.helpers.url_meta import (  # noqa: E402
    _content_gate_enabled,
    _derive_branded_pool,
    _derive_exact_pool,
    _derive_partial_pool,
    _DERIVED_PARTIAL_KEEP,
    _DERIVED_PARTIAL_MAX,
    _DERIVED_PARTIAL_SPLIT_RE,
    _normalize_url,
    _verify_urls_or_error,
    detect_language,
    detect_platform,
    fetch_full_tdk,
    fetch_url_metadata,
    get_main_domain,
)
from webui_store import (  # noqa: E402
    drafts_store as _drafts_store,
    history_store as _history_store,
    profiles_store as _profiles_store,
    schedule_store as _schedule_store,
)
from backlink_publisher import checkpoint as _checkpoint_mod  # noqa: E402
from backlink_publisher.content import fetch as content_fetch  # noqa: E402, F401
from backlink_publisher.content.scraper import fetch_work_metadata  # noqa: E402, F401





if __name__ == '__main__':
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    _wire_content_fetch_ttl_from_env()

    port = int(os.environ.get('PORT', 8888))
    bind_host = _resolve_bind_host()
    # FLASK_DEBUG env gate: launcher exports FLASK_DEBUG=0 so route exceptions
    # exit the process (observable by the bash restart loop) instead of being
    # absorbed by Werkzeug's debug page. Direct `python webui.py` invocations
    # keep debug=True (default '1'). Only the exact string '1' enables debug.
    debug_mode = os.environ.get('FLASK_DEBUG', '1') == '1'
    print("Starting Backlink Publisher Web UI...")
    print(f"Open: http://{bind_host}:{port}  (debug={debug_mode})")
    app.run(host=bind_host, port=port, debug=debug_mode, use_reloader=False)
