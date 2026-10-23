"""Real BitTorrent transfers between two libtorrent sessions on localhost.

A seeder session (this test) shares freshly generated torrents; MDM's
Torrent Engine downloads them through magnet links that carry an `x.pe`
peer hint, so no internet, tracker or DHT is involved.
"""

import os
import time
import warnings

import pytest

lt = pytest.importorskip("libtorrent")

from ytd.engine import DownloadManager  # noqa: E402
from ytd.models import STATUS_CANCELLED, STATUS_COMPLETED, STATUS_DOWNLOADING, STATUS_PAUSED, DownloadJob  # noqa: E402
from ytd.providers.base import UnsupportedMediaError  # noqa: E402
from ytd.providers.torrent import TorrentProvider  # noqa: E402

from local_http_server import LocalHTTPServer  # noqa: E402


class Seeder:
    def __init__(self, upload_limit=0):
        settings = {"listen_interfaces": "127.0.0.1:0", "enable_dht": False, "enable_lsd": False,
                    "enable_upnp": False, "enable_natpmp": False}
        if upload_limit:
            settings.update({"upload_rate_limit": upload_limit, "ignore_limits_on_local_network": False})
        self.session = lt.session(settings)

    def share(self, folder, files, piece_size=32768):
        """files: {relative_name: bytes}. Returns (torrent_info, magnet)."""
        root = os.path.join(folder, "content")
        for name, data in files.items():
            path = os.path.join(root, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            fs = lt.file_storage()
            lt.add_files(fs, root if len(files) > 1 else os.path.join(root, next(iter(files))))
            ct = lt.create_torrent(fs, piece_size)
            lt.set_piece_hashes(ct, os.path.dirname(root) if len(files) > 1 else root)
        raw = lt.bencode(ct.generate())
        ti = lt.torrent_info(raw)
        handle = self.session.add_torrent({"ti": ti, "save_path": os.path.dirname(root) if len(files) > 1 else root})
        deadline = time.time() + 15
        while not handle.status().is_seeding and time.time() < deadline:
            time.sleep(0.05)
        assert handle.status().is_seeding
        ih = str(ti.info_hashes().v1)
        magnet = f"magnet:?xt=urn:btih:{ih}&dn={ti.name()}&x.pe=127.0.0.1:{self.session.listen_port()}"
        return ti, magnet, raw


def _payload(size, seed):
    import hashlib
    block = hashlib.sha256(str(seed).encode()).digest()
    return (block * (size // 32 + 1))[:size]


def _wait(predicate, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


@pytest.fixture
def manager(settings_store, history_store):
    mgr = DownloadManager(settings_store, history_store)
    mgr.start()
    yield mgr
    mgr.stop()


def test_detect_magnet_reads_real_file_list(tmp_path):
    seeder = Seeder()
    files = {"a.txt": _payload(40_000, 1), "sub/b.bin": _payload(70_000, 2)}
    ti, magnet, _ = seeder.share(str(tmp_path / "seed"), files)
    media = TorrentProvider().detect(magnet)
    assert media.title == ti.name()
    by_label = {f.label: f.filesize_bytes for f in media.formats}
    assert by_label["a.txt"] == 40_000 and by_label["b.bin"] == 70_000
    assert media.formats[0].filesize_bytes == 110_000
    assert media.extra["info_hash"] == str(ti.info_hashes().v1)


def test_magnet_download_end_to_end_through_queue(tmp_path, manager, history_store):
    seeder = Seeder()
    data = _payload(1_500_000, 3)
    _, magnet, _ = seeder.share(str(tmp_path / "seed"), {"movie.mkv": data})
    job = manager.add_job(url=magnet, kind="torrent", quality_label="Entire torrent", format_key="torrent-all",
                          download_dir=str(tmp_path / "dl"), engine="torrent")
    assert job.category == "Torrent"
    assert _wait(lambda: manager.get_job(job.job_id).status == STATUS_COMPLETED)
    done = manager.get_job(job.job_id)
    assert open(done.result_path, "rb").read() == data
    assert done.peers is not None
    entry = history_store.all()[0]
    assert entry["engine"] == "torrent" and entry["category"] == "Torrents"


def test_selected_files_only(tmp_path, manager):
    seeder = Seeder()
    files = {"one.bin": _payload(64 * 1024, 4), "two.bin": _payload(64 * 1024, 5), "three.bin": _payload(64 * 1024, 6)}
    ti, magnet, _ = seeder.share(str(tmp_path / "seed"), files, piece_size=16384)
    storage = ti.files()
    names = [os.path.basename(storage.file_path(i)) for i in range(storage.num_files())]
    keep = [names.index("one.bin"), names.index("three.bin")]
    job = manager.add_job(url=magnet, kind="torrent", quality_label="2 files",
                          format_key=f"torrent-files-{keep[0]},{keep[1]}", download_dir=str(tmp_path / "dl"),
                          engine="torrent")
    assert _wait(lambda: manager.get_job(job.job_id).status == STATUS_COMPLETED)
    root = manager.get_job(job.job_id).result_path
    assert open(os.path.join(root, "one.bin"), "rb").read() == files["one.bin"]
    assert open(os.path.join(root, "three.bin"), "rb").read() == files["three.bin"]
    two = os.path.join(root, "two.bin")
    assert not os.path.exists(two) or open(two, "rb").read() != files["two.bin"]


def test_pause_resume_and_cancel_torrent(tmp_path, manager):
    seeder = Seeder(upload_limit=300 * 1024)
    data = _payload(3 * 1024 * 1024, 7)
    _, magnet, _ = seeder.share(str(tmp_path / "seed"), {"big.bin": data})
    job = manager.add_job(url=magnet, kind="torrent", quality_label="Entire torrent", format_key="torrent-all",
                          download_dir=str(tmp_path / "dl"), engine="torrent")
    assert _wait(lambda: manager.get_job(job.job_id).status == STATUS_DOWNLOADING
                 and manager.get_job(job.job_id).downloaded_bytes > 200_000)
    manager.pause(job.job_id)
    assert _wait(lambda: manager.get_job(job.job_id).status == STATUS_PAUSED, timeout=10)
    paused_at = manager.get_job(job.job_id).downloaded_bytes
    assert 0 < paused_at < len(data)
    manager.resume(job.job_id)
    assert _wait(lambda: manager.get_job(job.job_id).status == STATUS_COMPLETED, timeout=90)
    assert open(manager.get_job(job.job_id).result_path, "rb").read() == data

    seeder2 = Seeder(upload_limit=200 * 1024)
    _, magnet2, _ = seeder2.share(str(tmp_path / "seed2"), {"other.bin": _payload(3 * 1024 * 1024, 8)})
    job2 = manager.add_job(url=magnet2, kind="torrent", quality_label="Entire torrent", format_key="torrent-all",
                           download_dir=str(tmp_path / "dl2"), engine="torrent")
    assert _wait(lambda: manager.get_job(job2.job_id).downloaded_bytes > 100_000)
    manager.cancel(job2.job_id)
    assert _wait(lambda: manager.get_job(job2.job_id).status == STATUS_CANCELLED, timeout=10)


def test_torrent_file_url_is_fetched_and_parsed(tmp_path):
    seeder = Seeder()
    ti, _, raw = seeder.share(str(tmp_path / "seed"), {"iso.img": _payload(50_000, 9)})
    with LocalHTTPServer() as srv:
        srv.files["dist.torrent"] = raw
        media = TorrentProvider().detect(f"{srv.base}/files/dist.torrent")
    assert media.title == ti.name()
    assert media.formats[1].filesize_bytes == 50_000


def test_local_torrent_file_and_malformed_metadata(tmp_path):
    seeder = Seeder()
    ti, _, raw = seeder.share(str(tmp_path / "seed"), {"doc.pdf": _payload(30_000, 10)})
    good = tmp_path / "good.torrent"
    good.write_bytes(raw)
    assert TorrentProvider().detect(str(good)).title == ti.name()
    bad = tmp_path / "bad.torrent"
    bad.write_bytes(b"this is not bencoded at all")
    with pytest.raises(UnsupportedMediaError, match="valid .torrent"):
        TorrentProvider().detect(str(bad))


def test_unknown_peerless_magnet_times_out_cleanly(monkeypatch, tmp_path):
    import ytd.providers.torrent as tmod
    monkeypatch.setattr(tmod, "METADATA_TIMEOUT_SECONDS", 2)
    job = DownloadJob(url="magnet:?xt=urn:btih:" + "ab" * 20, kind="torrent", quality_label="", format_key="torrent-all",
                      is_playlist=False, download_dir=str(tmp_path))
    with pytest.raises(UnsupportedMediaError, match="metadata"):
        TorrentProvider().run_download(job, lambda d: None)
