from assistant.automation.browser import parse_open_modifiers

BROWSERS = ["chrome", "edge", "firefox", "brave", "opera"]


def test_plain_open_has_no_modifiers():
    remaining, request = parse_open_modifiers("open youtube", BROWSERS)
    assert remaining == "open youtube"
    assert request.browser is None
    assert request.new_window is False
    assert request.incognito is False


def test_in_a_new_browser_means_a_new_window():
    _remaining, request = parse_open_modifiers("open youtube in a new browser", BROWSERS)
    assert request.new_window is True


def test_in_the_current_browser_means_reuse_the_window():
    _remaining, request = parse_open_modifiers("open youtube in the current browser", BROWSERS)
    assert request.new_window is False


def test_current_wins_when_both_are_said():
    # "open it in a new tab in the current browser" - the last intent is the
    # one that matters: stay in the window that's already open.
    _remaining, request = parse_open_modifiers("open youtube in a new window in this browser", BROWSERS)
    assert request.new_window is False


def test_a_named_browser_is_picked_up_and_stripped():
    remaining, request = parse_open_modifiers("open my portfolio in edge", BROWSERS)
    assert request.browser == "edge"
    assert "edge" not in remaining
    assert "portfolio" in remaining


def test_named_browser_and_new_window_combine():
    _remaining, request = parse_open_modifiers("open github in a new firefox window", BROWSERS)
    assert request.browser == "firefox"
    assert request.new_window is True


def test_incognito_implies_a_new_window():
    _remaining, request = parse_open_modifiers("open gmail in incognito", BROWSERS)
    assert request.incognito is True
    assert request.new_window is True


def test_using_and_with_are_accepted_as_well_as_in():
    for phrase in ("open github using brave", "open github with brave", "open github on brave"):
        _remaining, request = parse_open_modifiers(phrase, BROWSERS)
        assert request.browser == "brave", phrase


def test_the_site_name_survives_stripping():
    remaining, _request = parse_open_modifiers("open client site admin in a new chrome window", BROWSERS)
    assert "client site admin" in remaining


def test_describe_reads_naturally():
    _remaining, request = parse_open_modifiers("open x in a new edge window", BROWSERS)
    assert request.describe() == "a new window in Edge"
