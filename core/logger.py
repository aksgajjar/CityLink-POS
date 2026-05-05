"""Rotating file logger for CityLink POS. Single source of truth for app logging."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_DEFAULT_LOG_PATH = Path("errors.log")
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per blueprint
_BACKUP_COUNT = 5
_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logger(
    log_path: Path | str = _DEFAULT_LOG_PATH,
    level: int = logging.INFO,
    console: bool = True,
) -> logging.Logger:
    """Configure root 'citylink' logger. Idempotent — safe to call repeatedly."""
    global _configured
    root = logging.getLogger("citylink")

    if _configured:
        return root

    root.setLevel(level)
    root.propagate = False

    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_FMT, datefmt=_DATEFMT)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if console:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(level)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    _configured = True
    return root


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the 'citylink' namespace."""
    return logging.getLogger(f"citylink.{name}")
