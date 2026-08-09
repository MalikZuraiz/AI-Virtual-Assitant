"""Hand gestures: finger counts for static actions, one pose that carries
swipes, and one pose that is a continuous hold, not a fire-once event.

**The vocabulary**

| Show | Kind | Does |
|---|---|---|
| Fist (0 fingers) | fire-once | close the current virtual desktop |
| 1 finger | **held** | mute while shown, resume the moment it's lowered |
| 2 fingers (index+middle) | **swipe carrier** | no static action - see below |
| 3 fingers | fire-once | volume up |
| 4 fingers | fire-once | volume down |
| 5 fingers | neutral | re-arms the recogniser, fires nothing |
| Rock (index+pinky) | fire-once | screenshot |
| L (index+thumb) | fire-once | switch windows (reopens the last one if none is active) |
| Pinky alone | fire-once | Task View (same screen as Windows+Tab) |
| Swipe left/right/up/down, shown as 2 fingers | fire-once | prev/next/new desktop, minimise |

**Why swipes are gated to one specific pose.** The first version supported
swipes with *any* hand shape moving, and that was the actual bug: a hand
mid-swipe is always incidentally holding some shape, so one movement could
fire a stray pose action, the swipe, or both - motion detection and pose
detection were fighting over the same frames. Restricting swipe detection to
frames where the hand is specifically showing 2 fingers removes the conflict
entirely: every other pose (fist, 3, 4, 5, rock) is *never* treated as a
swipe candidate, so a fired pose and a fired swipe can no longer collide.
Two fingers held still, not moving, does nothing on its own - it is a
deliberate "I am about to swipe" shape, not an action.

**Why 1 finger is not just another fire-once pose.** Muting is not a button
press, it is a switch: it should engage the instant you raise the finger and
release the instant you lower it, for as long as you hold it - not once per
hold-and-release like everything else. It runs through its own debounced
level-triggered tracker (:class:`HoldTracker`), entirely separate from
:class:`GestureRecogniser`'s edge-triggered fire-once model, with its own
short debounce on each edge so a single misread frame can't flicker it.

Mappings live in ``config/gestures.json``.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from assistant.vision.handtracking import (
    INDEX_MCP,
    PINKY_MCP,
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
#: True on entering the held pose, False on leaving it (or losing the hand,
#: or the camera stopping - see MUTE_POSE and the _run() safety net).
HoldCallback = Callable[[bool], None]
FrameCallback = Callable[[object], None]
ActionCallback = Callable[[str, dict], str]

#: Fire-once poses the debounced GestureRecogniser handles.
POSES = ("fist", "three", "four", "five", "rock", "l_sign", "pinky")
#: The continuous hold pose - tracked by HoldTracker, never by
#: GestureRecogniser (see the module docstring for why).
HOLD_POSE = "one"
#: The pose that carries directional swipes and has no static action of its
#: own - see the module docstring for why swipes are gated to one pose.
SWIPE_POSE = "two"
#: The four swipe directions, as bindable gesture names.
SWIPES = ("swipe_left", "swipe_right", "swipe_up", "swipe_down")

#: Consecutive frames the hand may briefly leave SWIPE_POSE - a misread from
#: motion blur or a one-frame tracking dropout during the wrist swing itself
#: - without wiping the swipe motion history. A real, deliberate switch to a
#: different pose lasts longer than this and still resets normally.
SWIPE_MISS_TOLERANCE = 2

#: Poses that fire nothing and re-arm the recogniser. Five fingers only,
#: deliberately: it is the one shape that never doubles as an action, so it
#: is the unambiguous "I mean to reset, not to gesture" signal - show it
#: between gestures whenever you want to repeat the one you just did.
NEUTRAL = {"none", "five"}

#: A thumb folded across the palm ends up near the pinky knuckle; an extended
#: one is far outside it. Measured against palm width, this separates cleanly
#: - unlike a test against the *index* knuckle, where a thumb held alongside
#: the index (exactly what a flat hand does) is ambiguous.
THUMB_OUT_RATIO = 1.30


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


def classify(shape: HandShape) -> str:
    """Name the pose from the finger shape. Returns "none" if unclear."""
    index, middle, ring, pinky = shape.extended
    count = sum(shape.extended)

    # Rock (index + pinky, "horns") is a 2-finger count like the swipe pose,
    # but a completely different pair of fingers - checked first so it is
    # never swallowed by the generic count logic below.
    if index and pinky and not middle and not ring:
        return "rock"

    if count == 0:
        return "fist"
    if count == 1:
        # A lone middle or ring finger is far more often a misread of a
        # curling hand than a deliberate signal, so those still fall through
        # to "none". Index and pinky are the two deliberate one-finger
        # signals - the thumb is what tells the two index-only poses apart:
        # tucked in is the relaxed mute hold, stuck out to the side is the
        # deliberate "L" shape.
        if index:
            return "l_sign" if thumb_is_out(shape) else "one"
        if pinky:
            return "pinky"
        return "none"
    if count == 2:
        return "two" if index and middle else "none"
    if count == 3:
        return "three" if not pinky else "none"
    if count == 4:
        # Thumb decides the last split: tucked is "four", extended is the
        # open "five" - no spread check any more, since the pose it used to
        # separate ("stop", palm-out-fingers-together) moved to the 1-finger
        # hold. One less geometric judgement call, one more reliable pose.
        return "five" if thumb_is_out(shape) else "four"
    return "none"


class GestureRecogniser:
    """Debounces fire-once poses. Pure logic - no camera, no actions.

    Deliberately knows nothing about ``HOLD_POSE`` or ``SWIPE_POSE`` -
    ``GestureController`` never feeds it a shape classified as either (it
    passes ``None`` instead, the same as "no hand"), so this class only ever
    sees the fire-once vocabulary and can stay exactly as simple as that.
    """

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
        return self.mark_fired(pose, now)

    def hold_for(self, pose: str) -> int:
        """Frames this pose must be held. Uniform - nothing bound is costly."""
        return self.hold_frames

    def mark_fired(self, name: str, now: float) -> str:
        """Record a fire and start its cooldown - also called for swipes,
        so a swipe and a fire-once pose share one cooldown clock and can
        never both go off from the same instant of motion."""
        self._last_fired = name
        self._last_fire_time = now
        self._armed = False
        self._streak = 0
        return name


class HoldTracker:
    """Tracks a pose that means "while shown", not "when it appears".

    Debounced on *both* edges, but not symmetrically: entering needs a few
    consecutive confirming frames (a stray single-frame misread must not
    engage it), leaving needs fewer (releasing the finger should feel
    immediate - "resume the moment it's lowered" is the whole point).
    """

    def __init__(self, enter_frames: int = 3, exit_frames: int = 2) -> None:
        self.enter_frames = max(1, enter_frames)
        self.exit_frames = max(1, exit_frames)
        self._streak_on = 0
        self._streak_off = 0
        self.active = False

    def update(self, held: bool) -> Optional[bool]:
        """Feed one frame's "is the pose currently showing" bit.

        Returns True the instant it engages, False the instant it releases,
        None on every other frame (nothing changed).
        """
        if held:
            self._streak_on += 1
            self._streak_off = 0
            if not self.active and self._streak_on >= self.enter_frames:
                self.active = True
                return True
        else:
            self._streak_off += 1
            self._streak_on = 0
            if self.active and self._streak_off >= self.exit_frames:
                self.active = False
                return False
        return None

    def reset(self) -> Optional[bool]:
        """Force-release, e.g. hand lost or the camera loop stopping."""
        self._streak_on = 0
        self._streak_off = 0
        if self.active:
            self.active = False
            return False
        return None


@dataclass
class SwipeDetector:
    """Turns palm movement into swipe_left / _right / _up / _down.

    Only ever fed frames where the hand is showing :data:`SWIPE_POSE` - see
    the module docstring for why that is what makes swipes reliable this
    time. Reset on every frame that pose is *not* showing, so there is never
    stale motion history left over from a moment ago in a different shape.
    """

    #: Long enough that a deliberate, unhurried swipe still accumulates the
    #: required travel; short enough that it doesn't lag behind a fast one.
    window_seconds: float = 0.45
    #: Minimum travel, as a fraction of the frame, to count as a swipe.
    min_travel: float = 0.22
    #: How much the dominant axis must beat the other one.
    axis_ratio: float = 1.8
    _samples: deque = field(default_factory=lambda: deque(maxlen=32))

    def reset(self) -> None:
        self._samples.clear()

    def update(self, palm: tuple[float, float], now: float) -> Optional[str]:
        self._samples.append((now, palm[0], palm[1]))
        while self._samples and now - self._samples[0][0] > self.window_seconds:
            self._samples.popleft()
        # 3, not 4: a fast, deliberate wrist swing (the motion the module
        # docstring asks for) can cover the whole window in 3 frames at the
        # 15 FPS this normally runs at - requiring a 4th sample meant a quick
        # swipe could finish before there was ever enough history to judge it.
        if len(self._samples) < 3:
            return None

        _t0, x0, y0 = self._samples[0]
        _t1, x1, y1 = self._samples[-1]
        dx, dy = x1 - x0, y1 - y0
        adx, ady = abs(dx), abs(dy)

        if adx >= self.min_travel and adx > ady * self.axis_ratio:
            self.reset()
            return "swipe_right" if dx > 0 else "swipe_left"
        if ady >= self.min_travel and ady > adx * self.axis_ratio:
            self.reset()
            return "swipe_down" if dy > 0 else "swipe_up"
        return None


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
        min_travel: float = 0.22,
        model_path: Optional[Path] = None,
        on_status: Optional[StatusCallback] = None,
        on_fired: Optional[FiredCallback] = None,
        on_mute: Optional[HoldCallback] = None,
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
        self.on_mute = on_mute
        self.on_frame = on_frame

        self.recogniser = GestureRecogniser(hold_frames, cooldown)
        self.swipes = SwipeDetector(min_travel=min_travel)
        self.mute_tracker = HoldTracker()
        # Consecutive off-pose frames tolerated mid-swipe before the motion
        # history is thrown away - see _process() for why this exists.
        self._swipe_miss_streak = 0
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
        self.swipes.reset()
        self._swipe_miss_streak = 0
        self.mute_tracker = HoldTracker()
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
                pose = classify(shape) if shape else "no hand"
                self.last_pose = pose

                if time.perf_counter() >= warmup_until:
                    self._process(shape, pose, loop_start)

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
            # Safety net, mirroring WindowSwitcher.release() for a held Alt:
            # however the loop ends - explicit stop, a webcam error, a crash
            # in the try block above - a mute the hold pose engaged must
            # never survive past it. Speech getting permanently stuck
            # silent because the camera closed mid-hold would be exactly
            # the kind of "stuck" this whole rewrite is trying to avoid.
            released = self.mute_tracker.reset()
            if released is False and self.on_mute:
                self.on_mute(False)
            capture.release()
            try:
                detector.close()
            except Exception:  # noqa: BLE001
                pass
            if self.on_status:
                self.on_status("Gestures: stopped")

    def _process(self, shape: Optional[HandShape], pose: str, now: float) -> None:
        """One frame's worth of gesture logic, split three ways by pose:
        the held mute pose, the swipe-carrier pose, and everything else
        (fire-once poses, handled by the debounced recogniser)."""
        # 1. The continuous hold - checked every frame regardless of what
        #    else is going on, so muting is never delayed by a cooldown or
        #    another pose's debounce.
        edge = self.mute_tracker.update(pose == HOLD_POSE)
        if edge is not None and self.on_mute:
            self.on_mute(edge)

        # 2. The swipe carrier - motion only matters while this pose is
        #    showing. A single off-pose frame is tolerated (see
        #    SWIPE_MISS_TOLERANCE) so a one-frame tracking dropout during
        #    the swing itself - normal during fast hand motion - doesn't
        #    throw away motion that was building correctly; a genuine
        #    switch to a different held pose still resets it, just not
        #    instantly on the very first flickered frame.
        if pose == SWIPE_POSE:
            self._swipe_miss_streak = 0
            direction = self.swipes.update(shape.palm, now)
            if direction and not self.recogniser.is_cooling_down(now):
                self.recogniser.mark_fired(direction, now)
                self._dispatch(direction)
        else:
            self._swipe_miss_streak += 1
            if self._swipe_miss_streak > SWIPE_MISS_TOLERANCE:
                self.swipes.reset()

        # 3. Everything else goes through the normal fire-once path. The
        #    hold and swipe poses are never fed to it - passing None is
        #    exactly what "no hand" already means to it, so both poses stay
        #    fully invisible to the recogniser's own state instead of
        #    needing special-casing inside it.
        recognised_shape = shape if pose not in (HOLD_POSE, SWIPE_POSE) else None
        fired = self.recogniser.update(recognised_shape, now)
        if fired:
            self._dispatch(fired)

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
        label = "muted (hold 1 finger)" if self.mute_tracker.active else self.last_pose.replace("_", " ")
        cv2.putText(
            frame, label, (10, 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (250, 44, 250), 2,
        )
        if self.last_gesture:
            cv2.putText(
                frame, f"last: {self.last_gesture.replace('_', ' ')}", (10, 52),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (120, 220, 120), 2,
            )
        if self.recogniser.cooling_down:
            cv2.putText(
                frame, "cooldown", (10, frame.shape[0] - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 160, 240), 1,
            )
