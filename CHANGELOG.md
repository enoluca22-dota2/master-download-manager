# Changelog

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
