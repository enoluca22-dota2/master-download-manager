# Changelog

## 4.1.0 — 2026-10-24

### Added
- **One-click updates.** Help → Check for Updates shows the installed and
  latest version, release date, size and what's new, with *Update Now*,
  *Remind Me Later*, *View Release Notes* and *Cancel*. Update Now downloads
  and verifies the update; *Install and Restart* pauses running downloads,
  closes MDM, upgrades it in place and starts the new version, which then
  reports the result; if the installation fails, the previous version starts
  again and says so. Daily background check (can be turned off), stable and
  beta channels, never downgrades, one update at a time.
- **Signed releases.** Every release carries an Ed25519-signed
  `update-manifest.json`. MDM installs an update only if the signature and
  the installer's name, URL, size and SHA-256 all match. Settings, queues and
  the history database are backed up before each update. A damaged settings
  file is restored from that backup afterwards.
- **Menu bar** — File, Tasks, Tools, Help, with keyboard shortcuts (Ctrl+N,
  Ctrl+Shift+N, Ctrl+O, Ctrl+Shift+M, Ctrl+I, Ctrl+E, Ctrl+J, Ctrl+H, Ctrl+,,
  Ctrl+P, Ctrl+R, Ctrl+Q, F1, Alt+F/T/O/H). Every entry works; entries that
  don't apply right now are greyed out.
- Add Multiple URLs, Import/Export Download List (JSON or plain text),
  Download History window (search, filter, open, download again), Check File
  Integrity (MD5/SHA-1/SHA-256/SHA-512), Import/Export Settings (without the
  security token), log viewer.
- **About** window (version, copyright, project, open-source and update
  panels) and **Open Source Software Used in Master Download Manager**:
  searchable, sortable list of all 61 bundled components with author,
  license, version, source repository and full license texts.
- Documentation inside the app (Help → Documentation): User Guide,
  Installation, Browser Integration, Troubleshooting. Also new: Updating,
  Uninstallation, Licensing, Update Security, Release Process.
- Settings → Updates: automatic check, check on start, channel, Check Now,
  current version, last check, status and the last update's result.
- Version shown in the status bar.

### Changed
- **New Setup wizard**: Welcome, License Information, Choose Components
  (with descriptions and sizes: program, Start Menu shortcut, Desktop
  shortcut, Start with Windows, Browser Integration, .torrent and magnet:
  associations), Installation Location (new installs only), Installing with
  *Show details*, Completing with *Run Master Download Manager* and *Open
  GitHub Project*. Upgrades keep the folder and your previous choices. The
  wizard no longer asks "for me / for all users" (it installs per user, so
  updates never need administrator rights; administrators can still use
  `/ALLUSERS`).
- Uninstall asks whether to remove settings/history (default: keep). It
  removes only the files MDM created, and never downloaded files.
- The license inventory is generated from the build and verified as a release
  gate. It now also lists the native libraries bundled with Python (OpenSSL,
  SQLite, Tcl/Tk, zlib, libffi, expat, bzip2, xz), the MSVC runtime, the
  JavaScript bundled in yt-dlp-ejs (meriyah, astring) and every winrt
  package. Corrected: pywin32 (BSD-3-Clause for the bundled parts), certifi
  (full MPL-2.0 text).
- Licensing is documented accurately: MDM's own code is all rights reserved
  (not open source), third-party components keep their licenses, and the
  build is not code-signed.
- The update check refuses releases that aren't from the official repository
  and plain-HTTP addresses.
- Single version source (`ytd/__init__.py`) for the app, installer, extensions
  and release assets.

### Fixed
- A background task (for example the update check) could hang forever when
  Python's garbage collector happened to free a window's font in that thread.
  Garbage collection now runs on the window thread only.
- A second MDM instance under a test/portable profile no longer blocks the
  main one (per-profile single-instance lock).


## 4.0.0 — 2026-10-23

### Added
- New main window: category sidebar with counts (All, Compressed, Programs,
  Videos, Music, Pictures, Documents, Torrents, Media sites, Finished,
  Unfinished, Failed) and queues; icon toolbar (New Download, Paste URL,
  Resume, Pause, Start/Stop Queue, Queues, Stop All, Delete, Settings);
  virtualised download table with progress bars, category icons, sortable /
  resizable / hideable columns (type, size, downloaded, progress, status,
  speed, time left, connections, engine, queue, added, completed, source),
  multi-select and drag-and-drop ordering; status bar with ↓/↑ speed and the
  active speed limit; date filter and instant search.
- Themes: Dark, Light, Black (AMOLED), System; accent colours (Monochrome keeps
  the 3.0 look); title bar follows the theme; optional Mica title bar.
- Named queues: per-queue simultaneous downloads and connections, weekly
  start/stop schedule, stop when empty, completion action (notify, exit, sleep,
  hibernate, shut down — after a cancellable 60-second countdown).
- Scheduler: missed starts caught up, correct across sleep/wake and DST;
  speed-limit profiles by time of day; recurring downloads; "Schedule start…"
  for single downloads.
- Priorities (High / Normal / Low) with aging so nothing starves.
- Bandwidth: global speed limit shared fairly by all downloads, live
  per-download limits, tray "Limit download speed" (limited mode).
- Direct downloads: adaptive connections, dynamic splitting of the busiest
  segment, per-download / per-queue connection counts, optional checksum
  (MD5/SHA-1/SHA-256/SHA-512), Last-Modified validation.
- New Download: several links at once (batch, with sizes and duplicate
  warnings), queue selector, connections, checksum, duplicate warning.
- Context menu: Open with, Restart from zero, Move to queue, Priority, Speed
  limit, Schedule start, Copy file path, View error.
- Details: open connections and a segment map.
- History in SQLite (from download_history.json, which is kept): unlimited,
  search, filters, export/import (JSON, CSV).
- Settings: Appearance, Bandwidth, Queues & Scheduler, History & Storage,
  browser capture options, update source, safe "Reset all settings".
- Browser extension 4.0: "Download selected links", queue choice, optional
  capture of browser downloads (off by default; types, minimum size, excluded
  sites; falls back to the browser when MDM can't take it).
- Local API: `/queues`, `/downloads/batch`, `/browser/config`; queue,
  priority, start-paused, connections and checksum on downloads.
- Updates from GitHub Releases, verified against `SHA256SUMS.txt`.
- Installer: dark-aware wizard with MDM artwork, information page, components
  (browser extension files optional), Start-menu/desktop shortcut choices,
  project links.

### Changed
- Direct downloads start with one connection and add more only while that
  measurably increases throughput (measured: on a fast CDN a fixed 4 connections were 14–28% slower than one across two benchmark runs; a
  connection count chosen for a download is used as is).
- Failed connections hand their segment to the others instead of stopping the
  download; retry counters reset after progress; segment state is flushed to
  disk before it is saved.
- History is no longer capped at 2,000 entries (100,000).

### Fixed
- A `settings.json` with a UTF-8 byte-order mark (e.g. saved by Notepad) was
  ignored and then overwritten with defaults and a new API token; unreadable
  settings are now backed up before anything is written.
- Unticking "Start now" in the Add dialog could let a download start before it
  was paused.
- Installer: another copy of MDM could remove this install's magnet/.torrent
  registration; re-installing with fewer components/shortcuts left them behind;
  a duplicate "Licenses" Start-menu shortcut from an early 3.0 build.

## 3.0.0 — 2026-10-03

Master Download Manager: media engine (yt-dlp), direct HTTP engine, torrent
engine (libtorrent), unified queue, local API and browser extensions,
installer. See `PROJECT_AUDIT.md`.
