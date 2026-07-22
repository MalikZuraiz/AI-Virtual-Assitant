"""Launch local applications by name, using a config-driven registry."""
from __future__ import annotations

import logging
import os
import shutil

logger = logging.getLogger("assistant.apps")


class AppNotFoundError(Exception):
    """Raised when ``name`` isn't a registered app and can't be resolved
    from PATH either - lets callers (e.g. the 'open' command) fall back to
    trying it as a website instead of silently failing."""


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
            # Only fall back to the raw name if Windows can actually resolve
            # it to something on PATH - previously this always fell through
            # to `subprocess.Popen(name, shell=True)`, which "succeeds" (cmd.exe
            # itself starts fine) even when the name means nothing, silently
            # printing "'x' is not recognized..." to the console while this
            # method still reported "Opening x." as if it had worked.
            resolved = shutil.which(name)
            if resolved is None:
                raise AppNotFoundError(name)
            path = resolved

        try:
            os.startfile(path)  # noqa: S606 - user-configured/trusted app registry, or resolved via PATH
        except OSError:
            # `path` may be a bare command name from the registry (e.g. "code"
            # for VS Code) that only resolves via the PATH env var rather than
            # being a real file path - os.startfile doesn't search PATH, but
            # shutil.which does, so try that before reporting failure.
            resolved = shutil.which(path)
            if resolved is None:
                return f"I couldn't open '{name}': no matching application found."
            try:
                os.startfile(resolved)
            except OSError as exc:
                logger.warning("Could not launch %r (%r)", name, resolved, exc_info=True)
                return f"I couldn't open '{name}': {exc}"
        return f"Opening {name}."

    def register(self, name: str, path: str) -> str:
        name = name.strip().lower()
        self._config.apps[name] = path
        self._config.save()
        return f"Registered '{name}' -> {path}"

    def list_running(self, limit: int = 40) -> str:
        import psutil

        names = sorted({p.info["name"] for p in psutil.process_iter(["name"]) if p.info["name"]})
        preview = ", ".join(names[:limit])
        more = f" (+{len(names) - limit} more)" if len(names) > limit else ""
        return f"{len(names)} running processes: {preview}{more}"

    def is_running(self, name: str) -> str:
        import psutil

        name = name.strip().lower()
        if not name:
            return "Which application should I check?"
        for proc in psutil.process_iter(["name", "pid"]):
            proc_name = (proc.info["name"] or "").lower()
            if name in proc_name:
                return f"Yes, '{proc.info['name']}' is running (PID {proc.info['pid']})."
        return f"'{name}' doesn't appear to be running."

    def close(self, name: str) -> str:
        import psutil

        name = name.strip().lower()
        if not name:
            return "Which application should I close?"
        closed: set[str] = set()
        for proc in psutil.process_iter(["name", "pid"]):
            proc_name = (proc.info["name"] or "").lower()
            if name in proc_name:
                try:
                    proc.terminate()
                    closed.add(proc.info["name"])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        if not closed:
            return f"No running process matched '{name}'."
        return f"Closed: {', '.join(sorted(closed))}"
