from types import SimpleNamespace

from assistant.vision.virtual_mouse import (
    HysteresisButton,
    OneEuroFilter,
    is_finger_extended,
    remap_to_screen,
)


def _lm(x, y):
    return SimpleNamespace(x=x, y=y)


def test_remap_clamps_to_0_1_range():
    assert remap_to_screen(0.0, center=0.5, sensitivity=1.0) == 0.0
    assert remap_to_screen(1.0, center=0.5, sensitivity=1.0) == 1.0
    assert remap_to_screen(0.5, center=0.5, sensitivity=1.0) == 0.5


def test_remap_higher_sensitivity_reaches_edges_with_less_movement():
    # At sensitivity=1.0 (old behavior), a hand at 0.75 doesn't reach the
    # screen edge; at higher sensitivity, the same hand position should
    # map closer to (or past, then clamped to) the edge.
    low = remap_to_screen(0.75, center=0.5, sensitivity=1.0)
    high = remap_to_screen(0.75, center=0.5, sensitivity=2.0)
    assert high > low
    assert high == 1.0  # reaches the full edge instead of stopping short


def test_remap_center_bias_shifts_reachable_range():
    # With a center below 0.5 (the default, matching a hand held lower in
    # frame), the same raw y value maps further down the screen than with
    # a plain centered mapping - i.e. the bottom becomes easier to reach.
    biased = remap_to_screen(0.6, center=0.42, sensitivity=1.7)
    centered = remap_to_screen(0.6, center=0.5, sensitivity=1.7)
    assert biased > centered


def test_is_finger_extended_true_when_tip_far_from_wrist():
    wrist = _lm(0.5, 0.9)
    pip = _lm(0.5, 0.6)
    tip_extended = _lm(0.5, 0.2)  # far from wrist, past the pip
    tip_curled = _lm(0.5, 0.65)  # barely past the pip, close to wrist-ish
    assert is_finger_extended(tip_extended, pip, wrist) is True
    assert is_finger_extended(tip_curled, pip, wrist) is False


def test_hysteresis_button_requires_crossing_release_threshold_not_just_engage():
    button = HysteresisButton(engage_below=0.35, release_above=0.5)
    assert button.update(0.9) is None  # far from pinch, no event
    assert button.update(0.30) == "down"  # crosses engage threshold
    assert button.pressed is True
    # Ratio bounces back up but not past the (looser) release threshold -
    # should stay pressed, unlike a single-threshold design that would
    # flicker here.
    assert button.update(0.40) is None
    assert button.pressed is True
    assert button.update(0.6) == "up"
    assert button.pressed is False


def test_hysteresis_button_force_release():
    button = HysteresisButton(engage_below=0.35, release_above=0.5)
    button.update(0.1)
    assert button.pressed is True
    assert button.force_release() == "up"
    assert button.pressed is False
    assert button.force_release() is None  # already released, no duplicate event


def test_one_euro_filter_smooths_a_step_change_gradually():
    f = OneEuroFilter(freq=30.0, min_cutoff=0.6, beta=0.0)
    first = f(0.0, timestamp=0.0)
    assert first == 0.0
    jumped = f(100.0, timestamp=1 / 30)
    # A single frame after a big jump shouldn't snap all the way there -
    # that's the whole point of filtering - but should move meaningfully
    # toward it.
    assert 0 < jumped < 100

    value = jumped
    t = 1 / 30
    for _ in range(60):
        t += 1 / 30
        value = f(100.0, timestamp=t)
    # After ~2 seconds of a held target, the filter should have converged
    # close to it.
    assert value > 95


def test_one_euro_filter_reduces_jitter_around_a_steady_value():
    f = OneEuroFilter(freq=30.0, min_cutoff=0.6, beta=0.0)
    t = 0.0
    outputs = []
    for i in range(30):
        t += 1 / 30
        noisy = 50.0 + (1 if i % 2 == 0 else -1)  # +/-1 jitter around 50
        outputs.append(f(noisy, timestamp=t))
    # The filtered output should settle to a much smaller spread than the
    # +/-1 raw jitter once it has a few samples to work with.
    tail = outputs[10:]
    assert (max(tail) - min(tail)) < 1.0
