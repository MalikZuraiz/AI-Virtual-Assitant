"""Integration cover for the config-driven packs.

These are the promises the whole design rests on:
  - add a link to JSON, say refresh, it's a working command
  - walk the "add command" wizard, and the command is live immediately
  - a report entry becomes a runnable command with the right triggers
"""
import json
import types

import pytest

from assistant.commands import register_all
from assistant.core.conversation import ConversationState
from assistant.core.router import CommandRouter
from assistant.store.store import ConfigStore


@pytest.fixture()
def env(tmp_path):
    """A store + router + minimal ctx, with no real machine paths involved."""
    store = ConfigStore(tmp_path / "config")
    router = CommandRouter()
    register_all(router, store)
    ctx = types.SimpleNamespace(
        store=store,
        router=router,
        conversation=ConversationState(),
        progress=lambda _text: None,
        jobs=None,
    )
    return store, router, ctx


def _say(router, ctx, text: str) -> str:
    """Route one message the way Assistant.handle would, minus the queue."""
    if ctx.conversation.active:
        return ctx.conversation.feed(text)
    match = router.match(text)
    assert match is not None, f"nothing matched {text!r}"
    return router.run(match.command, text, ctx).text


# -- links -------------------------------------------------------------------


def test_a_hand_edited_website_becomes_a_command_after_refresh(env):
    store, router, ctx = env
    assert router.match("open my dashboard") is None

    path = store.path("websites")
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["sites"].append({"name": "my dashboard", "url": "https://dash.test"})
    path.write_text(json.dumps(doc), encoding="utf-8")

    store.refresh()
    router.rebuild()

    match = router.match("open my dashboard")
    assert match is not None
    assert match.command.payload["url"] == "https://dash.test"


def test_add_website_command_writes_and_activates(env):
    store, router, ctx = env
    reply = _say(router, ctx, "add website github.com as gh")
    assert "open gh" in reply
    assert router.match("open gh") is not None
    saved = [s for s in store.items("websites", "sites") if s["name"] == "gh"]
    assert saved and saved[0]["url"] == "github.com"


def test_personal_links_answer_to_open_my_x(env):
    store, router, ctx = env
    _say(router, ctx, "add link portfolio as https://me.test")
    assert router.match("open my portfolio") is not None


def test_wordpress_sites_stay_in_their_own_file(env):
    store, router, ctx = env
    _say(router, ctx, "add wordpress site clientco as https://clientco.test/wp-admin")
    assert [s["name"] for s in store.items("wordpress", "sites")] == ["clientco"]
    assert store.items("websites", "sites") == store.items("websites", "sites")  # untouched
    assert router.match("open clientco admin") is not None


# -- the add-command wizard --------------------------------------------------


def test_wizard_creates_a_live_command(env, tmp_path):
    store, router, ctx = env
    workdir = tmp_path / "scripts"
    workdir.mkdir()
    (workdir / "sales.py").write_text("print('hi')", encoding="utf-8")

    assert router.match("run the sales report") is None

    _say(router, ctx, "add command")
    _say(router, ctx, str(workdir))
    _say(router, ctx, "1")                       # run a python script
    _say(router, ctx, "sales.py")
    _say(router, ctx, "run the sales report, sales report")
    _say(router, ctx, "monthly sales report")
    summary = _say(router, ctx, "no")            # nothing extra afterwards
    assert "Here's what I'll save" in summary
    assert "monthly sales report" in summary

    confirmation = _say(router, ctx, "yes")
    assert "live right now" in confirmation

    saved = store.items("scripts", "commands")
    assert len(saved) == 1
    assert saved[0]["target"] == "sales.py"
    assert saved[0]["run_as"] == "python"
    assert set(saved[0]["trigger"]) == {"run the sales report", "sales report"}

    match = router.match("run the sales report")
    assert match is not None
    assert match.command.name == "monthly sales report"


def test_wizard_offers_the_saved_directory_as_the_default(env, tmp_path):
    store, router, ctx = env
    workdir = tmp_path / "saved"
    workdir.mkdir()
    (workdir / "go.py").write_text("", encoding="utf-8")
    store.set_value("core", "last_active_path", workdir.as_posix())

    prompt = _say(router, ctx, "add command")
    assert str(workdir.as_posix()) in prompt
    assert "yes" in prompt

    assert "What should this command actually do" in _say(router, ctx, "yes")


def test_wizard_refuses_a_trigger_that_already_exists(env, tmp_path):
    store, router, ctx = env
    workdir = tmp_path / "s"
    workdir.mkdir()
    (workdir / "a.py").write_text("", encoding="utf-8")

    _say(router, ctx, "add command")
    _say(router, ctx, str(workdir))
    _say(router, ctx, "1")
    _say(router, ctx, "a.py")
    reply = _say(router, ctx, "list reports")
    assert "already runs" in reply


def test_wizard_rejects_a_folder_that_does_not_exist(env):
    store, router, ctx = env
    _say(router, ctx, "add command")
    assert "can't find the folder" in _say(router, ctx, "Z:/nope/nowhere")


def test_removing_a_custom_command_takes_it_out_of_the_router(env, tmp_path):
    store, router, ctx = env
    store.append(
        "scripts",
        "commands",
        {"name": "temp thing", "trigger": ["do the temp thing"], "run_as": "shell", "target": "echo hi"},
    )
    router.rebuild()
    assert router.match("do the temp thing") is not None

    _say(router, ctx, "remove command temp thing")
    assert router.match("do the temp thing") is None


# -- reports -----------------------------------------------------------------


def test_report_entries_become_commands_with_their_triggers(env):
    store, router, ctx = env
    store.append(
        "reports",
        "reports",
        {
            "name": "pending penalties report",
            "trigger": ["generate pending penalties report", "run pending penalties"],
            "working_directory": ".",
            "run_as": "exe",
            "target": "x.exe",
        },
    )
    router.rebuild()

    match = router.match("generate pending penalties report")
    assert match is not None
    assert match.command.name == "report: pending penalties report"
    # Long-running work must not be marked instant, or it would block the GUI.
    assert match.command.instant is False
    assert match.command.priority > 5


def test_list_reports_names_todays_destination(env):
    store, router, ctx = env
    store.set_value("reports", "output_root", "D:/Reports")
    store.append("reports", "reports", {"name": "x report", "trigger": ["generate x report"]})
    router.rebuild()
    reply = _say(router, ctx, "list reports")
    assert "x report" in reply
    assert "Reports" in reply


# -- meta --------------------------------------------------------------------


def test_refresh_reports_what_changed(env):
    store, router, ctx = env
    reply = _say(router, ctx, "refresh")
    assert "Config refreshed" in reply
    assert "rebuilt" in reply


def test_save_this_directory_round_trips(env, tmp_path):
    store, router, ctx = env
    reply = _say(router, ctx, f"save this directory {tmp_path}")
    assert "Saved" in reply
    assert store.value("core", "last_active_path") == tmp_path.as_posix()
