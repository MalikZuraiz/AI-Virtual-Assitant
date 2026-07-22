"""Launch local applications by name, using a config-driven registry."""
from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger("assistant.apps")


class AppLauncher:
    def __init__(self, config) -> None:
        self._config = config

    def open(self, name: str) -> str:
        name = name.strip().lower()
        if not name:
            return "Which application should I open?"

        path = self._config.apps.get(name)
        if path is None:
            for key, value in self._config.apps.items():
                if name in key or key in name:
                    path = value
                    break
        if path is None:
            path = name  # last resort: let Windows try to resolve it from PATH

        try:
            os.startfile(path)  # noqa: S606 - user-configured/trusted app registry
        except OSError:
            try:
                subprocess.Popen(path, shell=True)  # noqa: S602 - same trust boundary
            except OSError as exc:
                logger.warning("Could not launch %r (%r)", name, path, exc_info=True)
                return f"I couldn't open '{name}': {exc}"
        return f"Opening {name}."

    def register(self, name: str, path: str) -> str:
        name = name.strip().lower()
        self._config.apps[name] = path
        self._config.save()
        return f"Registered '{name}' -> {path}"
