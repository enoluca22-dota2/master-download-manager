# Master Download Manager

One Windows app for **direct file downloads** (multi-connection, resumable,
adaptive), **media sites** (video, audio, playlists, channels on YouTube and
~1,700 other sites) and **BitTorrent** (magnet links, `.torrent` files and
URLs) — with named queues, a scheduler, speed limits and a browser extension.

The packaged app is self-contained: it includes Python, the media engine
(built on yt-dlp), FFmpeg/FFprobe, a JavaScript runtime for YouTube and the
torrent engine. It uses the Internet only for the downloads you start (and, if
you ask, to check GitHub for an update); it never downloads code at runtime.

## Download / install

Get the latest **`Master.Download.Manager.Setup.<version>.exe`** from
[Releases](https://github.com/enoluca22-dota2/master-download-manager/releases)
(per-user install, no admin needed) and check it against the release's
`SHA256SUMS.txt`. The build is **not code-signed**, so Windows SmartScreen may
ask for confirmation. Setup lets you choose the components (shortcuts, start
with Windows, browser integration, `.torrent`/`magnet:` associations) —
see [docs/INSTALLATION.md](docs/INSTALLATION.md).

Installing over an earlier version upgrades it in place: settings, history,
unfinished downloads and the browser-extension pairing are kept. From 4.1.0 on,
**Help → Check for Updates** does this in one click
([docs/UPDATING.md](docs/UPDATING.md)). The browser extension zips are attached
to each release and are also in the app's `extensions\` folder.

## What's new in 4.1

* **One-click updates** — Update Now → Install and Restart. Updates are
  installed only when their Ed25519-signed release manifest, size and SHA-256
  verify; your data is backed up first. Stable and beta channels, daily check
  (optional), never a downgrade. How it's secured:
  [docs/UPDATE_SECURITY.md](docs/UPDATE_SECURITY.md).
* **Professional Setup** — components with descriptions and sizes, install
  details, run-after-install, clean uninstall that keeps your data unless you
  say otherwise and never touches downloaded files.
* **Menu bar** — File, Tasks, Tools, Help with shortcuts: multiple URLs,
  import/export lists and settings, history window, file integrity check, log
  viewer, documentation, About.
* **Open-source licenses window** — all 61 bundled components with author,
  license, version, source and full text; the inventory is verified against
  every build.

## What's new in 4.0

* **New main window** — category sidebar (Compressed, Programs, Videos, Music,
  Pictures, Documents, Torrents, Media sites, Finished/Unfinished/Failed,
  Queues) with counts, icon toolbar, a fast download table with progress bars
  and sortable/resizable/hideable columns, multi-select and drag-to-reorder,
  status bar with ↓/↑ speed. Dark, Light, true-Black and System themes with
  accent colours.
* **Faster, safer direct downloads** — adaptive connections (start with one,
  add more only while they make the download faster), dynamic splitting of the
  slowest part, global + per-download speed limits, optional checksum check,
  crash-safe resume state.
* **Queues & scheduler** — named queues with their own limits, weekly start/stop
  times, speed-limit profiles, recurring downloads, priorities, and an optional
  notify / exit / sleep / shut-down when a queue finishes (always after a
  cancellable countdown).
* **History** in a database: instant search, status/category/date filters,
  export and import.
* **Browser extension** — download selected links, choose the queue, and
  (opt-in, off by default) hand normal browser downloads of chosen file types
  to MDM. No cookies or browsing history ever leave the browser.
Full list: [CHANGELOG.md](CHANGELOG.md) and `docs/`.

## Using it

* **New Download / Ctrl+V** — paste a link (or several). The app detects the
  engine and shows what the source really offers: media formats and
  subtitles, a playlist's items, a torrent's files, or a file's name, size and
  resume support (plus connections and checksum). Pick a queue and go.
* **Queues** (toolbar) — create queues, set "downloads at the same time",
  schedules and completion actions; the Main queue always runs.
* **Right-click** a download for open/open with/folder, pause/resume/restart,
  move to queue, priority, speed limit, schedule start, copy link/path,
  properties and errors. Removing from the list never deletes files; deleting
  a file is a separate, confirmed action.
* **Tray** — open, new download, pause/resume all, "Limit download speed".

Data (settings, history, queues, logs) lives in
`%LOCALAPPDATA%\MasterDownloadManager`; secrets are redacted from logs.

## Browser extension

Chrome/Edge/Brave: `chrome://extensions` → Developer mode → *Load unpacked* →
the app's `extensions\chrome` folder (or the unzipped release zip). Firefox:
`about:debugging` → *Load Temporary Add-on* → `extensions\firefox\manifest.json`.
Then paste the security token from the app (Settings → Browser Integration).

* YouTube: draggable download button in the player, real formats, playlist tab.
* Any site: right-click a link, page, media element or **a selection of links**.
* Optional capture of browser downloads (turn it on in the app; choose file
  types, minimum size and excluded sites). If MDM isn't running or can't fetch
  the file on its own, the browser keeps the download.

## Building

Requires Python 3.12. `build.py setup` creates `.venv`, installs the pinned
yt-dlp and requirements (exact versions used for 4.0 are in
`requirements-lock.txt`) and stages FFmpeg, deno and Inno Setup (SHA-256
verified).

```bash
python build.py setup       # environment + bundled tools
python build.py test        # automated tests
python build.py exe         # dist/MasterDownloadManager/MasterDownloadManager.exe
python build.py installer   # dist/Master Download Manager Setup <version>.exe
python build.py extensions  # extension zips
python build.py release-assets  # signed update manifest, release notes, licenses zip, SHA256SUMS
python build.py all         # everything, with the license gate (collect_licenses.py --verify)
```

Run from source: `.venv\Scripts\python master_download_manager.py`
(`MDM_DATA_DIR=<folder>` runs a separate, portable profile).

## Testing

* `python build.py test` — automated tests (count in `docs/TEST_REPORT.md`) (engines, queues, scheduler,
  history, API, routing, extension logic, safety), using local HTTP servers and
  local BitTorrent peers — no internet needed.
* `tools/gui_smoke.py`, `tools/gui_smoke41.py` — drive the real window
  (menus, About, Licenses, update dialog against a local fake GitHub;
  screenshots).
* `tools/real_world_test.py` — real downloads through the packaged app
  (Creative Commons / official targets, 22 checks).
* `tools/browser_extension_test.py`, `tools/browser_capture_test.py` — the
  extension in a real Chromium against the packaged app.
* `tools/installer_test.ps1` — install / run / re-install / uninstall with an
  isolated installer variant.
* `tools/benchmark_connections.py`, `tools/perf_queue.py` — measurements.

Results: `docs/TEST_REPORT.md`.

## Documentation

* For users: [User Guide](docs/USER_GUIDE.md), [Installation](docs/INSTALLATION.md),
  [Updating](docs/UPDATING.md), [Uninstallation](docs/UNINSTALLATION.md),
  [Browser Integration](docs/BROWSER_INTEGRATION.md),
  [Troubleshooting](docs/TROUBLESHOOTING.md) — also under Help → Documentation
* [Licensing](docs/LICENSING.md), [Update security](docs/UPDATE_SECURITY.md),
  [Release process](docs/RELEASE_PROCESS.md)
* `docs/AB_MDM_COMPARISON.md` — feature-by-feature comparison with AB Download Manager
* `docs/ARCHITECTURE_UPGRADE.md` — how 4.0 is built and why
* `docs/FEATURE_IMPLEMENTATION.md` — every requirement and where it lives
* `docs/TEST_REPORT.md`, `docs/RELEASE_REPORT.md`
* `docs/THIRD_PARTY_LICENSES.md`, `THIRD_PARTY_NOTICES.md`, `licenses/`

## Known limitations

* YouTube changes regularly; keeping media downloads working means shipping
  updated builds with a newer yt-dlp.
* The installer and program are not code-signed (updates are verified with
  the project's own signing key instead).
* No mirror/multi-source downloads; no "slow down while I'm using the PC"
  detection (use speed profiles or Limited mode); English only.
* The extension can't start MDM when it's closed (that would need Native
  Messaging; see `docs/ARCHITECTURE_UPGRADE.md`).
* Browser impersonation (curl_cffi) and the GPL-licensed mutagen are not
  bundled; Chromium browsers often lock their cookie store while running.
* DRM-protected media is not supported. Only download content you have the
  right to download.

## License

Copyright (c) 2026 the Master Download Manager author. **All rights
reserved** — the source is public but not released under an open-source
license; see [docs/LICENSING.md](docs/LICENSING.md). Bundled third-party
components keep their own licenses (Help → View Open-Source Licenses,
`THIRD_PARTY_NOTICES.md`).

Master Download Manager is not affiliated with AB Download Manager; some of
its techniques were adapted from that Apache-2.0 project (see
`THIRD_PARTY_NOTICES.md`).
