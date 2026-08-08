import types

import pytest

from assistant.core.selection import Choice, Selection, SelectionState, offer, parse_pick


@pytest.fixture()
def ctx():
    return types.SimpleNamespace(selection=SelectionState())


def _selection(actions=None):
    return Selection(
        title="Pick one:",
        choices=[Choice("alpha", "a"), Choice("beta", "b"), Choice("gamma", "c")],
        actions=actions or {"open": lambda c, _cx: f"opened {c.payload}"},
        default_action="open",
    )


# -- parsing -----------------------------------------------------------------


def test_a_bare_number_is_a_pick():
    assert parse_pick("3") == (None, 3)
    assert parse_pick(" 12 ") == (None, 12)


def test_a_verb_plus_number_selects_the_action():
    assert parse_pick("open 2") == ("open", 2)
    assert parse_pick("vs code 3") == ("code", 3)
    assert parse_pick("folder 1") == ("folder", 1)
    assert parse_pick("generate 4") == ("run", 4)
    assert parse_pick("play 2") == ("play", 2)


def test_word_numbers_work_too():
    assert parse_pick("first") == (None, 1)
    assert parse_pick("the second one") == (None, 2)
    assert parse_pick("last") == (None, -1)


def test_a_normal_command_is_not_swallowed():
    """Critical: an ordinary command must still route while a list is parked."""
    for text in ("open chrome", "list reports", "generate pending penalties report", "help", ""):
        assert parse_pick(text) is None, text


def test_an_unknown_verb_is_not_a_pick():
    assert parse_pick("frobnicate 3") is None


# -- resolving ---------------------------------------------------------------


def test_picking_runs_the_default_action(ctx):
    ctx.selection.offer(_selection())
    assert ctx.selection.resolve("2", ctx) == "opened b"


def test_picking_with_a_verb_runs_that_action(ctx):
    ctx.selection.offer(
        _selection({"open": lambda c, _cx: "opened", "code": lambda c, _cx: f"code {c.payload}"})
    )
    assert ctx.selection.resolve("vs code 3", ctx) == "code c"


def test_out_of_range_says_so_without_clearing_the_list(ctx):
    ctx.selection.offer(_selection())
    assert "no option 9" in ctx.selection.resolve("9", ctx)
    assert ctx.selection.active


def test_last_resolves_to_the_final_choice(ctx):
    ctx.selection.offer(_selection())
    assert ctx.selection.resolve("last", ctx) == "opened c"


def test_an_unsupported_verb_lists_what_is_available(ctx):
    ctx.selection.offer(_selection())
    assert "Available: open" in ctx.selection.resolve("play 1", ctx)


def test_the_list_clears_after_a_successful_pick(ctx):
    ctx.selection.offer(_selection())
    ctx.selection.resolve("1", ctx)
    assert not ctx.selection.active
    assert ctx.selection.resolve("1", ctx) is None


def test_an_expired_list_is_ignored(ctx):
    selection = _selection()
    selection.ttl = -1
    ctx.selection.offer(selection)
    assert not ctx.selection.active
    assert ctx.selection.resolve("1", ctx) is None


def test_an_action_that_raises_is_reported_not_propagated(ctx):
    ctx.selection.offer(_selection({"open": lambda c, _cx: 1 / 0}))
    assert "didn't work" in ctx.selection.resolve("1", ctx)


# -- offering ----------------------------------------------------------------


def test_offer_renders_a_numbered_list(ctx):
    text = offer(
        ctx, "Three things:",
        [Choice("alpha", "a"), Choice("beta", "b"), Choice("gamma", "c")],
        actions={"open": lambda c, _cx: "ok"},
    )
    assert "1. alpha" in text
    assert "3. gamma" in text
    assert ctx.selection.active


def test_offer_short_circuits_a_single_choice(ctx):
    text = offer(ctx, "One thing:", [Choice("only", "x")], actions={"open": lambda c, _cx: "ran it"})
    assert text == "ran it"
    assert not ctx.selection.active


def test_offer_with_nothing_says_so(ctx):
    assert "nothing matched" in offer(ctx, "Results:", [], actions={})


def test_render_truncates_a_long_list():
    selection = Selection(
        title="Many:",
        choices=[Choice(f"item {i}", i) for i in range(60)],
        actions={"open": lambda c, _cx: ""},
    )
    text = selection.render(limit=10)
    assert "10. item 9" in text
    assert "and 50 more" in text


def test_details_are_shown_alongside_labels():
    selection = Selection(
        title="Files:",
        choices=[Choice("data.csv", "p", "120 KB")],
        actions={"open": lambda c, _cx: ""},
    )
    assert "120 KB" in selection.render()
