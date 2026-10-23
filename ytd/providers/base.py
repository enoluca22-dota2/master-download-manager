"""Engine (provider) architecture: the UI, queue and API never talk to
yt-dlp, libtorrent or HTTP directly — only to a MediaProvider, and only see
MediaInfo/FormatInfo/DownloadJob.

Engines shipped:
    media    yt-dlp (≈1,750 sites: video/audio pages, playlists, channels)
    torrent  libtorrent (magnet links, .torrent files and URLs)
    http     direct HTTP/HTTPS file downloads
"""

from ..errors import PauseRequested, UnsupportedMediaError  # noqa: F401 (re-exported)


class MediaProvider:
    name = "base"
    display_name = "Base"

    def supports(self, url):
        raise NotImplementedError

    def detect(self, url):
        """Return a MediaInfo describing only formats that are actually
        downloadable. Raises UnsupportedMediaError if not supported/protected."""
        raise NotImplementedError

    def resolve_format_selection(self, format_id):
        """Translate a FormatInfo.format_id (from detect()) into the
        (kind, quality_label, format_key, audio_bitrate) tuple add_job()
        needs. Raises ValueError for an unrecognised format_id."""
        raise NotImplementedError

    def build_download_opts(self, job, progress_hook):
        """Return engine-specific options (e.g. yt-dlp opts) to fulfil `job`.
        Only yt-dlp-backed engines need this."""
        raise NotImplementedError

    def run_download(self, job, progress_hook):
        """Execute the download for `job`. Must respect job.cancel_event and
        job.pause_event, calling progress_hook with status updates. Returns a
        dict that may include "title" and "filepath"/"filepaths"."""
        raise NotImplementedError

    def cleanup_cancelled(self, job):
        """Remove temporary/partial files after a user cancel. Optional."""
