"""Registry-like config keys must let deletions actually stick.

``bindings`` (gestures) and ``modes`` (personas) are maps the user directly
owns - adds and removes entries by name. Recursively merging them with the
seed defaults means a delete never survives a reload; the seed's copy of that
entry silently comes back. This is what let the swipe-era gesture bindings
keep reappearing even after they were deleted from config/gestures.json.
"""
from assistant.store.store import ConfigStore, deep_merge


def test_deep_merge_replaces_a_wholesale_key_entirely():
    base = {"bindings": {"a": 1, "b": 2}}
    override = {"bindings": {"a": 9}}  # "b" deliberately dropped
    merged = deep_merge(base, override, wholesale=frozenset({"bindings"}))
    assert merged["bindings"] == {"a": 9}


def test_deep_merge_still_recurses_normally_for_keys_outside_the_set():
    base = {"settings": {"x": 1, "y": 2}}
    override = {"settings": {"y": 5}}
    merged = deep_merge(base, override, wholesale=frozenset({"bindings"}))
    assert merged["settings"] == {"x": 1, "y": 5}  # x survives - not wholesale


def test_a_key_absent_from_override_still_falls_back_to_the_default():
    base = {"bindings": {"a": 1}}
    merged = deep_merge(base, {}, wholesale=frozenset({"bindings"}))
    assert merged["bindings"] == {"a": 1}


def test_deleting_a_gesture_binding_through_the_store_stays_deleted(tmp_path):
    store = ConfigStore(tmp_path)
    assert "rock" in store.get("gestures")["bindings"]

    store.update("gestures", lambda doc: doc["bindings"].pop("rock"))
    store.refresh()

    assert "rock" not in store.get("gestures")["bindings"]
    # Everything else the user didn't touch is still there.
    assert "fist" in store.get("gestures")["bindings"]


def test_deleting_a_chat_persona_stays_deleted(tmp_path):
    store = ConfigStore(tmp_path)
    modes = store.get("personas")["modes"]
    assert "fun" in modes

    store.update("personas", lambda doc: doc["modes"].pop("fun"))
    store.refresh()

    assert "fun" not in store.get("personas")["modes"]


def test_ordinary_domains_are_unaffected_by_the_wholesale_exemption(tmp_path):
    """core.json etc. have no wholesale keys - normal recursive merge holds."""
    store = ConfigStore(tmp_path)
    store.set_value("core", "behaviour.speak_replies", False)
    store.refresh()
    # A sibling default under the same recursively-merged dict must survive.
    assert store.value("core", "behaviour.speak_replies") is False
    assert store.value("core", "behaviour.confirm_destructive") is not None
