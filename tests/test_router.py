from assistant.core.router import CommandRouter, Reply, make_command


def test_keyword_match_prefers_longer_phrase():
    router = CommandRouter()

    @router.register("open", keywords=("open",), help="generic open")
    def _open(text, ctx):
        return "generic"

    @router.register("open settings", keywords=("open settings",), help="settings")
    def _settings(text, ctx):
        return "settings"

    assert router.dispatch("open settings please", None).text == "settings"
    assert router.dispatch("open notepad", None).text == "generic"


def test_pattern_match_wins_and_extracts_args():
    router = CommandRouter()

    @router.register("rename", pattern=r"rename (.+?) to (.+)", help="rename")
    def _rename(text, ctx):
        return f"matched:{text}"

    assert router.dispatch("rename a.txt to b.txt", None).text == "matched:rename a.txt to b.txt"


def test_unknown_command_returns_fallback():
    router = CommandRouter()
    assert "don't have a command" in router.dispatch("do a backflip", None).text


def test_help_lists_registered_commands():
    router = CommandRouter()

    @router.register("time", keywords=("time",), help="tells the time")
    def _time(text, ctx):
        return "..."

    text = router.help_text()
    assert "time" in text
    assert "tells the time" in text


def test_handler_exception_is_reported_not_raised():
    router = CommandRouter()

    @router.register("boom", keywords=("boom",), help="explodes")
    def _boom(text, ctx):
        raise ValueError("kaboom")

    assert "error" in router.dispatch("boom", None).text.lower()


def test_filler_words_are_ignored():
    router = CommandRouter()

    @router.register("time", keywords=("what is the time",), help="")
    def _time(text, ctx):
        return "12:00"

    assert router.dispatch("please could you what is the time", None).text == "12:00"


def test_fuzzy_match_catches_a_typo():
    router = CommandRouter()

    @router.register("penalties", keywords=("generate pending penalties report",), help="")
    def _report(text, ctx):
        return "ran"

    match = router.match("generat pendng penalties reprt")
    assert match is not None
    assert match.command.name == "penalties"


def test_fuzzy_match_respects_the_threshold():
    router = CommandRouter(fuzzy_threshold=99)

    @router.register("penalties", keywords=("generate pending penalties report",), help="")
    def _report(text, ctx):
        return "ran"

    assert router.match("something else entirely") is None


def test_dynamic_provider_commands_rebuild_on_demand():
    router = CommandRouter()
    sites = ["github"]

    def provider():
        return [
            make_command(
                name=f"open {site}",
                triggers=[f"open {site}"],
                handler=lambda text, ctx, s=site: f"opened {s}",
                category="links",
            )
            for site in sites
        ]

    router.add_provider(provider)
    assert router.dispatch("open github", None).text == "opened github"
    assert router.match("open gitlab") is None or router.match("open gitlab").how == "fuzzy"

    sites.append("gitlab")
    router.rebuild()
    assert router.dispatch("open gitlab", None).text == "opened gitlab"


def test_reply_object_passes_through_untouched():
    router = CommandRouter()

    @router.register("quiet", keywords=("quiet",), help="")
    def _quiet(text, ctx):
        return Reply("a long listing", speak=False)

    reply = router.dispatch("quiet", None)
    assert reply.speak is False
    assert reply.text == "a long listing"


def test_suggestions_offered_for_a_near_miss():
    router = CommandRouter()

    @router.register("reports", keywords=("list reports",), help="")
    def _reports(text, ctx):
        return ""

    assert "list reports" in router.unknown_text("list report")
