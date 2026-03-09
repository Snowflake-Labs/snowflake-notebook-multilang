"""Logging setup for the multi-language toolkit.

Provides configurable console + file handlers with optional JSON
formatting for machine-parseable output.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

LOGGER_NAME = "sfnb_multilang"


class JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "phase"):
            entry["phase"] = record.phase
        if hasattr(record, "language"):
            entry["language"] = record.language
        if hasattr(record, "duration_ms"):
            entry["duration_ms"] = record.duration_ms
        if record.exc_info and record.exc_info[1]:
            entry["error"] = str(record.exc_info[1])
            entry["error_type"] = type(record.exc_info[1]).__name__
        return json.dumps(entry, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable console format matching the existing shell script style."""

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
        return f"[{ts}] [{record.levelname}] {record.getMessage()}"


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    json_format: bool = False,
) -> logging.Logger:
    """Configure the toolkit's root logger.

    Args:
        level: Log level name (DEBUG, INFO, WARNING, ERROR).
        log_file: Optional path to write logs to.
        json_format: If True, use JSON format for the file handler.

    Returns:
        The configured root logger for the toolkit.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Avoid duplicate handlers on repeated calls
    logger.handlers.clear()

    # Console handler (always human-readable)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(ConsoleFormatter())
    logger.addHandler(console)

    # File handler (optional)
    if log_file:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        fh = logging.FileHandler(log_file, mode="w", encoding="utf-8")
        if json_format:
            fh.setFormatter(JsonFormatter())
        else:
            fh.setFormatter(ConsoleFormatter())
        logger.addHandler(fh)

    return logger
