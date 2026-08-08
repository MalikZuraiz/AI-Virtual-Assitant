"""The one place the worker threads and the Qt GUI meet.

Qt widgets may only be touched from the GUI thread - the project brief calls
this a hard requirement, and it is the single most common way a desktop app
turns into intermittent crashes. So workers never call a widget. They call
:meth:`AssistantBridge.publish`, which emits a Qt signal; because the bridge
lives in the GUI thread, Qt automatically queues the emission across the
thread boundary and delivers it to connected slots *on the GUI thread*.

That is the whole contract: below this file, plain Python threads; above it,
pure Qt. Nothing else in the app needs to think about thread affinity.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, pyqtSignal

from assistant.core.assistant import AssistantEvent

logger = logging.getLogger("assistant.ui.bridge")


class AssistantBridge(QObject):
    """Turns :class:`AssistantEvent` objects into thread-safe Qt signals."""

    #: text
    replied = pyqtSignal(str)
    #: text
    errored = pyqtSignal(str)
    #: text (a queued command's "running ..." acknowledgement)
    pending = pyqtSignal(str)
    #: text (a line of live output from a running job)
    progressed = pyqtSignal(str)
    #: text
    noticed = pyqtSignal(str)
    #: text (a reminder firing)
    reminded = pyqtSignal(str)
    #: the answer-so-far, replacing the live bubble each time
    streamed = pyqtSignal(str)

    _KIND_TO_SIGNAL = {
        "reply": "replied",
        "error": "errored",
        "pending": "pending",
        "progress": "progressed",
        "notice": "noticed",
        "reminder": "reminded",
        "stream": "streamed",
    }

    def publish(self, event: AssistantEvent) -> None:
        """Called from *any* thread. Never touches a widget itself."""
        name = self._KIND_TO_SIGNAL.get(event.kind)
        if name is None:
            logger.debug("Dropping unknown event kind %r", event.kind)
            return
        getattr(self, name).emit(event.text)
