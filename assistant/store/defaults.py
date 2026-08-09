"""Seed contents for each ``config/*.json`` file.

These are deliberately *structural* defaults, not this machine's paths. The
old prototype's biggest sin was baking one laptop's folders into source
(``E:\\...JARVIS SERIES...``, ``C:\\Users\\hp\\...``); nothing here does that.
Real, machine-specific paths are discovered once by
:mod:`assistant.store.bootstrap` and written into the JSON files, which are
then the single source of truth and freely hand-editable.

Adding a new domain later means adding one entry to :data:`DEFAULTS` - not
growing an existing file (project brief §4.1).
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers used to build sensible first-run defaults on *any* machine.
# ---------------------------------------------------------------------------


def preferred_data_root() -> Path:
    """Best guess at the drive the user keeps their work on.

    Prefers a large non-system drive (that is where most Windows users keep
    projects and media), falling back to the home directory so this still
    works on a single-drive machine.
    """
    override = os.environ.get("NOVA_WORKSPACE_ROOT")
    if override:
        return Path(override)
    for letter in ("D:", "E:", "F:"):
        candidate = Path(letter + "\\")
        if candidate.exists():
            return candidate
    return Path.home()


def _known_browsers() -> dict[str, str]:
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    return {
        "chrome": str(Path(program_files) / "Google/Chrome/Application/chrome.exe"),
        "edge": str(Path(program_files_x86) / "Microsoft/Edge/Application/msedge.exe"),
        "firefox": str(Path(program_files) / "Mozilla Firefox/firefox.exe"),
        "brave": str(
            Path(program_files) / "BraveSoftware/Brave-Browser/Application/brave.exe"
        ),
        "opera": str(Path(local_app_data) / "Programs/Opera/opera.exe")
        if local_app_data
        else "opera.exe",
    }


def _p(*parts: object) -> str:
    """Join into a forward-slash path string - what we store in JSON.

    Forward slashes survive JSON round-tripping without escaping and Windows
    accepts them everywhere, so config files stay readable when hand-edited.
    """
    return Path(*[str(p) for p in parts]).as_posix()


# ---------------------------------------------------------------------------
# Per-file defaults
# ---------------------------------------------------------------------------


def _core() -> dict:
    root = preferred_data_root()
    return {
        "_comment": "Broadest shared settings. Other config files' entries are created inside these defaults.",
        "assistant_name": "Nova",
        "defaults": {
            "workspace_root": _p(root),
            "office_root": _p(root, "Office"),
            "entertainment_root": _p(root, "Entertainment"),
            "flutter_projects_dir": _p(root, "Office", "Projects", "Flutter"),
            "python_projects_dir": _p(root, "Office", "Projects", "Python"),
            "scripts_dir": _p(root, "Office", "Scripts"),
            "reports_root": _p(root, "Reports"),
            "data_dir": _p(root, "Office", "Data"),
            "documents_dir": _p(Path.home(), "Documents"),
            "downloads_dir": _p(Path.home(), "Downloads"),
        },
        "day_folder": {
            "_comment": "Tokens: {d} {dd} {month} {mon} {m} {mm} {yyyy} {yy} {weekday}",
            "format": "{d} {month}",
        },
        "browsers": {
            "default": "chrome",
            "known": _known_browsers(),
        },
        "behaviour": {
            "speak_replies": True,
            "confirm_destructive": True,
            "fuzzy_match_threshold": 82,
        },
        "llm": {
            "_comment": (
                "Local-only chat via Ollama. Reached ONLY when a message starts "
                "with the prefix below - everything else goes to the command "
                "router. Personas live in personas.json."
            ),
            "enabled": True,
            "provider": "ollama",
            "base_url": "http://localhost:11434",
            "model": "qwen2.5:1.5b-instruct",
            "prefix": "nova",
            "history_turns": 6,
        },
        "last_active_path": None,
    }


def _projects() -> dict:
    return {
        "_comment": "Registered code projects. 'create flutter app X' auto-appends here.",
        "projects": [],
    }


def _scripts() -> dict:
    return {
        "_comment": (
            "Custom runnable commands built through the in-chat 'add command' "
            "wizard. run_as: python | venv_python | exe | shell | open"
        ),
        "commands": [],
    }


def _reports() -> dict:
    root = preferred_data_root()
    return {
        "_comment": (
            "Report generators. Output files produced by a run are collected "
            "and moved into today's folder under output_root, e.g. "
            "'<output_root>/7 August'. The folder is created if missing."
        ),
        "output_root": _p(root, "Reports"),
        "collect_extensions": [".xlsx", ".xlsm", ".csv", ".pdf", ".docx", ".pptx"],
        "reports": [],
    }


def _websites() -> dict:
    return {
        "_comment": (
            "Add a site here and it is instantly openable by name - no restart, "
            "no code change. 'open <name>' uses the default browser; "
            "'open <name> in a new window' / 'in <browser>' override that."
        ),
        "default_browser": "chrome",
        "sites": [
            {"name": "youtube", "url": "https://www.youtube.com", "trigger": []},
            {"name": "github", "url": "https://github.com", "trigger": []},
            {"name": "gmail", "url": "https://mail.google.com", "trigger": ["open mail"]},
            {"name": "chatgpt", "url": "https://chat.openai.com", "trigger": []},
            {"name": "google drive", "url": "https://drive.google.com", "trigger": []},
            {"name": "whatsapp", "url": "https://web.whatsapp.com", "trigger": ["open whatsapp web"]},
        ],
    }


def _wordpress() -> dict:
    return {
        "_comment": "WordPress sites, admin panels and hosting dashboards.",
        "sites": [],
    }


def _personal_links() -> dict:
    return {
        "_comment": "Your own presence on the web.",
        "links": [],
    }


def _reminders() -> dict:
    return {
        "_comment": (
            "Human-editable source of truth for reminders. The runtime copy "
            "lives in SQLite; edits here are picked up on 'refresh'. "
            "schedule: once | daily | weekly | monthly"
        ),
        "reminders": [],
    }


def _media() -> dict:
    root = preferred_data_root()
    return {
        "_comment": "Entertainment libraries. 'play <name>' searches these folders.",
        "libraries": {
            "movies": [_p(root, "Entertainment", "Movies")],
            "series": [_p(root, "Entertainment", "Series")],
            "music": [_p(Path.home(), "Music")],
            "clips": [_p(root, "Entertainment", "Funny Clips")],
        },
        "video_extensions": [".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm"],
        "audio_extensions": [".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"],
        "player": None,
    }


def _workspaces() -> dict:
    root = preferred_data_root()
    return {
        "_comment": (
            "Folder scaffolding the assistant keeps in place. 'setup workspace' "
            "(re)creates any missing folder; it never moves or deletes "
            "anything you already have."
        ),
        "hubs": [
            {
                "name": "entertainment",
                "path": _p(root, "Entertainment"),
                "folders": ["Movies", "Series", "Music", "Funny Clips", "Downloads"],
            },
            {
                "name": "office",
                "path": _p(root, "Office"),
                "folders": [
                    "Scripts",
                    "Reports",
                    "Data",
                    "Documents",
                    "Projects/Python",
                    "Projects/Flutter",
                ],
            },
        ],
    }


def _personas() -> dict:
    from assistant.store.personas import default_personas

    return default_personas()


def _gestures() -> dict:
    # Fire-once poses (assistant/vision/gestures.py) plus the four swipe
    # directions. Kept in lockstep with that module's POSES/SWIPES/NEUTRAL:
    # this dict is deep-merged *underneath* whatever is hand-edited in
    # config/gestures.json (via the "bindings" wholesale-merge key - see
    # store.WHOLESALE_DICT_KEYS), so a stale binding left here would
    # otherwise resurrect itself after being deleted from the JSON - that
    # was the actual cause of gestures "colliding" for a whole round after
    # the poses that caused it had already been removed from disk.
    #
    # "one" (1 finger, held) and "two" (2 fingers, swipe carrier) are
    # deliberately absent - they are structural, handled by GestureController
    # directly (mute-while-held and swipe detection), never dispatched
    # through this bindings dict at all.
    return {
        "_comment": (
            "Hand gestures -> Windows actions. action: hotkey | switch_window | "
            "command | stop_speaking | text | none. Hold a pose for hold_frames "
            "frames; a swipe needs swipe_travel worth of motion while showing 2 "
            "fingers. 'five' is the ONLY neutral pose - never bind an action to "
            "it, it exists to re-arm the recogniser so a gesture can repeat. "
            "'one' (hold to mute) and 'two' (swipe carrier) are built in, not "
            "listed here - see assistant/vision/gestures.py. Deleting a binding "
            "here unbinds it; there is no hidden fallback."
        ),
        "camera_index": 0,
        "fps_limit": 15,
        "hold_frames": 5,
        "cooldown_seconds": 1.0,
        "swipe_travel": 0.22,
        "confidence": 0.5,
        "start_with_app": False,
        "bindings": {
            "fist": {"action": "hotkey", "keys": "ctrl+windows+f4", "label": "close this desktop"},
            "three": {"action": "hotkey", "keys": "volume up", "label": "volume up"},
            "four": {"action": "hotkey", "keys": "volume down", "label": "volume down"},
            "five": {"action": "none", "label": "(neutral - re-arms, fires nothing)"},
            "rock": {"action": "hotkey", "keys": "windows+shift+s", "label": "screenshot"},
            "swipe_left": {"action": "hotkey", "keys": "ctrl+windows+left", "label": "previous desktop"},
            "swipe_right": {"action": "hotkey", "keys": "ctrl+windows+right", "label": "next desktop"},
            "swipe_up": {"action": "hotkey", "keys": "ctrl+windows+d", "label": "new desktop"},
            "swipe_down": {"action": "hotkey", "keys": "windows+m", "label": "minimise everything"},
        },
    }


DEFAULTS: dict[str, "callable"] = {
    "core": _core,
    "projects": _projects,
    "scripts": _scripts,
    "reports": _reports,
    "websites": _websites,
    "wordpress": _wordpress,
    "personal_links": _personal_links,
    "reminders": _reminders,
    "media": _media,
    "workspaces": _workspaces,
    "personas": _personas,
    "gestures": _gestures,
}

#: Files loaded on start, in hierarchy order - broadest first (brief §4.1).
FILE_ORDER: tuple[str, ...] = (
    "core",
    "workspaces",
    "projects",
    "scripts",
    "reports",
    "websites",
    "wordpress",
    "personal_links",
    "media",
    "reminders",
    "personas",
    "gestures",
)


def default_for(name: str) -> dict:
    """Fresh default document for ``name`` (raises KeyError if unknown)."""
    return DEFAULTS[name]()
