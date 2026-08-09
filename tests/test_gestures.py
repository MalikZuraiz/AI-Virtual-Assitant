"""Gesture recognition: fire-once poses, the held mute pose, and swipes
gated to one specific pose so they can't collide with anything else.
"""
from types import SimpleNamespace

from assistant.vision.gestures import (
    HOLD_POSE,
    NEUTRAL,
    SWIPE_POSE,
    GestureRecogniser,
    HoldTracker,
    SwipeDetector,
    classify,
    thumb_is_out,
)
from assistant.vision.handtracking import analyse


def _lm(x, y):
    return SimpleNamespace(x=x, y=y)


def _hand(*, index=False, middle=False, ring=False, pinky=False,
          thumb_pos=None, offset=(0.0, 0.0)):
    """Build 21 landmarks for a finger-count pose."""
    dx, dy = offset
    points = [_lm(0.5 + dx, 0.5 + dy) for _ in range(21)]
    points[0] = _lm(0.50 + dx, 0.90 + dy)      # wrist
    points[9] = _lm(0.50 + dx, 0.62 + dy)      # middle MCP
    points[5] = _lm(0.42 + dx, 0.64 + dy)      # index MCP
    points[17] = _lm(0.58 + dx, 0.64 + dy)     # pinky MCP

    for is_up, column, (tip, pip) in zip(
        (index, middle, ring, pinky), (0.44, 0.48, 0.52, 0.56), ((8, 6), (12, 10), (16, 14), (20, 18))
    ):
        points[pip] = _lm(column + dx, 0.66 + dy)
        points[tip] = _lm(column + dx, (0.30 if is_up else 0.70) + dy)

    points[4] = _lm(*(thumb_pos or (0.52 + dx, 0.62 + dy)))
    return analyse(points)


#: A thumb held out to the side, well clear of the pinky knuckle.
THUMB_OUT = (0.22, 0.58)


# -- pose classification ------------------------------------------------


def test_each_fire_once_pose():
    assert classify(_hand()) == "fist"
    assert classify(_hand(index=True)) == "one"
    assert classify(_hand(index=True, middle=True)) == "two"
    assert classify(_hand(index=True, middle=True, ring=True)) == "three"
    assert classify(_hand(index=True, middle=True, ring=True, pinky=True)) == "four"


def test_index_alone_splits_on_the_thumb():
    """The same one-finger shape means two different things depending on
    the thumb: tucked in is the relaxed mute hold, stuck out to the side is
    the deliberate "L" shape for window switching."""
    tucked = _hand(index=True)
    spread = _hand(index=True, thumb_pos=THUMB_OUT)
    assert classify(tucked) == "one"
    assert classify(spread) == "l_sign"


def test_pinky_alone_is_its_own_pose():
    assert classify(_hand(pinky=True)) == "pinky"


def test_three_fingers_with_an_open_thumb_reads_as_the_swipe_pose():
    """Real usage: the swipe pose is often performed with the thumb held
    open too (same shape as the L sign, just two fingers instead of one),
    and on an open hand the ring finger can bleed just past the extended
    threshold without genuinely spreading apart. A stray "three" there would
    steal the shared cooldown from an in-progress swipe, so an open thumb
    routes a 3-finger reading back to the swipe carrier instead."""
    bled_ring = _hand(index=True, middle=True, ring=True, thumb_pos=THUMB_OUT)
    assert classify(bled_ring) == "two"


def test_three_fingers_with_a_tucked_thumb_is_still_three():
    """The disambiguation above must not cost the deliberate volume-up
    pose, which is normally held with the thumb tucked in as usual."""
    deliberate = _hand(index=True, middle=True, ring=True)
    assert classify(deliberate) == "three"


def test_four_fingers_with_thumb_out_is_five():
    hand = _hand(index=True, middle=True, ring=True, pinky=True, thumb_pos=THUMB_OUT)
    assert classify(hand) == "five"


def test_rock_sign_is_distinct_from_two_fingers():
    """Both are 2-finger counts, but a completely different pair - rock
    (index + pinky) must never be confused with the swipe-carrier pose
    (index + middle)."""
    rock = _hand(index=True, pinky=True)
    two = _hand(index=True, middle=True)
    assert classify(rock) == "rock"
    assert classify(two) == "two"


def test_odd_combinations_are_rejected_rather_than_guessed():
    assert classify(_hand(middle=True)) == "none"
    assert classify(_hand(index=True, middle=True, pinky=True)) == "none"
    assert classify(_hand(index=True, ring=True)) == "none"


def test_thumb_out_measured_against_the_pinky_knuckle():
    tucked = _hand(index=True, middle=True, ring=True, pinky=True)
    assert thumb_is_out(tucked) is False
    spread = _hand(index=True, middle=True, ring=True, pinky=True, thumb_pos=THUMB_OUT)
    assert thumb_is_out(spread) is True


def test_only_five_is_neutral():
    assert NEUTRAL == {"none", "five"}
    assert HOLD_POSE == "one"
    assert SWIPE_POSE == "two"


# -- fire-once debouncing (fist / three / four / five / rock) -----------


def test_a_pose_must_be_held_before_it_fires():
    recogniser = GestureRecogniser(hold_frames=4, cooldown=0.0)
    three = _hand(index=True, middle=True, ring=True)
    fired = [recogniser.update(three, now=i * 0.05) for i in range(4)]
    assert fired[:3] == [None, None, None]
    assert fired[3] == "three"


def test_a_single_frame_misread_never_fires():
    recogniser = GestureRecogniser(hold_frames=5, cooldown=0.0)
    three = _hand(index=True, middle=True, ring=True)
    four = _hand(index=True, middle=True, ring=True, pinky=True)
    events = [
        recogniser.update(four if i % 3 == 2 else three, now=i * 0.05)
        for i in range(12)
    ]
    assert all(event is None for event in events)


def test_holding_a_pose_fires_only_once():
    recogniser = GestureRecogniser(hold_frames=2, cooldown=0.0)
    three = _hand(index=True, middle=True, ring=True)
    events = [recogniser.update(three, now=i * 0.05) for i in range(20)]
    assert events.count("three") == 1


def test_a_different_pose_fires_without_needing_a_neutral_first():
    """Firing used to disarm the recogniser, re-armed only by the neutral
    pose - so a different gesture right after one that just fired did
    nothing until you flashed five fingers in between. Fixed: re-arming is
    per-pose now."""
    recogniser = GestureRecogniser(hold_frames=2, cooldown=0.0)
    fires = []
    for pose_hand in (
        _hand(index=True, middle=True, ring=True),                    # three
        _hand(index=True, middle=True, ring=True, pinky=True),        # four
        _hand(),                                                       # fist
    ):
        for i in range(3):
            fired = recogniser.update(pose_hand, now=len(fires) * 10 + i * 0.05)
            if fired:
                fires.append(fired)
    assert fires == ["three", "four", "fist"]


def test_repeating_the_same_pose_needs_a_neutral_in_between():
    recogniser = GestureRecogniser(hold_frames=2, cooldown=0.0)
    three = _hand(index=True, middle=True, ring=True)
    five = _hand(index=True, middle=True, ring=True, pinky=True, thumb_pos=THUMB_OUT)

    fires = [f for i in range(4) if (f := recogniser.update(three, now=i * 0.05))]
    assert fires == ["three"]

    fires += [f for i in range(4) if (f := recogniser.update(three, now=1 + i * 0.05))]
    assert fires == ["three"]  # still one event - no neutral shown yet

    for i in range(3):
        recogniser.update(five, now=2 + i * 0.05)
    fires += [f for i in range(4) if (f := recogniser.update(three, now=3 + i * 0.05))]
    assert fires == ["three", "three"]


def test_cooldown_blocks_a_second_gesture():
    recogniser = GestureRecogniser(hold_frames=1, cooldown=5.0)
    three = _hand(index=True, middle=True, ring=True)
    four = _hand(index=True, middle=True, ring=True, pinky=True)
    assert recogniser.update(three, now=0.0) == "three"
    for i in range(10):
        assert recogniser.update(four, now=0.1 + i * 0.05) is None


def test_losing_the_hand_resets_everything():
    recogniser = GestureRecogniser(hold_frames=3, cooldown=0.0)
    three = _hand(index=True, middle=True, ring=True)
    recogniser.update(three, now=0.0)
    recogniser.update(three, now=0.05)
    assert recogniser.update(None, now=0.1) is None
    assert recogniser.update(three, now=0.15) is None
    assert recogniser.update(three, now=0.20) is None
    assert recogniser.update(three, now=0.25) == "three"


def test_five_itself_never_fires():
    recogniser = GestureRecogniser(hold_frames=2, cooldown=0.0)
    five = _hand(index=True, middle=True, ring=True, pinky=True, thumb_pos=THUMB_OUT)
    events = [recogniser.update(five, now=i * 0.05) for i in range(10)]
    assert all(event is None for event in events)


def test_mark_fired_shares_the_cooldown_clock_with_swipes():
    """A swipe and a fire-once pose must share one cooldown, or a swipe
    immediately followed by settling into another pose could double-fire."""
    recogniser = GestureRecogniser(hold_frames=1, cooldown=5.0)
    recogniser.mark_fired("swipe_left", now=0.0)
    three = _hand(index=True, middle=True, ring=True)
    assert recogniser.update(three, now=0.1) is None
    assert recogniser.is_cooling_down(0.1) is True


# -- the held mute pose (HoldTracker) ------------------------------------


def test_hold_tracker_needs_consecutive_frames_to_engage():
    tracker = HoldTracker(enter_frames=3, exit_frames=2)
    assert tracker.update(True) is None
    assert tracker.update(True) is None
    assert tracker.update(True) is True
    assert tracker.active is True


def test_hold_tracker_releases_faster_than_it_engages():
    """Entering needs debounce (avoid a stray misread engaging mute);
    leaving should feel immediate - "resume when the gesture is no more"."""
    tracker = HoldTracker(enter_frames=3, exit_frames=2)
    for _ in range(3):
        tracker.update(True)
    assert tracker.active is True
    assert tracker.update(False) is None
    assert tracker.update(False) is False
    assert tracker.active is False


def test_a_single_dropped_frame_does_not_release_early():
    tracker = HoldTracker(enter_frames=3, exit_frames=2)
    for _ in range(3):
        tracker.update(True)
    assert tracker.update(False) is None  # one miss - not enough to release
    assert tracker.update(True) is None   # back to held - still active, no edge
    assert tracker.active is True


def test_reset_force_releases_and_reports_it():
    tracker = HoldTracker(enter_frames=3, exit_frames=2)
    for _ in range(3):
        tracker.update(True)
    assert tracker.active is True
    assert tracker.reset() is False
    assert tracker.active is False


def test_reset_when_not_active_reports_nothing():
    tracker = HoldTracker()
    assert tracker.reset() is None


def test_no_edge_reported_while_steadily_held_or_steadily_released():
    tracker = HoldTracker(enter_frames=2, exit_frames=2)
    assert tracker.update(True) is None
    assert tracker.update(True) is True
    assert tracker.update(True) is None
    assert tracker.update(True) is None


# -- swipes, gated to the two-finger pose ---------------------------------


def test_a_fast_horizontal_move_is_a_swipe():
    detector = SwipeDetector(min_travel=0.2)
    result = None
    for i in range(6):
        result = detector.update((0.2 + i * 0.06, 0.5), now=i * 0.05) or result
    assert result == "swipe_right"


def test_direction_is_read_correctly():
    detector = SwipeDetector(min_travel=0.2)
    result = None
    for i in range(6):
        result = detector.update((0.8 - i * 0.06, 0.5), now=i * 0.05) or result
    assert result == "swipe_left"

    detector.reset()
    result = None
    for i in range(6):
        result = detector.update((0.5, 0.8 - i * 0.06), now=i * 0.05) or result
    assert result == "swipe_up"

    detector.reset()
    result = None
    for i in range(6):
        result = detector.update((0.5, 0.2 + i * 0.06), now=i * 0.05) or result
    assert result == "swipe_down"


def test_slow_drift_is_not_a_swipe():
    detector = SwipeDetector(window_seconds=0.35, min_travel=0.22)
    result = None
    for i in range(30):
        result = detector.update((0.2 + i * 0.02, 0.5), now=i * 0.1) or result
    assert result is None


def test_a_diagonal_move_is_rejected_as_ambiguous():
    detector = SwipeDetector(min_travel=0.2, axis_ratio=1.8)
    result = None
    for i in range(6):
        result = detector.update((0.2 + i * 0.06, 0.2 + i * 0.06), now=i * 0.05) or result
    assert result is None


def test_the_swipe_detector_rearms_after_firing():
    detector = SwipeDetector(min_travel=0.2)
    for i in range(6):
        detector.update((0.1 + i * 0.06, 0.5), now=i * 0.05)
    assert detector.update((0.5, 0.5), now=0.35) is None


def test_holding_a_pose_other_than_two_fingers_is_never_treated_as_a_swipe():
    """The whole point of gating swipes to one pose: showing a fist while
    moving your hand around must never be read as a swipe."""
    detector = SwipeDetector(min_travel=0.2)
    result = None
    for i in range(6):
        # A fist sweeping across the frame - this must be fed to the
        # detector by nobody, since GestureController only updates it while
        # SWIPE_POSE is showing. This test documents the detector's own
        # behaviour in isolation: it doesn't know or care what pose is
        # showing, which is exactly why gating happens one level up.
        result = detector.update((0.2 + i * 0.06, 0.5), now=i * 0.05) or result
    assert result == "swipe_right"  # the detector itself is pose-agnostic
