"""Structured logging for the backlink pipeline.

All diagnostic output goes to stderr via this module.
Never emits to stdout — stdout is reserved for structured JSONL data.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any


class PipelineLogger:
    """Structured logger that writes to stderr with consistent format."""

    def __init__(self, name: str = "backlink-publisher", level: str = "INFO") -> None:
        self.name = name
        self.level = level
        self._levels = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}

    def _should_log(self, level: str) -> bool:
        return self._levels.get(level, 1) >= self._levels.get(self.level, 1)

    def _emit(self, level: str, message: str, extra: dict[str, Any] | None = None) -> None:
        if not self._should_log(level):
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "logger": self.name,
            "msg": message,
        }
        if extra:
            record.update(extra)
        print(json.dumps(record, ensure_ascii=False), file=sys.stderr, flush=True)

    def debug(self, msg: str, **extra: Any) -> None:
        self._emit("DEBUG", msg, extra or None)

    def info(self, msg: str, **extra: Any) -> None:
        self._emit("INFO", msg, extra or None)

    def warn(self, msg: str, **extra: Any) -> None:
        self._emit("WARN", msg, extra or None)

    def error(self, msg: str, **extra: Any) -> None:
        self._emit("ERROR", msg, extra or None)


# Module-level singleton instances
plan_logger = PipelineLogger("plan-backlinks")
validate_logger = PipelineLogger("validate-backlinks")
publish_logger = PipelineLogger("publish-backlinks")
opencli_logger = PipelineLogger("opencli-runner")


def set_log_level(level: str) -> None:
    """Set log level for all pipeline loggers."""
    for logger in (plan_logger, validate_logger, publish_logger, opencli_logger):
        logger.level = level


def get_logger(name: str) -> PipelineLogger:
    """Get a named logger instance."""
    return PipelineLogger(name)