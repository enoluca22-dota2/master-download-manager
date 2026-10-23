import hashlib
import json
import os

import pytest

from ytd import updater
from ytd.updater import UpdateError, check_for_update, download_update, validate_manifest

from local_http_server import LocalHTTPServer


def _manifest(srv, payload, **overrides):
    m = {"version": "9.9.9", "url": f"{srv.base}/files/MDM-Setup.exe",
         "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload), "notes": "fixes"}
    m.update(overrides)
    srv.files["manifest.json"] = json.dumps(m).encode()
    srv.files["MDM-Setup.exe"] = payload
    return f"{srv.base}/files/manifest.json"


def test_check_and_download_verified_update(tmp_path):
    payload = os.urandom(300_000)
    with LocalHTTPServer() as srv:
        url = _manifest(srv, payload)
        available, manifest = check_for_update(url, current_version="3.0.0")
        assert available and manifest["version"] == "9.9.9"
        path = download_update(manifest, str(tmp_path))
    assert open(path, "rb").read() == payload


def test_no_update_when_current_is_newer():
    with LocalHTTPServer() as srv:
        available, _ = check_for_update(_manifest(srv, b"x", version="1.0.0"), current_version="3.0.0")
    assert available is False


def test_tampered_installer_is_rejected_and_deleted(tmp_path):
    with LocalHTTPServer() as srv:
        url = _manifest(srv, b"genuine installer bytes")
        _, manifest = check_for_update(url, current_version="3.0.0")
        srv.files["MDM-Setup.exe"] = b"malicious replacement!!"  # same length, different bytes
        with pytest.raises(UpdateError, match="SHA-256"):
            download_update(manifest, str(tmp_path))
    assert os.listdir(tmp_path) == []


def test_size_mismatch_is_rejected(tmp_path):
    with LocalHTTPServer() as srv:
        _, manifest = check_for_update(_manifest(srv, b"abc" * 1000, size=10), current_version="3.0.0")
        with pytest.raises(UpdateError, match="larger"):
            download_update(manifest, str(tmp_path))


@pytest.mark.parametrize("bad", [
    {"version": "not-a-version", "url": "https://x/y.exe", "sha256": "a" * 64},
    {"version": "1.0", "url": "http://example.com/y.exe", "sha256": "a" * 64},  # not https
    {"version": "1.0", "url": "https://x/y.exe", "sha256": "short"},
    {"version": "1.0", "url": "https://x/y.exe"},
    {"version": "1.0", "url": "https://x/y.exe", "sha256": "a" * 64, "size": -1},
    ["not", "a", "dict"],
])
def test_invalid_manifests(bad):
    with pytest.raises(UpdateError):
        validate_manifest(bad)


def test_insecure_or_missing_manifest_url():
    with pytest.raises(UpdateError, match="https"):
        check_for_update("http://updates.example.com/manifest.json")
    with pytest.raises(UpdateError, match="configured"):
        check_for_update("")
    assert updater.__version__  # the app's own version, not yt-dlp's
