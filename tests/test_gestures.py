"""Finger-count gesture recognition.

The whole vocabulary is "how many fingers are up", plus one bit for whether
they are pressed together. These tests pin the two things that made earlier
versions unusable: poses being confused with each other, and the recogniser
silently refusing to fire.
"""
from types import SimpleNamespace

from assistant.vision.gestures import (
    NEUTRAL,
    GestureRecogniser,
    classify,
    finger_spread,
    thumb_is_out,
)
from assistant.vision.handtracking import analyse


def _lm(x, y):
    return SimpleNamespace(x=x, y=y)


def _hand(*, index=False, middle=False, ring=False, pinky=False,
          thumb_pos=None, spread=0.06):
    """Build 21 landmarks for a finger-count pose.

    ``spread`` is the horizontal gap between neighbouring fingertips; small
    means fingers pressed together, large means splayed.
    """
    points = [_lm(0.5, 0.5) for _ in range(21)]
    points[0] = _lm(0.50, 0.90)      # wrist
    points[9] = _lm(0.50, 0.62)      # middle MCP  -> size = 0.28
    points[5] = _lm(0.42, 0.64)      # index MCP   -> palm width = 0.16
    points[17] = _lm(0.58, 0.64)     # pinky MCP

    # Fingertips fan out from the middle of the hand by `spread` each.
    columns = [0.50 - 1.5 * spread, 0.50 - 0.5 * spread,
               0.50 + 0.5 * spread, 0.50 + 1.5 * spread]
    for is_up, column, (tip, pip) in zip(
        (index, middle, ring, pinky), columns, ((8, 6), (12, 10), (16, 14), (20, 18))
    ):
        points[pip] = _lm(column, 0.66)
        points[tip] = _lm(column, 0.30 if is_up else 0.70)

    # Thumb: tucked across the palm by default (close to the pinky knuckle).
    points[4] = _lm(*thumb_pos) if thumb_pos else _lm(0.52, 0.62)
    return analyse(points)


#: A thumb held out to the side, well clear of the pinky knuckle.
THUMB_OUT = (0.22, 0.58)


# -- the geometry the poses rest on ------------------------------------------


def test_a_tucked_thumb_is_not_out():
    assert thumb_is_out(_hand(index=True, middle=True, ring=True, pinky=True)) is False


def test_an_extended_thumb_is_out():
    hand = _hand(index=True, middle=True, ring=True, pinky=True, thumb_pos=THUMB_OUT)
    assert thumb_is_out(hand) is True


def test_spread_separates_a_stop_sign_from_an_open_palm():
    together = _hand(index=True, middle=True, ring=True, pinky=True, spread=0.03)
    splayed = _hand(index=True, middle=True, ring=True, pinky=True, spread=0.14)
    assert finger_spread(together) < finger_spread(splayed)


# -- one pose per finger count -----------------------------------------------


def test_each_finger_count_is_its_own_pose():
    assert classify(_hand()) == "fist"
    assert classify(_hand(index=True)) == "one"
    assert classify(_hand(index=True, middle=True)) == "two"
    assert classify(_hand(index=True, middle=True, ring=True)) == "three"
    assert classify(_hand(index=True, middle=True, ring=True, pinky=True)) == "four"


def test_five_fingers_together_is_stop_and_spread_is_open_palm():
    stop = _hand(index=True, middle=True, ring=True, pinky=True,
                 thumb_pos=THUMB_OUT, spread=0.03)
    palm = _hand(index=True, middle=True, ring=True, pinky=True,
                 thumb_pos=THUMB_OUT, spread=0.14)
    assert classify(stop) == "stop"
    assert classify(palm) == "open_palm"


def test_four_is_distinct_from_stop():
    """The pair that used to collide: both have four fingers up."""
    four = _hand(index=True, middle=True, ring=True, pinky=True, spread=0.03)
    stop = _hand(index=True, middle=True, ring=True, pinky=True,
                 thumb_pos=THUMB_OUT, spread=0.03)
    assert classify(four) == "four"
    assert classify(stop) == "stop"


def test_odd_finger_combinations_are_rejected_rather_than_guessed():
    """A lone middle finger is a misread of a curling hand, not a signal."""
    assert classify(_hand(middle=True)) == "none"
    assert classify(_hand(index=True, pinky=True)) == "none"
    assert classify(_hand(index=True, middle=True, pinky=True)) == "none"


def test_open_palm_is_the_only_neutral_pose():
    """Fist carries an action (previous desktop) - only open palm resets."""
    assert NEUTRAL == {"none", "open_palm"}
    assert "fist" not in NEUTRAL
    assert "stop" not in NEUTRAL


# -- debouncing --------------------------------------------------------------


def test_a_pose_must_be_held_before_it_fires():
    recogniser = GestureRecogniser(hold_frames=4, cooldown=0.0)
    two = _hand(index=True, middle=True)
    fired = [recogniser.update(two, now=i * 0.05) for i in range(4)]
    assert fired[:3] == [None, None, None]
    assert fired[3] == "two"


def test_a_single_frame_misread_never_fires():
    recogniser = GestureRecogniser(hold_frames=5, cooldown=0.0)
    two, three = _hand(index=True, middle=True), _hand(index=True, middle=True, ring=True)
    events = [
        recogniser.update(three if i % 3 == 2 else two, now=i * 0.05)
        for i in range(12)
    ]
    assert all(event is None for event in events)


def test_holding_a_pose_fires_only_once():
    recogniser = GestureRecogniser(hold_frames=2, cooldown=0.0)
    two = _hand(index=True, middle=True)
    events = [recogniser.update(two, now=i * 0.05) for i in range(20)]
    assert events.count("two") == 1


def test_a_different_pose_fires_without_needing_a_neutral_first():
    """The bug that made gestures feel dead.

    Firing disarmed the recogniser, and only an open palm re-armed it - so
    two fingers followed by three did nothing at all until you happened to
    flash a palm. Re-arming is per-pose now.
    """
    recogniser = GestureRecogniser(hold_frames=2, cooldown=0.0)
    fires = []
    for pose_hand in (
        _hand(index=True, middle=True),                              # two
        _hand(index=True, middle=True, ring=True),                   # three
        _hand(index=True, middle=True, ring=True, pinky=True),       # four
        _hand(index=True),                                           # one
    ):
        for i in range(3):
            fired = recogniser.update(pose_hand, now=len(fires) * 10 + i * 0.05)
            if fired:
                fires.append(fired)
    assert fires == ["two", "three", "four", "one"]


def test_repeating_the_same_pose_needs_a_neutral_in_between():
    recogniser = GestureRecogniser(hold_frames=2, cooldown=0.0)
    two = _hand(index=True, middle=True)
    palm = _hand(index=True, middle=True, ring=True, pinky=True,
                 thumb_pos=THUMB_OUT, spread=0.14)

    fires = [f for i in range(4) if (f := recogniser.update(two, now=i * 0.05))]
    assert fires == ["two"]

    # Straight back to two: still one event.
    fires += [f for i in range(4) if (f := recogniser.update(two, now=1 + i * 0.05))]
    assert fires == ["two"]

    # A palm re-arms it, so the same pose can fire again.
    for i in range(3):
        recogniser.update(palm, now=2 + i * 0.05)
    fires += [f for i in range(4) if (f := recogniser.update(two, now=3 + i * 0.05))]
    assert fires == ["two", "two"]


def test_cooldown_blocks_a_second_gesture():
    recogniser = GestureRecogniser(hold_frames=1, cooldown=5.0)
    two, three = _hand(index=True, middle=True), _hand(index=True, middle=True, ring=True)
    assert recogniser.update(two, now=0.0) == "two"
    for i in range(10):
        assert recogniser.update(three, now=0.1 + i * 0.05) is None


def test_losing_the_hand_resets_everything():
    recogniser = GestureRecogniser(hold_frames=3, cooldown=0.0)
    two = _hand(index=True, middle=True)
    recogniser.update(two, now=0.0)
    recogniser.update(two, now=0.05)
    assert recogniser.update(None, now=0.1) is None
    assert recogniser.update(two, now=0.15) is None
    assert recogniser.update(two, now=0.20) is None
    assert recogniser.update(two, now=0.25) == "two"


def test_open_palm_itself_never_fires():
    recogniser = GestureRecogniser(hold_frames=2, cooldown=0.0)
    palm = _hand(index=True, middle=True, ring=True, pinky=True,
                 thumb_pos=THUMB_OUT, spread=0.14)
    events = [recogniser.update(palm, now=i * 0.05) for i in range(10)]
    assert all(event is None for event in events)


def test_a_fist_fires_previous_desktop_not_neutral():
    """The gesture this round added - a fist is now an action, not a reset."""
    recogniser = GestureRecogniser(hold_frames=3, cooldown=0.0)
    fist = _hand()
    fires = [f for i in range(6) if (f := recogniser.update(fist, now=i * 0.05))]
    assert fires == ["fist"]
