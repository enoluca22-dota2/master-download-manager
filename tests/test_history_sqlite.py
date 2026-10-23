"""SQLite history: migration from 3.x JSON, search/filters, export/import,
scale, and file categories."""

import json
import os
import time

import pytest

from ytd.categories import FILE_CATEGORIES, MEDIA_SITES, TORRENTS, file_category
from ytd.history import HistoryStore

# Shape of real entries written by MDM 3.0 (newest first), plus a YTD-era entry
# without id/engine/category fields.
V3_ENTRIES = [
    {"id": "aa11", "title": "Sample Series [Season 1]", "format": "All files", "path": "C:\\Downloads\\MDM",
     "file": "C:\\Downloads\\MDM\\Sample Series [Season 1]", "url": "magnet:?xt=urn:btih:0123abcd",
     "status": "Completed", "source": "gui", "engine": "torrent", "kind": "torrent", "category": "Torrents",
     "file_size": 947_000_000, "date": "2026-10-03 23:09"},
    {"id": "bb22", "title": "Song (Official HD Video)", "format": "MP3 — 320 kbps", "path": "C:\\Downloads\\MDM",
     "file": "C:\\Downloads\\MDM\\Song.mp3", "url": "https://www.youtube.com/watch?v=abc", "status": "Completed",
     "source": "extension", "engine": "media", "kind": "audio", "category": "Audio", "file_size": 9_749_679,
     "date": "2026-10-03 23:00"},
    {"title": "Old YTD video", "format": "720p", "path": "C:\\YT", "url": "https://youtu.be/x",
     "status": "Completed", "date": "2026-09-18 21:16"},
]


def _write_v3(tmp_path):
    path = tmp_path / "download_history.json"
    path.write_text(json.dumps(V3_ENTRIES), encoding="utf-8")
    return path


def test_migrates_v3_json_once_and_keeps_original(tmp_path):
    path = _write_v3(tmp_path)
    original = path.read_bytes()
    store = HistoryStore(path=str(path))
    entries = store.all()
    assert [e["title"] for e in entries] == [e["title"] for e in V3_ENTRIES]  # order kept, newest first
    assert entries[0]["id"] == "aa11" and entries[0]["file_size"] == 947_000_000
    assert entries[2]["id"] and entries[2]["category"] == "Other"  # old entry got an id + category
    assert path.read_bytes() == original  # JSON untouched (backup / downgrade)
    store.add("New one", "x", "C:\\")
    store.close()
    # reopening must not import the JSON a second time
    again = HistoryStore(path=str(path))
    assert again.count() == 4
    assert (tmp_path / "download_history.sqlite3").exists()


def test_corrupt_json_does_not_block_startup(tmp_path):
    path = tmp_path / "download_history.json"
    path.write_text("[{broken", encoding="utf-8")
    store = HistoryStore(path=str(path))
    assert store.all() == []
    store.add("x", "y", "z")
    assert store.count() == 1


def test_search_filters(tmp_path):
    store = HistoryStore(path=str(_write_v3(tmp_path)))
    store.add("100% legit_file", "f", "C:\\", url="https://a/x.zip", status="Failed", category="Archives")
    assert [e["title"] for e in store.search("song")] == ["Song (Official HD Video)"]
    assert [e["title"] for e in store.search("100%")] == ["100% legit_file"]  # LIKE wildcards are escaped
    assert store.search("_") and len(store.search("_")) == 1
    assert [e["title"] for e in store.search(status="Failed")] == ["100% legit_file"]
    assert {e["title"] for e in store.search(category="Torrents")} == {"Sample Series [Season 1]"}
    assert {e["title"] for e in store.search(since="2026-10-01")} >= {"Song (Official HD Video)"}
    assert all(e["date"] < "2026-10-01" for e in store.search(until="2026-10-01"))
    assert len(store.search(limit=2)) == 2 and store.search(limit=2, offset=3)
    assert store.find_by_url("https://www.youtube.com/watch?v=abc")[0]["id"] == "bb22"
    assert store.find_by_url("https://nope") == []


@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_export_import_roundtrip(tmp_path, fmt):
    store = HistoryStore(path=str(_write_v3(tmp_path)))
    store.add("=cmd|' /C calc'!A0", "f", "C:\\", url="https://evil")  # formula-like title
    out = tmp_path / f"export.{fmt}"
    assert store.export(str(out)) == 4
    if fmt == "csv":
        assert "'=cmd" in out.read_text(encoding="utf-8-sig")  # neutralised for spreadsheets
    other = HistoryStore(path=str(tmp_path / "other.json"))
    assert other.import_file(str(out)) == 4
    assert [e["title"] for e in other.all()] == [e["title"] for e in store.all()]
    assert other.import_file(str(out)) == 0  # same ids: no duplicates
    with pytest.raises(ValueError):
        store.export(str(tmp_path / "x.xml"))


def test_import_plain_v3_file(tmp_path):
    src = _write_v3(tmp_path)
    store = HistoryStore(path=str(tmp_path / "fresh.json"))
    assert store.import_file(str(src)) == 3


def test_thousands_of_entries_stay_fast(tmp_path):
    store = HistoryStore(path=str(tmp_path / "big.json"))
    store._conn()
    from ytd import history as h
    rows = [h._clean({"title": f"File {i}", "url": f"https://host/{i}.zip", "status": "Completed" if i % 7 else "Failed",
                      "date": f"2026-{1 + i % 12:02d}-{1 + i % 28:02d} 10:00", "file": f"C:\\d\\{i}.zip"})
            for i in range(20_000)]
    start = time.perf_counter()
    store._insert_many(rows)
    insert_s = time.perf_counter() - start
    start = time.perf_counter()
    found = store.search("File 1999")
    store.search(status="Failed", limit=200)
    everything = store.all()
    query_s = time.perf_counter() - start
    assert len(everything) == 20_000
    assert {e["title"] for e in found} == {"File 1999", *{f"File 1999{d}" for d in range(10)}}
    print(f"\n[perf] 20k history rows: insert {insert_s * 1000:.0f} ms, search+filter+all {query_s * 1000:.0f} ms")
    assert insert_s < 5 and query_s < 2, (insert_s, query_s)


def test_file_categories():
    assert file_category("setup.exe", "http") == "Programs"
    assert file_category("a.7z") == "Compressed"
    assert file_category("x.flac") == "Music"
    assert file_category("doc.PDF") == "Documents"
    assert file_category("photo.heic") == "Pictures"
    assert file_category("", "torrent") == TORRENTS
    assert file_category("Video title", "media", "video") == "Videos"
    assert file_category("", "http", url="https://h/dl/file.iso?token=1") == "Compressed"
    assert file_category("noext") == "Other"
    assert MEDIA_SITES not in FILE_CATEGORIES
