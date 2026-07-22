"""Run cmd/PowerShell-style commands, with a configurable allowlist.

This is a single-user desktop assistant, so the model isn't a network
sandbox - it's "don't silently run something the user didn't ask for."
Every command that runs is echoed back with its exact text and output
before/after execution, and anything outside the allowlist requires the
user to explicitly say "force run ..." rather than matching silently.
"""
from __future__ import annotations

import logging
import shlex
import subprocess

logger = logging.getLogger("assistant.shell")


class SafeShell:
    def __init__(self, config) -> None:
        self._config = config

    def is_allowed(self, command: str) -> bool:
        try:
            first = shlex.split(command, posix=False)[0]
        except (ValueError, IndexError):
            return False
        first = first.strip('"').lower()
        return first in (c.lower() for c in self._config.shell_allowlist)

    def run(self, command: str, timeout: int = 30, force: bool = False) -> str:
        command = command.strip()
        if not command:
            return "No command given."

        if not force and not self.is_allowed(command):
            allowed = ", ".join(self._config.shell_allowlist)
            return (
                f"'{command}' isn't in the allowed command list ({allowed}). "
                "Add it in Settings, or say 'force run <command>' to run it anyway."
            )

        logger.info("Running shell command: %s", command)
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            return f"'{command}' timed out after {timeout}s."
        except OSError as exc:
            return f"Failed to run '{command}': {exc}"

        output = ((result.stdout or "") + (result.stderr or "")).strip() or "(no output)"
        if len(output) > 2000:
            output = output[:2000] + "\n...(truncated)"
        return f"$ {command}\n{output}"
