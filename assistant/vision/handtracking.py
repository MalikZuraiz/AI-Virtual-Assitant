"""Shared hand-tracking plumbing for the gesture module.

Both features need the same things - the MediaPipe model, a camera, landmark
geometry, smoothing - so they share them here rather than drifting apart.

Two things in this file fix real, reproducible bugs:

**DPI awareness.** This is why clicks landed in the wrong place. On a scaled
display (125% is the Windows default on a 1080p laptop), a process that has
not declared DPI awareness sees a *virtualised* desktop: ``GetSystemMetrics``
reports 1536x864 while ``SetCursorPos`` works in real 1920x1080 pixels. The
cursor is then placed at 80% of where it should be - it looks roughly right
near the top-left and gets progressively worse toward the bottom-right, which
is exactly "it clicks, but always in the wrong place". Declaring
per-monitor-v2 awareness before reading any metric makes both sides agree.

**Virtual-screen bounds.** Using ``SM_CXSCREEN`` (primary monitor only) means
the cursor can never reach a second monitor. The virtual-screen metrics cover
every display, including monitors positioned left of or above the primary,
which is where the negative origin comes from.
"""
from __future__ import annotations

import logging
import math
import os
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("assistant.vision")

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)

# MediaPipe's 21-point hand landmark indices.
WRIST = 0
THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 5, 6, 7, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP = 9, 10, 11, 12
RING_MCP, RING_PIP, RING_DIP, RING_TIP = 13, 14, 15, 16
PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP = 17, 18, 19, 20

FINGER_TIPS = (INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP)
FINGER_PIPS = (INDEX_PIP, MIDDLE_PIP, RING_PIP, PINKY_PIP)


# ---------------------------------------------------------------------------
# Screen geometry
# ---------------------------------------------------------------------------


def enable_dpi_awareness() -> bool:
    """Declare per-monitor DPI awareness. Must run before reading metrics."""
    try:
        import ctypes

        # -4 == DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(-4):
            return True
    except (AttributeError, OSError):
        pass
    try:  # Windows 8.1 fallback
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return True
    except (AttributeError, OSError):
        pass
    try:  # Vista+ fallback
        import ctypes

        return bool(ctypes.windll.user32.SetProcessDPIAware())
    except (AttributeError, OSError):
        logger.warning("Could not set DPI awareness; cursor placement may be offset")
        return False


@dataclass(frozen=True)
class ScreenBounds:
    """The whole virtual desktop, in real pixels."""

    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def to_pixels(self, nx: float, ny: float) -> tuple[int, int]:
        """Map normalised [0,1] coordinates onto the desktop."""
        x = self.left + nx * (self.width - 1)
        y = self.top + ny * (self.height - 1)
        return int(round(x)), int(round(y))


def screen_bounds() -> ScreenBounds:
    """Virtual-screen bounds across every monitor."""
    try:
        import win32api
        import win32con

        return ScreenBounds(
            left=win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN),
            top=win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN),
            width=win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN),
            height=win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN),
        )
    except ImportError:  # pragma: no cover - non-Windows
        return ScreenBounds(0, 0, 1920, 1080)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


def default_model_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "NovaAssistant" / "models" / "hand_landmarker.task"


def ensure_model(path: Optional[Path] = None) -> Path:
    """Download the ~8MB landmark model once and cache it."""
    path = Path(path or default_model_path())
    if path.is_file() and path.stat().st_size > 1_000_000:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading hand landmark model to %s", path)
    temporary = path.with_suffix(".part")
    urllib.request.urlretrieve(MODEL_URL, temporary)
    temporary.replace(path)
    return path


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def distance(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


@dataclass
class HandShape:
    """One frame's hand pose, reduced to the facts gestures are built from."""

    landmarks: list
    size: float                    # wrist -> middle knuckle, the scale unit
    extended: tuple[bool, bool, bool, bool]   # index, middle, ring, pinky
    thumb_out: bool
    palm: tuple[float, float]      # palm centre, normalised frame coords
    pinch_index: float             # thumb-index gap / hand size
    pinch_middle: float
    facing: float                  # >0 when the palm faces the camera

    @property
    def finger_count(self) -> int:
        return sum(self.extended) + (1 if self.thumb_out else 0)

    @property
    def all_extended(self) -> bool:
        return all(self.extended)

    @property
    def none_extended(self) -> bool:
        return not any(self.extended)


def _extended(landmarks, tip: int, pip: int, factor: float) -> bool:
    """A finger is extended when its tip is farther from the wrist than its
    own middle joint - orientation-independent, unlike comparing raw y."""
    wrist = landmarks[WRIST]
    return distance(wrist, landmarks[tip]) > distance(wrist, landmarks[pip]) * factor


def analyse(landmarks, extend_factor: float = 1.12) -> HandShape:
    """Turn 21 raw landmarks into the derived facts every gesture needs.

    ``extend_factor`` above 1 adds deliberate slack: a finger has to be
    clearly straight to count as extended, so a half-curled resting hand
    doesn't flicker between poses frame to frame.
    """
    wrist = landmarks[WRIST]
    middle_mcp = landmarks[MIDDLE_MCP]
    size = max(distance(wrist, middle_mcp), 1e-6)

    extended = tuple(
        _extended(landmarks, tip, pip, extend_factor)
        for tip, pip in zip(FINGER_TIPS, FINGER_PIPS)
    )

    # The thumb folds sideways, so measure its distance from the index
    # knuckle rather than asking whether it is "straight".
    thumb_out = distance(landmarks[THUMB_TIP], landmarks[INDEX_MCP]) > size * 0.75

    palm_x = (wrist.x + landmarks[INDEX_MCP].x + landmarks[PINKY_MCP].x + middle_mcp.x) / 4
    palm_y = (wrist.y + landmarks[INDEX_MCP].y + landmarks[PINKY_MCP].y + middle_mcp.y) / 4

    # Sign of the palm cross-product tells front from back of the hand.
    ax, ay = landmarks[INDEX_MCP].x - wrist.x, landmarks[INDEX_MCP].y - wrist.y
    bx, by = landmarks[PINKY_MCP].x - wrist.x, landmarks[PINKY_MCP].y - wrist.y
    facing = ax * by - ay * bx

    return HandShape(
        landmarks=landmarks,
        size=size,
        extended=extended,  # type: ignore[arg-type]
        thumb_out=thumb_out,
        palm=(palm_x, palm_y),
        pinch_index=distance(landmarks[THUMB_TIP], landmarks[INDEX_TIP]) / size,
        pinch_middle=distance(landmarks[THUMB_TIP], landmarks[MIDDLE_TIP]) / size,
        facing=facing,
    )


# ---------------------------------------------------------------------------
# Smoothing
# ---------------------------------------------------------------------------


class OneEuroFilter:
    """Adaptive low-pass filter for noisy pointer signals.

    Smooths jitter when the hand is nearly still and automatically cuts lag
    once it moves quickly - so it is not the usual choice between "smooth but
    laggy" and "responsive but shaky". (Casiez, Godin & Vogel, CHI 2012.)
    """

    def __init__(self, freq: float = 30.0, min_cutoff: float = 1.0, beta: float = 0.0, d_cutoff: float = 1.0):
        self.freq = max(1.0, freq)
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x_prev: Optional[float] = None
        self._dx_prev = 0.0
        self._t_prev: Optional[float] = None

    def _alpha(self, cutoff: float) -> float:
        te = 1.0 / self.freq
        tau = 1.0 / (2 * math.pi * max(cutoff, 1e-6))
        return 1.0 / (1.0 + tau / te)

    def reset(self) -> None:
        self._x_prev = None
        self._dx_prev = 0.0
        self._t_prev = None

    def __call__(self, x: float, timestamp: Optional[float] = None) -> float:
        if self._t_prev is not None and timestamp is not None:
            dt = timestamp - self._t_prev
            if dt > 1e-6:
                self.freq = 1.0 / dt
        if self._x_prev is None:
            self._x_prev = x
            self._t_prev = timestamp
            return x
        dx = (x - self._x_prev) * self.freq
        a_d = self._alpha(self.d_cutoff)
        dx_hat = a_d * dx + (1 - a_d) * self._dx_prev
        a = self._alpha(self.min_cutoff + self.beta * abs(dx_hat))
        x_hat = a * x + (1 - a) * self._x_prev
        self._x_prev, self._dx_prev, self._t_prev = x_hat, dx_hat, timestamp
        return x_hat


@dataclass
class PositionHistory:
    """Recent cursor positions, so a click can use where you *were* pointing.

    Pinching physically drags the index fingertip toward the thumb - several
    hundred pixels once amplified - so the cursor has already left the target
    by the time the pinch registers. Rewinding to the position from just
    before the pinch started is what makes clicks land where they are aimed.
    """

    seconds: float = 0.25
    _samples: deque = field(default_factory=lambda: deque(maxlen=64))

    def push(self, x: float, y: float, when: float | None = None) -> None:
        self._samples.append((when if when is not None else time.perf_counter(), x, y))

    def before(self, when: float | None = None) -> Optional[tuple[float, float]]:
        """The newest sample at least ``seconds`` older than ``when``."""
        if not self._samples:
            return None
        cutoff = (when if when is not None else time.perf_counter()) - self.seconds
        chosen = None
        for stamp, x, y in self._samples:
            if stamp <= cutoff:
                chosen = (x, y)
            else:
                break
        return chosen or (self._samples[0][1], self._samples[0][2])

    def clear(self) -> None:
        self._samples.clear()


def open_camera(index: int = 0, width: int = 640, height: int = 480):
    """Open a webcam with DirectShow (far faster to start on Windows)."""
    import cv2

    capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not capture.isOpened():
        capture.release()
        return None
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # always process the newest frame
    return capture


def create_landmarker(model_path: Path, num_hands: int = 1, confidence: float = 0.6):
    """Build a VIDEO-mode HandLandmarker (reuses tracking between frames)."""
    from mediapipe.tasks import python as mp_tasks
    from mediapipe.tasks.python import vision as mp_vision

    options = mp_vision.HandLandmarkerOptions(
        base_options=mp_tasks.BaseOptions(model_asset_path=str(model_path)),
        running_mode=mp_vision.RunningMode.VIDEO,
        num_hands=num_hands,
        min_hand_detection_confidence=confidence,
        min_hand_presence_confidence=confidence,
        min_tracking_confidence=confidence,
    )
    return mp_vision.HandLandmarker.create_from_options(options)
