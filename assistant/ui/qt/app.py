"""Wires the HUD, the tray and the assistant core together, and runs the app.

Everything crossing a thread boundary goes through a Qt signal:

* worker -> GUI: :class:`AssistantBridge` (job results, progress, reminders)
* hotkey thread -> GUI: :class:`HotkeyBridge`
* mic/STT thread -> GUI: :class:`ListenerBridge` (state, transcript, errors)
* GUI -> worker: ``Assistant.handle`` returns immediately and queues the work

``QTimer.singleShot`` is not a substitute for a signal here and must never be
used to hop from a plain ``threading.Thread`` onto the GUI thread - it only
fires if the *calling* thread has its own active Qt event loop, which a bare
worker thread does not. It silently never fires otherwise: no exception, no
log, nothing - see :class:`ListenerBridge` for the bug this caused.

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

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

from assistant.config import AppConfig
from assistant.core.assistant import Assistant, AssistantEvent
from assistant.logging_setup import setup_logging
from assistant.ui.qt.bridge import AssistantBridge
from assistant.ui.qt.hud import HudWindow
from assistant.ui.qt.tray import Tray

logger = logging.getLogger("assistant.ui.app")

SUMMON_HOTKEY = "ctrl+alt+space"   # show/hide the window from anywhere
TALK_HOTKEY = "ctrl+shift+space"  # hold to talk
HIDE_HOTKEY = "ctrl+alt+h"        # hide without toggling back


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
    """Global hotkeys, fired on the ``keyboard`` library's own thread.

    Push-to-talk needs *press* and *release* as separate events so the mic
    records for exactly as long as the key is held. ``keyboard.add_hotkey``
    only gives the press, so the talk key is registered with two raw hooks
    instead, and a guard flag stops key auto-repeat from firing "pressed"
    dozens of times while it is held down.
    """

    summon = pyqtSignal()
    talk = pyqtSignal()            # tap: record until you stop speaking
    talk_pressed = pyqtSignal()    # hold: start recording
    talk_released = pyqtSignal()   # hold: stop recording
    hide_window = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._talk_down = False
        self._registered: list[str] = []

    def install(self, summon: str = SUMMON_HOTKEY, talk: str = TALK_HOTKEY, hide: str = HIDE_HOTKEY) -> str:
        try:
            import keyboard
        except ImportError:
            return "global hotkeys unavailable (keyboard not installed)"

        def _press() -> None:
            if not self._talk_down:
                self._talk_down = True
                self.talk_pressed.emit()

        def _release() -> None:
            if self._talk_down:
                self._talk_down = False
                self.talk_released.emit()

        try:
            keyboard.add_hotkey(summon, self.summon.emit, suppress=False)
            self._registered.append(f"{summon} show/hide")
            keyboard.add_hotkey(hide, self.hide_window.emit, suppress=False)
            self._registered.append(f"{hide} hide")
            keyboard.on_press_key(talk.split("+")[-1], lambda _e: self._maybe(keyboard, talk, _press))
            keyboard.on_release_key(talk.split("+")[-1], lambda _e: _release())
            self._registered.append(f"hold {talk} to talk")
        except Exception as exc:  # noqa: BLE001 - hooks can be blocked by policy
            logger.warning("Could not register global hotkeys: %s", exc)
            return "global hotkeys unavailable (try running as administrator)"
        return " · ".join(self._registered)

    @staticmethod
    def _maybe(keyboard_module, combo: str, callback) -> None:
        """Only fire when the whole chord is down, not just the final key."""
        modifiers = [part for part in combo.split("+")[:-1]]
        if all(keyboard_module.is_pressed(modifier) for modifier in modifiers):
            callback()


class ListenerBridge(QObject):
    """Speech events, from any thread, delivered on the GUI thread.

    This exists because ``QTimer.singleShot(0, ...)`` - what this used to be
    wired with - only works as a thread hop when the *calling* thread has an
    active Qt event loop pumping it. ``SpeechInput`` runs its recording and
    transcription on plain ``threading.Thread`` workers, which never do -
    they are not ``QThread``s and nothing ever calls ``exec()`` on them. The
    timer was created successfully every time and then simply never fired,
    silently: no exception, no log line, nothing. A voice command that
    matched an exact, unambiguous command ("open youtube") produced no
    console output and no chat message at all, because ``self._handle_
    transcript`` - and therefore ``Assistant.handle`` - was never actually
    called.

    A ``pyqtSignal`` is the correct tool for this: Qt detects that the
    thread emitting a signal differs from the thread the connected slot's
    receiver lives in, and automatically queues delivery onto the
    receiver's own event loop - exactly what :class:`AssistantBridge`
    already relies on for job results elsewhere in this file.
    """

    state_changed = pyqtSignal(bool)
    transcript_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)


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
        self.listener_bridge = ListenerBridge()
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
        self.bridge.streamed.connect(self._on_stream)

        self.tray.toggle_window.connect(self.window.toggle_visibility)
        self.tray.quit_requested.connect(self.quit)
        self.tray.command_requested.connect(self._on_submit_echo)
        self.tray.mic_toggled.connect(self._set_voice_enabled)

        self.hotkeys.summon.connect(self.window.toggle_visibility)
        self.hotkeys.hide_window.connect(self.window.hide)
        self.hotkeys.talk.connect(self._on_mic)
        self.hotkeys.talk_pressed.connect(self._on_hold_start)
        self.hotkeys.talk_released.connect(self._on_hold_stop)

        self.listener_bridge.state_changed.connect(self.window.set_listening)
        self.listener_bridge.transcript_ready.connect(self._handle_transcript)
        self.listener_bridge.error_occurred.connect(lambda t: self.window.append(t, kind="error"))

        self.assistant.set_confirm(self.confirm_broker.confirm)
        self.assistant.set_notifier(self.tray.notify)
        self.assistant.set_listener_control(self._listener_control)

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

    def _on_stream(self, text: str) -> None:
        """A reply still being written - rewrite the live bubble in place."""
        self.window.stream_update(
            text, prefix=f"{self.assistant.config.assistant_name.lower()}:"
        )

    def _on_reply(self, text: str, kind: str = "assistant") -> None:
        self.window.job_finished()
        # Clear the live bubble first, so the finished reply replaces it
        # rather than appearing underneath a duplicate.
        self.window.end_stream()
        self.window.append(text, kind=kind, prefix=f"{self.assistant.config.assistant_name.lower()}:")

    def _on_reminder(self, text: str) -> None:
        self.window.show_and_focus()
        self.window.append_reminder(text.removeprefix("Reminder: "))
        self.window.flash()

    def _set_voice_enabled(self, enabled: bool) -> None:
        self.assistant.tts.enabled = enabled
        self.window.set_status("voice replies on" if enabled else "voice replies muted")

    def _ensure_listener(self):
        from assistant.core.listening import SpeechInput

        if self.listener is None:
            self.listener = SpeechInput(
                on_state=self.listener_bridge.state_changed.emit,
                on_text=self.listener_bridge.transcript_ready.emit,
                on_error=self.listener_bridge.error_occurred.emit,
                wake_word=str(
                    self.assistant.store.value("core", "voice.live_wake_word", "") or ""
                ),
            )
            self.listener.preload()  # so the first phrase isn't slow
        return self.listener

    def _on_mic(self) -> None:
        self._ensure_listener().listen_once()

    def _on_hold_start(self) -> None:
        self._ensure_listener().hold_start()

    def _on_hold_stop(self) -> None:
        if self.listener is not None:
            self.listener.hold_stop()

    def _listener_control(self, action: str) -> str:
        """Lets chat commands turn live listening on and off."""
        listener = self._ensure_listener()
        if action == "start":
            return listener.start_live()
        if action == "stop":
            return listener.stop_live()
        return "Live listening is " + ("on." if listener.live else "off.")

    def _handle_transcript(self, text: str) -> None:
        # Reached via listener_bridge.transcript_ready - already on the GUI
        # thread by the time this runs; see ListenerBridge for why.
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
