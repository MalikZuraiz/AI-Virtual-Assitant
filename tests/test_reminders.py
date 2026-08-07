from datetime import date, datetime, timedelta

from assistant.core.reminders import Reminder, parse_reminder


def test_weekly():
    reminder = parse_reminder("remind me to post on linkedin every monday at 9am")
    assert reminder.schedule == "weekly"
    assert reminder.day == "monday"
    assert reminder.at == "09:00"
    assert reminder.text == "post on linkedin"


def test_daily_with_24h_time():
    reminder = parse_reminder("remind me to stretch every day at 15:30")
    assert reminder.schedule == "daily"
    assert reminder.at == "15:30"
    assert reminder.text == "stretch"


def test_monthly_with_a_day_number():
    reminder = parse_reminder("remind me to update the portfolio monthly on the 1 at 10:00")
    assert reminder.schedule == "monthly"
    assert reminder.day_of_month == 1
    assert reminder.at == "10:00"


def test_relative_minutes():
    reminder = parse_reminder("remind me to call ali in 30 minutes")
    assert reminder.schedule == "once"
    assert reminder.text == "call ali"
    expected = datetime.now() + timedelta(minutes=30)
    assert reminder.date == expected.date().isoformat()


def test_tomorrow():
    reminder = parse_reminder("remind me to send the report tomorrow at 8am")
    assert reminder.schedule == "once"
    assert reminder.date == (date.today() + timedelta(days=1)).isoformat()
    assert reminder.at == "08:00"


def test_explicit_date():
    reminder = parse_reminder("remind me to renew the domain on 12 December at 11:00")
    assert reminder.schedule == "once"
    assert reminder.date.endswith("-12-12")


def test_pm_times_convert_to_24_hour():
    assert parse_reminder("remind me to eat at 7pm").at == "19:00"
    assert parse_reminder("remind me to eat at 12am").at == "00:00"
    assert parse_reminder("remind me to eat at 12pm").at == "12:00"


def test_a_bare_time_rolls_to_tomorrow_if_it_has_passed():
    reminder = parse_reminder("remind me to check the build at 00:01")
    assert reminder.schedule == "once"
    assert reminder.date in (date.today().isoformat(), (date.today() + timedelta(days=1)).isoformat())


def test_unparseable_returns_none():
    assert parse_reminder("remind me") is None


def test_round_trips_through_json():
    original = Reminder(text="water the plants", schedule="weekly", day="friday", at="18:00")
    restored = Reminder.from_json(original.to_json())
    assert restored == original
    assert restored.key == original.key


def test_describe_is_readable():
    reminder = Reminder(text="standup", schedule="weekly", day="monday", at="09:15")
    assert reminder.describe() == "standup - every monday at 09:15"


def test_a_past_one_off_is_flagged():
    past = Reminder(text="x", schedule="once", date="2020-01-01", at="09:00")
    future = Reminder(text="x", schedule="once", date="2999-01-01", at="09:00")
    assert past.is_past is True
    assert future.is_past is False
    assert Reminder(text="x", schedule="daily").is_past is False
