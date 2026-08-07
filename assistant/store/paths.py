"""Where the assistant keeps things on disk.

Two distinct locations, deliberately not merged:

``<repo>/config/``
    Human-editable JSON the user hand-edits (§4 of the project brief). Kept
    in the repo so it is easy to find, diff and back up.

``%APPDATA%/NovaAssistant/``
    Runtime state the user never edits by hand: the reminders SQLite
    database, logs, downloaded ML models. Never mixed into ``config/``,
    because a user hand-editing JSON should never risk clobbering runtime
    state.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "NovaAssistant"


def repo_root() -> Path:
    """The project root (the folder holding ``assistant/`` and ``config/``)."""
    return Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    """Hand-editable JSON config directory, created on first use."""
    path = Path(os.environ.get("NOVA_CONFIG_DIR") or (repo_root() / "config"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def app_data_dir() -> Path:
    """Per-user runtime directory under %APPDATA% (or ~ as a fallback)."""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    path = base / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_dir() -> Path:
    """Runtime state (SQLite db, cached models). Alias kept for readability."""
    return app_data_dir()


def models_dir() -> Path:
    path = app_data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path
