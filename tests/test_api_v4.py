"""4.0 local API additions: queues, batch, capture gating, new download options."""

import pytest
from fastapi.testclient import TestClient

from ytd.api import server as server_mod
from ytd.engine import DownloadManager


@pytest.fixture
def client(settings_store, history_store):
    """Real engines (no fake provider): only paused or unreachable 127.0.0.1:9
    links are queued, so nothing touches the network."""
    manager = DownloadManager(settings_store, history_store)
    manager.start()
    c = TestClient(server_mod.create_app(manager, settings_store), client=("127.0.0.1", 51234))
    c.manager, c.token = manager, settings_store.get("api_token")
    yield c
    manager.stop()


def _auth(c):
    return {"X-MDM-Token": c.token}


@pytest.mark.parametrize("method,path", [
    ("get", "/queues"), ("post", "/queues/main/start"), ("post", "/queues/main/stop"), ("get", "/browser/config"),
    ("post", "/downloads/batch"),
])
def test_new_endpoints_require_token(client, method, path):
    resp = client.post(path, json={"urls": ["https://a/b.zip"]}) if method == "post" else client.get(path)
    assert resp.status_code == 401


def test_capture_is_refused_until_enabled(client, settings_store):
    body = {"url": "http://127.0.0.1:9/file.zip", "origin": "capture", "start_paused": True}
    assert client.post("/downloads/auto", json=body, headers=_auth(client)).status_code == 409
    settings_store.set("browser_capture_enabled", True)
    resp = client.post("/downloads/auto", json=body, headers=_auth(client))
    assert resp.status_code == 200, resp.text
    assert resp.json()["engine"] == "http" and resp.json()["status"] == "Paused"


def test_browser_config_reflects_settings(client, settings_store):
    settings_store.update({"browser_capture_enabled": True, "browser_capture_types": "zip .ISO bad/type exe",
                           "browser_capture_min_kb": 50, "browser_capture_blacklist": "example.com  *.bank.com"})
    cfg = client.get("/browser/config", headers=_auth(client)).json()
    assert cfg == {"capture_enabled": True, "file_types": ["zip", "iso", "exe"], "min_size_kb": 50,
                   "blacklist": ["example.com", "*.bank.com"]}


def test_batch_queues_valid_links_and_reports_bad_ones(client):
    q = client.manager.queues.create("From browser")
    body = {"urls": ["http://127.0.0.1:9/a.zip", "http://127.0.0.1:9/a.zip", "magnet:?xt=urn:btih:" + "a" * 40,
                     "javascript:alert(1)", "http://127.0.0.1:9/b.iso"],
            "queue_id": q.id, "start_paused": True}
    resp = client.post("/downloads/batch", json=body, headers=_auth(client))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["added"]) == 3  # duplicate collapsed, javascript: rejected
    assert [f["url"] for f in data["failed"]] == ["javascript:alert(1)"]
    jobs = [client.manager.get_job(i) for i in data["added"]]
    assert all(j.queue_id == q.id and j.source == "extension" for j in jobs)


def test_batch_validation(client):
    assert client.post("/downloads/batch", json={"urls": []}, headers=_auth(client)).status_code == 422
    assert client.post("/downloads/batch", json={"urls": ["  ", "\n"]}, headers=_auth(client)).status_code == 422
    too_many = {"urls": [f"https://h/{i}.zip" for i in range(501)]}
    assert client.post("/downloads/batch", json=too_many, headers=_auth(client)).status_code == 422
    assert client.post("/downloads/batch", json={"urls": ["https://h/a.zip"], "queue_id": "../x"},
                       headers=_auth(client)).status_code == 422


def test_queue_endpoints(client):
    q = client.manager.queues.create("Night")
    listed = client.get("/queues", headers=_auth(client)).json()
    assert [x["name"] for x in listed] == ["Main", "Night"] and listed[1]["running"] is False
    assert client.post(f"/queues/{q.id}/start", headers=_auth(client)).json()["running"] is True
    assert client.post(f"/queues/{q.id}/stop", headers=_auth(client)).json()["running"] is False
    assert client.post("/queues/nope/start", headers=_auth(client)).status_code == 404


def test_create_download_with_queue_priority_and_checksum(client):
    body = {"url": "http://127.0.0.1:9/x.bin", "format_id": "file", "engine": "http", "queue_id": "main", "priority": 1,
            "start_paused": True, "options": {"connections": 4, "checksum": "SHA256:" + "A" * 64}}
    resp = client.post("/downloads", json=body, headers=_auth(client))
    assert resp.status_code == 200, resp.text
    job = client.manager.get_job(resp.json()["id"])
    assert job.priority == 1 and job.status == "Paused"
    assert job.options["connections"] == 4 and job.options["checksum"] == "sha256:" + "a" * 64
    bad = dict(body, options={"checksum": "sha256:abc"})
    assert client.post("/downloads", json=bad, headers=_auth(client)).status_code == 422
    assert client.post("/downloads", json=dict(body, priority=5), headers=_auth(client)).status_code == 422
    assert client.post("/downloads", json=dict(body, options={"connections": 64}),
                       headers=_auth(client)).status_code == 422


def test_save_dir_traversal_rejected_for_auto(client, settings_store):
    body = {"url": "http://127.0.0.1:9/f.zip", "save_dir": "..\\..\\Windows", "start_paused": True}
    assert client.post("/downloads/auto", json=body, headers=_auth(client)).status_code == 422
