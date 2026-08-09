"""Regression cover for the two bugs this round exposed:

1. ``assistant/commands/vision.py`` called ``GestureController(min_travel=...)``
   after the swipe-based recogniser (and its ``min_travel``/``swipes``
   constructor args) had already been rewritten to finger-counts-only. The
   next ``start gestures`` crashed with a ``TypeError`` the moment a real
   camera and config were involved - inspecting the signature statically
   below is what would have caught it before a user did.
2. ``assistant/store/defaults.py``'s seed content for ``gestures.json`` still
   listed the old swipe/thumbs/rock/ok/call bindings. ``ConfigStore`` deep-
   merges defaults *underneath* the real file, so those bindings kept
   reappearing in the merged, in-memory document even after being deleted
   from the JSON on disk - which is why gestures kept "colliding" after the
   rewrite that was supposed to remove them.
"""
import inspect

from assistant.store.defaults import default_for
from assistant.store.store import ConfigStore
from assistant.vision.gestures import NEUTRAL, POSES, GestureController


def test_vision_command_pack_only_passes_arguments_the_controller_accepts():
    """Every keyword `_gesture_controller` could pass must exist on the class.

    A static check, not a call - it doesn't need a webcam and it fails at
    collection time instead of the first time a user says 'start gestures'.
    """
    import re

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


def test_the_default_gestures_seed_matches_the_actual_pose_set():
    """The bug: stale seed bindings resurrecting themselves via deep-merge."""
    seed = default_for("gestures")
    assert set(seed["bindings"]) <= set(POSES), (
        f"defaults.py still seeds unknown poses: {set(seed['bindings']) - set(POSES)}"
    )
    # The two neutral poses must never carry a real action in the seed - a
    # default action on them would fire every time the recogniser re-arms.
    for name in NEUTRAL - {"none"}:
        assert seed["bindings"].get(name, {}).get("action") in (None, "none"), name


def test_a_deleted_binding_does_not_come_back_from_defaults(tmp_path):
    """The exact failure mode: remove a binding, reload, expect it gone."""
    store = ConfigStore(tmp_path)
    assert "two" in store.get("gestures")["bindings"]

    def _remove_two(doc: dict) -> None:
        del doc["bindings"]["two"]

    store.update("gestures", _remove_two)
    store.refresh()

    assert "two" not in store.get("gestures")["bindings"]


def test_no_swipe_or_removed_pose_bindings_leak_into_a_fresh_store(tmp_path):
    """Every binding a brand-new install ships with must be a real pose."""
    store = ConfigStore(tmp_path)
    bindings = store.get("gestures")["bindings"]
    stale = {"swipe_left", "swipe_right", "swipe_up", "swipe_down",
             "thumbs_up", "thumbs_down", "ok", "rock", "call", "peace", "point"}
    assert not (set(bindings) & stale), set(bindings) & stale


# -- this round: previous-desktop, announce, and no more silent LLM detour --


def test_fist_is_bound_to_previous_desktop_in_the_real_seed():
    seed = default_for("gestures")
    assert seed["bindings"]["fist"]["label"] == "previous desktop"


def test_only_open_palm_is_the_neutral_binding_in_the_seed():
    seed = default_for("gestures")
    for name, binding in seed["bindings"].items():
        if name == "open_palm":
            assert binding["action"] == "none"
        else:
            assert binding["action"] != "none", f"{name} should not be neutral"


def test_gesture_controller_separates_fired_from_lifecycle_status():
    """on_fired (chat+speech, per-gesture) is distinct from on_status
    (lifecycle bookkeeping like 'camera ready') - collapsing them back into
    one callback would make every 'camera ready' get spoken too."""
    import inspect

    params = inspect.signature(GestureController.__init__).parameters
    assert "on_fired" in params
    assert "on_status" in params
