from ytd.history import HistoryStore, MAX_HISTORY_ENTRIES


def test_add_entry_and_read_back(tmp_path):
    store = HistoryStore(path=str(tmp_path / "history.json"))
    store.add("My Video", "720p (HD)", "/downloads", url="http://x", status="Completed")
    entries = store.all()
    assert len(entries) == 1
    assert entries[0]["title"] == "My Video"
    assert entries[0]["status"] == "Completed"


def test_newest_entry_first(tmp_path):
    store = HistoryStore(path=str(tmp_path / "history.json"))
    store.add("First", "MP3", "/downloads")
    store.add("Second", "MP3", "/downloads")
    entries = store.all()
    assert entries[0]["title"] == "Second"
    assert entries[1]["title"] == "First"


def test_history_capped_at_max_entries(tmp_path, monkeypatch):
    # 4.0: the cap went from 2,000 (JSON) to 100,000 (SQLite); exercise the
    # pruning logic with a small cap instead of inserting 100k rows.
    from ytd import history as history_mod
    monkeypatch.setattr(history_mod, "MAX_HISTORY_ENTRIES", 25)
    assert MAX_HISTORY_ENTRIES >= 100_000
    store = HistoryStore(path=str(tmp_path / "history.json"))
    for i in range(35):
        store.add(f"Video {i}", "MP3", "/downloads")
    entries = store.all()
    assert len(entries) == 25
    assert entries[0]["title"] == "Video 34" and entries[-1]["title"] == "Video 10"


def test_clear_empties_history(tmp_path):
    store = HistoryStore(path=str(tmp_path / "history.json"))
    store.add("Video", "MP3", "/downloads")
    store.clear()
    assert store.all() == []


def test_missing_file_returns_empty_list(tmp_path):
    store = HistoryStore(path=str(tmp_path / "does_not_exist.json"))
    assert store.all() == []


def test_remove_deletes_matching_entry(tmp_path):
    store = HistoryStore(path=str(tmp_path / "history.json"))
    store.add("Video A", "720p", "/downloads", url="http://a")
    store.add("Video B", "720p", "/downloads", url="http://b")
    date_a = store.all()[1]["date"]  # "Video A" is now the older, second entry

    removed = store.remove("http://a", date_a, "Video A")
    assert removed is True
    titles = [h["title"] for h in store.all()]
    assert titles == ["Video B"]


def test_remove_returns_false_when_no_match(tmp_path):
    store = HistoryStore(path=str(tmp_path / "history.json"))
    store.add("Video A", "720p", "/downloads", url="http://a")
    assert store.remove("http://nonexistent", "2020-01-01 00:00", "Nope") is False
    assert len(store.all()) == 1
