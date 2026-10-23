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


def _release(assets, tag="v4.1.0", **kw):
    return dict({"tag_name": tag, "draft": False, "prerelease": False, "body": "notes", "assets": assets}, **kw)


def test_github_release_is_turned_into_a_verified_manifest():
    from ytd.updater import manifest_from_github_release
    sha = "a" * 64
    rel = _release([{"name": "Master.Download.Manager.Setup.4.1.0.exe", "size": 123,
                     "browser_download_url": "https://github.com/o/r/releases/download/v4.1.0/Master.Download.Manager.Setup.4.1.0.exe"},
                    {"name": "SHA256SUMS.txt", "browser_download_url": "https://github.com/o/r/x"}])
    m = manifest_from_github_release(rel, f"{sha}  Master.Download.Manager.Setup.4.1.0.exe\n{'b' * 64}  other.zip\n")
    assert m["version"] == "4.1.0" and m["sha256"] == sha and m["size"] == 123


def test_github_release_without_matching_checksum_or_installer_is_refused():
    import pytest
    from ytd.updater import UpdateError, manifest_from_github_release
    good = {"name": "Master.Download.Manager.Setup.4.1.0.exe", "size": 1,
            "browser_download_url": "https://github.com/o/r/a.exe"}
    with pytest.raises(UpdateError):  # installer not listed in SHA256SUMS
        manifest_from_github_release(_release([good]), "c" * 64 + "  something-else.exe\n")
    with pytest.raises(UpdateError):  # no installer asset at all
        manifest_from_github_release(_release([{"name": "evil.exe", "browser_download_url": "https://x/evil.exe"}]), "")
    with pytest.raises(UpdateError):  # pre-releases are never offered
        manifest_from_github_release(_release([good], prerelease=True), "d" * 64 + "  " + good["name"])
    with pytest.raises(UpdateError):  # plain http download URL refused
        manifest_from_github_release(_release([dict(good, browser_download_url="http://github.com/a.exe")]),
                                     "e" * 64 + "  " + good["name"])
