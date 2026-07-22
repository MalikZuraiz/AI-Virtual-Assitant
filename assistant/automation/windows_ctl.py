"""Windows OS-level controls: window management, power actions, lock, screenshot."""
from __future__ import annotations

import ctypes
import logging
import subprocess

import keyboard

logger = logging.getLogger("assistant.windows_ctl")


class WindowsController:
    def minimize_all(self) -> str:
        keyboard.send("windows+m")
        return "Minimized all windows."

    def restore_minimized(self) -> str:
        keyboard.send("windows+shift+m")
        return "Restored minimized windows."

    def show_start_menu(self) -> str:
        keyboard.send("windows")
        return "Opened the Start menu."

    def open_settings(self) -> str:
        keyboard.send("windows+i")
        return "Opening Windows Settings."

    def open_search(self) -> str:
        keyboard.send("windows+s")
        return "Opening Windows Search."

    def screenshot(self) -> str:
        keyboard.send("windows+shift+s")
        return "Opening the snipping tool for a screenshot."

    def lock(self) -> str:
        ctypes.windll.user32.LockWorkStation()
        return "Locking the workstation."

    def shutdown(self, delay_seconds: int = 10) -> str:
        subprocess.run(["shutdown", "/s", "/t", str(delay_seconds)], check=False)
        return f"Shutting down in {delay_seconds} seconds. Say 'cancel shutdown' to abort."

    def restart(self, delay_seconds: int = 10) -> str:
        subprocess.run(["shutdown", "/r", "/t", str(delay_seconds)], check=False)
        return f"Restarting in {delay_seconds} seconds. Say 'cancel shutdown' to abort."

    def cancel_shutdown(self) -> str:
        subprocess.run(["shutdown", "/a"], check=False)
        return "Cancelled the pending shutdown/restart."
