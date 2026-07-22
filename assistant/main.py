"""Entry point: python -m assistant.main"""
from __future__ import annotations

from assistant.config import AppConfig
from assistant.logging_setup import setup_logging
from assistant.ui.app import run


def main() -> None:
    config = AppConfig.load()
    logger = setup_logging(config)
    logger.info("Starting %s", config.assistant_name)
    run(config)


if __name__ == "__main__":
    main()
