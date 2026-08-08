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
    "edge": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "firefox": r"C:\Program Files\Mozilla Firefox\firefox.exe",
    "notepad": "notepad.exe",
    "wordpad": "wordpad.exe",
    "calculator": "calc.exe",
    "explorer": "explorer.exe",
    "word": "winword.exe",
    "excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "outlook": "outlook.exe",
    "paint": "mspaint.exe",
    "snipping tool": "SnippingTool.exe",
    "task manager": "taskmgr.exe",
    "control panel": "control.exe",
    "vs code": "code",
    "terminal": "wt.exe",
    "powershell": "powershell.exe",
    "cmd": "cmd.exe",
    "camera": "microsoft.windows.camera:",
    "photos": "ms-photos:",
    "magnifier": "magnify.exe",
    "on-screen keyboard": "osk.exe",
    "character map": "charmap.exe",
    "remote desktop": "mstsc.exe",
    "media player": "wmplayer.exe",
    "device manager": "devmgmt.msc",
    "services": "services.msc",
    "event viewer": "eventvwr.msc",
    "registry editor": "regedit",
    "disk management": "diskmgmt.msc",
    "task scheduler": "taskschd.msc",
}

DEFAULT_SHELL_ALLOWLIST: list[str] = [
    "dir", "cd", "echo", "ipconfig", "ping", "tasklist", "systeminfo",
    "where", "whoami", "tree", "git", "python", "pip", "node", "npm", "code",
    "powershell", "netstat", "nslookup", "hostname", "tracert", "type", "findstr",
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
    # Camera used by the gesture module. Everything else about gestures -
    # which poses map to which actions, hold time, cooldown - is data in
    # config/gestures.json rather than a field here.
    camera_index: int = 0
    gesture_fps_limit: int = 24

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
            # Merge in newly-added default apps/allowlist entries for configs
            # saved by an older version of the assistant, without discarding
            # the user's own customizations.
            merged_apps = dict(DEFAULT_APPS)
            merged_apps.update(cfg.apps)
            cfg.apps = merged_apps
            cfg.shell_allowlist = sorted(set(cfg.shell_allowlist) | set(DEFAULT_SHELL_ALLOWLIST))
        except (json.JSONDecodeError, OSError):
            pass
        return cfg
