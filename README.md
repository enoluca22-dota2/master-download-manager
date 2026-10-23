# Master Download Manager

One Windows app for **media sites** (video, audio, playlists, channels on
YouTube and ~1,700 other sites), **direct file downloads** (resumable,
multi-connection) and **BitTorrent** (magnet links, `.torrent` files and
`.torrent` URLs) — in one queue, with a browser extension that sends links to it.

The packaged app is self-contained: it includes Python, the media engine
(built on yt-dlp), FFmpeg/FFprobe, a JavaScript runtime for YouTube, and the
torrent engine. It uses the Internet only for the downloads you start; it
never downloads code from GitHub or anywhere else at runtime.

## Download / install

| What | Path (after `python build.py all`) |
|---|---|
| Installer | `dist/Master Download Manager Setup 3.0.0.exe` (per-user install, no admin needed) |
| Portable folder | `dist/MasterDownloadManager/MasterDownloadManager.exe` |
| Browser extensions | `dist/MasterDownloadManager-extension-chrome.zip`, `…-extension-firefox.zip` (also in the app's `extensions/` folder) |

The installer can optionally make the app open `.torrent` files and `magnet:`
links (both unchecked by default so an existing torrent client isn't replaced).

## Using it

* **+ Add URL / Ctrl+V / Paste** — paste anything. The app detects the engine:
  * media page → choose Video (only the resolutions the source really has,
    plus Best/Smallest), Audio only (Original, MP3, M4A, Opus, Vorbis, FLAC,
    WAV — only what the bundled FFmpeg can encode), Subtitles only (uploaded
    and auto-generated, SRT/VTT/ASS) or Thumbnail; container MP4/MKV/Original,
    H.264 preference, embedded metadata/chapters/thumbnail/subtitles; an
    Advanced table of every real format, a custom format selector, a time
    section, a per-download speed limit and a custom file name
  * playlist / channel → item list with select all/none/invert/range, one
    task per item (numbered, in a playlist folder) or one task for the selection
  * file link → real file name (Content-Disposition), size, type, and whether
    the server supports resuming
  * magnet / `.torrent` → the torrent's real file list with checkboxes
* **Queue**: Pending → Preparing → Downloading → Processing → Completed, plus
  Paused, Retrying, Seeding, Failed, Cancelled. Pause/resume/cancel/retry,
  reorder (also move to top/bottom), retry failed, clear completed, concurrent
  downloads. Unfinished downloads come back (paused) after a restart.
* **Library**: Videos, Audio, Torrents, Archives, Other, and History —
  open file, show in folder, download again, remove from history (files are
  only deleted with the explicit "Delete file from disk…" action).
* **Errors** explain the reason and a suggested action, with
  *View technical details* and *Copy error* in the details window.
* **Settings**: General, Downloads, Media, Audio, Subtitles, Network,
  Authentication (browser cookies or cookies.txt, local only), Torrent,
  Browser Integration, Advanced, Updates, About, Licenses.

Data (settings, history, queue, logs) lives in
`%LOCALAPPDATA%\MasterDownloadManager` (`logs\app.log`, `download.log`,
`media.log`, `torrent.log`, `browser.log`; secrets are redacted). Settings and
history from earlier versions (YTD / Enagrio) are imported automatically.

## Browser extension

Chrome/Edge/Brave: `chrome://extensions` → Developer mode → *Load unpacked* →
the app's `extensions\chrome` folder. Firefox: `about:debugging` → *Load
Temporary Add-on* → `extensions\firefox\manifest.json`. Then paste the security
token from the app (Settings → Browser Integration) into the extension's settings.

On YouTube the extension adds a draggable button inside the player (position
remembered; × hides it for the session). Its popup lists the video's real
formats and, on playlist pages, a Playlist tab; the full URL including
`list=`/`index=` is sent to the app. On any site, right-click a link, page or
video/audio element → *Download … with Master Download Manager*.

## Architecture

```
master_download_manager.py      entry point (single instance; hands links to the running window)
ytd/
  gui/          app.py (main window), add_dialog.py, details.py, settings_window.py,
                theme.py/effects.py/backdrop.py (glass design system), common.py
  engine.py     unified queue: statuses, concurrency, retry policy, persistence, history
  router.py     automatic engine detection (+ one HEAD/GET probe for unknown pages)
  providers/    media.py   Media Engine (bundled yt-dlp)   youtube.py (compat shim)
                torrent.py Torrent Engine (libtorrent)      http.py    Direct HTTP Engine
  api/          local API for the extension (127.0.0.1 only, token, CORS to extensions)
  errors.py     reason + suggested action + technical details
  files.py      filename/path safety          tools.py   bundled FFmpeg/deno lookup
  history.py    history + library categories  updater.py  manifest + SHA-256 verified updates
  config.py, settings_store.py, logging_setup.py, tray.py, notifications.py, startup.py
extension-shared/ytd-client.js  single source of the extension's API client (copied by build.py)
extension-chrome/, extension-firefox/
tools/          fetch_tools.py, collect_licenses.py, gui_smoke.py, real_world_test.py,
                browser_extension_test.py
installer/      Inno Setup script
```

How yt-dlp is integrated, why, and the original audit: see `PROJECT_AUDIT.md`.
Licenses and what they require: `THIRD_PARTY_NOTICES.md` and `licenses/`.

## Building

Requires Python 3.12 on the build machine and the `yt-dlp-master` source
folder next to this project.

```bash
python build.py setup       # .venv + yt-dlp from ../yt-dlp-master + pinned deps + FFmpeg/deno/Inno Setup (SHA-256 verified)
python build.py test        # automated tests
python build.py exe         # dist/MasterDownloadManager/MasterDownloadManager.exe
python build.py installer   # dist/Master Download Manager Setup <version>.exe
python build.py extensions  # extension zips
python build.py all         # everything
```

Run from source: `.venv\Scripts\python master_download_manager.py`.

## Testing

* `python build.py test` — 292 automated tests: engines, queue, API, routing,
  errors/redaction/path safety, updater, a real local HTTP server (ranges,
  resume, segmented download, pause/cancel, speed limit) and real BitTorrent
  transfers between two local libtorrent sessions (no internet needed).
* `tools/gui_smoke.py [--online]` — drives the real window and screenshots it.
* `tools/real_world_test.py` — real downloads through a running app's API
  (public / Creative Commons targets).
* `tools/browser_extension_test.py` — the Chrome extension in a real Chromium
  against a running app.

## Known limitations

* YouTube changes regularly; keeping media downloads working means shipping
  updated builds (rebuild with a newer `yt-dlp-master`). The app never
  self-updates from GitHub.
* No update server is configured; *Check for updates* works once you publish
  a manifest (format in `ytd/updater.py`). The installer is not code-signed.
* Browser impersonation (curl_cffi) is not bundled; a few sites that require
  it may fail. Thumbnail embedding into Opus/Vorbis/FLAC is not offered
  (that needs the GPL-licensed mutagen, deliberately excluded).
* Chromium-based browsers often lock their cookie store while running;
  Firefox or a cookies.txt file is more reliable for signed-in downloads.
* DRM-protected media is not supported. Only download content you have the
  right to download.
