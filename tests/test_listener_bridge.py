"""The real bug behind "voice commands do nothing at all, not even 'open
youtube' - not even a log line."

``SpeechInput`` runs recording and transcription on plain
``threading.Thread`` workers - never ``QThread``, never anything that calls
``exec()``. The Qt UI used to hop from those threads to the GUI thread with
``QTimer.singleShot(0, callback)``. That only works when the *calling*
thread has its own active Qt event loop pumping timer events; a bare worker
thread has none, so the timer was created successfully every time and then
simply never fired - no exception, no log, the callback just never ran.
``Assistant.handle()`` was never reached for voice input at all, regardless
of whether the transcript would have matched a command.

These tests run a real ``QCoreApplication`` event loop (needed to prove
delivery actually happens, not just that a signal *could* be connected) and
assert the fix delivers where the old approach provably did not.
"""
import sys
import threading
import time

import pytest

QtCore = pytest.importorskip("PyQt6.QtCore")
from PyQt6.QtCore import QCoreApplication, QTimer  # noqa: E402

from assistant.ui.qt.app import ListenerBridge  # noqa: E402


def _run_on_background_thread(app: QCoreApplication, work) -> None:
    """Call ``work()`` on a plain thread while the event loop is running,
    then quit - the same shape SpeechInput's workers use."""

    def _worker():
        time.sleep(0.05)
        work()
        time.sleep(0.15)  # let any queued delivery land before quitting
        app.quit()

    threading.Thread(target=_worker, daemon=True).start()
    app.exec()


@pytest.fixture()
def qapp():
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    yield app


def test_transcript_ready_reaches_its_slot_from_a_plain_background_thread(qapp):
    bridge = ListenerBridge()
    received = []
    bridge.transcript_ready.connect(received.append)

    _run_on_background_thread(qapp, lambda: bridge.transcript_ready.emit("open youtube"))

    assert received == ["open youtube"]


def test_state_changed_reaches_its_slot_from_a_plain_background_thread(qapp):
    bridge = ListenerBridge()
    received = []
    bridge.state_changed.connect(received.append)

    _run_on_background_thread(qapp, lambda: bridge.state_changed.emit(True))

    assert received == [True]


def test_error_occurred_reaches_its_slot_from_a_plain_background_thread(qapp):
    bridge = ListenerBridge()
    received = []
    bridge.error_occurred.connect(received.append)

    _run_on_background_thread(qapp, lambda: bridge.error_occurred.emit("mic failed"))

    assert received == ["mic failed"]


def test_qtimer_singleshot_from_the_same_kind_of_thread_does_not_deliver(qapp):
    """Documents the actual bug, so nobody 'simplifies' the fix back to this.

    This is not asserting desired behaviour - it is pinning the failure mode
    that made "open youtube" produce nothing, so the reasoning behind using
    signals instead stays verifiable rather than just asserted in a comment.
    """
    received = []

    _run_on_background_thread(
        qapp, lambda: QTimer.singleShot(0, lambda: received.append("open youtube"))
    )

    assert received == []  # the old approach: silently nothing


def test_assistant_app_wires_the_listener_through_the_bridge_not_qtimer():
    """Static check on the actual wiring, so a future edit can't reintroduce
    QTimer.singleShot for this without a test noticing."""
    import inspect

    import assistant.ui.qt.app as app_module

    source = inspect.getsource(app_module.AssistantApp._ensure_listener)
    assert "QTimer" not in source
    assert "listener_bridge.state_changed.emit" in source
    assert "listener_bridge.transcript_ready.emit" in source
    assert "listener_bridge.error_occurred.emit" in source
