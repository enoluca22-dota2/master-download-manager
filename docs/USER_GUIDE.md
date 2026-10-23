# Master Download Manager — User Guide

Master Download Manager (MDM) downloads three kinds of things in one window:

* **Direct files** — any `http(s)` link to a file: multi-connection,
  resumable, with an optional checksum check.
* **Media sites** — videos, audio, playlists and channels from YouTube and
  about 1,700 other sites (built on yt-dlp), with real format and subtitle
  choices.
* **Torrents** — magnet links, `.torrent` files and `.torrent` URLs, with file
  selection.

Only download content you have the right to download. DRM-protected media is
not supported.

## Adding downloads

* **File → New Download… (Ctrl+N)**, or copy a link and press **Ctrl+V** in the
  main window. MDM analyses the link and shows what the source really offers:
  media formats, a playlist's items, a torrent's files, or a file's name, size
  and resume support. Choose a folder and a queue, then *Download*.
* **File → Add Multiple URLs… (Ctrl+Shift+N)** — paste many links at once.
  Duplicates and links already downloaded are flagged.
* **File → Open Torrent File… (Ctrl+O)** and **Add Magnet Link… (Ctrl+Shift+M)**.
* **File → Import Download List… (Ctrl+I)** — a text file with one link per
  line (or a list exported by MDM). **Export Download List… (Ctrl+E)** saves
  the links of the current list.
* From the browser — see [BROWSER_INTEGRATION.md](BROWSER_INTEGRATION.md).
* **Tools → Clipboard Monitoring** — when on, MDM offers to download links you
  copy. Off by default.

## The main window

* **Sidebar** — categories (All, Compressed, Programs, Videos, Music,
  Pictures, Documents, Torrents, Media sites), states (Finished, Unfinished,
  Failed) and your queues, each with a count.
* **Toolbar** — New Download, Paste URL, Resume, Pause, Start/Stop Queue,
  Queues, Stop All, Delete, Settings.
* **Download list** — click a column header to sort (a third click restores
  queue order); right-click a header to show or hide columns; drag rows to
  reorder; Ctrl/Shift-click to select several.
* **Right-click a download** — open, open with, show in folder,
  pause/resume/restart, move to queue, priority, speed limit, schedule start,
  copy link or path, properties, view error.
* **Status bar** — total ↓/↑ speed, the active speed limit and the version.

Removing a download from the list never deletes its file. Deleting a file is a
separate action that always asks first.

## Menus

| Menu | What's there |
|---|---|
| **File** | New Download, Add Multiple URLs, Open Torrent File, Add Magnet Link, Import/Export Download List, Open Downloads Folder (Ctrl+J), Download History (Ctrl+H), Settings (Ctrl+,), Minimize to Tray, Exit (Ctrl+Q) |
| **Tasks** | Start/Pause/Resume selected (Ctrl+P / Ctrl+R), Retry failed, Start All, Pause All, Stop All, start/stop the current queue, Queue Manager, Download Scheduler, Change Priority, Move to Queue, Remove, Open file, Show in folder |
| **Tools** | Browser Integration, Clipboard Monitoring, Speed Limiter, Network settings, Default folders, Scheduler, Queue Manager, Check File Integrity, Import/Export Settings, View Application Logs, Open Data Folder, Settings |
| **Help** | GitHub, Report a Bug, Request a Feature, the guides (F1 = this guide), Open-Source Licenses, Third-Party Notices, Source Code Information, Check for Updates, What's New, Changelog, About |

Items that can't be used right now (for example *Pause Selected* with nothing
selected) are greyed out. Keyboard: Alt+F, Alt+T, Alt+O, Alt+H open the menus.

## Queues and the scheduler

* The **Main** queue always runs. Create more queues in **Tasks → Queue
  Manager…**, each with its own number of simultaneous downloads and
  connections, a weekly start/stop time, "stop when empty", and an optional
  completion action (notify, exit, sleep, hibernate, shut down — always after a
  60-second countdown you can cancel).
* **Download Scheduler** — speed-limit profiles by time of day and recurring
  downloads.
* **Priorities** — High, Normal, Low. Waiting downloads gain priority over
  time, so nothing waits forever.

## Speed limits

* **Tools → Speed Limiter → Limit download speed** switches a global limit on
  and off (also in the tray menu). The limit is shared fairly between running
  downloads.
* Per-download limits are in the right-click menu.

## History

**File → Download History… (Ctrl+H)** lists every finished, failed and
cancelled download, with search and a status filter. You can open the file,
its folder, download it again or remove the entry (the file stays).

## Checking a file

**Tools → Check File Integrity…** computes the MD5, SHA-1, SHA-256 or SHA-512
of any file and compares it with a value you paste (for example from a
project's download page).

## Settings

**File → Settings… (Ctrl+,)**. *Import/Export Settings* (Tools menu) moves
your preferences between PCs. The browser security token is never exported.
*Settings → General → Reset all settings* restores the defaults and keeps
downloads, history and the browser pairing.

## Updates

**Help → Check for Updates…** — see [UPDATING.md](UPDATING.md).

## Your data

Settings, history, queues and logs are in `%LOCALAPPDATA%\MasterDownloadManager`
(**Tools → Open Data Folder**). Logs never contain your security token or
cookies. More help: [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
