"""Regression cover for bugs found integrating gestures with the rest of the
app - the kind that only show up once a real config and a real command pack
are involved, not from gestures.py's own unit tests.
"""
import inspect
import re

from assistant.store.defaults import default_for
from assistant.store.store import ConfigStore
from assistant.vision.gestures import (
    HOLD_POSE,
    NEUTRAL,
    POSES,
    SWIPE_POSE,
    SWIPES,
    GestureController,
)

#: Every pose a real, current config entry could legitimately name.
KNOWN_POSES = set(POSES) | set(SWIPES)


def test_vision_command_pack_only_passes_arguments_the_controller_accepts():
    """Every keyword `_gesture_controller` could pass must exist on the class.

    A static check, not a call - it doesn't need a webcam and it fails at
    collection time instead of the first time a user says 'start gestures'.
    This is what caught a real TypeError crash once already, when the
    swipe-era `min_travel` argument was dropped from the constructor but a
    leftover call site still passed it.
    """
    import assistant.commands.vision as vision

    source = inspect.getsource(vision._gesture_controller)
    # Only the GestureController(...) call itself - the function body also
    # builds an ActionRunner and calls ctx.announce(text, speak=...), and a
    # blanket scan of the whole source picks up unrelated keywords like
    # 'speak' from those calls too.
    call = re.search(r"GestureController\((.*?)\n    \)", source, re.DOTALL)
    assert call, "couldn't find the GestureController(...) call in _gesture_controller"
    accepted = set(inspect.signature(GestureController.__init__).parameters)
    passed = {name.strip() for name in re.findall(r"(\w+)=", call.group(1))}
    unknown = passed - accepted
    assert not unknown, f"vision.py passes {unknown}, which GestureController doesn't accept"


def test_the_default_gestures_seed_only_names_real_poses():
    """The bug: stale seed bindings resurrecting themselves via deep-merge -
    see test_wholesale_merge.py for the mechanism this depends on."""
    seed = default_for("gestures")
    unknown = set(seed["bindings"]) - KNOWN_POSES
    assert not unknown, f"defaults.py seeds unknown poses: {unknown}"


def test_the_seed_never_binds_the_structural_poses():
    """'one' (hold-to-mute) and 'two' (swipe carrier) are never dispatched
    through the bindings dict at all - see GestureController._process."""
    seed = default_for("gestures")
    assert HOLD_POSE not in seed["bindings"]
    assert SWIPE_POSE not in seed["bindings"]


def test_five_is_the_only_neutral_binding_in_the_seed():
    seed = default_for("gestures")
    for name, binding in seed["bindings"].items():
        if name == "five":
            assert binding["action"] == "none"
        else:
            assert binding["action"] != "none", f"{name} should not be neutral"


def test_a_deleted_binding_does_not_come_back_from_defaults(tmp_path):
    """The exact failure mode: remove a binding, reload, expect it gone."""
    store = ConfigStore(tmp_path)
    assert "three" in store.get("gestures")["bindings"]

    def _remove_three(doc: dict) -> None:
        del doc["bindings"]["three"]

    store.update("gestures", _remove_three)
    store.refresh()

    assert "three" not in store.get("gestures")["bindings"]


def test_no_stale_pose_bindings_leak_into_a_fresh_store(tmp_path):
    """Every binding a brand-new install ships with must be a real,
    currently-recognised pose - not a leftover from an earlier design."""
    store = ConfigStore(tmp_path)
    bindings = store.get("gestures")["bindings"]
    stale = {
        "swipe_left_old", "one", "two", "stop", "open_palm", "peace", "point",
        "thumbs_up", "thumbs_down", "ok", "call",
    }
    assert not (set(bindings) & stale), set(bindings) & stale
    assert set(bindings) <= KNOWN_POSES


def test_fist_is_bound_to_close_desktop_in_the_real_seed():
    seed = default_for("gestures")
    assert seed["bindings"]["fist"]["label"] == "close this desktop"


def test_swipes_are_bound_to_desktop_navigation_in_the_real_seed():
    seed = default_for("gestures")
    labels = {name: b["label"] for name, b in seed["bindings"].items() if name in SWIPES}
    assert labels == {
        "swipe_left": "previous desktop",
        "swipe_right": "next desktop",
        "swipe_up": "new desktop",
        "swipe_down": "minimise everything",
    }


def test_rock_sign_is_bound_to_screenshot_in_the_real_seed():
    seed = default_for("gestures")
    assert "screenshot" in seed["bindings"]["rock"]["keys"].lower() or \
        seed["bindings"]["rock"]["label"] == "screenshot"


def test_gesture_controller_separates_fired_from_lifecycle_status():
    """on_fired (chat+speech, per-gesture) is distinct from on_status
    (lifecycle bookkeeping like 'camera ready') - collapsing them back into
    one callback would make every 'camera ready' get spoken too."""
    params = inspect.signature(GestureController.__init__).parameters
    assert "on_fired" in params
    assert "on_status" in params


def test_gesture_controller_exposes_a_mute_callback():
    params = inspect.signature(GestureController.__init__).parameters
    assert "on_mute" in params


def test_the_neutral_set_only_contains_five_and_none():
    assert NEUTRAL == {"none", "five"}
