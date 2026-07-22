from assistant.core.router import CommandRouter


def test_keyword_match_prefers_longer_phrase():
    router = CommandRouter()

    @router.register("open", keywords=("open",), help="generic open")
    def _open(text, ctx):
        return "generic"

    @router.register("open settings", keywords=("open settings",), help="settings")
    def _settings(text, ctx):
        return "settings"

    assert router.dispatch("open settings please", None) == "settings"
    assert router.dispatch("open notepad", None) == "generic"


def test_pattern_match_wins_and_extracts_args():
    router = CommandRouter()

    @router.register("rename", pattern=r"rename (.+?) to (.+)", help="rename")
    def _rename(text, ctx):
        return f"matched:{text}"

    assert router.dispatch("rename a.txt to b.txt", None) == "matched:rename a.txt to b.txt"


def test_unknown_command_returns_fallback():
    router = CommandRouter()
    response = router.dispatch("do a backflip", None)
    assert "don't have a command" in response


def test_help_lists_registered_commands():
    router = CommandRouter()

    @router.register("time", keywords=("time",), help="tells the time")
    def _time(text, ctx):
        return "..."

    help_text = router.dispatch("help", None)
    assert "time" in help_text
    assert "tells the time" in help_text


def test_handler_exception_is_reported_not_raised():
    router = CommandRouter()

    @router.register("boom", keywords=("boom",), help="explodes")
    def _boom(text, ctx):
        raise ValueError("kaboom")

    response = router.dispatch("boom", None)
    assert "error" in response.lower()
