from datetime import date

from assistant.workspace.daybook import day_folder, find_day_folder, format_day, recent_day_folders


def test_default_format_matches_the_existing_archive_naming():
    # The user's real archive uses "5 August" / "7 August" - no leading zero.
    assert format_day(date(2026, 8, 7)) == "7 August"
    assert format_day(date(2026, 8, 15)) == "15 August"


def test_custom_formats():
    when = date(2026, 8, 7)
    assert format_day(when, "{yyyy}-{mm}-{dd}") == "2026-08-07"
    assert format_day(when, "{weekday} {d} {mon}") == "Friday 7 Aug"


def test_a_broken_format_falls_back_instead_of_crashing():
    assert format_day(date(2026, 8, 7), "{nonsense}") == "7 August"


def test_existing_folder_is_reused_regardless_of_case_or_spacing(tmp_path):
    (tmp_path / "7  august").mkdir()
    found = find_day_folder(tmp_path, "7 August")
    assert found is not None
    assert found.name == "7  august"


def test_day_folder_creates_when_missing(tmp_path):
    folder = day_folder(tmp_path, when=date(2026, 8, 7))
    assert folder.is_dir()
    assert folder.name == "7 August"


def test_day_folder_does_not_create_a_duplicate_for_the_same_day(tmp_path):
    (tmp_path / "7 AUGUST").mkdir()
    folder = day_folder(tmp_path, when=date(2026, 8, 7))
    assert folder.name == "7 AUGUST"
    assert len(list(tmp_path.iterdir())) == 1


def test_create_false_returns_the_path_without_making_it(tmp_path):
    folder = day_folder(tmp_path, when=date(2026, 8, 7), create=False)
    assert not folder.exists()


def test_recent_day_folders_are_newest_first(tmp_path):
    import os
    import time

    for i, name in enumerate(["5 August", "6 August", "7 August"]):
        path = tmp_path / name
        path.mkdir()
        os.utime(path, (time.time() + i, time.time() + i))
    names = [p.name for p in recent_day_folders(tmp_path)]
    assert names[0] == "7 August"
