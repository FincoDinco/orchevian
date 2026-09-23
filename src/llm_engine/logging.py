from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from llm_engine.config import default_log_path, make_private

_LOGGER_NAME = "llm_engine"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(*, verbose: bool = False, log_path: Path | None = None) -> logging.Logger:
    """Attach stderr + rotating file handlers. Creates the log directory on first use."""
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        logger.setLevel(logging.DEBUG if verbose else logging.INFO)
        return logger

    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter(_FORMAT)

    # Windows desktop bundles have no stderr; the rotating file remains available.
    if sys.stderr is not None:
        stderr = logging.StreamHandler(sys.stderr)
        stderr.setFormatter(formatter)
        logger.addHandler(stderr)

    path = log_path if log_path is not None else default_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if log_path is None:
        make_private(path.parent, directory=True)
    file_handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    make_private(path)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    if name:
        return logging.getLogger(f"{_LOGGER_NAME}.{name}")
    return logging.getLogger(_LOGGER_NAME)
