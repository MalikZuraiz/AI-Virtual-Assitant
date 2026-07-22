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
    "netflix": "https://www.netflix.com/",
    "spotify": "https://open.spotify.com/",
    "reddit": "https://www.reddit.com/",
    "stackoverflow": "https://stackoverflow.com/",
    "drive": "https://drive.google.com/",
    "docs": "https://docs.google.com/",
    "sheets": "https://sheets.google.com/",
    "slides": "https://slides.google.com/",
    "calendar": "https://calendar.google.com/",
    "translate": "https://translate.google.com/",
    "maps": "https://maps.google.com/",
    "twitch": "https://www.twitch.tv/",
    "discord": "https://discord.com/app",
    "notion": "https://www.notion.so/",
    "amazon": "https://www.amazon.com/",
}


class ChromeController:
    # -- tabs / windows -------------------------------------------------------
    def new_tab(self) -> str:
        keyboard.send("ctrl+t")
        return "Opened a new tab."

    def close_tab(self) -> str:
        keyboard.send("ctrl+w")
        return "Closed the current tab."

    def reopen_closed_tab(self) -> str:
        keyboard.send("ctrl+shift+t")
        return "Reopened the last closed tab."

    def next_tab(self) -> str:
        keyboard.send("ctrl+tab")
        return "Switched to the next tab."

    def previous_tab(self) -> str:
        keyboard.send("ctrl+shift+tab")
        return "Switched to the previous tab."

    def new_window(self) -> str:
        keyboard.send("ctrl+n")
        return "Opened a new window."

    def close_window(self) -> str:
        keyboard.send("ctrl+shift+w")
        return "Closed the current window."

    def incognito(self) -> str:
        keyboard.send("ctrl+shift+n")
        return "Opened an incognito window."

    def switch_to_tab(self, number: int) -> str:
        number = max(1, min(number, 8))
        keyboard.send(f"ctrl+{number}")
        return f"Switched to tab {number}."

    # -- page actions ---------------------------------------------------------
    def reload(self) -> str:
        keyboard.send("f5")
        return "Reloading the page."

    def hard_reload(self) -> str:
        keyboard.send("ctrl+shift+r")
        return "Hard-reloading the page (cache bypassed)."

    def zoom_in(self) -> str:
        keyboard.send("ctrl+=")
        return "Zoomed in."

    def zoom_out(self) -> str:
        keyboard.send("ctrl+-")
        return "Zoomed out."

    def zoom_reset(self) -> str:
        keyboard.send("ctrl+0")
        return "Reset zoom."

    def print_page(self) -> str:
        keyboard.send("ctrl+p")
        return "Opening print dialog."

    def find_in_page(self) -> str:
        keyboard.send("ctrl+f")
        return "Opened find-in-page."

    def focus_address_bar(self) -> str:
        keyboard.send("ctrl+l")
        return "Focused the address bar."

    def view_source(self) -> str:
        keyboard.send("ctrl+u")
        return "Opened page source."

    def dev_tools(self) -> str:
        keyboard.send("f12")
        return "Opened Developer Tools."

    def full_screen(self) -> str:
        keyboard.send("f11")
        return "Toggled full screen."

    # -- browser chrome ---------------------------------------------------------
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

    def bookmarks_manager(self) -> str:
        keyboard.send("ctrl+shift+o")
        return "Opened the bookmarks manager."

    def clear_browsing_data(self) -> str:
        keyboard.send("ctrl+shift+delete")
        return "Opened clear browsing data."

    def browser_task_manager(self) -> str:
        keyboard.send("shift+esc")
        return "Opened the browser task manager."

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
        "captions": "c",
        "subtitles": "c",
        "speed up": "shift+.",
        "speed down": "shift+,",
        "miniplayer": "i",
        "restart": "0",
    }

    def control(self, action: str) -> str:
        action = action.strip().lower()
        key = self._KEYS.get(action)
        if not key:
            return f"I don't know the YouTube action '{action}'."
        keyboard.send(key)
        return f"YouTube: {action}."
