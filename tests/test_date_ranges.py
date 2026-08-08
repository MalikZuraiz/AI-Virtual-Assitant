"""Reporting periods: parsing them, and asking when they matter."""
from datetime import date

import pytest

from assistant.commands.reportpack import _stated_range
from assistant.workspace.inputs import InputSpec, build_args, parse_date_range

TODAY = date(2026, 8, 8)   # a Saturday


@pytest.mark.parametrize("text,start,end", [
    ("1 august to 7 august", "2026-08-01", "2026-08-07"),
    ("2026-08-01 to 2026-08-07", "2026-08-01", "2026-08-07"),
    ("01/08/2026 - 07/08/2026", "2026-08-01", "2026-08-07"),
    ("aug 1 to aug 7", "2026-08-01", "2026-08-07"),
    ("1 aug to 7 aug 2026", "2026-08-01", "2026-08-07"),
    ("between 1 aug and 7 aug", "2026-08-01", "2026-08-07"),
    ("this month", "2026-08-01", "2026-08-31"),
    ("last month", "2026-07-01", "2026-07-31"),
    ("july", "2026-07-01", "2026-07-31"),
    ("today", "2026-08-08", "2026-08-08"),
    ("yesterday", "2026-08-07", "2026-08-07"),
    ("1 august", "2026-08-01", "2026-08-01"),
])
def test_ranges_people_actually_type(text, start, end):
    parsed = parse_date_range(text, TODAY)
    assert parsed is not None, text
    assert parsed[0].isoformat() == start
    assert parsed[1].isoformat() == end


def test_last_n_days_includes_today():
    start, end = parse_date_range("last 7 days", TODAY)
    assert end == TODAY
    assert (end - start).days == 6


def test_a_backwards_range_is_put_the_right_way_round():
    assert parse_date_range("7 august to 1 august", TODAY) == (
        date(2026, 8, 1), date(2026, 8, 7)
    )


def test_nonsense_returns_none_rather_than_guessing():
    for text in ("", "   ", "nonsense here", "the usual"):
        assert parse_date_range(text, TODAY) is None


def test_an_impossible_date_is_rejected():
    assert parse_date_range("31 february", TODAY) is None


# -- turning a range into arguments -----------------------------------------


def _tmo_spec():
    return InputSpec.from_entry({
        "input": {
            "mode": "flag", "flag": "--csv",
            "date_flags": {"from": "--from-date", "to": "--to-date"},
            "date_format": "%Y-%m-%d", "ask_dates": True,
        }
    })


def test_args_match_the_documented_tmo_command_line():
    from pathlib import Path

    args = build_args(
        [Path("data.csv")], _tmo_spec(),
        date_range=(date(2026, 8, 1), date(2026, 8, 7)),
    )
    assert args == [
        "--csv", "data.csv", "--from-date", "2026-08-01", "--to-date", "2026-08-07"
    ]


def test_without_a_range_it_falls_back_to_this_month():
    from pathlib import Path

    args = build_args([Path("d.csv")], _tmo_spec(), when=date(2026, 8, 8))
    assert "2026-08-01" in args and "2026-08-08" in args


def test_ask_dates_is_read_from_config():
    assert _tmo_spec().ask_dates is True
    assert InputSpec.from_entry({"input": {"mode": "arg"}}).ask_dates is False


# -- a range stated in the command itself ------------------------------------


def test_a_period_in_the_command_skips_the_question():
    parsed = _stated_range("generate tmo scoring report for 1 august to 7 august")
    assert parsed == (date(2026, 8, 1), date(2026, 8, 7))


def test_a_file_path_is_not_mistaken_for_a_period():
    assert _stated_range("generate tmo scoring report from D:/data.csv") is None


def test_a_path_and_a_period_together():
    parsed = _stated_range("generate tmo scoring report from D:/data.csv for this month")
    assert parsed is not None
    assert parsed[0].day == 1


def test_no_period_mentioned():
    assert _stated_range("generate pending penalties report") is None
