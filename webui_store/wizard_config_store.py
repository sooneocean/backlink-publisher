"""Persistent store for setup-wizard completion state and configuration.

Schema (singleton key ``"wizard_config"``)::

    {
      "wizard_config": {
        "completed": bool,
        "completed_at": str | None,    # ISO-8601
        "skipped": bool,
        "wizard_version": str,
        "seed_sources": [
          {
            "id": str,                 # uuid
            "type": "sitemap" | "manual" | "bookmark",
            "value": str,              # URL, text, or file path
            "label": str | None,
            "created_at": str,         # ISO-8601
            "enabled": bool,
          }
        ],
        "channels": [
          {
            "channel": str,
            "bound": bool,
            "daily_cap": int,
            "dofollow_preference": bool,
            "language_whitelist": list[str],
          }
        ],
        "automation_rules": {
          "polling_interval_seconds": int,  # default 21600
          "default_daily_cap": int,         # default 10
          "max_daily_publish": int,         # default 50
          "language_filter": list[str],     # empty = all
        }
      }
    }
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from .base import JsonStore


# Keep in sync with webui_store/__init__.py __all__
__all__ = ["WizardConfigStore"]

_WIZARD_VERSION = "1"
_KEY = "wizard_config"

_DEFAULT_CONFIG: dict[str, Any] = {
    "completed": False,
    "completed_at": None,
    "skipped": False,
    "wizard_version": _WIZARD_VERSION,
    "seed_sources": [],
    "channels": [],
    "automation_rules": {
        "polling_interval_seconds": 21600,
        "default_daily_cap": 10,
        "max_daily_publish": 50,
        "language_filter": [],
    },
}


class WizardConfigStore(JsonStore):
    """JSON store for setup-wizard state."""

    def __init__(self, path) -> None:
        super().__init__(path, default_factory=dict)

    def _get(self) -> dict[str, Any]:
        """Return the wizard config dict, filling defaults for missing keys."""
        data = self.load()
        cfg = data.get(_KEY, {})
        merged = copy.deepcopy(_DEFAULT_CONFIG)
        merged.update(cfg)
        return merged

    # ── Status queries ─────────────────────────────────────────────────

    def is_completed(self) -> bool:
        """Return True if the wizard has been completed."""
        return self._get().get("completed", False)

    def is_skipped(self) -> bool:
        return self._get().get("skipped", False)

    # ── Mutations ───────────────────────────────────────────────────────

    def mark_completed(
        self,
        seed_sources: list[dict[str, Any]] | None = None,
        channels: list[dict[str, Any]] | None = None,
        automation_rules: dict[str, Any] | None = None,
    ) -> None:
        """Persist the full wizard configuration and mark as completed."""

        def _write(data: dict) -> dict:
            cfg = data.get(_KEY, dict(_DEFAULT_CONFIG))
            cfg["completed"] = True
            cfg["completed_at"] = datetime.now(timezone.utc).isoformat()
            cfg["wizard_version"] = _WIZARD_VERSION
            if seed_sources is not None:
                cfg["seed_sources"] = seed_sources
            if channels is not None:
                cfg["channels"] = channels
            if automation_rules is not None:
                cfg["automation_rules"] = automation_rules
            data[_KEY] = cfg
            return data

        self.update(_write)

    def mark_skipped(self) -> None:
        def _write(data: dict) -> dict:
            cfg = data.get(_KEY, dict(_DEFAULT_CONFIG))
            cfg["skipped"] = True
            cfg["completed"] = False
            data[_KEY] = cfg
            return data

        self.update(_write)

    # ── Accessors ──────────────────────────────────────────────────────

    def get_config(self) -> dict[str, Any]:
        """Return the full wizard configuration dict."""
        return self._get()

    def get_seed_sources(self) -> list[dict[str, Any]]:
        return self._get().get("seed_sources", [])

    def get_channels(self) -> list[dict[str, Any]]:
        return self._get().get("channels", [])

    def get_automation_rules(self) -> dict[str, Any]:
        return self._get().get("automation_rules", {})

    def add_seed_source(
        self,
        source_type: str,
        value: str,
        label: str | None = None,
    ) -> dict[str, Any]:
        """Add a seed source and return the source record."""
        from uuid import uuid4
        record = {
            "id": str(uuid4()),
            "type": source_type,
            "value": value,
            "label": label,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "enabled": True,
        }

        def _add(data: dict) -> dict:
            cfg = data.setdefault(_KEY, copy.deepcopy(_DEFAULT_CONFIG))
            sources = list(cfg.get("seed_sources", []))
            sources.append(record)
            cfg["seed_sources"] = sources
            return data

        self.update(_add)
        return record
