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
import sys

# Ensure the backlink_publisher package is importable when running from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

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
    _content_gate_enabled,
    _derive_branded_pool,
    _derive_exact_pool,
    _derive_partial_pool,
    _DERIVED_PARTIAL_KEEP,
    _DERIVED_PARTIAL_MAX,
    _DERIVED_PARTIAL_SPLIT_RE,
    _draft_tab_extra,
    _ensure_csrf_token,
    _get_blogger_token_status,
    _load_incomplete_run,
    _load_schedule_settings,
    _normalize_url,
    _oauth_callback_uri,
    _parse_lines,
    _parse_publish_results,
    _persist_three_tier_config,
    _render,
    _save_schedule_settings,
    _settings_context,
    _validate_webui_run_id,
    _verify_urls_or_error,
    detect_language,
    detect_platform,
    fetch_full_tdk,
    fetch_url_metadata,
    get_main_domain,
    run_pipe,
)
from webui_store import (  # noqa: E402
    drafts_store as _drafts_store,
    history_store as _history_store,
    profiles_store as _profiles_store,
    schedule_store as _schedule_store,
)
from backlink_publisher import checkpoint as _checkpoint_mod  # noqa: E402
from backlink_publisher import content_fetch  # noqa: E402, F401
from backlink_publisher.work_scraper import fetch_work_metadata  # noqa: E402, F401


# Legacy helpers expressed as one-liner delegations (Unit 2 had them as
# delegations to the stores; routes use the stores directly now, but tests
# may still call these). Kept as functions, not aliases, so ``patch.object``
# can target them at module level.
def _load_history():
    return _history_store.load()


def _append_history(item: dict) -> list:
    return _history_store.update(lambda hist: [item, *hist][:100])


def _load_profiles() -> list:
    return _profiles_store.load()


def _save_profiles(profiles: list) -> None:
    _profiles_store.save(profiles)


def _load_draft_queue() -> list:
    return _drafts_store.load()


def _save_draft_queue(items: list) -> None:
    _drafts_store.save(items)


def _get_draft_item(item_id: str) -> dict | None:
    return _drafts_store.get_item(item_id)


def _update_draft_item(item_id: str, **fields) -> bool:
    return _drafts_store.update_item(item_id, **fields)


def _delete_draft_item(item_id: str) -> bool:
    return _drafts_store.delete_item(item_id)


# Path aliases — same as Unit 2 (read store path at import time).
_HISTORY_FILE = _history_store.path
_PROFILES_FILE = _profiles_store.path
_DRAFT_FILE = _drafts_store.path
_SCHEDULE_SETTINGS_FILE = _schedule_store.path


if __name__ == '__main__':
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    _wire_content_fetch_ttl_from_env()

    port = int(os.environ.get('PORT', 8888))
    bind_host = _resolve_bind_host()
    print("Starting Backlink Publisher Web UI...")
    print(f"Open: http://{bind_host}:{port}")
    app.run(host=bind_host, port=port, debug=True, use_reloader=False)
