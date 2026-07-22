"""Command routing.

Replaces the old giant ``if/elif`` chain (see ``legacy_old/main.py``) with a
small registry: each command declares the keywords/pattern that trigger it
and a handler function. Adding a new capability to the assistant means
writing one function and decorating it with ``@router.register(...)`` in
``assistant/core/commands.py`` - nothing else needs to change.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger("assistant.router")

Handler = Callable[[str, "object"], str]


@dataclass
class RegisteredCommand:
    name: str
    keywords: tuple[str, ...]
    pattern: Optional[re.Pattern]
    handler: Handler
    help: str
    destructive: bool = False


class CommandRouter:
    """Keyword/pattern based intent router for typed or spoken text."""

    def __init__(self) -> None:
        self._commands: list[RegisteredCommand] = []

    def register(
        self,
        name: str,
        keywords: tuple[str, ...] = (),
        pattern: str | None = None,
        help: str = "",
        destructive: bool = False,
    ):
        compiled = re.compile(pattern, re.IGNORECASE) if pattern else None
        lowered_keywords = tuple(k.lower() for k in keywords)

        def decorator(fn: Handler) -> Handler:
            self._commands.append(
                RegisteredCommand(name, lowered_keywords, compiled, fn, help, destructive)
            )
            return fn

        return decorator

    def match(self, text: str) -> Optional[RegisteredCommand]:
        low = text.lower()
        best: Optional[RegisteredCommand] = None
        best_len = -1
        for cmd in self._commands:
            if cmd.pattern is not None and cmd.pattern.search(text):
                return cmd
            for kw in cmd.keywords:
                if kw in low and len(kw) > best_len:
                    best = cmd
                    best_len = len(kw)
        return best

    def dispatch(self, text: str, ctx: object) -> str:
        text = (text or "").strip()
        if not text:
            return "I didn't catch a command."

        if text.lower() in ("help", "what can you do", "commands"):
            return self.help_text()

        cmd = self.match(text)
        if cmd is None:
            return "I don't have a command for that yet. Say 'help' to see what I can do."

        logger.info("Dispatching %r to command %r", text, cmd.name)
        try:
            return cmd.handler(text, ctx)
        except Exception as exc:  # noqa: BLE001 - surface any handler failure to the user
            logger.exception("Command %r failed", cmd.name)
            return f"'{cmd.name}' hit an error: {exc}"

    def help_text(self) -> str:
        lines = ["Here is what I can currently do:"]
        for cmd in sorted(self._commands, key=lambda c: c.name):
            lines.append(f"  - {cmd.name}: {cmd.help}")
        return "\n".join(lines)
