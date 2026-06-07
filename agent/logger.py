"""Structured JSON logging utilities."""
from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pythonjsonlogger import jsonlogger

from design_audit_agent.config import LOG_FILE, OUTPUT_DIR


def setup_logger() -> logging.Logger:
    """Configure and return a module-level logger."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("design_audit_agent")
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        fmt = jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        sh = logging.StreamHandler()
        sh.setLevel(logging.INFO)
        sh.setFormatter(fmt)

        fh = RotatingFileHandler(LOG_FILE, maxBytes=10_000_000, backupCount=3)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)

        logger.addHandler(sh)
        logger.addHandler(fh)

    return logger


logger = setup_logger()
