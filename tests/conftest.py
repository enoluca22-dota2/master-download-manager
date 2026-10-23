import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from yt_dlp.utils import DownloadError

from ytd.models import FormatInfo, MediaInfo
from ytd.providers.base import MediaProvider, UnsupportedMediaError
from ytd.providers.youtube import resolve_format_selection as _youtube_resolve_format_selection
from ytd.settings_store import SettingsStore
from ytd.history import HistoryStore


class FakeProvider(MediaProvider):
    """A controllable stand-in for YouTubeProvider so engine/API tests never
    touch the network. URLs starting with http://fake/ select behavior:
      /instant   - completes immediately
      /slow      - 10 steps of ~40ms each (good for concurrency/pause tests)
      /flaky     - fails with a transient network error on the first attempt,
                   then succeeds on retry
      /unsupported-detect - detect() raises UnsupportedMediaError
      /broken    - run_download always raises a permanent error
    """
    name = "fake"

    def __init__(self):
        self.attempts = {}

    def supports(self, url):
        return url.startswith("http://fake/")

    def resolve_format_selection(self, format_id):
        # FakeProvider's format_ids intentionally mirror YouTubeProvider's
        # scheme (video-720, mp3-192, thumbnail, subtitle-en, ...), so the
        # same parsing logic applies.
        return _youtube_resolve_format_selection(format_id)

    def detect(self, url):
        if url.endswith("/unsupported-detect"):
            raise UnsupportedMediaError("This media cannot be downloaded by Enagrio.")
        return MediaInfo(
            title="Fake Video",
            source_url=url,
            source_site="Fake",
            duration_seconds=42,
            formats=[
                FormatInfo(format_id="video-720", kind="video", label="720p (HD)", ext="mp4",
                           height=720, fps=30, bitrate_kbps=1500, quality_tier="Medium", filesize_bytes=1000),
                FormatInfo(format_id="mp3-192", kind="audio", label="MP3 — 192 kbps", ext="mp3", abr_kbps=192),
                FormatInfo(format_id="thumbnail", kind="image", label="JPG 1280x720", ext="jpg",
                           resolution="1280x720"),
                FormatInfo(format_id="subtitle-en", kind="subtitle", label="English", ext="srt",
                           lang_code="en", lang_name="English"),
            ],
        )

    def run_download(self, job, progress_hook):
        self.attempts[job.url] = self.attempts.get(job.url, 0) + 1

        if job.url.endswith("/broken"):
            raise DownloadError("Video unavailable")

        if job.url.endswith("/flaky") and self.attempts[job.url] == 1:
            raise OSError("Temporary failure in name resolution (network)")

        if job.kind in ("image", "subtitle"):
            # Real yt-dlp never fires "downloading" progress for a
            # skip_download=True thumbnail/subtitle-only job — only the
            # final postprocessing step runs.
            progress_hook({"status": "finished"})
            return {"title": "Fake Video"}

        steps = 2 if job.url.endswith("/instant") else 10
        total = 1000
        for i in range(1, steps + 1):
            progress_hook({
                "status": "downloading",
                "downloaded_bytes": int(total * i / steps),
                "total_bytes": total,
                "filename": "fake_video [720p].mp4",
            })
            time.sleep(0.04)
        progress_hook({"status": "finished"})
        return {"title": "Fake Video"}


@pytest.fixture
def fake_provider():
    return FakeProvider()


@pytest.fixture
def settings_store(tmp_path):
    return SettingsStore(path=str(tmp_path / "settings.json"))


@pytest.fixture
def history_store(tmp_path):
    return HistoryStore(path=str(tmp_path / "history.json"))


@pytest.fixture
def download_dir(tmp_path):
    d = tmp_path / "downloads"
    d.mkdir()
    return str(d)
