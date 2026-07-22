"""Persistent, user-editable configuration for the assistant.

Replaces every hardcoded, machine-specific path from the old scripts
(``D://SnapTube Audio``, ``C:\\Users\\hp\\...``, ``E:\\...JARVIS SERIES...``)
with values loaded from a JSON file in the current user's AppData folder,
editable at runtime from the Settings dialog.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR_NAME = "NovaAssistant"


def _default_config_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / APP_DIR_NAME


def _default_music_dir() -> str:
    return str(Path.home() / "Music")


DEFAULT_APPS: dict[str, str] = {
    "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "explorer": "explorer.exe",
    "word": "winword.exe",
    "excel": "excel.exe",
    "paint": "mspaint.exe",
    "task manager": "taskmgr.exe",
    "control panel": "control.exe",
    "vs code": "code",
}

DEFAULT_SHELL_ALLOWLIST: list[str] = [
    "dir", "cd", "echo", "ipconfig", "ping", "tasklist", "systeminfo",
    "where", "whoami", "tree", "git", "python", "pip", "node", "npm", "code",
]


@dataclass
class AppConfig:
    assistant_name: str = "Nova"
    voice_replies_enabled: bool = True
    voice_input_enabled: bool = True
    tts_rate: int = 190
    tts_voice_index: int = 1
    theme: str = "dark"
    music_dir: str = field(default_factory=_default_music_dir)
    weather_city: str = "Islamabad"
    apps: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_APPS))
    shell_allowlist: list[str] = field(default_factory=lambda: list(DEFAULT_SHELL_ALLOWLIST))
    require_confirmation_for_destructive: bool = True
    virtual_mouse_camera_index: int = 0
    virtual_mouse_fps_limit: int = 30

    @property
    def config_dir(self) -> Path:
        return _default_config_dir()

    @property
    def config_path(self) -> Path:
        return self.config_dir / "config.json"

    @property
    def log_path(self) -> Path:
        return self.config_dir / "assistant.log"

    def save(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2)

    @classmethod
    def load(cls) -> "AppConfig":
        cfg = cls()
        path = cfg.config_path
        if not path.exists():
            cfg.save()
            return cfg
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for key, value in data.items():
                if hasattr(cfg, key):
                    setattr(cfg, key, value)
        except (json.JSONDecodeError, OSError):
            pass
        return cfg
