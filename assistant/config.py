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
    virtual_mouse_camera_index: int = 0
    virtual_mouse_fps_limit: int = 30
    # How much of the camera frame maps to the full screen: 1.0 = the whole
    # frame edge-to-edge (the old, unreachable-corners behavior); higher
    # values shrink the "active" region so a smaller, more comfortable hand
    # movement range covers the entire screen - raise this if you can't
    # reach the screen edges (especially the bottom).
    virtual_mouse_sensitivity: float = 1.7
    # Center of the active region, as a fraction of the frame (0.5 = middle).
    # The default y is below center because a hand held up in front of a
    # laptop webcam naturally sits in the lower-middle of the frame, not
    # dead center - this makes the reachable-bottom problem better out of
    # the box; nudge lower (e.g. 0.35) if the bottom is still hard to reach,
    # or back toward 0.5 if the top is now too easy to overshoot.
    virtual_mouse_center_x: float = 0.5
    virtual_mouse_center_y: float = 0.42
    # One-Euro-filter smoothing: min_cutoff lower = smoother/less jitter
    # when the hand is nearly still; beta higher = less lag when moving
    # fast. These two together give smooth-but-responsive cursor motion
    # instead of the flat, laggy exponential smoothing this used to use.
    virtual_mouse_min_cutoff: float = 0.6
    virtual_mouse_beta: float = 0.6

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
