"""Launching with Windows.

Uses a shortcut in the user's Startup folder rather than a ``Run`` registry
key or a scheduled task: it needs no admin rights, it is visible and
removable in ``shell:startup`` if the user ever wants it gone without asking
the assistant, and it survives Python being reinstalled elsewhere as long as
the venv stays put.

The shortcut targets ``pythonw.exe`` (not ``python.exe``) so logging in does
not flash a console window, and passes ``--hidden`` so the assistant starts
in the tray instead of popping a window over whatever you are doing.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("assistant.autostart")

SHORTCUT_NAME = "Nova Assistant.lnk"


def startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_dir() / SHORTCUT_NAME


def _pythonw() -> Path:
    """The windowed interpreter beside the current one, if there is one."""
    current = Path(sys.executable)
    candidate = current.with_name("pythonw.exe")
    return candidate if candidate.is_file() else current


def is_enabled() -> bool:
    return shortcut_path().is_file()


def enable(project_root: Path | None = None) -> str:
    root = Path(project_root or Path(__file__).resolve().parents[2])
    target = _pythonw()
    link = shortcut_path()
    link.parent.mkdir(parents=True, exist_ok=True)

    try:
        import win32com.client  # type: ignore[import-untyped]
    except ImportError:
        return (
            "I need pywin32 to create the startup shortcut "
            "(pip install pywin32), or you can make one by hand in shell:startup."
        )

    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(str(link))
    shortcut.TargetPath = str(target)
    shortcut.Arguments = "-m assistant.main --hidden"
    shortcut.WorkingDirectory = str(root)
    shortcut.Description = "Nova personal desktop assistant"
    shortcut.IconLocation = str(target)
    shortcut.save()
    logger.info("Autostart shortcut written to %s", link)
    return (
        f"Done - I'll start with Windows, hidden in the tray.\n"
        f"Shortcut: {link}\n"
        f"Runs: {target.name} -m assistant.main --hidden (from {root})"
    )


def disable() -> str:
    link = shortcut_path()
    if not link.is_file():
        return "I wasn't set to start with Windows anyway."
    link.unlink()
    return f"Removed {link} - I won't start with Windows any more."
