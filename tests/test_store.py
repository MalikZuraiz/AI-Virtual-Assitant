import json

import pytest

from assistant.store.store import ConfigStore, deep_merge


@pytest.fixture()
def store(tmp_path):
    return ConfigStore(tmp_path)


def test_missing_files_are_created_with_defaults(tmp_path):
    store = ConfigStore(tmp_path)
    assert (tmp_path / "core.json").is_file()
    assert (tmp_path / "websites.json").is_file()
    assert store.value("core", "defaults.reports_root")


def test_refresh_picks_up_a_hand_edit_without_a_restart(store):
    path = store.path("websites")
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["sites"].append({"name": "hand edited", "url": "https://example.com"})
    path.write_text(json.dumps(doc), encoding="utf-8")

    report = store.refresh()

    assert "websites" in report.changed
    names = [s["name"] for s in store.items("websites", "sites")]
    assert "hand edited" in names


def test_broken_json_keeps_the_last_good_copy_and_reports_it(store):
    store.append("websites", "sites", {"name": "keepme", "url": "https://x.test"})
    store.path("websites").write_text("{ not valid json", encoding="utf-8")

    report = store.refresh()

    assert not report.ok
    assert "websites" in report.errors
    # The good in-memory copy survives, so one bad file can't break routing.
    assert any(s["name"] == "keepme" for s in store.items("websites", "sites"))
    assert "could not be parsed" in report.summary()


def test_writes_are_atomic_and_leave_no_temp_files(store, tmp_path):
    for i in range(5):
        store.append("websites", "sites", {"name": f"s{i}", "url": "https://x.test"})
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
    assert len(store.items("websites", "sites")) >= 5


def test_set_value_creates_nested_keys(store):
    store.set_value("core", "a.b.c", 42)
    assert store.value("core", "a.b.c") == 42
    reread = json.loads(store.path("core").read_text(encoding="utf-8"))
    assert reread["a"]["b"]["c"] == 42


def test_defaults_are_merged_under_user_edits_not_over_them(store):
    store.set_value("core", "assistant_name", "Jarvis")
    store.refresh()
    assert store.value("core", "assistant_name") == "Jarvis"
    # A key the user never touched still comes from defaults.
    assert store.value("core", "browsers.default") == "chrome"


def test_deep_merge_replaces_lists_wholesale():
    merged = deep_merge({"a": [1, 2, 3], "b": {"x": 1, "y": 2}}, {"a": [9], "b": {"y": 5}})
    assert merged["a"] == [9]
    assert merged["b"] == {"x": 1, "y": 5}


def test_refresh_listeners_fire(store):
    seen = []
    store.on_refresh(lambda report: seen.append(report))
    store.refresh()
    assert len(seen) == 1


def test_a_broken_listener_cannot_break_refresh(store):
    store.on_refresh(lambda report: 1 / 0)
    assert store.refresh() is not None
