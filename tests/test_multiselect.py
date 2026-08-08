"""Answering a numbered list with several numbers at once."""
import types

import pytest

from assistant.core.selection import Choice, SelectionState, offer, parse_pick, parse_picks


@pytest.fixture()
def ctx():
    return types.SimpleNamespace(selection=SelectionState(), progress=lambda _t: None)


# -- parsing -----------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("1 2 3 4", [1, 2, 3, 4]),
    ("1,2,3", [1, 2, 3]),
    ("1, 2, 3", [1, 2, 3]),
    ("2 and 4", [2, 4]),
    ("1 & 3", [1, 3]),
    ("1-4", [1, 2, 3, 4]),
    ("2 to 5", [2, 3, 4, 5]),
])
def test_multiple_numbers_parse(text, expected):
    action, numbers = parse_picks(text)
    assert numbers == expected
    assert action is None


def test_a_verb_applies_to_the_whole_list():
    assert parse_picks("merge 1 2 3") == ("run", [1, 2, 3])


def test_duplicates_are_collapsed():
    assert parse_picks("1 1 2")[1] == [1, 2]


def test_single_numbers_still_work():
    assert parse_picks("3") == (None, [3])
    assert parse_pick("3") == (None, 3)
    assert parse_pick("vs code 2") == ("code", 2)


def test_ordinary_sentences_are_not_picks():
    for text in ("list reports", "open chrome", "merge downloads", "remind me at 5"):
        assert parse_picks(text) is None, text


def test_an_unknown_verb_is_a_command_not_a_pick():
    assert parse_picks("frobnicate 1 2") is None


# -- resolving ---------------------------------------------------------------


def _choices(n=5):
    return [Choice(f"file{i}.csv", f"/tmp/file{i}.csv") for i in range(1, n + 1)]


def test_several_picks_reach_the_multi_handler(ctx):
    seen = {}

    def _multi(chosen, _ctx):
        seen["labels"] = [c.label for c in chosen]
        return f"merged {len(chosen)}"

    offer(ctx, "pick some", _choices(), actions={"use": lambda c, x: "one"},
          default_action="use", on_multi=_multi)

    assert ctx.selection.resolve("1 3 5", ctx) == "merged 3"
    assert seen["labels"] == ["file1.csv", "file3.csv", "file5.csv"]


def test_order_given_is_order_used(ctx):
    """Merging stacks in the order you list, so 3 1 2 is not 1 2 3."""
    seen = {}
    offer(ctx, "pick", _choices(), actions={"use": lambda c, x: ""},
          default_action="use", on_multi=lambda chosen, _c: seen.setdefault(
              "labels", [c.label for c in chosen]) and "")
    ctx.selection.resolve("3 1 2", ctx)
    assert seen["labels"] == ["file3.csv", "file1.csv", "file2.csv"]


def test_a_single_pick_still_uses_the_normal_action(ctx):
    offer(ctx, "pick", _choices(), actions={"use": lambda c, x: f"used {c.label}"},
          default_action="use", on_multi=lambda chosen, _c: "multi")
    assert ctx.selection.resolve("2", ctx) == "used file2.csv"


def test_multiple_picks_are_refused_when_the_list_is_single_choice(ctx):
    offer(ctx, "pick", _choices(), actions={"use": lambda c, x: "one"}, default_action="use")
    reply = ctx.selection.resolve("1 2", ctx)
    assert "only take one number" in reply


def test_out_of_range_numbers_are_named(ctx):
    offer(ctx, "pick", _choices(3), actions={"use": lambda c, x: ""},
          default_action="use", on_multi=lambda c, x: "")
    reply = ctx.selection.resolve("1 9", ctx)
    assert "no option 9" in reply


def test_a_multi_list_is_offered_even_with_one_candidate(ctx):
    """With on_multi set, one entry must still be shown, not auto-run."""
    reply = offer(ctx, "pick", _choices(1), actions={"use": lambda c, x: "auto-ran"},
                  default_action="use", on_multi=lambda c, x: "")
    assert "auto-ran" not in reply
    assert ctx.selection.active
