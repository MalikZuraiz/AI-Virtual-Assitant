"""Opening URLs, with control over *which* browser and *which* window.

``webbrowser.open`` alone cannot express "in a new Brave window" or "in
whatever's already open" - its ``new=`` argument is a hint that Chrome
ignores. So named browsers and new windows go through the executable
directly with the right flag, and only the plain "just open it" case falls
back to ``webbrowser``.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import webbrowser
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("assistant.browser")

#: Per-browser command-line flags. Chromium-family browsers share theirs.
_CHROMIUM = {"new_window": "--new-window", "incognito": "--incognito"}
BROWSER_FLAGS: dict[str, dict[str, str]] = {
    "chrome": _CHROMIUM,
    "edge": {"new_window": "--new-window", "incognito": "--inprivate"},
    "brave": _CHROMIUM,
    "opera": _CHROMIUM,
    "chromium": _CHROMIUM,
    "firefox": {"new_window": "-new-window", "incognito": "-private-window"},
}

#: Fallbacks used when the configured path does not exist on this machine.
FALLBACK_EXES = {
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "firefox": "firefox.exe",
    "brave": "brave.exe",
    "opera": "opera.exe",
}


class BrowserNotFoundError(RuntimeError):
    pass


@dataclass
class OpenRequest:
    """A parsed "open X [in Y]" instruction."""

    browser: str | None = None
    new_window: bool = False
    incognito: bool = False

    def describe(self) -> str:
        bits = []
        if self.incognito:
            bits.append("incognito")
        if self.new_window:
            bits.append("a new window")
        if self.browser:
            bits.append(f"in {self.browser.title()}")
        return " ".join(bits)


#: Phrases that mean "reuse whatever window is already open".
_CURRENT_WINDOW = (
    "in the current browser", "in current browser", "in this browser",
    "in the same browser", "in the current window", "in this window",
    "in the same window", "in the current tab", "in a new tab", "in new tab",
)

_NEW_WINDOW = (
    "in a new browser", "in new browser", "in a new window", "in new window",
    "in a separate window", "new browser window",
)

_INCOGNITO = ("in incognito", "incognito", "in private", "private window", "inprivate")


def parse_open_modifiers(text: str, known_browsers: list[str]) -> tuple[str, OpenRequest]:
    """Strip "... in a new Chrome window" off ``text``, returning both parts.

    Returns ``(remaining_text, request)`` so the caller is left with just the
    site name to look up.
    """
    request = OpenRequest()
    low = text.lower()

    for phrase in _INCOGNITO:
        if phrase in low:
            request.incognito = True
            request.new_window = True
            low = low.replace(phrase, " ")

    # Most specific first: "in a new firefox window" carries both the browser
    # *and* the window preference, and matching them separately misses it -
    # the browser name sits between "in a new" and "window", so neither the
    # plain "in <browser>" pattern nor the "in a new window" phrase fires.
    # Longest names first so "brave browser" is not half-matched as "brave".
    names = "|".join(re.escape(n) for n in sorted(known_browsers, key=len, reverse=True))
    if names:
        combined = re.compile(
            rf"\b(?:in|with|on|using)\s+(?:a\s+|an\s+|the\s+)?(new\s+|another\s+|separate\s+)?"
            rf"({names})(?:\s+(browser|window|tab))?\b"
        )
        match = combined.search(low)
        if match:
            request.browser = match.group(2)
            noun = match.group(3)
            if match.group(1) and noun != "tab":
                request.new_window = True
            low = low[: match.start()] + " " + low[match.end():]

    for phrase in _NEW_WINDOW:
        if phrase in low:
            request.new_window = True
            low = low.replace(phrase, " ")

    for phrase in _CURRENT_WINDOW:
        if phrase in low:
            request.new_window = False
            low = low.replace(phrase, " ")

    remaining = re.sub(r"\s+", " ", low).strip(" .,")
    return remaining, request


def resolve_executable(name: str, known: dict[str, str]) -> str:
    """Path to a browser executable, tolerating a stale configured path."""
    configured = known.get(name)
    if configured and Path(configured).is_file():
        return configured
    fallback = FALLBACK_EXES.get(name, f"{name}.exe")
    found = shutil.which(fallback)
    if found:
        return found
    if configured:
        raise BrowserNotFoundError(
            f"{name.title()} isn't at {configured} and isn't on PATH. "
            f"Fix the path in config/core.json under browsers.known.{name}."
        )
    raise BrowserNotFoundError(f"I don't know where to find {name}.")


def open_url(
    url: str,
    *,
    browser: str | None = None,
    new_window: bool = False,
    incognito: bool = False,
    known: dict[str, str] | None = None,
    default_browser: str | None = None,
) -> str:
    """Open ``url``, honouring browser/window preferences. Returns what it did."""
    known = known or {}
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url.lstrip("/")

    target = browser or (default_browser if (new_window or incognito) else None)

    if not target:
        # Plain case: hand it to the OS default browser, existing window.
        webbrowser.open(url, new=0, autoraise=True)
        return f"Opened {url}"

    target = target.lower()
    exe = resolve_executable(target, known)
    flags = BROWSER_FLAGS.get(target, _CHROMIUM)
    args = [exe]
    if incognito:
        args.append(flags["incognito"])
    elif new_window:
        args.append(flags["new_window"])
    args.append(url)

    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    subprocess.Popen(args, creationflags=creation)  # noqa: S603 - user-configured browser
    where = "a new incognito window" if incognito else ("a new window" if new_window else "")
    suffix = f" in {where}" if where else ""
    return f"Opened {url} in {target.title()}{suffix}"
