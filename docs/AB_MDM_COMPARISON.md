# AB Download Manager vs Master Download Manager — comparison matrix

Date: 2026-10-23. Compared: **AB Download Manager 1.11.1** (source at
`amir1376/ab-download-manager@e329495`, browser integration
`amir1376/ab-download-manager-browser-integration` 1.5.0, plus the copy
installed at `%LOCALAPPDATA%\ABDownloadManager`, inspected read-only) against
**Master Download Manager 3.0.0** (the source that produced the installed
build; 292/292 tests passing at baseline).

AB is Kotlin + Compose Multiplatform (JVM runtime bundled, ~550 KB launcher +
`app/` + `runtime/`), Apache-2.0. MDM is Python 3.12 + CustomTkinter/Tk,
yt-dlp, libtorrent, FastAPI. No AB code is copied verbatim: AB's
*techniques* were studied and re-implemented in Python where they are the
better design (see `THIRD_PARTY_LICENSES.md` for attribution).

## Status legend

| Status | Meaning |
|---|---|
| ✅ MDM has it | Already implemented and working in MDM |
| 🔧 Improve | Exists in MDM but needs improvement |
| ➕ Missing | Not in MDM |
| 🅰 AB better | AB's implementation is better → adopt the technique |
| Ⓜ MDM better | MDM is already stronger → keep MDM's |
| ⛔ N/A | Not compatible or unnecessary |
| 🔎 Investigate | Needs further investigation |

Decision column: **Adopt** (implement AB's approach in MDM), **Keep** (keep
MDM's), **Build** (neither has it well — build independently), **Skip**.

## 1. Download engine (HTTP/HTTPS)

| Capability | AB 1.11.1 | MDM 3.0.0 | Status | Decision / notes |
|---|---|---|---|---|
| Parallel segmented download | `splitToRange()` into N parts, one `HttpPartDownloader` per part, `SparseFile` dest | Static segments (size/(conn·4)) into a pre-sized part file, worker pool | 🔧 | **Adopt** dynamic splitting (below) |
| Dynamic part splitting | When a connection frees up, halve the part with most remaining bytes, keeping a 1 MiB "safe zone" ahead of the writer (`PartSplitSupport`) | None — a slow last segment leaves other connections idle ("tail problem") | 🅰 | **Adopt**. Re-implemented: thread-safe split of the busiest live segment; writer stops at the segment's (possibly shrunk) end |
| Range / resume | Range + validates server info on resume (`FileChangedException`) | Range + `If-Range` (ETag/Last-Modified), restart from zero if size/ETag changed | Ⓜ | **Keep** MDM (If-Range is stronger: the server itself refuses mixing). Add Last-Modified comparison too |
| No-range fallback | Single blind part | Single connection, restart from 0 | ✅ | Keep |
| Atomic completion | Writes to final path (with `.abdm` incomplete suffix option) | `.mdm-part` → size verified → `os.replace` | Ⓜ | Keep |
| Persisted part state | `PartListFileStorage` (JSON per download) | `.mdm-part.json` next to the file | ✅ | Keep; **fix**: flush+fsync data before writing the state (state could otherwise claim bytes that never reached disk after a power loss) |
| Output file deleted while paused | `isDownloadedPartsIsValid()` → reset parts | Resets if part missing | ✅ | Keep |
| Retries | Per-part error counter; retry counter **resets when progress was made**; 3 s delay | Per-segment 5 retries w/ backoff; job-level auto-retry with backoff (2…30 s) for transient errors | 🔧 | **Adopt** "reset on progress" so a long download on a flaky link isn't failed by old errors |
| Speed limit | Global `SpeedLimiter` chained with per-job throttler | Per-job token bucket only (`speed_limit_kbps` applied *per download*) | 🅰 | **Adopt** global + per-job chain. Global limit is shared fairly across all HTTP connections |
| Connection count per download | `preferredConnectionCount`, changeable live | Global `http_connections` | 🔧 | **Build** per-download override in the New Download dialog |
| Checksum verification | Separate "File checksum" tool (manual) | None | ➕ | **Build** optional expected-hash check (SHA-256/SHA-1/MD5) before a download is marked complete, plus a checksum calculator in Properties |
| Filename detection | `fileNameExtractor` (CD + URL) | RFC 6266 incl. `filename*=`, sanitized, Windows reserved names | Ⓜ | Keep |
| Redirects / proxy / timeout | OkHttp; system/PAC/manual proxy | requests; manual proxy; timeout | ✅ | Keep (system proxy is inherited by requests via env) |
| HLS downloader | Native HLS downloader (`HLSDownloader`) | yt-dlp (HLS/DASH via generic extractor, AES-128 via pycryptodomex) | Ⓜ | Keep yt-dlp (handles DASH too, mux via FFmpeg) |
| Mirrors | No | No | ⛔ | **Skip**: needs per-mirror validation of identical bytes; documented as a limitation |
| Disk-full / permissions | `NoSpaceInStorageException`, prepare-dest failures | 100 MB floor + errno 28; `explain_error` | ✅ | Keep |

## 2. Download management

| Capability | AB | MDM | Status | Decision |
|---|---|---|---|---|
| Queues | Multiple named queues (`QueueModel`: name, maxConcurrent, ordered items, schedule, stopQueueOnEmpty); "Main" queue | One implicit queue; all Pending jobs auto-start | 🅰 | **Adopt** named queues. MDM's always-on behaviour is kept as the **Main** queue (zero regression); custom queues run when started manually or by schedule |
| Per-queue concurrency | Yes | Global `max_concurrent_downloads` | 🅰 | **Adopt** (global cap still applies) |
| Ordering | Swap up/down within queue | Up/down/top/bottom | ✅ | Keep + add drag-and-drop in the list |
| Priority | None (queue order only) | `priority` field unused | ➕ | **Build** High/Normal/Low with **aging** (waiting time raises effective priority) so low priority can't starve |
| Scheduler | Per-queue: weekdays + start time + stop time (`ScheduleTimes`) | None | 🅰 | **Adopt** + extend: per-download "start at", speed-limit profiles by time, missed-schedule & sleep/wake handling |
| Completion actions | Shutdown / Sleep / Hibernate with countdown alert (`PowerActionComponent`) | Notification only | 🅰 | **Adopt** (opt-in, 60 s cancellable countdown) + "Exit MDM" |
| Categories | Compressed/Programs/Videos/Music/Pictures/Documents by extension, custom categories, per-category folder | Library: Videos/Audio/Torrents/Archives/Other | 🅰 | **Adopt** extension-based categories; add Torrents + YouTube/Media; keep per-category folders |
| Status filters | Finished / Unfinished (+ sub-states) | All/Active/Completed/Failed | 🔧 | Merge: Finished, Unfinished, Failed in the tree |
| History | Download list DB (JSON files per item) | JSON file, 2,000 entries cap | 🔧 | **Build** SQLite history (unlimited, indexed search), migrated from JSON (original kept as backup) |
| Export / import | Export/import download list | None | ➕ | **Build** export/import (JSON/CSV) |
| Duplicate detection | `OnDuplicateStrategy` (rename/overwrite/abort) | `unique_path` auto-rename | 🔧 | **Build** duplicate warning in New Download (same URL already queued/downloaded) |
| Remove vs delete file | Prompt with "also delete file" | Remove keeps file; separate confirmed delete | Ⓜ | Keep MDM (safer default) |

## 3. Engines

| Capability | AB | MDM | Status | Decision |
|---|---|---|---|---|
| YouTube / media sites | None (only direct media captured by the browser) | yt-dlp: 1,750 extractors, quality tiers, MP3, subtitles, thumbnails, playlists, deno JS solver, bundled FFmpeg | Ⓜ | **Keep** — MDM is far stronger |
| Torrents | None | libtorrent 2.1.1: magnet, .torrent (file/URL), file selection, limits, seeding ratio | Ⓜ | **Keep** |
| Engine routing | Single HTTP/HLS | `router.classify/detect_engine` (torrent → media → http, probes ambiguous URLs) | Ⓜ | Keep; add explicit manifest (.m3u8/.mpd) → media rule |

## 4. Browser integration

| Capability | AB ext 1.5.0 | MDM ext 3.0.0 | Status | Decision |
|---|---|---|---|---|
| Context menu: download link/media | Yes | Yes (link / media / page) | ✅ | Keep |
| Download **selected** links | Yes (scans selection) | No | ➕ | **Adopt** |
| Capture browser downloads | `downloads.onCreated` → cancel + hand to app; file-type list, min size, blacklist, pass-through if app doesn't answer | No | 🅰 | **Adopt**, but **off by default** with a clear toggle (brief: never intercept silently) |
| YouTube in-player button, playlist picker | No | Yes (draggable, remembered position) | Ⓜ | Keep |
| Send headers/cookies | Sends request headers incl. cookies to the app | Referer only | 🔎 | Keep MDM's privacy default; cookies stay in the browser |
| Transport | HTTP API (optional key) + Native Messaging | HTTP API, mandatory token, origin + peer checks, 240 req/min limit | Ⓜ | Keep MDM's API; Native Messaging investigated — see ARCHITECTURE_UPGRADE.md (not adopted this release) |
| Choose queue / folder / name from browser | Via app dialog | Via app dialog (format popup on YouTube) | 🔧 | **Build** queue selection in the API + extension options |

## 5. UI

| Capability | AB | MDM | Status | Decision |
|---|---|---|---|---|
| Layout | Collapsible category tree + queues in sidebar, icon toolbar, table, status bar | Flat nav list, text buttons, ttk.Treeview with ASCII progress | 🅰 | **Adopt** structure; MDM branding kept |
| Table | Sortable, resizable, column chooser | Sortable, fixed columns, rebuild on structure change | 🔧 | **Build** virtualised canvas grid: real progress bars, icons, resizable/hideable columns, multi-select, drag reorder, only visible rows drawn |
| Themes | Dark/Light/Black + accent | Dark/Light monochrome | 🔧 | Add AMOLED + accent colour (Monochrome keeps the 3.0 look) |
| Properties / progress | Per-download window with parts map & speed graph | Details window with speed graph | 🔧 | Add connection/segment map |

## 6. Platform

| Capability | AB | MDM | Status | Decision |
|---|---|---|---|---|
| Installer | NSIS-style custom installer, per-user | Inno Setup, per-user, stable AppId, opt-in associations with backup/restore | Ⓜ | Keep Inno Setup; add components page (browser extension files), GitHub link, launch-after-install already present |
| Updater | Checks GitHub releases | Manifest + SHA-256, user-initiated, no default server | 🔧 | **Build** GitHub Releases source with SHA-256 from `SHA256SUMS.txt` |
| Single instance | Socket server | Named mutex + local API forward | ✅ | Keep |
| Runtime size | JVM bundle | Python bundle (+FFmpeg/deno) | ⛔ | No Kotlin/JVM introduced |
| Local API security | `127.0.0.1`, optional API key | `127.0.0.1`, mandatory token, extension-origin CORS, peer re-check, rate limit | Ⓜ | Keep MDM |

## 7. Risk assessment of adopted items

| Item | Benefit | Difficulty | Perf | Security | Reliability | Deps | License | Regression risk |
|---|---|---|---|---|---|---|---|---|
| Dynamic splitting | High on uneven servers | Medium | + | — | + (tested on local server) | none | own code | Medium → covered by tests |
| Global speed limit | High | Low | neutral | — | + | none | own code | Low |
| Named queues + priority | High | Medium | neutral | — | + persisted | none | own code | Medium (dispatcher) → Main queue keeps old semantics |
| Scheduler + power actions | High | Medium | idle timer 15 s | power actions opt-in + countdown | + | none | own code | Low |
| SQLite history | Medium-High | Medium | + for 1000s | parameterised SQL | + | stdlib sqlite3 | PSF | Medium → JSON kept as backup, API unchanged |
| Canvas data grid | High (UX) | High | + (virtualised) | — | — | none | own code | Medium → GUI smoke + manual |
| Download capture (ext) | High | Medium | — | opt-in, blacklist, no cookies | pass-through when app down | none | own code | Low (off by default) |
| GitHub updater | Medium | Low | — | HTTPS + SHA-256 | — | none | own code | Low |
