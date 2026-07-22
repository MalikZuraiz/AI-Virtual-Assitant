"""Chrome tab/window control (via OS-level hotkeys) and quick site opening."""
from __future__ import annotations

import re
import webbrowser

import keyboard

_SITE_SHORTCUTS = {
    "youtube": "https://www.youtube.com/",
    "instagram": "https://www.instagram.com/",
    "facebook": "https://www.facebook.com/",
    "gmail": "https://mail.google.com/",
    "github": "https://github.com/",
    "twitter": "https://x.com/",
    "x": "https://x.com/",
    "linkedin": "https://www.linkedin.com/",
    "whatsapp": "https://web.whatsapp.com/",
    "chatgpt": "https://chat.openai.com/",
    "claude": "https://claude.ai/",
}


class ChromeController:
    def new_tab(self) -> str:
        keyboard.send("ctrl+t")
        return "Opened a new tab."

    def close_tab(self) -> str:
        keyboard.send("ctrl+w")
        return "Closed the current tab."

    def new_window(self) -> str:
        keyboard.send("ctrl+n")
        return "Opened a new window."

    def incognito(self) -> str:
        keyboard.send("ctrl+shift+n")
        return "Opened an incognito window."

    def history(self) -> str:
        keyboard.send("ctrl+h")
        return "Opened browsing history."

    def downloads(self) -> str:
        keyboard.send("ctrl+j")
        return "Opened downloads."

    def bookmark_page(self) -> str:
        keyboard.send("ctrl+d")
        keyboard.send("enter")
        return "Bookmarked the current page."

    def switch_to_tab(self, number: int) -> str:
        number = max(1, min(number, 8))
        keyboard.send(f"ctrl+{number}")
        return f"Switched to tab {number}."

    def open_site(self, name: str) -> str:
        name = name.strip().lower()
        if not name:
            return "Which site would you like me to open?"
        url = _SITE_SHORTCUTS.get(name)
        if url is None:
            safe = re.sub(r"[^a-z0-9.-]", "", name.replace(" ", ""))
            if not safe:
                return "I couldn't work out a website from that."
            url = f"https://www.{safe}.com"
        webbrowser.open(url)
        return f"Opening {url}"


class YouTubeController:
    """Sends real YouTube keyboard shortcuts to the focused browser tab."""

    _KEYS = {
        "play": "space",
        "pause": "space",
        "resume": "space",
        "fullscreen": "f",
        "full screen": "f",
        "theater": "t",
        "theatre": "t",
        "forward": "l",
        "skip": "l",
        "rewind": "j",
        "back": "j",
        "mute": "m",
        "unmute": "m",
        "next": "shift+n",
        "previous": "shift+p",
        "volume up": "up",
        "volume down": "down",
    }

    def control(self, action: str) -> str:
        action = action.strip().lower()
        key = self._KEYS.get(action)
        if not key:
            return f"I don't know the YouTube action '{action}'."
        keyboard.send(key)
        return f"YouTube: {action}."
