"""The floating HUD widget - the "buddy in the corner of the screen".

Frameless, always-on-top, translucent and rounded, per the project brief. A
few decisions worth calling out:

* **The window never does work.** Pressing enter calls
  ``Assistant.handle()``, which returns immediately; every answer arrives
  later through :class:`~assistant.ui.qt.bridge.AssistantBridge` signals. So
  the clock keeps ticking and the animation keeps playing while a report
  generator runs for four minutes.
* **Job output goes to the status line, not the chat.** A report can print
  hundreds of progress lines; pouring those into the transcript would bury
  the actual answer. The chat keeps the conversation, the status bar shows
  the live tail.
* **Closing hides.** The X button hides to tray rather than quitting - the
  assistant is meant to be running from login to shutdown. Quit is on the
  tray menu, where it can't be hit by muscle memory.
"""
from __future__ import annotations

import logging
from datetime import datetime
from html import escape
from pathlib import Path

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QKeyEvent, QMovie, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizeGrip,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from assistant.store.paths import repo_root
from assistant.ui.qt.theme import ACCENT, BUBBLE_COLORS, OK, QSS, TEXT, make_icon

logger = logging.getLogger("assistant.ui.hud")

AVATARS = {
    "idle": "sophie body.gif",
    "listening": "voice.gif",
    "busy": "Loader.gif",
}


class DragBar(QFrame):
    """The header. Frameless windows have to move themselves."""

    def __init__(self, window: QWidget) -> None:
        super().__init__()
        self._window = window
        self._offset: QPoint | None = None
        self.setObjectName("header")
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._offset = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self._offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._offset)

    def mouseReleaseEvent(self, event) -> None:
        self._offset = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)


class HudWindow(QWidget):
    """The assistant's floating window."""

    submitted = pyqtSignal(str)
    mic_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, assistant_name: str = "Nova", asset_dir: Path | None = None) -> None:
        super().__init__()
        self.assistant_name = assistant_name
        self.assets = asset_dir or (repo_root() / "GUI")
        self._history: list[str] = []
        self._history_index = 0
        self._movies: dict[str, QMovie] = {}
        #: Document position where the live streaming bubble begins.
        self._stream_anchor: int | None = None
        self._busy_jobs = 0

        self.setWindowTitle(assistant_name)
        self.setWindowIcon(make_icon(assistant_name))
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # keeps it out of the taskbar and alt-tab
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(380, 420)
        self.resize(430, 620)
        self.setStyleSheet(QSS)

        self._build()
        self._place_bottom_right()

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick)
        self._clock_timer.start(1000)
        self._tick()

    # -- layout -----------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        root = QFrame()
        root.setObjectName("root")
        shadow = QGraphicsDropShadowEffect(blurRadius=28, xOffset=0, yOffset=4)
        shadow.setColor(Qt.GlobalColor.black)
        root.setGraphicsEffect(shadow)
        outer.addWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(9)

        layout.addWidget(self._build_header())
        layout.addWidget(self._build_avatar())

        self.chat = QTextBrowser()
        self.chat.setObjectName("chat")
        self.chat.setOpenExternalLinks(True)
        layout.addWidget(self.chat, stretch=1)

        layout.addLayout(self._build_input())

        footer = QHBoxLayout()
        self.status = QLabel("starting up...")
        self.status.setObjectName("status")
        footer.addWidget(self.status, stretch=1)
        grip = QSizeGrip(root)
        grip.setFixedSize(14, 14)
        footer.addWidget(grip, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(footer)

    def _build_header(self) -> QWidget:
        header = DragBar(self)
        row = QVBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 8)
        row.setSpacing(1)

        top = QHBoxLayout()
        title = QLabel(self.assistant_name.upper())
        title.setObjectName("title")
        top.addWidget(title)
        top.addStretch(1)

        self.mic_button = QPushButton("MIC")
        self.mic_button.setObjectName("chip")
        self.mic_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mic_button.setToolTip("Push to talk (or press Ctrl+Shift+Space anywhere)")
        self.mic_button.clicked.connect(self.mic_requested.emit)
        top.addWidget(self.mic_button)

        minimise = QPushButton("—")
        minimise.setObjectName("windowbtn")
        minimise.setFixedSize(24, 22)
        minimise.setCursor(Qt.CursorShape.PointingHandCursor)
        minimise.setToolTip("Hide to tray (I keep running)")
        minimise.clicked.connect(self.hide)
        top.addWidget(minimise)

        close = QPushButton("✕")
        close.setObjectName("windowbtn")
        close.setProperty("danger", "true")
        close.setFixedSize(24, 22)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip("Hide to tray - quit from the tray menu")
        close.clicked.connect(self.hide)
        top.addWidget(close)
        row.addLayout(top)

        self.clock = QLabel("--:--:--")
        self.clock.setObjectName("clock")
        row.addWidget(self.clock)

        self.datestamp = QLabel("")
        self.datestamp.setObjectName("datestamp")
        row.addWidget(self.datestamp)
        return header

    def _build_avatar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("avatarframe")
        frame.setFixedHeight(112)
        box = QHBoxLayout(frame)
        box.setContentsMargins(8, 6, 8, 6)

        self.avatar = QLabel()
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setFixedSize(96, 96)
        box.addWidget(self.avatar)

        self.tagline = QLabel("ready when you are")
        self.tagline.setObjectName("datestamp")
        self.tagline.setWordWrap(True)
        box.addWidget(self.tagline, stretch=1)

        self.set_avatar("idle")
        return frame

    def _build_input(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setObjectName("input")
        self.input.setPlaceholderText("type a command, or 'help'...")
        self.input.returnPressed.connect(self._submit)
        self.input.installEventFilter(self)
        row.addWidget(self.input, stretch=1)

        self.send = QPushButton("Send")
        self.send.setObjectName("send")
        self.send.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send.clicked.connect(self._submit)
        row.addWidget(self.send)
        return row

    def _place_bottom_right(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - 24, area.bottom() - self.height() - 24)

    # -- avatar -----------------------------------------------------------
    def set_avatar(self, state: str) -> None:
        """Swap the idle/listening/busy animation, if the GIF exists."""
        movie = self._movies.get(state)
        if movie is None:
            path = self.assets / AVATARS.get(state, "")
            if not path.is_file():
                self.avatar.setText(self.assistant_name[:1].upper())
                return
            movie = QMovie(str(path))
            movie.setScaledSize(self.avatar.size())
            self._movies[state] = movie
        for other in self._movies.values():
            if other is not movie:
                other.stop()
        self.avatar.setMovie(movie)
        movie.start()

    # -- chat -------------------------------------------------------------
    def append(self, text: str, kind: str = "assistant", prefix: str = "") -> None:
        if not text:
            return
        colour = BUBBLE_COLORS.get(kind, BUBBLE_COLORS["assistant"])
        stamp = datetime.now().strftime("%H:%M")
        body = escape(text).replace("\n", "<br>").replace("  ", "&nbsp;&nbsp;")
        label = f"<b>{escape(prefix)}</b> " if prefix else ""
        self.chat.append(
            f'<div style="margin:3px 0"><span style="color:#5d7186;font-size:9px">{stamp}</span> '
            f'<span style="color:{colour}">{label}{body}</span></div>'
        )
        bar = self.chat.verticalScrollBar()
        bar.setValue(bar.maximum())

    def append_reminder(self, text: str) -> None:
        """A reminder gets its own banner - it must not read as one more line.

        A reminder that scrolls past looking like ordinary chat is a reminder
        you miss, which defeats the point of setting one.
        """
        stamp = datetime.now().strftime("%H:%M")
        body = escape(text).replace("\n", "<br>")
        self.chat.append(
            f'<div style="margin:10px 0;padding:10px 12px;border-left:3px solid {OK};'
            f'background-color:rgba(74,222,128,0.10);border-radius:6px">'
            f'<span style="color:{OK};font-weight:700;letter-spacing:1px">⏰ REMINDER</span>'
            f'<span style="color:#5d7186;font-size:9px"> · {stamp}</span><br>'
            f'<span style="color:{TEXT};font-size:13px">{body}</span></div>'
        )
        bar = self.chat.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.set_status(f"⏰ {text[:80]}")

    # -- live streaming ---------------------------------------------------
    def stream_update(self, text: str, prefix: str = "") -> None:
        """Show a reply that is still being written, growing in place.

        The local model produces about seven words a second, so a finished-
        only reply means half a minute of nothing. This rewrites one bubble
        as the text arrives: the position where the bubble started is
        remembered, and each update selects from there to the end and
        replaces it. Appending instead would leave a trail of partial copies.
        """
        if not text:
            return
        cursor = self.chat.textCursor()
        if self._stream_anchor is None:
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self._stream_anchor = cursor.position()
        else:
            cursor.setPosition(self._stream_anchor)
            cursor.movePosition(
                QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor
            )
            cursor.removeSelectedText()

        stamp = datetime.now().strftime("%H:%M")
        body = escape(text).replace("\n", "<br>")
        label = f"<b>{escape(prefix)}</b> " if prefix else ""
        cursor.insertHtml(
            f'<div style="margin:3px 0"><span style="color:#5d7186;font-size:9px">{stamp}</span> '
            f'<span style="color:{BUBBLE_COLORS["assistant"]}">{label}{body}'
            f'<span style="color:{ACCENT}">▌</span></span></div>'
        )
        bar = self.chat.verticalScrollBar()
        bar.setValue(bar.maximum())

    def end_stream(self) -> None:
        """Drop the live bubble so the finished reply can be appended cleanly."""
        if self._stream_anchor is None:
            return
        cursor = self.chat.textCursor()
        cursor.setPosition(self._stream_anchor)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        self._stream_anchor = None

    def flash(self) -> None:
        """Bounce the taskbar/window so an off-screen user still notices."""
        app = QApplication.instance()
        if app is not None:
            app.alert(self, 3000)

    def set_status(self, text: str) -> None:
        self.status.setText(text[:120])

    def set_tagline(self, text: str) -> None:
        self.tagline.setText(text)

    # -- job state --------------------------------------------------------
    def job_started(self) -> None:
        self._busy_jobs += 1
        self.set_avatar("busy")

    def job_finished(self) -> None:
        self._busy_jobs = max(0, self._busy_jobs - 1)
        if self._busy_jobs == 0:
            self.set_avatar("idle")
            self.set_status("ready")

    def set_listening(self, listening: bool) -> None:
        self.mic_button.setChecked(listening)
        self.set_avatar("listening" if listening else ("busy" if self._busy_jobs else "idle"))
        self.set_status("listening..." if listening else "ready")

    # -- input ------------------------------------------------------------
    def _submit(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self._history.append(text)
        self._history_index = len(self._history)
        self.append(text, kind="user", prefix="you:")
        self.submitted.emit(text)

    def eventFilter(self, source, event):  # noqa: N802 - Qt naming
        """Up/Down in the input box walks command history, like a shell."""
        if source is self.input and isinstance(event, QKeyEvent) and event.type() == QKeyEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Up and self._history:
                self._history_index = max(0, self._history_index - 1)
                self.input.setText(self._history[self._history_index])
                return True
            if event.key() == Qt.Key.Key_Down and self._history:
                self._history_index = min(len(self._history), self._history_index + 1)
                self.input.setText(
                    "" if self._history_index >= len(self._history) else self._history[self._history_index]
                )
                return True
            if event.key() == Qt.Key.Key_Escape:
                self.hide()
                return True
        return super().eventFilter(source, event)

    # -- misc -------------------------------------------------------------
    def _tick(self) -> None:
        now = datetime.now()
        self.clock.setText(now.strftime("%H:%M:%S"))
        self.datestamp.setText(now.strftime("%A, %d %B %Y").upper())

    def show_and_focus(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show_and_focus()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """X hides to tray; quitting is deliberate, from the tray menu."""
        event.ignore()
        self.hide()
