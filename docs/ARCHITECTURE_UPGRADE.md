# Architecture upgrade — Master Download Manager 3.0 → 4.0

## 1. Shape of the application (unchanged foundations)

MDM stays a single Python 3.12 process with one framework-agnostic core and
a CustomTkinter/Tk GUI. The 3.0 split between engines, queue, local API and
GUI was sound and is kept; 4.0 adds modules around it rather than replacing it.

```
master_download_manager.py   single-instance mutex, second-launch forwarding, logging, Tk root
│
├── ytd/engine.py            DownloadManager — one list of jobs, dispatcher, retries, history, persistence
│     ├── queues.py          NEW named queues (queues.json) + weekly Schedule model
│     ├── scheduler.py       NEW 15-s tick: queue start/stop, speed profiles, recurring downloads
│     ├── bandwidth.py       NEW global + per-download token buckets, limited mode, profiles
│     ├── categories.py      NEW file-type categories for the sidebar
│     ├── history.py         SQLite (was JSON, capped at 2,000)
│     └── power.py           NEW opt-in sleep / hibernate / shutdown
├── ytd/providers/           engines behind one interface (supports / detect / run_download)
│     ├── http.py            segmented HTTP engine — 4.0: dynamic splitting, checksum, fsync'd state
│     ├── media.py           yt-dlp media engine (unchanged behaviour; now honours the global cap)
│     └── torrent.py         libtorrent engine (unchanged behaviour; now honours the global cap)
├── ytd/router.py            URL → engine (torrent → media site → direct file → probe)
├── ytd/api/                 FastAPI on 127.0.0.1 — 4.0: /queues, /downloads/batch, /browser/config
├── ytd/updater.py           4.0: GitHub Releases source verified against SHA256SUMS.txt
└── ytd/gui/                 CustomTkinter windows
      ├── app.py             REWRITTEN main window (toolbar, sidebar, grid, status bar)
      ├── datagrid.py        NEW virtualised Canvas table
      ├── sidebar.py         NEW category tree + queues
      ├── queues_window.py   NEW queues, schedules, speed profiles, recurring downloads
      ├── power_dialog.py    NEW cancellable countdown
      └── add_dialog.py, settings_window.py, details.py   extended
```

## 2. Download engine

### 2.1 Segmented HTTP (ytd/providers/http.py)

| Step | 3.0 | 4.0 |
|---|---|---|
| Initial split | size / (connections × 4) fixed-size segments | `split_to_range(size, min_part, connections)` — one segment per connection (as AB's `splitToRange`) |
| Idle connection | stops | `take_work()` splits the live segment with the most bytes left: the new segment starts at the midpoint of what lies beyond `SAFE_ZONE` (1 MiB) ahead of the busy writer, never below `2 × SEGMENT_MIN_SIZE` |
| Writer/split race | — | the writer re-reads its segment end under the lock before every chunk and truncates the chunk to the room left; a split can only move the end beyond `current + SAFE_ZONE`, and `CHUNK` (256 KiB) < `SAFE_ZONE`, so an in-flight write can never cross into the new segment |
| Failed connection | one failure stopped every connection | the segment goes back to `pending`; other connections continue; the job only fails if nobody can finish it |
| Retry counter | per attempt | reset whenever the attempt received data (AB's rule), so a long download over a flaky link isn't failed by stale errors |
| State durability | JSON written every 2 s | data is `fsync`ed through a monitor handle **before** the snapshot is written: the state never claims bytes that aren't on disk |
| Validation | size + ETag; `If-Range` | + Last-Modified comparison; optional SHA-256/SHA-1/MD5/SHA-512 checksum before the rename; a mismatch discards the data and fails the job |
| Limits | one per-download bucket | `bandwidth.consume_all([global, per-download])` reserves from both and sleeps once for the longer wait |
| Connections | global setting | per download (Add dialog / API) → per queue → global setting |

Atomic completion is unchanged: `<name>.mdm-part` is only renamed into place
after its size (and checksum) is verified.

### 2.2 Bandwidth (ytd/bandwidth.py)

`BandwidthManager.effective_kbps()` returns the lowest of the global limit,
the active scheduled profile and limited mode, with the reason (shown in the
status bar). HTTP downloads share one `RateLimiter`, so the cap is divided
fairly among all connections. yt-dlp and libtorrent can't draw from a Python
bucket: media downloads get `cap / active downloads` as yt-dlp's `ratelimit`
when they start; the shared libtorrent session gets the cap as its session
download limit.

## 3. Queues and scheduler

* **Queue model** (`queues.json`): id, name, `max_concurrent`,
  `max_connections`, `running`, `stop_when_empty`, `on_complete`, `schedule`
  (days, start/stop time + enable flags). The **Main** queue always exists and
  is forced to `running` at startup, which is exactly MDM 3.0's behaviour.
* **Dispatch** (`DownloadManager._next_candidate`): among Pending jobs whose
  queue is running, whose queue has a free slot and whose `start_at` has
  passed, pick the highest `priority + waited / AGING_SECONDS` (600 s), ties
  by list order. Priorities are High (+1) / Normal (0) / Low (−1), so a Low
  download that waited 20 minutes overtakes a fresh High one: no starvation.
* **Queue complete**: when a running queue that did work has nothing Pending,
  running or retrying, `queue_complete` fires once. A user-stopped queue
  never fires it, so stopping a queue can never trigger a shutdown.
* **Scheduler**: a 15-second tick compares the wall-clock interval since the
  previous tick against each schedule (`Schedule.events_between`) and applies
  the last event in it. This makes it correct after sleep/hibernate (the
  interval spans the sleep), when the clock goes back (empty interval: no
  double fire) and on DST spring-forward (the skipped time is inside the
  interval). On the first tick a start that was missed while MDM wasn't
  running is caught up if "now" is inside the start→stop window.
* **Power actions** are only performed from the GUI after a 60-second
  `PowerCountdown` (Cancel focused, Esc/close also cancel); the engine first
  pauses and persists all downloads.

## 4. History (ytd/history.py)

SQLite with WAL, a `history` table indexed by url/status/category/date and a
`meta` table. The public API (`all/add/get/remove/remove_by_id/clear`) is
unchanged, so the engine and GUI code didn't change; new: `search`,
`count`, `find_by_url`, `export` (JSON/CSV with spreadsheet-formula
neutralisation), `import_file`. On first open the 3.x
`download_history.json` is imported once (order preserved, ids added to old
entries) and **left untouched** as a backup. Measured: 20,000 rows insert in
0.16 s, search + filter + full read in 0.08 s.

## 5. User interface

### 5.1 Framework decision

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| Keep CustomTkinter, replace the Treeview | no rewrite of ~200 KB of working dialogs; small bundle; same threading model | must draw the table ourselves | **Chosen** |
| PySide6 / Qt | QTableView + delegates, native high-DPI | full GUI rewrite, ~+100 MB bundle, LGPL compliance for Qt, high regression risk | rejected for 4.0 |
| Kotlin/Compose (AB's stack) | — | introduces a JVM runtime and a second codebase | rejected (brief) |

The only real limitation was `ttk.Treeview` (no progress bars or icons and
slow full rebuilds), so only the list was replaced: `DataGrid` draws on a
Canvas and only renders visible rows. Measured on this PC with 3,007 rows:
full rebuild 71 ms, scroll redraw 10 ms per frame.

### 5.2 Theme

`theme.apply_palette(mode, accent)` rewrites the shared `COLORS` table
(Dark, Light, Black/AMOLED, System; accents Indigo/Blue/Teal/Rose or
Monochrome = the 3.0 look) and the main window rebuilds its widgets; the
download engine keeps running. Icons are glyphs of Windows' own Segoe Fluent
Icons font (no bundled icon assets, crisp at any DPI). The title bar follows
the theme through DWM (`DWMWA_USE_IMMERSIVE_DARK_MODE`, and on Windows 11
`DWMWA_CAPTION_COLOR`).

## 6. Browser integration

* **Transport**: unchanged local HTTP API on 127.0.0.1 with the mandatory
  token, extension-origin CORS, loopback re-check and 240 req/min limit.
* **Native Messaging — investigated, not adopted in 4.0.** AB ships a
  separate `ABDownloadManagerNativeMessagingHost.exe`. For MDM it would need a
  second executable (or a console-subsystem mode of the windowed PyInstaller
  build, whose stdio handles aren't usable), per-browser host manifests in
  the registry (HKCU\Software\Google\Chrome\NativeMessagingHosts, the Edge
  and Mozilla equivalents) written and removed by the installer, and allowed
  extension ids pinned in each manifest. Its main gain over the token-protected
  local API is launching MDM when it's closed. The extension instead degrades
  gracefully: when MDM isn't running, links can't be sent (the popup says so)
  and captured downloads stay in the browser. Recorded as a follow-up.
* **New**: "Download selected links" (collects `<a href>` and URL/magnet text
  inside the selection via `scripting.executeScript` on the clicked tab only,
  sends one `/downloads/batch`); optional download capture
  (`downloads.onCreated` → `ytdShouldCapture` → pause → `/downloads/auto`
  with `origin: capture` → cancel+erase only if MDM accepted, else resume);
  queue choice. MDM refuses capture unless enabled in its own settings and
  probes captured links itself, refusing any that return a web page or an
  error — those need the browser's session, which MDM never receives.
* **Permissions**: `storage activeTab contextMenus downloads scripting`,
  host access only to `http://127.0.0.1/*`. No `cookies`, `webRequest` or
  `<all_urls>`. AB's extension requests `cookies`, `webRequest`, `tabs`,
  `nativeMessaging` and `*://*/*`.

## 7. Packaging and updates

* Inno Setup kept (per-user, same AppId `{6F2E9C1B-…}` → in-place upgrade,
  `AppMutex` makes setup wait for MDM to close). 4.0 adds the modern
  dark-aware wizard with MDM artwork, a components page (browser extension
  files optional), Start-menu/desktop shortcut tasks, an information page,
  project links and an optional "open GitHub" finish action. MSI/NSIS were
  not needed: Inno already provides upgrade-in-place, association backup and
  restore, and per-user install without admin.
* Data in `%LOCALAPPDATA%\MasterDownloadManager` is never touched by setup or
  uninstall. New files: `queues.json`, `scheduler.json`,
  `download_history.sqlite3`.
* Updater: Settings → Updates (or a weekly check if enabled) reads the latest
  GitHub release, takes the installer asset and its hash from the release's
  `SHA256SUMS.txt`, downloads and verifies size + SHA-256, and only then
  offers to run it. Nothing installs silently.

## 8. Compatibility and migration

| Data | 3.0 | 4.0 behaviour |
|---|---|---|
| settings.json | flat keys | read as-is; new keys take defaults |
| download_history.json | JSON list | imported once into SQLite; file kept |
| queue.json (unfinished downloads) | v1 | read as-is; jobs without `queue_id` land in Main |
| API token / port | — | unchanged → paired extensions keep working |
| Extension (3.0) against app 4.0 | — | all 3.0 endpoints unchanged |
| `X-Enagrio-Token` header (YTD-era extension) | accepted | still accepted |
