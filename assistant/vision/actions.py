"""Executing what a gesture is bound to.

Kept apart from recognition so the recogniser stays testable without a
keyboard, and so adding a new *kind* of binding never touches the vision
code.

Window switching is the one that needs care: a bare ``alt+tab`` press-and-
release only ever bounces between the two most recent windows. Holding Alt
down for a moment after the Tab leaves the switcher open, so repeating the
gesture within that window walks further down the list - which is what
"change windows like the Windows-Tab part" actually needs.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger("assistant.gestures.actions")

ACTION_KINDS = ("hotkey", "switch_window", "command", "text", "stop_speaking", "none")

#: How long Alt stays down after a gesture-driven Tab.
ALT_HOLD_SECONDS = 1.6


def _keyboard():
    try:
        import keyboard

        return keyboard
    except ImportError:  # pragma: no cover
        return None


class WindowSwitcher:
    """Alt-Tab that can be stepped through by repeating the gesture."""

    def __init__(self, hold_seconds: float = ALT_HOLD_SECONDS) -> None:
        self.hold_seconds = hold_seconds
        self._alt_down = False
        self._release_at = 0.0
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None

    def step(self, backwards: bool = False) -> str:
        keyboard = _keyboard()
        if keyboard is None:
            return "Window switching needs the 'keyboard' package."
        with self._lock:
            if not self._alt_down:
                keyboard.press("alt")
                self._alt_down = True
            if backwards:
                keyboard.press("shift")
            keyboard.press_and_release("tab")
            if backwards:
                keyboard.release("shift")
            self._release_at = time.monotonic() + self.hold_seconds
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.hold_seconds + 0.05, self._maybe_release)
            self._timer.daemon = True
            self._timer.start()
        return "Switching window"

    def _maybe_release(self) -> None:
        with self._lock:
            if self._alt_down and time.monotonic() >= self._release_at:
                keyboard = _keyboard()
                if keyboard is not None:
                    keyboard.release("alt")
                self._alt_down = False

    def release(self) -> None:
        """Force Alt back up - called on stop so it can never stick down."""
        with self._lock:
            if self._alt_down:
                keyboard = _keyboard()
                if keyboard is not None:
                    keyboard.release("alt")
                self._alt_down = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


class ActionRunner:
    """Turns a binding dict into a real effect."""

    def __init__(
        self,
        on_command: Optional[Callable[[str], None]] = None,
        on_stop_speaking: Optional[Callable[[], str]] = None,
    ) -> None:
        self.on_command = on_command
        self.on_stop_speaking = on_stop_speaking
        self.switcher = WindowSwitcher()

    def shutdown(self) -> None:
        self.switcher.release()

    def __call__(self, gesture: str, binding: dict) -> str:
        kind = str(binding.get("action") or "hotkey").lower()
        label = str(binding.get("label") or gesture.replace("_", " "))

        if kind == "none":
            return ""

        if kind == "switch_window":
            return self.switcher.step(backwards=bool(binding.get("backwards")))

        if kind == "stop_speaking":
            # The palm-out "stop" sign. Deliberately not a hotkey: there is
            # no Windows shortcut for "shut up", and this has to cut off
            # whatever is mid-sentence rather than queue behind it.
            if self.on_stop_speaking is None:
                return "Nothing here can stop the speech."
            return self.on_stop_speaking() or label

        if kind == "hotkey":
            keys = str(binding.get("keys") or "")
            if not keys:
                return f"'{gesture}' has no keys set."
            keyboard = _keyboard()
            if keyboard is None:
                return "Hotkey gestures need the 'keyboard' package."
            for combo in [k.strip() for k in keys.split(",") if k.strip()]:
                keyboard.send(combo)
                time.sleep(0.05)
            return label

        if kind == "text":
            keyboard = _keyboard()
            if keyboard is None:
                return "Typing gestures need the 'keyboard' package."
            keyboard.write(str(binding.get("text") or ""))
            return label

        if kind == "command":
            command = str(binding.get("command") or "")
            if not command:
                return f"'{gesture}' has no command set."
            if self.on_command is None:
                return "Command gestures aren't available here."
            self.on_command(command)
            return f"{label} ({command})"

        return f"'{gesture}' has an unknown action '{kind}'."
