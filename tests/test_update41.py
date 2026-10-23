"""4.1 update system against a local fake GitHub (no internet): channels,
signed-manifest verification, refusals, interrupted/corrupt downloads, the
installer hand-over and the post-update check."""

import base64
import functools
import hashlib
import json
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from Cryptodome.PublicKey import ECC
from Cryptodome.Signature import eddsa

from ytd import update_install, updater
from ytd.updater import UpdateError

REPO = "test-owner/mdm-test"
INSTALLER = os.urandom(300_000)


class FakeGitHub:
    """Serves /repos/<REPO>/releases(/latest) and the release assets."""

    def __init__(self):
        self.releases = []
        self.files = {}
        self.truncate = set()
        handler = self._handler()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def _handler(self):
        gh = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == f"/repos/{REPO}/releases/latest":
                    stable = [r for r in gh.releases if not r["prerelease"]]
                    return self._send(json.dumps(stable[-1]).encode() if stable else b"{}",
                                      200 if stable else 404)
                if path == f"/repos/{REPO}/releases":
                    return self._send(json.dumps(list(reversed(gh.releases))).encode())
                if path in gh.files:
                    body = gh.files[path]
                    if path in gh.truncate:  # announce the full size, send half, drop the connection
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body[: len(body) // 2])
                        self.wfile.flush()
                        self.connection.close()
                        return
                    return self._send(body)
                self._send(b"not found", 404)

            def _send(self, body, code=200):
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        return H

    def add_release(self, version, key, *, prerelease=False, sign=True, manifest_sha=None, installer=INSTALLER,
                    with_installer=True, html_repo=REPO):
        tag = f"v{version}"
        name = f"Master.Download.Manager.Setup.{version}.exe"
        dl = f"/dl/{tag}/"
        assets = []

        def asset(asset_name, data):
            self.files[dl + asset_name] = data
            assets.append({"name": asset_name, "size": len(data), "browser_download_url": self.base + dl + asset_name})
        sha = hashlib.sha256(installer).hexdigest()
        if with_installer:
            asset(name, installer)
        asset("SHA256SUMS.txt", f"{sha}  {name}\n".encode())
        if sign:
            manifest = {"app": "Master Download Manager", "repository": REPO, "version": version,
                        "installer": {"name": name, "url": self.base + dl + name, "size": len(installer),
                                      "sha256": manifest_sha or sha}}
            data = json.dumps(manifest).encode()
            asset("update-manifest.json", data)
            asset("update-manifest.sig", base64.b64encode(eddsa.new(key, "rfc8032").sign(data)) + b"\n")
        self.releases.append({"tag_name": tag, "draft": False, "prerelease": prerelease, "body": f"Notes for {version}",
                              "published_at": "2026-11-01T10:00:00Z",
                              "html_url": f"https://github.com/{html_repo}/releases/tag/{tag}", "assets": assets})

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def keys(tmp_path):
    key = ECC.generate(curve="ed25519")
    pub = tmp_path / "pub.pem"
    pub.write_text(key.public_key().export_key(format="PEM"), encoding="ascii")
    return key, str(pub)


@pytest.fixture
def gh(monkeypatch, keys, tmp_path):
    server = FakeGitHub()
    monkeypatch.setenv("MDM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MDM_UPDATE_API", server.base)
    monkeypatch.setenv("MDM_UPDATE_REPO", REPO)
    monkeypatch.setenv("MDM_UPDATE_PUBKEY_FILE", keys[1])
    yield server
    server.close()


def test_no_update_when_latest_is_current(gh, keys):
    gh.add_release("4.1.0", keys[0])
    info = updater.check_release(current_version="4.1.0")
    assert not info.available and info.verified


def test_newer_stable_release_is_verified(gh, keys):
    gh.add_release("4.1.0", keys[0])
    gh.add_release("4.2.0", keys[0])
    info = updater.check_release(current_version="4.1.0")
    assert info.available and info.verified and info.version == "4.2.0"
    assert info.size == len(INSTALLER) and info.notes == "Notes for 4.2.0" and info.published.startswith("2026-11-01")


def test_older_release_is_never_offered(gh, keys):
    gh.add_release("4.0.5", keys[0])
    assert not updater.check_release(current_version="4.1.0").available


def test_beta_only_on_beta_channel(gh, keys):
    gh.add_release("4.1.0", keys[0])
    gh.add_release("4.2.0b1", keys[0], prerelease=True)
    assert not updater.check_release(current_version="4.1.0", channel="stable").available
    beta = updater.check_release(current_version="4.1.0", channel="beta")
    assert beta.available and beta.prerelease and beta.version == "4.2.0b1" and beta.verified


def test_signature_from_another_key_is_not_verified(gh):
    gh.add_release("4.2.0", ECC.generate(curve="ed25519"))  # attacker's key
    info = updater.check_release(current_version="4.1.0")
    assert info.available and not info.verified and "signature is invalid" in info.reason


def test_release_without_signed_manifest_is_shown_but_not_installable(gh, keys):
    gh.add_release("4.2.0", keys[0], sign=False)
    info = updater.check_release(current_version="4.1.0")
    assert info.available and not info.verified and "release page" in info.reason
    with pytest.raises(UpdateError):
        update_install.prepare(info, os.environ["MDM_DATA_DIR"])


def test_manifest_and_checksum_list_must_agree(gh, keys):
    gh.add_release("4.2.0", keys[0], manifest_sha="0" * 64)
    info = updater.check_release(current_version="4.1.0")
    assert not info.verified and "SHA-256" in info.reason


def test_missing_installer_asset_is_an_error(gh, keys):
    gh.add_release("4.2.0", keys[0], with_installer=False)
    with pytest.raises(UpdateError):
        updater.check_release(current_version="4.1.0")


def test_release_from_another_repository_is_refused(gh, keys):
    gh.add_release("4.2.0", keys[0], html_repo="someone-else/fork")
    with pytest.raises(UpdateError, match="official"):
        updater.check_release(current_version="4.1.0")


def test_github_unreachable_is_a_clean_error(monkeypatch, keys, tmp_path):
    monkeypatch.setenv("MDM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MDM_UPDATE_API", "http://127.0.0.1:9")
    monkeypatch.setenv("MDM_UPDATE_REPO", REPO)
    with pytest.raises(UpdateError, match="Couldn't reach"):
        updater.check_release(current_version="4.1.0")


def test_interrupted_and_corrupt_downloads_are_refused(gh, keys, tmp_path):
    gh.add_release("4.2.0", keys[0])
    info = updater.check_release(current_version="4.1.0")
    path = "/dl/v4.2.0/Master.Download.Manager.Setup.4.2.0.exe"
    gh.truncate.add(path)
    with pytest.raises(UpdateError):
        updater.download_update(info.manifest(), str(tmp_path / "a"))
    assert not any(p.name.endswith(".exe") for p in (tmp_path / "a").iterdir())
    gh.truncate.clear()
    gh.files[path] = os.urandom(len(INSTALLER))  # same size, different bytes
    with pytest.raises(UpdateError, match="SHA-256"):
        updater.download_update(info.manifest(), str(tmp_path / "b"))


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    upd = data / "updates"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(update_install, "DATA_DIR", str(data))
    monkeypatch.setattr(update_install, "UPDATES_DIR", str(upd))
    monkeypatch.setattr(update_install, "PENDING_FILE", str(upd / "pending.json"))
    monkeypatch.setattr(update_install, "RESULT_FILE", str(upd / "last-update.json"))
    monkeypatch.setattr(update_install, "LOCK_FILE", str(upd / "update.lock"))
    monkeypatch.setattr(update_install, "backup_state", functools.partial(update_install.backup_state, str(data)))
    return data


def test_prepare_backs_up_state_and_launch_hands_over(gh, keys, tmp_path, monkeypatch, data_dir):
    (data_dir / "settings.json").write_text('{"api_token": "keep"}', encoding="utf-8")
    db = sqlite3.connect(data_dir / "download_history.sqlite3")
    db.execute("create table t(x)")
    db.execute("insert into t values (42)")
    db.commit()
    db.close()
    gh.add_release("4.2.0", keys[0])
    info = updater.check_release(current_version="4.1.0")
    installer, backup = update_install.prepare(info, str(tmp_path))
    assert open(installer, "rb").read() == INSTALLER
    assert not os.path.exists(update_install.LOCK_FILE)  # released afterwards
    assert json.loads(open(os.path.join(backup, "settings.json")).read())["api_token"] == "keep"
    con = sqlite3.connect(os.path.join(backup, "download_history.sqlite3"))
    assert con.execute("select x from t").fetchone() == (42,)
    con.close()
    started = []
    monkeypatch.setattr(update_install.subprocess, "Popen", lambda args, **kw: started.append(args))
    pending = update_install.launch(info, installer, backup)
    args = started[0]
    assert args[0] == installer and "/SILENT" in args and "/MDMUPDATE=1" in args
    assert f"/WAITPID={os.getpid()}" in args and any(a.startswith("/LOG=") for a in args)
    assert pending["to"] == "4.2.0"
    # tampering after verification is caught before the installer runs
    with open(installer, "ab") as f:
        f.write(b"x")
    with pytest.raises(UpdateError, match="changed after it was verified"):
        update_install.launch(info, installer, backup)


def test_second_update_attempt_is_refused(gh, keys, tmp_path, data_dir):
    gh.add_release("4.2.0", keys[0])
    info = updater.check_release(current_version="4.1.0")
    os.makedirs(update_install.UPDATES_DIR, exist_ok=True)
    with open(update_install.LOCK_FILE, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getppid()}, f)  # another live process holds it
    with pytest.raises(UpdateError, match="already in progress"):
        update_install.prepare(info, str(tmp_path))
    with open(update_install.LOCK_FILE, "w", encoding="utf-8") as f:
        json.dump({"pid": 999999}, f)  # stale lock from a crashed process is taken over
    update_install.prepare(info, str(tmp_path))


def _pending(tmp_path, monkeypatch, frm, to, backup=None):
    upd = tmp_path / "updates"
    upd.mkdir(exist_ok=True)
    monkeypatch.setattr(update_install, "UPDATES_DIR", str(upd))
    monkeypatch.setattr(update_install, "PENDING_FILE", str(upd / "pending.json"))
    monkeypatch.setattr(update_install, "RESULT_FILE", str(upd / "last-update.json"))
    (upd / "pending.json").write_text(json.dumps({"from": frm, "to": to, "pid": 0, "backup": backup or "",
                                                  "log": "x.log"}), encoding="utf-8")


def test_post_update_success_and_failure(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    _pending(tmp_path, monkeypatch, "4.0.9", update_install.__version__)
    r = update_install.post_update_check(str(settings))
    assert r["ok"] and "updated" in r["message"] and not os.path.exists(update_install.PENDING_FILE)
    _pending(tmp_path, monkeypatch, update_install.__version__, "9.9.9")
    r = update_install.post_update_check(str(settings))
    assert not r["ok"] and "didn't complete" in r["message"]
    assert update_install.post_update_check(str(settings)) is None  # reported once


def test_damaged_settings_are_restored_from_the_backup(tmp_path, monkeypatch):
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "settings.json").write_text('{"api_token": "original"}', encoding="utf-8")
    settings = tmp_path / "settings.json"
    settings.write_text("{broken", encoding="utf-8")
    _pending(tmp_path, monkeypatch, "4.0.9", update_install.__version__, str(backup))
    r = update_install.post_update_check(str(settings))
    assert r["restored_settings"] and json.loads(settings.read_text())["api_token"] == "original"


def test_disk_space_check(monkeypatch, tmp_path):
    monkeypatch.setattr(update_install, "UPDATES_DIR", str(tmp_path))

    class Tiny:
        free = 10_000_000
    monkeypatch.setattr(update_install.shutil, "disk_usage", lambda p: Tiny())
    with pytest.raises(UpdateError, match="Not enough free disk space"):
        update_install.check_disk_space(100_000_000, str(tmp_path))


def test_shipped_public_key_loads():
    key = updater.load_public_key(updater.PUBLIC_KEY_FILE)
    assert len(updater.public_key_fingerprint(key)) == 16


def test_release_without_html_url_is_refused(gh, keys):
    gh.add_release("4.2.0", keys[0])
    gh.releases[-1]["html_url"] = ""
    with pytest.raises(UpdateError, match="official"):
        updater.check_release(current_version="4.1.0")


def test_plain_http_is_refused_outside_a_test_profile(monkeypatch):
    monkeypatch.delenv("MDM_DATA_DIR", raising=False)
    with pytest.raises(UpdateError, match="https"):
        updater._require_secure("http://127.0.0.1:8000/x", "update manifest")
