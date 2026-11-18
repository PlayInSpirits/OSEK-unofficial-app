"""Local diagnostic events. Never record credentials, message bodies or URL queries."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ОСЭК-Remastered"
LOG_FILE = LOG_DIR / "events.txt"
logger = logging.getLogger("osek")


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.info("app_start")

    def uncaught(exc_type, exc_value, traceback):
        logger.error("uncaught_python_error type=%s", exc_type.__name__)
        sys.__excepthook__(exc_type, exc_value, traceback)

    sys.excepthook = uncaught
