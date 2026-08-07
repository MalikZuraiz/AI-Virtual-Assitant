"""Look and feel for the HUD: one QSS sheet and the icons it needs.

Kept apart from the widget code so restyling never means touching layout or
behaviour. Colours are defined once at the top and interpolated in, so
changing the accent is a one-line edit rather than a find-and-replace.
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap

# -- palette ----------------------------------------------------------------
BG = "#0d1420"
BG_SOFT = "#131c2b"
PANEL = "#0a111c"
BORDER = "#1e2f47"
ACCENT = "#38bdf8"
ACCENT_DIM = "#0e7490"
TEXT = "#dbe7f3"
TEXT_DIM = "#7d93ad"
USER = "#7dd3fc"
OK = "#4ade80"
WARN = "#fbbf24"
ERROR = "#f87171"

QSS = f"""
#root {{
    background-color: rgba(13, 20, 32, 244);
    border: 1px solid {BORDER};
    border-radius: 14px;
}}

#header {{
    background: transparent;
    border-bottom: 1px solid {BORDER};
}}
#title {{
    color: {ACCENT};
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 1.5px;
}}
#clock {{
    color: {TEXT};
    font-size: 20px;
    font-weight: 600;
}}
#datestamp {{
    color: {TEXT_DIM};
    font-size: 11px;
    letter-spacing: 0.6px;
}}

QPushButton#chip {{
    background-color: {BG_SOFT};
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 4px 12px;
    font-size: 11px;
}}
QPushButton#chip:hover {{
    color: {ACCENT};
    border-color: {ACCENT_DIM};
}}
QPushButton#chip:checked {{
    color: {BG};
    background-color: {ACCENT};
    border-color: {ACCENT};
}}

QPushButton#windowbtn {{
    background: transparent;
    color: {TEXT_DIM};
    border: none;
    font-size: 15px;
    font-weight: 700;
    padding: 0px;
}}
QPushButton#windowbtn:hover {{ color: {ACCENT}; }}
QPushButton#windowbtn[danger="true"]:hover {{ color: {ERROR}; }}

#chat {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 10px;
    color: {TEXT};
    font-size: 12px;
    padding: 6px;
}}

QLineEdit#input {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 9px 12px;
    color: {TEXT};
    font-size: 12px;
    selection-background-color: {ACCENT_DIM};
}}
QLineEdit#input:focus {{ border-color: {ACCENT}; }}

QPushButton#send {{
    background-color: {ACCENT_DIM};
    color: #eaf6ff;
    border: 1px solid {ACCENT};
    border-radius: 10px;
    padding: 9px 16px;
    font-weight: 600;
}}
QPushButton#send:hover {{ background-color: {ACCENT}; color: {BG}; }}
QPushButton#send:disabled {{ background-color: {BG_SOFT}; color: {TEXT_DIM}; border-color: {BORDER}; }}

#status {{ color: {TEXT_DIM}; font-size: 10px; }}
#avatarframe {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}

QScrollBar:vertical {{
    background: transparent; width: 8px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER}; border-radius: 4px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {ACCENT_DIM}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}

QMenu {{
    background-color: {BG};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px;
}}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {ACCENT_DIM}; color: #eaf6ff; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
"""

#: Colour used for each kind of chat line.
BUBBLE_COLORS = {
    "user": USER,
    "assistant": TEXT,
    "pending": WARN,
    "progress": TEXT_DIM,
    "error": ERROR,
    "notice": TEXT_DIM,
    "reminder": OK,
    "system": ACCENT,
}


def make_icon(letter: str = "N", size: int = 64) -> QIcon:
    """Draw the tray/window icon rather than shipping a binary asset.

    One less file to lose, and it scales cleanly to whatever size Windows
    asks for instead of blurring a fixed-size PNG.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor(ACCENT))
    gradient.setColorAt(1.0, QColor(ACCENT_DIM))
    painter.setBrush(QBrush(gradient))
    painter.setPen(QPen(QColor(BG), max(1, size // 22)))
    inset = size * 0.08
    painter.drawRoundedRect(
        QRectF(inset, inset, size - 2 * inset, size - 2 * inset), size * 0.24, size * 0.24
    )

    painter.setPen(QPen(QColor(BG)))
    font = QFont("Segoe UI", int(size * 0.46))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, letter[:1].upper())
    painter.end()
    return QIcon(pixmap)
