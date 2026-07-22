"""Centralized logging setup: console + rotating file handler."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from assistant.config import AppConfig


def setup_logging(config: AppConfig, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("assistant")
    if logger.handlers:
        return logger

    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    try:
        config.config_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            config.log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError:
        logger.warning("Could not open log file at %s; logging to console only", config.log_path)

    return logger
