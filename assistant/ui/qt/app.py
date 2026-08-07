"""Wires the HUD, the tray and the assistant core together, and runs the app.

Everything crossing a thread boundary goes through a Qt signal:

* worker -> GUI: :class:`AssistantBridge` (job results, progress, reminders)
* hotkey thread -> GUI: :class:`HotkeyBridge`
* GUI -> worker: ``Assistant.handle`` returns immediately and queues the work

The one awkward direction is *worker asks the GUI a question* - a destructive
command needing confirmation. :class:`ConfirmBroker` handles it by emitting a
signal and blocking the worker on an ``Event`` until the GUI thread answers,
which keeps the dialog on the GUI thread where Qt requires it without the
worker having to poll.
"""
from __future__ import annotations

import logging
import sys
import threading
from typing import Optional

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

from assistant.config import AppConfig
from assistant.core.assistant import Assistant, AssistantEvent
from assistant.logging_setup import setup_logging
from assistant.ui.qt.bridge import AssistantBridge
from assistant.ui.qt.hud import HudWindow
from assistant.ui.qt.tray import Tray

logger = logging.getLogger("assistant.ui.app")

SUMMON_HOTKEY = "ctrl+alt+space"
TALK_HOTKEY = "ctrl+shift+space"


class ConfirmBroker(QObject):
    """Lets a worker thread ask the GUI a yes/no question and wait for it."""

    asked = pyqtSignal(str, object, object)  # prompt, result list, threading.Event

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.asked.connect(self._show, Qt.ConnectionType.QueuedConnection)
        self._window: Optional[HudWindow] = None

    def attach(self, window: HudWindow) -> None:
        self._window = window

    def confirm(self, prompt: str) -> bool:
        """Called from a worker thread. Blocks until the user answers."""
        if QApplication.instance() is None:
            return False
        result: list[bool] = []
        done = threading.Event()
        self.asked.emit(prompt, result, done)
        if not done.wait(timeout=120):
            return False  # no answer in two minutes: treat as "no"
        return bool(result and result[0])

    def _show(self, prompt: str, result: list, done: threading.Event) -> None:
        try:
            answer = QMessageBox.question(
                self._window,
                "Confirm",
                prompt,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            result.append(answer == QMessageBox.StandardButton.Yes)
        finally:
            done.set()


class HotkeyBridge(QObject):
    """Global hotkeys fire on the ``keyboard`` library's own thread."""

    summon = pyqtSignal()
    talk = pyqtSignal()

    def install(self) -> str:
        try:
            import keyboard
        except ImportError:
            return "global hotkeys unavailable (keyboard not installed)"
        try:
            keyboard.add_hotkey(SUMMON_HOTKEY, self.summon.emit, suppress=False)
            keyboard.add_hotkey(TALK_HOTKEY, self.talk.emit, suppress=False)
        except Exception as exc:  # noqa: BLE001 - hooks can be blocked by policy
            logger.warning("Could not register global hotkeys: %s", exc)
            return "global hotkeys unavailable"
        return f"{SUMMON_HOTKEY} to summon, {TALK_HOTKEY} to talk"


class AssistantApp:
    """Owns the Qt application and every long-lived object in the UI."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.qt = QApplication.instance() or QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)  # tray keeps us alive

        self.bridge = AssistantBridge()
        self.assistant = Assistant(config, on_event=self._publish)

        name = self.assistant.config.assistant_name
        self.window = HudWindow(name)
        self.tray = Tray(name)
        self.confirm_broker = ConfirmBroker()
        self.confirm_broker.attach(self.window)
        self.hotkeys = HotkeyBridge()
        self.listener = None  # built lazily on first mic use

        self._connect()

    # -- wiring -----------------------------------------------------------
    def _publish(self, event: AssistantEvent) -> None:
        """Called from any thread - hands off to Qt immediately."""
        self.bridge.publish(event)

    def _connect(self) -> None:
        self.window.submitted.connect(self._on_submit)
        self.window.mic_requested.connect(self._on_mic)

        self.bridge.replied.connect(self._on_reply)
        self.bridge.errored.connect(lambda t: self._on_reply(t, kind="error"))
        self.bridge.noticed.connect(lambda t: self.window.append(t, kind="notice"))
        self.bridge.reminded.connect(self._on_reminder)
        self.bridge.pending.connect(self._on_pending)
        self.bridge.progressed.connect(self.window.set_status)

        self.tray.toggle_window.connect(self.window.toggle_visibility)
        self.tray.quit_requested.connect(self.quit)
        self.tray.command_requested.connect(self._on_submit_echo)
        self.tray.mic_toggled.connect(self._set_voice_enabled)

        self.hotkeys.summon.connect(self.window.show_and_focus)
        self.hotkeys.talk.connect(self._on_mic)

        self.assistant.set_confirm(self.confirm_broker.confirm)
        self.assistant.set_notifier(self.tray.notify)

    # -- slots (all on the GUI thread) ------------------------------------
    def _on_submit(self, text: str) -> None:
        self.assistant.handle(text)

    def _on_submit_echo(self, text: str) -> None:
        """A tray-menu command: show it in the chat as if it were typed."""
        self.window.show_and_focus()
        self.window.append(text, kind="user", prefix="you:")
        self.assistant.handle(text)

    def _on_pending(self, text: str) -> None:
        self.window.job_started()
        self.window.set_status(text)

    def _on_reply(self, text: str, kind: str = "assistant") -> None:
        self.window.job_finished()
        self.window.append(text, kind=kind, prefix=f"{self.assistant.config.assistant_name.lower()}:")

    def _on_reminder(self, text: str) -> None:
        self.window.show_and_focus()
        self.window.append(text, kind="reminder", prefix="⏰")

    def _set_voice_enabled(self, enabled: bool) -> None:
        self.assistant.tts.enabled = enabled
        self.window.set_status("voice replies on" if enabled else "voice replies muted")

    def _on_mic(self) -> None:
        from assistant.core.listening import SpeechInput

        if self.listener is None:
            self.listener = SpeechInput(
                on_state=lambda listening: self.window.set_listening(listening),
                on_text=self._on_transcript,
                on_error=lambda message: self.window.append(message, kind="error"),
            )
        self.listener.listen_once()

    def _on_transcript(self, text: str) -> None:
        # Arrives from the listener thread; hop to the GUI thread first.
        QTimer.singleShot(0, lambda: self._handle_transcript(text))

    def _handle_transcript(self, text: str) -> None:
        self.window.set_listening(False)
        if not text.strip():
            self.window.append("I didn't catch that.", kind="notice")
            return
        self.window.append(text, kind="user", prefix="you (voice):")
        self.assistant.handle(text)

    # -- lifecycle --------------------------------------------------------
    def run(self, *, show_window: bool = True) -> int:
        self.tray.show()
        greeting = self.assistant.start()
        hotkey_note = self.hotkeys.install()

        self.window.append(greeting, kind="system", prefix="●")
        self.window.append(
            "Try: 'help', 'list reports', 'generate pending penalties report', "
            "'open youtube in a new window', 'add command', 'remind me to ...'.",
            kind="notice",
        )
        self.window.set_status(hotkey_note)
        self.window.set_tagline(f"{len(self.assistant.router)} commands loaded")
        if show_window:
            self.window.show_and_focus()

        try:
            return self.qt.exec()
        finally:
            self.assistant.shutdown()

    def quit(self) -> None:
        self.assistant.shutdown()
        self.tray.hide()
        self.qt.quit()


def run(config: AppConfig | None = None, *, show_window: bool = True) -> int:
    config = config or AppConfig.load()
    setup_logging(config)
    return AssistantApp(config).run(show_window=show_window)
