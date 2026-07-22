"""Hand-tracking virtual mouse.

Fixes two real bugs present in the legacy ``VM.py`` / ``virtual mouse.py``:

1. The pinch-to-click check computed
   ``sqrt((index_x - thumb_x)**2 + (index_x - thumb_x)**2)`` - both terms
   used ``index_x``/``thumb_x`` for *both* axes, using pixel coordinates
   compared to a fixed threshold of 5. That is dimensionally meaningless
   (a raw pixel distance of "5" means something completely different up
   close vs. far from the camera) and doesn't actually measure openness of
   the pinch. This version computes the true Euclidean distance between
   the normalized index-tip and thumb-tip landmarks and scales the click
   threshold by the detected hand's own size, so it works at any distance
   from the camera.
2. There was no frame-rate cap, so the capture loop ran as fast as the
   webcam+CPU allowed, competing with the GUI thread for CPU and producing
   an unpredictably fast/jittery cursor. This version throttles the loop to
   a configurable target FPS (default 30) using delta timing.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger("assistant.virtual_mouse")

FrameCallback = Callable[[object], None]
StatusCallback = Callable[[str], None]


@dataclass
class _Point:
    x: float
    y: float


class VirtualMouseController:
    """Move the real Windows cursor with your index finger; pinch to click."""

    def __init__(
        self,
        camera_index: int = 0,
        fps_limit: int = 30,
        click_cooldown: float = 0.6,
        smoothing: float = 0.35,
        pinch_ratio_threshold: float = 0.4,
        on_frame: Optional[FrameCallback] = None,
        on_status: Optional[StatusCallback] = None,
    ) -> None:
        self.camera_index = camera_index
        self.fps_limit = max(5, fps_limit)
        self.click_cooldown = click_cooldown
        self.smoothing = smoothing
        self.pinch_ratio_threshold = pinch_ratio_threshold
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
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        time.sleep(0.6)  # let the camera open so we can report failure quickly
        if self.last_error:
            self._thread = None
            return self.last_error
        self.running = True
        return (
            "Virtual mouse started. Move your index finger to move the cursor; "
            "pinch your thumb and index finger together to click. "
            "Say 'stop virtual mouse' to end."
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

    def _run(self) -> None:
        try:
            import cv2
            import mediapipe as mp
            import win32api
            import win32con
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

        screen_w = win32api.GetSystemMetrics(0)
        screen_h = win32api.GetSystemMetrics(1)

        mp_hands = mp.solutions.hands
        mp_drawing = mp.solutions.drawing_utils
        cursor = _Point(screen_w / 2, screen_h / 2)
        last_click = 0.0
        frame_interval = 1.0 / self.fps_limit

        if self.on_status:
            self.on_status("Virtual mouse: camera ready")

        try:
            with mp_hands.Hands(
                max_num_hands=1, min_detection_confidence=0.6, min_tracking_confidence=0.6
            ) as hands:
                while not self._stop_event.is_set():
                    loop_start = time.perf_counter()
                    ok, frame = capture.read()
                    if not ok:
                        continue

                    frame = cv2.flip(frame, 1)
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    results = hands.process(rgb)

                    if results.multi_hand_landmarks:
                        hand = results.multi_hand_landmarks[0]
                        mp_drawing.draw_landmarks(frame, hand, mp_hands.HAND_CONNECTIONS)

                        index_tip = hand.landmark[mp_hands.HandLandmark.INDEX_FINGER_TIP]
                        thumb_tip = hand.landmark[mp_hands.HandLandmark.THUMB_TIP]
                        wrist = hand.landmark[mp_hands.HandLandmark.WRIST]
                        middle_mcp = hand.landmark[mp_hands.HandLandmark.MIDDLE_FINGER_MCP]

                        # Hand "size" (wrist -> middle knuckle) normalizes the
                        # pinch threshold so it works at any distance from the
                        # camera, instead of a fixed pixel threshold.
                        hand_size = (
                            (wrist.x - middle_mcp.x) ** 2 + (wrist.y - middle_mcp.y) ** 2
                        ) ** 0.5
                        pinch_distance = (
                            (index_tip.x - thumb_tip.x) ** 2 + (index_tip.y - thumb_tip.y) ** 2
                        ) ** 0.5

                        target_x = index_tip.x * screen_w
                        target_y = index_tip.y * screen_h
                        cursor.x += (target_x - cursor.x) * self.smoothing
                        cursor.y += (target_y - cursor.y) * self.smoothing
                        try:
                            win32api.SetCursorPos((int(cursor.x), int(cursor.y)))
                        except Exception:
                            pass

                        if hand_size > 0 and (pinch_distance / hand_size) < self.pinch_ratio_threshold:
                            now = time.perf_counter()
                            if now - last_click > self.click_cooldown:
                                last_click = now
                                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                                logger.info("Virtual mouse click")

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
            capture.release()
            if self.on_status:
                self.on_status("Virtual mouse: camera released")
