"""Entry point.

    python -m assistant.main              the tray + floating HUD (normal use)
    python -m assistant.main --cli        a terminal REPL through the same router
    python -m assistant.main --setup      rescan this machine, seed config, exit
    python -m assistant.main --hidden     start minimised to tray (for autostart)
    python -m assistant.main --legacy-ui  the previous CustomTkinter window

``--cli`` exists because every command goes through the same
``Assistant.handle`` entry point regardless of front-end - so the terminal is
a genuine way to exercise and debug the real routing, not a parallel
implementation that can drift.
"""
from __future__ import annotations

import argparse
import sys
import time

from assistant.config import AppConfig
from assistant.logging_setup import setup_logging


def _run_cli(config: AppConfig) -> int:
    from assistant.core.assistant import Assistant, AssistantEvent

    prefix = {"user": "you", "pending": "...", "progress": "   ", "error": "!!"}

    def on_event(event: AssistantEvent) -> None:
        tag = prefix.get(event.kind, config.assistant_name.lower())
        print(f"{tag}: {event.text}")

    assistant = Assistant(config, on_event)
    print(assistant.start())
    print("Type a command, or 'quit' to exit.\n")
    try:
        while True:
            try:
                text = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if text.lower() in {"quit", "exit"}:
                break
            assistant.handle(text)
            time.sleep(0.25)  # let queued output land before the next prompt
    finally:
        assistant.shutdown()
    return 0


def _run_setup(config: AppConfig) -> int:
    from assistant.core.assistant import Assistant

    assistant = Assistant(config, lambda _event: None, enable_reminders=False)
    print(assistant.first_run_setup())
    print(f"\nConfig files: {assistant.store.dir}")
    assistant.shutdown()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Nova - personal desktop assistant")
    parser.add_argument("--cli", action="store_true", help="run in the terminal instead of the HUD")
    parser.add_argument("--setup", action="store_true", help="rescan folders, seed config, then exit")
    parser.add_argument("--hidden", action="store_true", help="start minimised to the tray")
    parser.add_argument("--legacy-ui", action="store_true", help="use the old CustomTkinter window")
    args = parser.parse_args()

    config = AppConfig.load()
    logger = setup_logging(config)
    logger.info("Starting %s", config.assistant_name)

    if args.setup:
        return _run_setup(config)
    if args.cli:
        return _run_cli(config)
    if args.legacy_ui:
        from assistant.ui.app import run as run_legacy

        run_legacy(config)
        return 0

    from assistant.ui.qt.app import run as run_qt

    return run_qt(config, show_window=not args.hidden)


if __name__ == "__main__":
    sys.exit(main())
