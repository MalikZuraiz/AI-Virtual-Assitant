import pytest

from assistant.core.conversation import ConversationState, Retry, Step, Wizard


def _wizard(saved: list) -> Wizard:
    return Wizard(
        name="add thing",
        steps=[
            Step("colour", "Which colour?"),
            Step(
                "count",
                "How many?",
                lambda answer, data: int(answer) if answer.isdigit() else _raise(),
            ),
            Step("note", "Any note?", optional=True),
        ],
        on_finish=lambda data: saved.append(data) or "saved it",
        summarise=lambda data: f"{data['colour']} x{data['count']}",
    )


def _raise():
    raise Retry("That isn't a number.")


def test_happy_path_asks_then_confirms_then_saves():
    saved = []
    state = ConversationState()

    assert "Which colour?" in state.begin(_wizard(saved))
    assert "How many?" in state.feed("blue")
    assert "Any note?" in state.feed("3")
    summary = state.feed("skip")
    assert "blue x3" in summary
    assert "Save it?" in summary

    assert state.feed("yes") == "saved it"
    assert not state.active
    assert saved[0]["colour"] == "blue"
    assert saved[0]["count"] == 3
    assert saved[0]["note"] is None


def test_a_bad_answer_re_asks_instead_of_giving_up():
    state = ConversationState()
    state.begin(_wizard([]))
    state.feed("blue")
    reply = state.feed("lots")
    assert "isn't a number" in reply
    assert "How many?" in reply
    assert state.active


def test_cancel_works_at_any_point_and_saves_nothing():
    saved = []
    state = ConversationState()
    state.begin(_wizard(saved))
    state.feed("blue")
    assert "Cancelled" in state.feed("cancel")
    assert not state.active
    assert saved == []


def test_declining_the_confirmation_saves_nothing():
    saved = []
    state = ConversationState()
    state.begin(_wizard(saved))
    state.feed("blue")
    state.feed("3")
    state.feed("skip")
    assert "nothing was saved" in state.feed("no")
    assert saved == []


def test_an_unclear_confirmation_asks_again():
    state = ConversationState()
    state.begin(_wizard([]))
    state.feed("blue")
    state.feed("3")
    state.feed("skip")
    assert "yes" in state.feed("hmm maybe").lower()
    assert state.active


def test_back_returns_to_the_previous_question():
    state = ConversationState()
    state.begin(_wizard([]))
    state.feed("blue")
    assert "Which colour?" in state.feed("back")


def test_skip_if_jumps_a_question_it_already_knows():
    asked = []

    wizard = Wizard(
        name="skippy",
        steps=[
            Step("a", lambda d: asked.append("a") or "A?"),
            Step("b", lambda d: asked.append("b") or "B?", skip_if=lambda d: d.get("a") == "known"),
            Step("c", lambda d: asked.append("c") or "C?"),
        ],
        on_finish=lambda data: "done",
        summarise=lambda data: "summary",
    )
    state = ConversationState()
    state.begin(wizard)
    state.feed("known")
    assert asked == ["a", "c"]


def test_a_save_that_throws_is_reported_not_raised():
    def explode(_data):
        raise OSError("disk full")

    wizard = Wizard(
        name="doomed",
        steps=[Step("x", "X?")],
        on_finish=explode,
        summarise=lambda d: "x",
    )
    state = ConversationState()
    state.begin(wizard)
    state.feed("value")
    assert "disk full" in state.feed("yes")
    assert not state.active


def test_conversation_state_reports_no_active_flow():
    assert ConversationState().cancel() == "Nothing in progress."
