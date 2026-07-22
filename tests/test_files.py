from assistant.automation.files import FileManager


def test_create_and_list_folder(tmp_path):
    fm = FileManager()
    target = tmp_path / "sub"
    fm.create_folder(str(target))
    assert target.is_dir()

    (target / "a.txt").write_text("hi")
    (target / "b.txt").write_text("there")
    listing = fm.list_dir(str(target))
    assert "a.txt" in listing
    assert "b.txt" in listing


def test_create_file(tmp_path):
    fm = FileManager()
    target = tmp_path / "notes.txt"
    fm.create_file(str(target))
    assert target.exists()


def test_rename_and_move(tmp_path):
    fm = FileManager()
    src = tmp_path / "old.txt"
    src.write_text("data")

    fm.rename(str(src), "new.txt")
    renamed = tmp_path / "new.txt"
    assert renamed.exists()
    assert not src.exists()

    dest_dir = tmp_path / "dest"
    fm.move(str(renamed), str(dest_dir / "new.txt"))
    assert (dest_dir / "new.txt").exists()
    assert not renamed.exists()


def test_delete_removes_path(tmp_path):
    fm = FileManager()
    target = tmp_path / "gone.txt"
    target.write_text("bye")
    fm.delete(str(target))
    assert not target.exists()


def test_search_files_matches_pattern(tmp_path):
    fm = FileManager()
    (tmp_path / "report.pdf").write_text("x")
    (tmp_path / "notes.txt").write_text("x")

    result = fm.search_files(str(tmp_path), "*.pdf")
    assert "report.pdf" in result
    assert "notes.txt" not in result


def test_list_dir_missing_path_reports_message(tmp_path):
    fm = FileManager()
    missing = tmp_path / "does_not_exist"
    assert "does not exist" in fm.list_dir(str(missing))
