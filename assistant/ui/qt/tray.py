"""System tray icon - the assistant's real home.

The window is a guest; the tray icon is what makes this "always on from
startup to shutdown" rather than another app to remember to open. Left-click
toggles the HUD, right-click gets the quick menu, and quitting lives *only*
here so a stray click on the window's X never kills the process.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from assistant.ui.qt.theme import make_icon

logger = logging.getLogger("assistant.ui.tray")


class Tray(QSystemTrayIcon):
    toggle_window = pyqtSignal()
    quit_requested = pyqtSignal()
    command_requested = pyqtSignal(str)
    mic_toggled = pyqtSignal(bool)

    def __init__(self, assistant_name: str = "Nova", parent=None) -> None:
        super().__init__(make_icon(assistant_name), parent)
        self.assistant_name = assistant_name
        self.setToolTip(f"{assistant_name} - your desktop assistant")
        self.setContextMenu(self._build_menu())
        self.activated.connect(self._on_activated)

    def _build_menu(self) -> QMenu:
        menu = QMenu()

        show = QAction("Show / hide window", menu)
        show.triggered.connect(self.toggle_window.emit)
        menu.addAction(show)
        menu.addSeparator()

        for label, command in (
            ("Today's reports folder", "open today's reports"),
            ("What can you do?", "help"),
            ("Reload config from disk", "refresh"),
            ("Open config folder", "open config folder"),
        ):
            action = QAction(label, menu)
            action.triggered.connect(lambda _checked=False, c=command: self.command_requested.emit(c))
            menu.addAction(action)

        menu.addSeparator()
        self.mute_action = QAction("Mute voice replies", menu)
        self.mute_action.setCheckable(True)
        self.mute_action.toggled.connect(lambda checked: self.mic_toggled.emit(not checked))
        menu.addAction(self.mute_action)

        menu.addSeparator()
        quit_action = QAction(f"Quit {self.assistant_name}", menu)
        quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(quit_action)
        return menu

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.toggle_window.emit()

    def notify(self, title: str, message: str, seconds: int = 6) -> None:
        try:
            self.showMessage(title, message, make_icon(self.assistant_name), seconds * 1000)
        except Exception:  # noqa: BLE001 - notifications are cosmetic
            logger.exception("Tray notification failed")
