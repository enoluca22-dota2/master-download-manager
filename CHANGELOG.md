# Changelog

## 4.4.0 — 2026-10-27

### Added
- **Updates from a private release repository** for your own PCs: Settings → Updates → Update source →
  *Private GitHub releases (token)*. Paste a read-only GitHub token once per PC; it is encrypted for your Windows
  account (DPAPI), never stored in settings or exports, and only ever sent to GitHub's API. Updates are verified
  exactly like public ones (Ed25519-signed manifest, size, SHA-256).
- Settings → Updates: **Check every** 6 hours / day / week, last *successful* check, **View Release Notes**, and
  **Install Downloaded Update** (a verified download you didn't install yet; checked again before it runs).
- Clear messages when GitHub can't be used: offline, timeouts, GitHub unavailable, request limit reached (with
  the time to retry), token rejected or without access, invalid release data.

### Changed
- Update window: **Download Update** → progress → **Install Update** / **Cancel** (the verified download is kept).
  If downloads are running, MDM asks before closing for the update.
- **FFmpeg replaced by a minimal LGPL build** made from the official FFmpeg 9.0.2 release (LGPL-2.1-or-later; no GPL,
  non-free or "version3" parts) with only LAME, Opus, Vorbis/Ogg and zlib inside - instead of a build with 49
  libraries. Every conversion MDM offers still works (MP3, M4A, Opus, Vorbis, FLAC, WAV, merging, thumbnails,
  subtitles); the bundled FFmpeg folder shrinks from about 75 MB to 31 MB. License texts for every bundled library are in
  Help → View Open-Source Licenses, and each release now includes FFmpeg's complete Corresponding Source.
- Updates honour the signed manifest's `minimum_version` (older installs get the manual-download path instead).

### Security
- The update token is never sent to the storage server GitHub redirects downloads to; redirects are followed
  by MDM itself and every hop must be HTTPS.

## 4.3.0 — 2026-10-26

### Fixed
- **Music search: white rectangle under the search box.** Typing in the Music (or Videos) search opened a
  borderless, always-on-top window for the suggestions and then failed to fill it (a missing theme colour,
  `KeyError: 'surface_raised'` in the log), leaving an empty white rectangle over the results that couldn't be
  closed. The suggestions are now a themed list inside the main window, built only when there are suggestions:
  at most 8 rows, never taller than the room below the search box, readable in the dark and light themes.
  It closes on Escape, a click outside, clearing the search, choosing a suggestion, Enter, the Search button,
  switching sections, moving/resizing/minimizing the window. Up/Down select a suggestion, Enter searches it (or
  the typed text when none is selected). Typing is debounced; late, stale or failed answers (no internet,
  unknown artist, slow service) never show anything. Regression test: `tools/gui_smoke_search_popup.py`.

## 4.2.0 — 2026-10-25

### Added
- **Music, Videos, Radio and Library** next to Downloads (navigation rail on the left). Downloads keep running
  and playback continues while you switch sections.
- **Player** (Windows media engine, in its own process so a playback problem can't affect downloads): mini player
  at the bottom and a full player with large artwork tinted by the artwork's colour, seek, shuffle, repeat
  (all/one), queue (play next, add, remove), volume and mute, Windows media keys and media overlay.
- **Music**: search with suggestions and recent searches (songs, videos, artists, albums, playlists, paged);
  artist pages (banner, biography, popular tracks, albums, singles, music videos, related artists); album pages
  (MusicBrainz track lists, Cover Art Archive covers); playlist pages; play, shuffle, save, download.
- **Videos**: YouTube search, video details (channel, date, views, chapters, captions, more from the channel),
  watch in YouTube's own player in a separate window, play audio only, download with MDM's existing dialog.
- **Radio**: countries like MImic - Albania first, + / - to add and remove countries (all directory countries,
  including Kosovo), MImic's checked stations first, then radio-browser.info; real stream tests ("Working" only
  after audio data arrived), favorites, recent, custom stream URLs, automatic reconnect.
- **Library**: folders you choose (and finished MDM downloads, indexed where they are - no copies); songs,
  albums, artists, videos, playlists, favorites, recently played, duplicates; search and sort; file information,
  open location, remove from library, delete (to the Recycle Bin, after confirmation).
- **Lyrics**: embedded lyrics, .lrc files next to the song, LRCLIB (lrclib.net) with a cache; synchronized lyrics
  highlight the current line and jump there when clicked. Timestamps are never invented.
- **Three-dot menu** on every song row with the actions that apply (play now/next, queue, download, playlist,
  favorite, artist, album, lyrics, share/copy link, file location/information, remove, delete).
- **Navidrome / OpenSubsonic** (optional): connect with token authentication (password stored with Windows
  DPAPI), browse artists, albums, playlists, starred songs, search, stream, star, save for offline playback.

### Changed
- Proprietary licensing: About window and Help → Legal Information show the
  copyright, owner, license type (Proprietary) and links; "Source Code
  Information" replaced by "Legal Information". Legal identity is edited in
  one place (`ytd/legal.py`) and carried into the installer and the exe's
  file properties.
- Installer license page rendered from a template; an Accept/Decline EULA mode
  is prepared and stays off until a reviewed EULA is approved.
- Update manifests state `architecture` (x64) and `minimum_windows`.
- Update verification also trusts an offline recovery key once one is shipped.
- When a Windows code-signing publisher is configured, the updater requires a
  valid Authenticode signature by it before running a downloaded installer.
- Third-party inventory: Roboto font (shipped inside CustomTkinter, Apache-2.0)
  added; fonts are now checked by the license gate.

### Not included
- YouTube Music's own song/album catalog isn't served in Albania; Music uses YouTube, YouTube Music artist pages
  and MusicBrainz instead.
- Google sign-in / sync with MImic: needs a Desktop OAuth client ID for MImic's Firebase project (not available).

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
