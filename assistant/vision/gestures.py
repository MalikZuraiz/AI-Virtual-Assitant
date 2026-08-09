"""Hand gestures: counting fingers, and nothing else.

This is deliberately the simplest thing that can work, because every earlier
version was cleverer and less reliable.

**What was removed and why**

* *Swipes.* Motion detection and pose detection fought each other: a hand
  sweeping across the frame is always holding *some* shape, so one movement
  produced a stray action, the swipe, or both. Removing motion entirely also
  removes every timing knob it needed.
* *Rock, OK, call, thumbs up/down.* Each depended on a fiddly geometric test
  that worked in good light at one hand angle. A gesture that works most of
  the time is worse than no gesture, because you stop trusting the feature.

**What is left** is the one signal a webcam reads reliably: how many fingers
are sticking up. Zero through four are each their own pose (fist through four
fingers); five is split into two by one extra bit - whether the fingers are
pressed together or spread - which separates a traffic-policeman "stop" from
a relaxed open palm. Every count does something except open palm, which is
kept deliberately free as the one unambiguous "reset" shape (see ``NEUTRAL``).

**The bug that made it feel dead.** After a gesture fired, the recogniser
disarmed and only an *open palm* re-armed it. So showing two fingers then
three did nothing at all: the second pose was blocked until you happened to
flash a palm. Re-arming is now per-pose - a different shape always fires, and
only repeating the *same* shape needs a neutral (open palm) in between.

Mappings live in ``config/gestures.json``.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from assistant.vision.handtracking import (
    INDEX_MCP,
    INDEX_TIP,
    MIDDLE_TIP,
    PINKY_MCP,
    PINKY_TIP,
    RING_TIP,
    THUMB_TIP,
    HandShape,
    analyse,
    create_landmarker,
    distance as _gap,
    ensure_model,
    open_camera,
)

logger = logging.getLogger("assistant.gestures")

StatusCallback = Callable[[str], None]
#: (pose_name, what_happened) - called only when a gesture actually fires,
#: kept separate from ``StatusCallback`` so the two can be treated
#: differently (a fired gesture is worth interrupting speech for; "camera
#: ready" is not).
FiredCallback = Callable[[str, str], None]
FrameCallback = Callable[[object], None]
ActionCallback = Callable[[str, dict], str]

#: Every pose the classifier can produce. One per finger count, plus the two
#: five-finger variants.
POSES = ("fist", "one", "two", "three", "four", "stop", "open_palm")

#: Poses that fire nothing and re-arm the recogniser. Open palm only,
#: deliberately: it is the one shape that never doubles as an action, so it
#: is the unambiguous "I mean to reset, not to gesture" signal - show it
#: between gestures whenever you want to repeat the one you just did.
NEUTRAL = {"none", "open_palm"}

#: A thumb folded across the palm ends up near the pinky knuckle; an extended
#: one is far outside it. Measured against palm width, this separates cleanly
#: - unlike the old test against the *index* knuckle, where a thumb held
#: alongside the index (exactly what a "stop" hand does) was ambiguous.
THUMB_OUT_RATIO = 1.30

#: Mean gap between neighbouring fingertips, over palm width, above which the
#: fingers count as spread rather than pressed together.
SPREAD_RATIO = 0.33


@dataclass
class GestureEvent:
    name: str
    label: str
    detail: str = ""


def thumb_is_out(shape: HandShape) -> bool:
    """Whether the thumb is extended sideways rather than folded in."""
    palm_width = _gap(shape.landmarks[INDEX_MCP], shape.landmarks[PINKY_MCP])
    if palm_width <= 1e-6:
        return shape.thumb_out
    return _gap(shape.landmarks[THUMB_TIP], shape.landmarks[PINKY_MCP]) > palm_width * THUMB_OUT_RATIO


def finger_spread(shape: HandShape) -> float:
    """Average gap between neighbouring fingertips, in palm widths."""
    palm_width = _gap(shape.landmarks[INDEX_MCP], shape.landmarks[PINKY_MCP])
    if palm_width <= 1e-6:
        return 0.0
    tips = [shape.landmarks[i] for i in (INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP)]
    gaps = [_gap(a, b) for a, b in zip(tips, tips[1:])]
    return (sum(gaps) / len(gaps)) / palm_width


def classify(shape: HandShape) -> str:
    """Name the pose from the finger count. Returns "none" if unclear."""
    count = sum(shape.extended)

    if count == 0:
        return "fist"
    if count == 1:
        # Only a raised index counts. A lone middle or pinky finger is far
        # more often a misread of a curling hand than a deliberate signal.
        return "one" if shape.extended[0] else "none"
    if count == 2:
        return "two" if shape.extended[0] and shape.extended[1] else "none"
    if count == 3:
        return "three" if not shape.extended[3] else "none"

    # Four fingers up: the thumb and the finger spread decide which of the
    # three five-ish poses this is.
    if not thumb_is_out(shape):
        return "four"
    return "open_palm" if finger_spread(shape) > SPREAD_RATIO else "stop"


class GestureRecogniser:
    """Debounces classified poses. Pure logic - no camera, no actions."""

    def __init__(self, hold_frames: int = 5, cooldown: float = 1.0) -> None:
        self.hold_frames = max(1, hold_frames)
        self.cooldown = cooldown
        self._candidate = "none"
        self._streak = 0
        self._last_fired = "none"
        #: True when the *last fired* pose is allowed to fire again.
        self._armed = True
        # -inf, not 0: nothing has fired yet, so nothing is cooling down.
        self._last_fire_time = float("-inf")

    def is_cooling_down(self, now: float) -> bool:
        return (now - self._last_fire_time) < self.cooldown

    @property
    def cooling_down(self) -> bool:
        return self.is_cooling_down(time.perf_counter())

    def reset(self) -> None:
        self._candidate = "none"
        self._streak = 0
        self._armed = True

    def update(self, shape: Optional[HandShape], now: float | None = None) -> Optional[str]:
        """Feed one frame. Returns a pose name only when one truly fires."""
        now = time.perf_counter() if now is None else now

        if shape is None:
            self._candidate, self._streak, self._armed = "none", 0, True
            return None

        pose = classify(shape)
        if pose == self._candidate:
            self._streak += 1
        else:
            self._candidate, self._streak = pose, 1

        if pose in NEUTRAL:
            self._armed = True
            return None
        if self.is_cooling_down(now):
            return None
        if self._streak < self.hold_for(pose):
            return None
        # Repeating the same pose needs a neutral in between, so one held
        # shape is one event. A *different* pose always fires - blocking that
        # was what made the whole feature seem dead.
        if pose == self._last_fired and not self._armed:
            return None
        return self._fire(pose, now)

    def hold_for(self, pose: str) -> int:
        """Frames this pose must be held. Uniform - nothing bound is costly."""
        return self.hold_frames

    def _fire(self, name: str, now: float) -> str:
        self._last_fired = name
        self._last_fire_time = now
        self._armed = False
        self._streak = 0
        return name


class GestureController:
    """Runs the camera loop and hands recognised gestures to an action sink."""

    def __init__(
        self,
        on_action: ActionCallback,
        bindings: Optional[dict] = None,
        camera_index: int = 0,
        fps_limit: int = 15,
        hold_frames: int = 5,
        cooldown: float = 1.0,
        confidence: float = 0.5,
        model_path: Optional[Path] = None,
        on_status: Optional[StatusCallback] = None,
        on_fired: Optional[FiredCallback] = None,
        on_frame: Optional[FrameCallback] = None,
    ) -> None:
        self.on_action = on_action
        self.bindings = bindings or {}
        self.camera_index = camera_index
        # Static poses need no motion resolution, so 15 FPS is plenty and
        # leaves noticeably more CPU for whatever you are actually doing.
        self.fps_limit = max(6, fps_limit)
        # A lower detection threshold than the old 0.6: missing the hand
        # entirely is the worst failure, and the hold requirement already
        # filters the weak frames a permissive threshold lets through.
        self.confidence = confidence
        self.model_path = model_path
        self.on_status = on_status
        self.on_fired = on_fired
        self.on_frame = on_frame

        self.recogniser = GestureRecogniser(hold_frames, cooldown)
        self.last_error = ""
        self.last_gesture = ""
        self.last_pose = ""
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # -- lifecycle --------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> str:
        if self.running:
            return "Gesture control is already on."
        try:
            self.model_path = ensure_model(self.model_path)
        except Exception as exc:  # noqa: BLE001
            return f"Couldn't get the hand-tracking model: {exc}"
        self._stop.clear()
        self.last_error = ""
        self._thread = threading.Thread(target=self._run, name="gestures", daemon=True)
        self._thread.start()
        time.sleep(0.4)
        if self.last_error:
            return self.last_error
        return "Gesture control on. Hold a shape for about a third of a second.\n" + self.describe_bindings()

    def stop(self) -> str:
        if not self.running:
            return "Gesture control wasn't running."
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None
        return "Gesture control off."

    def describe_bindings(self) -> str:
        if not self.bindings:
            return "(no gestures bound - see config/gestures.json)"
        lines = []
        for name, binding in self.bindings.items():
            label = binding.get("label") or binding.get("keys") or binding.get("command", "")
            lines.append(f"  {name.replace('_', ' '):<12} {label}")
        return "\n".join(lines)

    # -- camera loop ------------------------------------------------------
    def _run(self) -> None:
        try:
            import cv2
            from mediapipe import Image, ImageFormat
        except ImportError as exc:
            self.last_error = f"Gesture dependencies missing: {exc}"
            logger.error(self.last_error)
            return

        capture = open_camera(self.camera_index)
        if capture is None:
            self.last_error = "Couldn't open the webcam for gestures."
            return

        try:
            detector = create_landmarker(self.model_path, num_hands=1, confidence=self.confidence)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"Couldn't start hand tracking: {exc}"
            logger.error(self.last_error, exc_info=True)
            capture.release()
            return

        interval = 1.0 / self.fps_limit
        started = time.perf_counter()
        warmup_until = started + 1.0
        if self.on_status:
            self.on_status("Gestures: camera ready")

        try:
            while not self._stop.is_set():
                loop_start = time.perf_counter()
                ok, frame = capture.read()
                if not ok:
                    time.sleep(0.01)
                    continue

                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                # detect_for_video demands strictly increasing timestamps.
                stamp = int((time.perf_counter() - started) * 1000)
                result = detector.detect_for_video(
                    Image(image_format=ImageFormat.SRGB, data=rgb), stamp
                )

                shape = analyse(result.hand_landmarks[0]) if result.hand_landmarks else None
                self.last_pose = classify(shape) if shape else "no hand"

                if time.perf_counter() >= warmup_until:
                    fired = self.recogniser.update(shape, loop_start)
                    if fired:
                        self._dispatch(fired)

                self._draw(cv2, frame)
                if self.on_frame is not None:
                    try:
                        self.on_frame(frame)
                    except Exception:  # noqa: BLE001
                        logger.exception("Gesture preview callback failed")

                remaining = interval - (time.perf_counter() - loop_start)
                if remaining > 0:
                    time.sleep(remaining)
        except Exception as exc:  # noqa: BLE001 - never take the app down
            self.last_error = f"Gesture loop stopped: {exc}"
            logger.exception("Gesture loop crashed")
        finally:
            capture.release()
            try:
                detector.close()
            except Exception:  # noqa: BLE001
                pass
            if self.on_status:
                self.on_status("Gestures: stopped")

    def _dispatch(self, name: str) -> None:
        binding = self.bindings.get(name)
        self.last_gesture = name
        if not binding:
            logger.debug("Gesture %s has no binding", name)
            if self.on_status:
                self.on_status(f"Gesture '{name}' (unbound)")
            return
        try:
            message = self.on_action(name, binding)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Gesture action failed")
            message = f"{name} failed: {exc}"
        # A real, bound gesture firing is worth telling the user about in
        # chat and out loud - that is what on_fired is for. on_status stays
        # reserved for lifecycle bookkeeping (camera ready/stopped), which
        # would be an annoying interruption if spoken every single fire.
        if self.on_fired and message:
            self.on_fired(name, message)

    def _draw(self, cv2, frame) -> None:
        cv2.putText(
            frame, self.last_pose.replace("_", " "), (10, 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (250, 44, 250), 2,
        )
        if self.last_gesture:
            cv2.putText(
                frame, f"last: {self.last_gesture}", (10, 52),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (120, 220, 120), 2,
            )
        if self.recogniser.cooling_down:
            cv2.putText(
                frame, "cooldown", (10, frame.shape[0] - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 160, 240), 1,
            )
