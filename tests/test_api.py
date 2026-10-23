import pytest
from fastapi.testclient import TestClient

from ytd import api as api_pkg  # noqa: F401  (ensures package import works)
from ytd.api import server as server_mod
from ytd.engine import DownloadManager


@pytest.fixture
def client(settings_store, history_store, fake_provider, monkeypatch):
    fake_find_provider = lambda url: fake_provider if fake_provider.supports(url) else None  # noqa: E731
    monkeypatch.setattr(server_mod, "find_provider", fake_find_provider)
    import ytd.engine as engine_mod
    monkeypatch.setattr(engine_mod, "find_provider", fake_find_provider)
    # resolve_format_selection() (called by POST /downloads) looks up
    # find_provider via ytd.providers' own module globals, not server_mod's
    # imported reference, so it needs patching separately.
    import ytd.providers as providers_mod
    monkeypatch.setattr(providers_mod, "find_provider", fake_find_provider)

    manager = DownloadManager(settings_store, history_store)
    manager.start()
    app = server_mod.create_app(manager, settings_store)
    # request.client.host must look like a loopback address for our
    # localhost-only auth dependency to accept it, same as a real browser
    # extension talking to 127.0.0.1 would.
    test_client = TestClient(app, client=("127.0.0.1", 51234))
    test_client.manager = manager
    test_client.token = settings_store.get("api_token")
    yield test_client
    manager.stop()


def test_health_requires_no_auth(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_version_requires_no_auth(client):
    resp = client.get("/version")
    assert resp.status_code == 200
    assert "version" in resp.json()


def test_protected_endpoint_without_token_is_rejected(client):
    resp = client.get("/connection")
    assert resp.status_code == 401


def test_protected_endpoint_with_wrong_token_is_rejected(client):
    resp = client.get("/connection", headers={"X-Enagrio-Token": "wrong"})
    assert resp.status_code == 401


def test_protected_endpoint_with_correct_token_succeeds(client):
    resp = client.get("/connection", headers={"X-Enagrio-Token": client.token})
    assert resp.status_code == 200
    assert resp.json()["connected"] is True


def _auth(client):
    return {"X-Enagrio-Token": client.token}


def test_media_detect_returns_real_formats_only(client):
    resp = client.post("/media/detect", json={"url": "http://fake/instant"}, headers=_auth(client))
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Fake Video"
    format_ids = {f["format_id"] for f in body["formats"]}
    assert format_ids == {"video-720", "mp3-192", "thumbnail", "subtitle-en"}


def test_media_detect_unsupported_url_returns_422(client):
    resp = client.post("/media/detect", json={"url": "https://not-real.example"}, headers=_auth(client))
    assert resp.status_code == 422


def test_playlist_detect_returns_entries(client, monkeypatch):
    import ytd.providers.youtube as yt_mod

    def fake_detect_playlist(url):
        return {"playlist_title": "My Playlist", "entry_count": 2, "entries": [
            {"video_id": "a", "url": "https://www.youtube.com/watch?v=a", "title": "A", "duration_seconds": 10, "thumbnail": None},
            {"video_id": "b", "url": "https://www.youtube.com/watch?v=b", "title": "B", "duration_seconds": 20, "thumbnail": None},
        ]}
    monkeypatch.setattr(yt_mod, "detect_playlist", fake_detect_playlist)

    resp = client.post("/playlist/detect", json={"url": "https://www.youtube.com/playlist?list=PLxyz"}, headers=_auth(client))
    assert resp.status_code == 200
    body = resp.json()
    assert body["playlist_title"] == "My Playlist"
    assert body["entry_count"] == 2
    assert len(body["entries"]) == 2


def test_playlist_detect_non_playlist_url_returns_422(client, monkeypatch):
    import ytd.providers.youtube as yt_mod
    from ytd.providers.base import UnsupportedMediaError

    def fake_detect_playlist(url):
        raise UnsupportedMediaError("That URL isn't part of a playlist.")
    monkeypatch.setattr(yt_mod, "detect_playlist", fake_detect_playlist)

    resp = client.post("/playlist/detect", json={"url": "https://www.youtube.com/watch?v=abc"}, headers=_auth(client))
    assert resp.status_code == 422


def test_create_list_get_download(client):
    resp = client.post("/downloads", json={"url": "http://fake/instant", "format_id": "video-720"}, headers=_auth(client))
    assert resp.status_code == 200
    job = resp.json()
    assert job["status"] in ("Pending", "Preparing", "Downloading")

    listed = client.get("/downloads", headers=_auth(client)).json()["downloads"]
    assert any(j["id"] == job["id"] for j in listed)

    fetched = client.get(f"/downloads/{job['id']}", headers=_auth(client))
    assert fetched.status_code == 200


def test_get_missing_download_is_404(client):
    resp = client.get("/downloads/999999", headers=_auth(client))
    assert resp.status_code == 404


def test_pause_resume_cancel_lifecycle(client):
    import time

    resp = client.post("/downloads", json={"url": "http://fake/slow", "format_id": "video-720"}, headers=_auth(client))
    job_id = resp.json()["id"]

    deadline = time.time() + 3
    while time.time() < deadline:
        if client.get(f"/downloads/{job_id}", headers=_auth(client)).json()["status"] == "Downloading":
            break
        time.sleep(0.02)

    pause_resp = client.post(f"/downloads/{job_id}/pause", headers=_auth(client))
    assert pause_resp.status_code == 200

    deadline = time.time() + 3
    while time.time() < deadline:
        if client.get(f"/downloads/{job_id}", headers=_auth(client)).json()["status"] == "Paused":
            break
        time.sleep(0.02)
    assert client.get(f"/downloads/{job_id}", headers=_auth(client)).json()["status"] == "Paused"

    resume_resp = client.post(f"/downloads/{job_id}/resume", headers=_auth(client))
    assert resume_resp.status_code == 200


def test_invalid_transition_returns_409(client):
    resp = client.post("/downloads", json={"url": "http://fake/instant", "format_id": "video-720"}, headers=_auth(client))
    job_id = resp.json()["id"]
    # A brand-new Queued/Downloading job has never been paused, so resuming
    # it immediately is not a valid transition.
    bad = client.post(f"/downloads/{job_id}/resume", headers=_auth(client))
    assert bad.status_code == 409


def test_create_download_with_unknown_format_id_is_422(client):
    resp = client.post("/downloads", json={"url": "http://fake/instant", "format_id": "nonsense"}, headers=_auth(client))
    assert resp.status_code == 422


def test_settings_roundtrip(client):
    resp = client.post("/settings", json={"max_concurrent_downloads": 5}, headers=_auth(client))
    assert resp.status_code == 200
    assert resp.json()["max_concurrent_downloads"] == 5

    fetched = client.get("/settings", headers=_auth(client))
    assert fetched.json()["max_concurrent_downloads"] == 5


def test_create_download_with_filename_and_category(client, tmp_path):
    save_dir = str(tmp_path / "custom")
    resp = client.post(
        "/downloads",
        json={
            "url": "http://fake/instant", "format_id": "video-720",
            "filename": 'My "Custom" Title.mp4', "save_dir": save_dir, "category": "Video",
        },
        headers=_auth(client),
    )
    assert resp.status_code == 200
    job = resp.json()
    assert job["filename_override"] == "My Custom Title"
    assert job["download_dir"] == save_dir
    assert job["category"] == "Video"


def test_create_download_thumbnail_and_subtitle(client):
    thumb = client.post("/downloads", json={"url": "http://fake/instant", "format_id": "thumbnail"}, headers=_auth(client))
    assert thumb.status_code == 200
    assert thumb.json()["kind"] == "image"

    sub = client.post("/downloads", json={"url": "http://fake/instant", "format_id": "subtitle-en"}, headers=_auth(client))
    assert sub.status_code == 200
    assert sub.json()["kind"] == "subtitle"


def test_duplicate_download_requests_create_separate_jobs(client):
    first = client.post("/downloads", json={"url": "http://fake/instant", "format_id": "video-720"}, headers=_auth(client))
    second = client.post("/downloads", json={"url": "http://fake/instant", "format_id": "video-720"}, headers=_auth(client))
    assert first.json()["id"] != second.json()["id"]
    listed = client.get("/downloads", headers=_auth(client)).json()["downloads"]
    assert len([j for j in listed if j["url"] == "http://fake/instant"]) == 2


def test_missing_x_ytd_token_header_is_rejected(client):
    # Simulates an extension message with no token configured yet.
    resp = client.post("/downloads", json={"url": "http://fake/instant", "format_id": "video-720"})
    assert resp.status_code == 401


def test_blank_filename_is_treated_as_not_provided(client):
    resp = client.post(
        "/downloads",
        json={"url": "http://fake/instant", "format_id": "video-720", "filename": "   "},
        headers=_auth(client),
    )
    assert resp.status_code == 200
    assert resp.json()["filename_override"] is None


def test_disk_space_reports_real_values(client, tmp_path):
    resp = client.get(f"/system/disk-space?path={tmp_path}", headers=_auth(client))
    assert resp.status_code == 200
    body = resp.json()
    assert body["free_bytes"] > 0
    assert body["total_bytes"] >= body["free_bytes"]


def test_disk_space_walks_up_to_existing_ancestor(client, tmp_path):
    not_yet_created = tmp_path / "does" / "not" / "exist" / "yet"
    resp = client.get(f"/system/disk-space?path={not_yet_created}", headers=_auth(client))
    assert resp.status_code == 200
    assert resp.json()["free_bytes"] > 0


def test_disk_space_requires_auth(client, tmp_path):
    resp = client.get(f"/system/disk-space?path={tmp_path}")
    assert resp.status_code == 401


def test_delete_download(client):
    resp = client.post("/downloads", json={"url": "http://fake/instant", "format_id": "video-720"}, headers=_auth(client))
    job_id = resp.json()["id"]
    import time
    time.sleep(0.3)  # let the /instant job finish so delete is allowed
    delete_resp = client.delete(f"/downloads/{job_id}", headers=_auth(client))
    assert delete_resp.status_code == 200
    assert client.get(f"/downloads/{job_id}", headers=_auth(client)).status_code == 404
