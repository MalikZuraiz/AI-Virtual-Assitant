"""Windows OS-level controls: window management, power, volume, virtual
desktops, quick Settings pages, and other shell-level actions.

Most actions here are OS keyboard shortcuts (via ``keyboard.send``) - the
same shortcuts you'd press yourself - so they work without any special
privileges. A few (volume percentage, theme toggle, active-window info)
talk to Windows APIs directly (pycaw, winreg, pywin32) for things a
keyboard shortcut can't express.
"""
from __future__ import annotations

import ctypes
import logging
import subprocess
import winreg
from datetime import datetime
from pathlib import Path

import keyboard

logger = logging.getLogger("assistant.windows_ctl")

_SETTINGS_PAGES = {
    "display": "display",
    "sound": "sound",
    "wifi": "network-wifi",
    "network": "network-status",
    "airplane mode": "network-airplanemode",
    "bluetooth": "bluetooth",
    "night light": "nightlight",
    "power": "powersleep",
    "battery": "batterysaver",
    "apps": "appsfeatures",
    "personalization": "personalization",
    "update": "windowsupdate",
    "storage": "storagesense",
    "privacy": "privacy",
    "accounts": "emailandaccounts",
    "notifications": "notifications",
}


class WindowsController:
    # -- windows / desktop management --------------------------------------
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

    def open_settings_page(self, name: str) -> str:
        key = name.strip().lower()
        page = _SETTINGS_PAGES.get(key)
        if page is None:
            known = ", ".join(sorted(_SETTINGS_PAGES))
            return f"I don't know the '{name}' settings page. Try one of: {known}."
        try:
            subprocess.run(["cmd", "/c", "start", "", f"ms-settings:{page}"], shell=False, check=False)
        except OSError as exc:
            return f"Couldn't open {name} settings: {exc}"
        return f"Opening {name} settings."

    def open_search(self) -> str:
        keyboard.send("windows+s")
        return "Opening Windows Search."

    def open_run_dialog(self) -> str:
        keyboard.send("windows+r")
        return "Opened the Run dialog."

    def open_action_center(self) -> str:
        keyboard.send("windows+a")
        return "Opened Action Center."

    def open_clipboard_history(self) -> str:
        keyboard.send("windows+v")
        return "Opened clipboard history."

    def open_emoji_panel(self) -> str:
        keyboard.send("windows+.")
        return "Opened the emoji panel."

    def switch_window(self) -> str:
        keyboard.send("alt+tab")
        return "Switching windows."

    def task_view(self) -> str:
        keyboard.send("windows+tab")
        return "Opened Task View."

    def snap_left(self) -> str:
        keyboard.send("windows+left")
        return "Snapped the active window to the left."

    def snap_right(self) -> str:
        keyboard.send("windows+right")
        return "Snapped the active window to the right."

    def maximize_active(self) -> str:
        keyboard.send("windows+up")
        return "Maximized the active window."

    def minimize_active(self) -> str:
        keyboard.send("windows+down")
        return "Minimized the active window."

    # -- virtual desktops -----------------------------------------------------
    def new_virtual_desktop(self) -> str:
        keyboard.send("windows+ctrl+d")
        return "Created a new virtual desktop."

    def close_virtual_desktop(self) -> str:
        keyboard.send("windows+ctrl+f4")
        return "Closed the current virtual desktop."

    def next_virtual_desktop(self) -> str:
        keyboard.send("windows+ctrl+right")
        return "Switched to the next virtual desktop."

    def previous_virtual_desktop(self) -> str:
        keyboard.send("windows+ctrl+left")
        return "Switched to the previous virtual desktop."

    # -- volume -------------------------------------------------------------------
    def volume_up(self) -> str:
        keyboard.send("volume up")
        return "Volume up."

    def volume_down(self) -> str:
        keyboard.send("volume down")
        return "Volume down."

    def mute_toggle(self) -> str:
        try:
            volume = self._volume_interface()
            muted = bool(volume.GetMute())
            volume.SetMute(0 if muted else 1, None)
            return "Unmuted." if muted else "Muted."
        except Exception:
            logger.warning("Mute toggle via pycaw failed, falling back to media key", exc_info=True)
            keyboard.send("volume mute")
            return "Toggled mute."

    def get_volume(self) -> str:
        try:
            volume = self._volume_interface()
            percent = round(volume.GetMasterVolumeLevelScalar() * 100)
            return f"Volume is at {percent}%."
        except Exception as exc:
            logger.warning("Volume query failed", exc_info=True)
            return f"Couldn't read the system volume: {exc}"

    def set_volume(self, percent: int) -> str:
        percent = max(0, min(100, percent))
        try:
            volume = self._volume_interface()
            volume.SetMasterVolumeLevelScalar(percent / 100, None)
            return f"Volume set to {percent}%."
        except Exception as exc:
            logger.warning("Volume set failed", exc_info=True)
            return f"Couldn't set the system volume: {exc}"

    @staticmethod
    def _volume_interface():
        from pycaw.pycaw import AudioUtilities

        # pycaw's AudioDevice wrapper already exposes the activated
        # IAudioEndpointVolume COM interface via .EndpointVolume.
        return AudioUtilities.GetSpeakers().EndpointVolume

    # -- theme / appearance --------------------------------------------------------
    def toggle_theme(self) -> str:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
                current, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            new_value = 0 if current else 1
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, new_value)
                winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, new_value)
            return "Switched Windows to light mode." if new_value else "Switched Windows to dark mode."
        except OSError as exc:
            return f"Couldn't toggle the Windows theme: {exc}"

    # -- screenshots / recycle bin ---------------------------------------------
    def screenshot(self) -> str:
        keyboard.send("windows+shift+s")
        return "Opening the snipping tool for a screenshot."

    def capture_screenshot(self) -> str:
        try:
            import pyautogui

            shots_dir = Path.home() / "Pictures" / "Nova Screenshots"
            shots_dir.mkdir(parents=True, exist_ok=True)
            filename = shots_dir / f"screenshot_{datetime.now():%Y%m%d_%H%M%S}.png"
            pyautogui.screenshot().save(filename)
            return f"Saved a screenshot to {filename}."
        except Exception as exc:
            logger.warning("Screenshot capture failed", exc_info=True)
            return f"Couldn't capture a screenshot: {exc}"

    def empty_recycle_bin(self) -> str:
        try:
            # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
            ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)
            return "Emptied the Recycle Bin."
        except Exception as exc:
            logger.warning("Emptying Recycle Bin failed", exc_info=True)
            return f"Couldn't empty the Recycle Bin: {exc}"

    # -- active window ------------------------------------------------------------
    def active_window(self) -> str:
        try:
            import win32gui

            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            return f"Active window: {title or '(untitled)'}"
        except Exception as exc:
            return f"Couldn't read the active window: {exc}"

    def close_active_window(self) -> str:
        try:
            import win32con
            import win32gui

            hwnd = win32gui.GetForegroundWindow()
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            return "Closed the active window."
        except Exception as exc:
            return f"Couldn't close the active window: {exc}"

    # -- lock / power ---------------------------------------------------------------
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
