"""Hand-tracking virtual mouse.

Uses MediaPipe's Tasks API (``mediapipe.tasks.python.vision.HandLandmarker``,
``VIDEO`` running mode) rather than the legacy ``mediapipe.solutions.hands``
API, which mediapipe 0.10.x removed entirely. The Tasks API needs a small
model file (``hand_landmarker.task``, ~8MB) that isn't bundled with the
package anymore - it's downloaded once from Google's official MediaPipe
model storage and cached under ``%APPDATA%\\NovaAssistant\\models\\``.

Gesture vocabulary (checked in this priority order each frame a hand is
visible):

- **Open palm** (index/middle/ring/pinky all extended) - pause: freezes the
  cursor and releases any held buttons, so you can reposition your hand
  without dragging the cursor across the screen (like lifting a real mouse).
- **Two fingers up** (index+middle extended, ring+pinky curled, not
  pinching) - scroll: moving the two fingertips up/down scrolls the wheel.
- Otherwise - **point and click**: the index fingertip moves the cursor;
  pinching thumb+index is a left click (press-and-hold to drag, release to
  let go); pinching thumb+middle is a right click, the same way.

Three real bugs from earlier/legacy versions are fixed here:

1. **Limited/awkward range ("can't reach the bottom of the screen")**: the
   whole camera frame ([0,1] on each axis) used to map directly to the
   whole screen, but a hand help up to a laptop webcam rarely reaches the
   frame's true edges (especially the bottom) without an awkward stretch.
   Movement now maps from a smaller, configurable "active region" of the
   frame (``virtual_mouse_sensitivity``/``_center_x``/``_center_y`` in
   config) to the full screen, so a comfortable range of motion reaches
   every screen edge.
2. **Laggy/jittery movement**: a fixed-factor exponential smoothing filter
   is either too laggy (fights fast movement) or too jittery (barely
   smooths at all) at any single fixed factor. Replaced with a One Euro
   Filter (Casiez et al., 2012) per axis, which is specifically designed
   for this: heavy smoothing when the hand is nearly still, and much less
   lag once it starts moving quickly.
3. **Unreliable click ("pinch gesture ... not that good")**: a single
   distance threshold flickers right at the boundary, firing spurious
   clicks. Replaced with hysteresis (an "engage" threshold to press and a
   looser "release" threshold to let go) plus real press/release mouse
   events instead of a synthetic click - this is also what makes
   click-and-drag possible.

The capture loop also now uses ``VIDEO`` running mode (rather than
stateless ``IMAGE`` mode) and requests a smaller capture resolution, both
of which measurably reduce per-frame inference time on modest CPUs -
though on very old/low-power hardware the hand-tracking model itself is
still the bottleneck; lower ``virtual_mouse_fps_limit`` in Settings if
needed rather than expecting it to look identical to a gaming mouse.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("assistant.virtual_mouse")

FrameCallback = Callable[[object], None]
StatusCallback = Callable[[str], None]

# Standard MediaPipe 21-point hand landmark indices we need.
_WRIST = 0
_THUMB_TIP = 4
_INDEX_PIP = 6
_INDEX_TIP = 8
_MIDDLE_MCP = 9
_MIDDLE_PIP = 10
_MIDDLE_TIP = 12
_RING_PIP = 14
_RING_TIP = 16
_PINKY_PIP = 18
_PINKY_TIP = 20

_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)


def _default_model_path() -> Path:
    import os

    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "NovaAssistant" / "models" / "hand_landmarker.task"


def remap_to_screen(value: float, center: float, sensitivity: float) -> float:
    """Maps a normalized [0,1] hand-landmark coordinate to a normalized
    [0,1] screen coordinate, using a region centered at ``center`` whose
    width is ``1/sensitivity`` of the frame - a higher sensitivity means a
    smaller hand-movement range covers the full screen. Clamped to [0,1]
    so the cursor still reaches the exact edge instead of stopping short."""
    half_range = 0.5 / max(sensitivity, 0.05)
    lo, hi = center - half_range, center + half_range
    if hi <= lo:
        return 0.5
    return min(1.0, max(0.0, (value - lo) / (hi - lo)))


def distance(a, b) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


def is_finger_extended(tip, pip, wrist, factor: float = 1.05) -> bool:
    """A finger counts as "extended" if its tip is meaningfully farther
    from the wrist than its own PIP joint - this works regardless of hand
    rotation/orientation, unlike comparing raw y-coordinates."""
    return distance(wrist, tip) > distance(wrist, pip) * factor


class OneEuroFilter:
    """Adaptive low-pass filter tuned for noisy position signals like hand
    landmarks: smooths out jitter when the signal is nearly still, and
    reduces lag automatically once it starts moving quickly. See Casiez,
    Godin & Vogel, "1€ Filter: A Simple Speed-based Low-pass Filter for
    Noisy Input in Interactive Systems" (CHI 2012).
    """

    def __init__(self, freq: float = 30.0, min_cutoff: float = 1.0, beta: float = 0.0, d_cutoff: float = 1.0):
        self.freq = max(1.0, freq)
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x_prev: Optional[float] = None
        self._dx_prev: float = 0.0
        self._t_prev: Optional[float] = None

    def _alpha(self, cutoff: float) -> float:
        te = 1.0 / self.freq
        tau = 1.0 / (2 * math.pi * max(cutoff, 1e-6))
        return 1.0 / (1.0 + tau / te)

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

        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = self._alpha(cutoff)
        x_hat = a * x + (1 - a) * self._x_prev

        self._x_prev = x_hat
        self._dx_prev = dx_hat
        self._t_prev = timestamp
        return x_hat


@dataclass
class HysteresisButton:
    """Turns a noisy continuous ratio (e.g. pinch distance) into a clean
    press/release event stream with two different thresholds, so the
    signal has to clearly cross before flipping state again instead of
    flickering right at one boundary."""

    engage_below: float
    release_above: float
    pressed: bool = False

    def update(self, ratio: float) -> Optional[str]:
        if not self.pressed and ratio < self.engage_below:
            self.pressed = True
            return "down"
        if self.pressed and ratio > self.release_above:
            self.pressed = False
            return "up"
        return None

    def force_release(self) -> Optional[str]:
        if self.pressed:
            self.pressed = False
            return "up"
        return None


class VirtualMouseController:
    """Move the real Windows cursor with your index finger; see the module
    docstring for the full gesture vocabulary (click/drag, right-click,
    scroll, pause)."""

    def __init__(
        self,
        camera_index: int = 0,
        fps_limit: int = 30,
        sensitivity: float = 1.7,
        center_x: float = 0.5,
        center_y: float = 0.42,
        min_cutoff: float = 0.6,
        beta: float = 0.6,
        click_engage_ratio: float = 0.35,
        click_release_ratio: float = 0.5,
        scroll_speed: float = 400.0,
        model_path: Optional[Path] = None,
        on_frame: Optional[FrameCallback] = None,
        on_status: Optional[StatusCallback] = None,
    ) -> None:
        self.camera_index = camera_index
        self.fps_limit = max(5, fps_limit)
        self.sensitivity = sensitivity
        self.center_x = center_x
        self.center_y = center_y
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.click_engage_ratio = click_engage_ratio
        self.click_release_ratio = click_release_ratio
        self.scroll_speed = scroll_speed
        self.model_path = Path(model_path) if model_path else _default_model_path()
        self.on_frame = on_frame
        self.on_status = on_status

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.running = False
        self.last_error: Optional[str] = None

    def start(self) -> str:
        if self.running:
            return "The virtual mouse is already running."
        self.last_error = None

        try:
            self._ensure_model()
        except Exception as exc:
            logger.error("Hand-tracking model download failed", exc_info=True)
            return f"Couldn't download the hand-tracking model (check your internet connection): {exc}"

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        time.sleep(0.6)  # let the camera/model open so we can report failure quickly
        if self.last_error:
            self._thread = None
            return self.last_error
        self.running = True
        return (
            "Virtual mouse started. Point your index finger to move the cursor. "
            "Pinch thumb+index to click (hold to drag). Pinch thumb+middle to "
            "right-click. Hold up index+middle together to scroll. Open your "
            "whole palm to pause. Say 'stop virtual mouse' to end."
        )

    def stop(self) -> str:
        if not self.running:
            return "The virtual mouse isn't running."
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None
        self.running = False
        return "Virtual mouse stopped."

    def _ensure_model(self) -> None:
        """Downloads the hand-landmark model on first use and caches it -
        subsequent calls are a no-op once the file exists."""
        if self.model_path.exists() and self.model_path.stat().st_size > 0:
            return
        import requests

        if self.on_status:
            self.on_status("Virtual mouse: downloading hand-tracking model (one-time, ~8MB)...")
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        response = requests.get(_MODEL_URL, timeout=60)
        response.raise_for_status()
        tmp_path = self.model_path.with_suffix(".tmp")
        tmp_path.write_bytes(response.content)
        tmp_path.replace(self.model_path)

    def _run(self) -> None:
        try:
            import cv2
            import win32api
            import win32con
            from mediapipe import Image, ImageFormat
            from mediapipe.tasks import python as mp_tasks
            from mediapipe.tasks.python import vision as mp_vision
        except ImportError as exc:
            self.last_error = f"Virtual mouse dependencies missing: {exc}"
            logger.error(self.last_error)
            return

        capture = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        if not capture.isOpened():
            self.last_error = "Could not open the webcam for the virtual mouse."
            logger.error(self.last_error)
            capture.release()
            return
        # A smaller capture resolution measurably reduces per-frame overhead
        # (color conversion, drawing, and the encode step feeding the model)
        # on modest CPUs - the model resizes internally regardless, so this
        # doesn't cost tracking accuracy.
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        try:
            base_options = mp_tasks.BaseOptions(model_asset_path=str(self.model_path))
            options = mp_vision.HandLandmarkerOptions(
                base_options=base_options,
                running_mode=mp_vision.RunningMode.VIDEO,
                num_hands=1,
                min_hand_detection_confidence=0.6,
                min_tracking_confidence=0.6,
            )
            detector = mp_vision.HandLandmarker.create_from_options(options)
        except Exception as exc:
            self.last_error = f"Couldn't initialize hand tracking: {exc}"
            logger.error(self.last_error, exc_info=True)
            capture.release()
            return

        screen_w = win32api.GetSystemMetrics(0)
        screen_h = win32api.GetSystemMetrics(1)
        filter_x = OneEuroFilter(freq=self.fps_limit, min_cutoff=self.min_cutoff, beta=self.beta)
        filter_y = OneEuroFilter(freq=self.fps_limit, min_cutoff=self.min_cutoff, beta=self.beta)
        left_button = HysteresisButton(self.click_engage_ratio, self.click_release_ratio)
        right_button = HysteresisButton(self.click_engage_ratio, self.click_release_ratio)
        scroll_active = False
        scroll_last_y = 0.0
        frame_interval = 1.0 / self.fps_limit
        session_start = time.perf_counter()

        if self.on_status:
            self.on_status("Virtual mouse: camera ready")

        try:
            while not self._stop_event.is_set():
                loop_start = time.perf_counter()
                ok, frame = capture.read()
                if not ok:
                    continue

                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = Image(image_format=ImageFormat.SRGB, data=rgb)
                timestamp_ms = int((time.perf_counter() - session_start) * 1000)
                result = detector.detect_for_video(mp_image, timestamp_ms)

                mode_label = "no hand"
                if result.hand_landmarks:
                    hand = result.hand_landmarks[0]
                    wrist = hand[_WRIST]
                    thumb_tip = hand[_THUMB_TIP]
                    index_tip = hand[_INDEX_TIP]
                    middle_tip = hand[_MIDDLE_TIP]
                    middle_mcp = hand[_MIDDLE_MCP]

                    index_ext = is_finger_extended(index_tip, hand[_INDEX_PIP], wrist)
                    middle_ext = is_finger_extended(middle_tip, hand[_MIDDLE_PIP], wrist)
                    ring_ext = is_finger_extended(hand[_RING_TIP], hand[_RING_PIP], wrist)
                    pinky_ext = is_finger_extended(hand[_PINKY_TIP], hand[_PINKY_PIP], wrist)

                    hand_size = distance(wrist, middle_mcp)
                    left_ratio = (distance(thumb_tip, index_tip) / hand_size) if hand_size > 0 else 1.0
                    right_ratio = (distance(thumb_tip, middle_tip) / hand_size) if hand_size > 0 else 1.0

                    frame_h, frame_w = frame.shape[:2]

                    if index_ext and middle_ext and ring_ext and pinky_ext:
                        # Open palm: pause - release anything held, freeze cursor.
                        mode_label = "paused (open palm)"
                        scroll_active = False
                        if left_button.force_release() == "up":
                            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                        if right_button.force_release() == "up":
                            win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)

                    elif (
                        index_ext and middle_ext and not ring_ext and not pinky_ext
                        and not left_button.pressed and not right_button.pressed
                    ):
                        mode_label = "scroll"
                        scroll_y = (index_tip.y + middle_tip.y) / 2
                        if not scroll_active:
                            scroll_active = True
                            scroll_last_y = scroll_y
                        else:
                            dy = scroll_y - scroll_last_y
                            scroll_last_y = scroll_y
                            wheel_delta = int(-dy * self.scroll_speed)
                            if wheel_delta != 0:
                                win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, wheel_delta, 0)

                    else:
                        mode_label = "move"
                        scroll_active = False

                        norm_x = remap_to_screen(index_tip.x, self.center_x, self.sensitivity)
                        norm_y = remap_to_screen(index_tip.y, self.center_y, self.sensitivity)
                        now = time.perf_counter()
                        cursor_x = filter_x(norm_x * screen_w, now)
                        cursor_y = filter_y(norm_y * screen_h, now)
                        try:
                            win32api.SetCursorPos((int(cursor_x), int(cursor_y)))
                        except Exception:
                            pass

                        left_event = left_button.update(left_ratio)
                        if left_event == "down":
                            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                        elif left_event == "up":
                            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

                        right_event = right_button.update(right_ratio)
                        if right_event == "down":
                            win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
                        elif right_event == "up":
                            win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)

                        if left_button.pressed:
                            mode_label = "left-drag"
                        elif right_button.pressed:
                            mode_label = "right-drag"

                    for lm in (wrist, thumb_tip, index_tip, middle_tip):
                        cv2.circle(
                            frame, (int(lm.x * frame_w), int(lm.y * frame_h)), 6, (250, 44, 250), -1
                        )
                else:
                    # No hand in frame - defensively release anything held so
                    # a lost hand can never leave the mouse button stuck down.
                    scroll_active = False
                    if left_button.force_release() == "up":
                        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                    if right_button.force_release() == "up":
                        win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)

                cv2.putText(
                    frame, mode_label, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (250, 44, 250), 2
                )

                if self.on_frame is not None:
                    try:
                        self.on_frame(frame)
                    except Exception:
                        logger.exception("Virtual mouse preview callback failed")

                elapsed = time.perf_counter() - loop_start
                remaining = frame_interval - elapsed
                if remaining > 0:
                    time.sleep(remaining)
        finally:
            if left_button.pressed:
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            if right_button.pressed:
                win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
            capture.release()
            detector.close()
            if self.on_status:
                self.on_status("Virtual mouse: camera released")
