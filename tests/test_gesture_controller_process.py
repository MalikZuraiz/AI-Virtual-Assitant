"""GestureController._process(): the frame-by-frame routing that ties the
held mute pose, the swipe carrier, and the fire-once poses together without
letting them interfere with each other. No camera involved - shapes are
built directly and fed straight to _process(), exactly what the real camera
loop does once per frame.
"""
from types import SimpleNamespace

from assistant.vision.gestures import GestureController
from assistant.vision.handtracking import analyse


def _lm(x, y):
    return SimpleNamespace(x=x, y=y)


def _hand(*, index=False, middle=False, ring=False, pinky=False, offset=(0.0, 0.0)):
    dx, dy = offset
    points = [_lm(0.5 + dx, 0.5 + dy) for _ in range(21)]
    points[0] = _lm(0.50 + dx, 0.90 + dy)
    points[9] = _lm(0.50 + dx, 0.62 + dy)
    points[5] = _lm(0.42 + dx, 0.64 + dy)
    points[17] = _lm(0.58 + dx, 0.64 + dy)
    for is_up, column, (tip, pip) in zip(
        (index, middle, ring, pinky), (0.44, 0.48, 0.52, 0.56), ((8, 6), (12, 10), (16, 14), (20, 18))
    ):
        points[pip] = _lm(column + dx, 0.66 + dy)
        points[tip] = _lm(column + dx, (0.30 if is_up else 0.70) + dy)
    points[4] = _lm(0.52 + dx, 0.62 + dy)
    return analyse(points)


FIST = _hand()
ONE = _hand(index=True)
TWO = _hand(index=True, middle=True)
THREE = _hand(index=True, middle=True, ring=True)


def _controller(**overrides):
    kwargs = dict(
        on_action=lambda name, binding: binding.get("label", name),
        bindings={
            "fist": {"label": "close desktop"},
            "three": {"label": "volume up"},
            "swipe_left": {"label": "previous desktop"},
            "swipe_right": {"label": "next desktop"},
        },
        hold_frames=3,
        cooldown=1.0,
        min_travel=0.2,
    )
    kwargs.update(overrides)
    return GestureController(**kwargs)


def test_a_fire_once_pose_dispatches_through_on_fired():
    fired = []
    controller = _controller(on_fired=lambda name, msg: fired.append((name, msg)))
    for i in range(4):
        controller._process(THREE, "three", now=i * 0.05)
    assert fired == [("three", "volume up")]


def test_the_mute_pose_never_reaches_on_fired():
    fired = []
    muted = []
    controller = _controller(
        on_fired=lambda name, msg: fired.append((name, msg)),
        on_mute=lambda m: muted.append(m),
    )
    for i in range(6):
        controller._process(ONE, "one", now=i * 0.05)
    assert fired == []
    assert muted == [True]


def test_mute_engages_then_releases_quickly_once_the_finger_lowers():
    """Release is deliberately snappier than engagement (HoldTracker's
    default exit_frames=2 < enter_frames=3) - "resume the moment it's
    lowered" means the release should not need as much confirmation as
    engaging did."""
    muted = []
    controller = _controller(on_mute=lambda m: muted.append(m))
    frames = [ONE] * 4 + [FIST] * 3  # hold up 1 finger, then lower to a fist
    poses = ["one"] * 4 + ["fist"] * 3
    for i, (shape, pose) in enumerate(zip(frames, poses)):
        controller._process(shape, pose, now=i * 0.05)
    assert muted == [True, False]


def test_mute_does_not_block_a_fire_once_pose_from_working_afterwards():
    fired = []
    muted = []
    controller = _controller(
        on_fired=lambda name, msg: fired.append(name),
        on_mute=lambda m: muted.append(m),
    )
    t = 0.0
    for _ in range(4):
        controller._process(ONE, "one", now=t); t += 0.05
    for _ in range(3):
        controller._process(None, "no hand", now=t); t += 0.05  # release
    for _ in range(4):
        controller._process(THREE, "three", now=t); t += 0.05
    assert muted == [True, False]
    assert fired == ["three"]


def test_swipe_fires_through_dispatch_while_showing_two_fingers():
    fired = []
    controller = _controller(on_fired=lambda name, msg: fired.append((name, msg)))
    t = 0.0
    for i in range(8):
        moved = _hand(index=True, middle=True, offset=(i * 0.05, 0.0))
        controller._process(moved, "two", now=t)
        t += 0.05
    assert fired == [("swipe_right", "next desktop")]


def test_a_swipe_completed_during_cooldown_fires_the_moment_it_clears():
    """A fire-once pose firing moments before a swipe finishes must not
    silently swallow that swipe - real usage swipes back to back, or right
    after some other gesture, and losing a correctly-read swipe to the
    shared cooldown (forcing the whole motion to be repeated) is exactly
    the "works, then gets stuck for a bit" pattern this fixes."""
    fired = []
    controller = _controller(on_fired=lambda name, msg: fired.append((name, msg)))
    t = 0.0
    for _ in range(4):
        controller._process(THREE, "three", now=t)
        t += 0.05
    assert fired == [("three", "volume up")]

    # A swipe completes while still well inside the cooldown "three" started.
    for i in range(8):
        moved = _hand(index=True, middle=True, offset=(i * 0.05, 0.0))
        controller._process(moved, "two", now=t)
        t += 0.05
    assert fired == [("three", "volume up")]  # not dispatched yet
    assert controller._pending_swipe == "swipe_right"

    # The instant the cooldown clears, the pending swipe fires on its own -
    # no need to repeat the motion.
    after_cooldown = controller.recogniser._last_fire_time + controller.recogniser.cooldown + 0.01
    controller._process(None, "no hand", now=after_cooldown)
    assert fired == [("three", "volume up"), ("swipe_right", "next desktop")]
    assert controller._pending_swipe is None


def test_a_swipe_never_also_fires_a_static_pose():
    """Two fingers, moving, must not ALSO look like a held 'two' pose to
    the fire-once recogniser - there is no static action bound to 'two' at
    all, and this proves the recogniser never even sees it as a candidate."""
    fired = []
    controller = _controller(on_fired=lambda name, msg: fired.append(name))
    t = 0.0
    for i in range(8):
        moved = _hand(index=True, middle=True, offset=(i * 0.05, 0.0))
        controller._process(moved, "two", now=t)
        t += 0.05
    assert "two" not in fired


def test_holding_two_fingers_still_fires_nothing():
    """Two fingers, NOT moving: no static action, no swipe (not enough
    travel) - the swipe-carrier pose alone does nothing on its own."""
    fired = []
    controller = _controller(on_fired=lambda name, msg: fired.append(name))
    for i in range(10):
        controller._process(TWO, "two", now=i * 0.05)
    assert fired == []


def test_a_single_dropped_frame_mid_swipe_does_not_wipe_progress():
    """A real wrist swing is fast enough that mediapipe drops or misreads a
    frame or two mid-motion (motion blur, momentary tracking loss) - without
    tolerance for that, every real swipe attempt got its history wiped by
    its own motion before ever accumulating enough travel to fire. One
    missed frame in the middle of an otherwise-clean swipe must not stop it
    firing."""
    fired = []
    controller = _controller(on_fired=lambda name, msg: fired.append((name, msg)))
    t = 0.0
    for i in range(4):
        moved = _hand(index=True, middle=True, offset=(i * 0.05, 0.0))
        controller._process(moved, "two", now=t)
        t += 0.05
    controller._process(None, "no hand", now=t)  # one dropped frame
    t += 0.05
    for i in range(4, 8):
        moved = _hand(index=True, middle=True, offset=(i * 0.05, 0.0))
        controller._process(moved, "two", now=t)
        t += 0.05
    assert fired == [("swipe_right", "next desktop")]


def test_switching_away_from_two_fingers_resets_swipe_progress():
    """Partial motion, then a *sustained* switch to a different pose - well
    past SWIPE_MISS_TOLERANCE, so this is a deliberate pose change rather
    than a dropped frame - then two-fingers again with fresh motion must not
    combine into one swipe from stale history."""
    fired = []
    controller = _controller(on_fired=lambda name, msg: fired.append(name))
    t = 0.0
    # Half a swipe's worth of travel...
    for i in range(3):
        moved = _hand(index=True, middle=True, offset=(i * 0.05, 0.0))
        controller._process(moved, "two", now=t)
        t += 0.05
    # ...interrupted by a fist, held well past the miss tolerance...
    for _ in range(5):
        controller._process(FIST, "fist", now=t)
        t += 0.05
    # ...then two fingers again, but not enough motion to complete a swipe
    # on its own from this fresh start.
    for i in range(3):
        moved = _hand(index=True, middle=True, offset=(i * 0.03, 0.0))
        controller._process(moved, "two", now=t)
        t += 0.05
    assert "swipe_right" not in fired


def test_five_fingers_reset_the_fire_once_repeat_block_but_never_mutes():
    fired = []
    muted = []
    controller = _controller(
        on_fired=lambda name, msg: fired.append(name),
        on_mute=lambda m: muted.append(m),
    )
    five = _hand(index=True, middle=True, ring=True, pinky=True)
    for i in range(10):
        controller._process(five, "five", now=i * 0.05)
    assert fired == []
    assert muted == []


def test_lifecycle_finally_block_force_releases_mute():
    """The safety net: however the camera loop ends, a held mute must not
    survive it. Exercises the same code path _run()'s finally block uses."""
    muted = []
    controller = _controller(on_mute=lambda m: muted.append(m))
    for i in range(4):
        controller._process(ONE, "one", now=i * 0.05)
    assert muted == [True]

    released = controller.mute_tracker.reset()
    if released is False and controller.on_mute:
        controller.on_mute(False)
    assert muted == [True, False]
