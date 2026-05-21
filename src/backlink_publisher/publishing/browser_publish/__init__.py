"""Browser-publish foundation — Plan 2026-05-21-001 Unit 1.

Extracts Chrome + CDP plumbing that bind already proved viable
(`cli/_bind/chrome_backend.py` from PR #129) into a shared module so the
publish pipeline can attach to (or launch) a real Chrome instance the
same way bind does, with per-channel profile isolation and listener
identity verification.

Public surface:

    BrowserPublishRecipe    — frozen dataclass: channel + compose_url + publish_flow
    ChromeAttachSession     — context manager: attach-or-launch Chrome, return Playwright Page
    chrome_session          — submodule with the shared helpers

The existing bind backend re-imports the path helpers from
``chrome_session`` so a single source of truth governs both phases
(verified by `tests/test_browser_publish_chrome_session.py`).
"""

from __future__ import annotations

from .chrome_session import (
    BrowserPublishRecipe,
    ChromeAttachSession,
    ChromeSessionError,
    _chrome_binary,
    _chrome_port,
    _chrome_profile_dir,
    _cdp_available,
    _websocket_available,
)

__all__ = [
    "BrowserPublishRecipe",
    "ChromeAttachSession",
    "ChromeSessionError",
    "_chrome_binary",
    "_chrome_port",
    "_chrome_profile_dir",
    "_cdp_available",
    "_websocket_available",
]
