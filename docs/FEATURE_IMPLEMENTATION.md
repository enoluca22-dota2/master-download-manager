# Feature implementation — Master Download Manager 4.0.0

What each requirement of the upgrade brief became, where it lives, and how it
was verified. "Kept" means MDM 3.0 already did it and it was preserved and
re-tested. Test names refer to `tests/` (pytest), `tools/gui_smoke.py` (real
window), `tools/real_world_test.py` (packaged app, real internet targets),
`tools/installer_test.ps1` and `tools/benchmark_connections.py`.

## Download engine (HTTP/HTTPS) — `ytd/providers/http.py`

| Requirement | Status | Implementation | Verified by |
|---|---|---|---|
| Multiple parallel / segmented connections | New + improved | pre-sized `.mdm-part`, disjoint segments, one thread per connection | `test_segmented_download_is_byte_exact` |
| Configurable connections | New | per download (Add dialog / API `options.connections`) → per queue → Settings | `test_connections_option_overrides_setting`, `test_queue_connection_limit_…` |
| Adaptive connections | New | starts with 1, doubles each second while throughput +15 % | `test_adaptive_*`, benchmark |
| Dynamic splitting | New (from AB) | `take_work()` splits the busiest segment beyond a 1 MiB safe zone | `test_idle_connection_splits_the_slow_segment`, benchmark (1.6× faster) |
| Range requests, fallback without ranges | Kept | `probe_url` + single-connection path | `test_server_without_range_support_…` |
| Pause/resume, If-Range, ETag | Kept | | `test_pause_then_resume_…` (both modes) |
| Last-Modified validation | New | `_server_file_changed` | `test_last_modified_change_restarts` |
| Temp files, atomic completion | Kept | `.mdm-part` → size/checksum verified → `os.replace` | `test_existing_file_is_never_overwritten` |
| Interrupted-download recovery | Improved | segment state fsync'd before being saved | `test_split_state_survives_pause_and_resume`, real-world pause/resume |
| Retries + exponential backoff | Improved | counter resets after progress; failed segment returns to the pool | `test_retry_counter_resets_after_progress` |
| Timeouts, redirects, filename detection | Kept | requests session, RFC 6266 names, reserved names | `test_filename_*`, `test_probe_*` |
| Content-Length verification | Kept | final size check before rename | |
| Optional checksum | New | `parse_checksum` (MD5/SHA-1/SHA-256/SHA-512); mismatch discards | `test_checksum_verified_and_mismatch_discards`, `test_parse_checksum_forms` |
| Mirrors | Not implemented | needs byte-identical guarantees across hosts — see limitations | — |
| Disk-full / permissions / expired URL / invalid response | Kept | `_check_disk`, `errors.explain_error` | `test_disk_space_check`, 404 test |
| Never mark incomplete as complete | Kept + checksum | size (and optional hash) gate the rename | |

## Speed management — `ytd/bandwidth.py`

| Requirement | Status | Implementation |
|---|---|---|
| Global limit | New | one `RateLimiter` shared by every HTTP connection (`test_global_limit_is_shared_by_concurrent_downloads`) |
| Per-download limit | Kept, now live | `job.rate_limiter`, changeable while running (context menu → Speed limit) |
| Torrent upload/download limits | Kept | Settings → Torrent; the session also honours the global cap |
| Scheduled speed profiles | New | Queues & Scheduler → Speed profiles (`test_speed_profiles_drive_global_limit`) |
| Tray speed-limit toggle | New | tray "Limit download speed" = limited mode (`limited_mode_kbps`) |
| Fair sharing | New | shared bucket for HTTP; equal share of the cap for yt-dlp jobs |
| Reduced bandwidth while the PC is in use | Not implemented | needs idle detection; profiles + limited mode cover the use case |

## Queues and scheduler — `ytd/queues.py`, `ytd/scheduler.py`, `ytd/engine.py`

| Requirement | Status |
|---|---|
| Named queues: create / rename / delete / add to | New — Queues window, sidebar "+", context menu "Move to queue", API `/queues` |
| Drag-and-drop ordering | New — main list (queue order view) and the queue's Downloads tab |
| Priority without starvation | New — High/Normal/Low + aging (`test_high_priority_jumps_ahead`, `test_aging_prevents_starvation`) |
| Sequential / parallel per queue | New — per-queue "Downloads at the same time" (`test_per_queue_concurrency`) |
| Per-queue connection limit | New |
| Queue start/stop, start/stop all | New — toolbar, sidebar menu, tray, API (`test_stop_queue_pauses_and_start_resumes`) |
| Persistent queue state across restart | New — `queues.json` + `queue.json` (`test_queue_fields_survive_restart`) |
| Start/stop at a time, weekdays | New (`test_schedule_*`, `test_scheduler_starts_stops_and_catches_up`) |
| Recurring downloads | New (`test_recurring_download_fires_once_per_slot`) |
| Individual scheduled downloads | New — context menu "Schedule start…", API (`test_scheduled_single_download_waits_for_its_time`) |
| Completion actions (notify / exit / sleep / hibernate / shutdown) | New — opt-in, 60 s cancellable countdown (`test_stopping_a_queue_never_fires_its_completion_action`, GUI smoke) |
| Sleep/wake, missed schedules, DST, restart | New — interval-based ticks (`test_dst_spring_forward…`, sleep case in scheduler test) |

## Engines and routing

| Requirement | Status |
|---|---|
| YouTube video/playlist/MP3/quality 360p–4K/metadata/thumbnail/subtitles | Kept (yt-dlp 2026.08.19) — 22/22 real-world checks on the packaged app |
| Torrents: magnet, .torrent, file selection, limits, seeding, resume | Kept (libtorrent 2.1.1) — integration tests + a real Debian torrent |
| HLS / DASH / direct media | Kept via yt-dlp; `.m3u8`/`.mpd` route to the media engine (`test_streaming_manifests_route_to_media_engine`) |
| Provider router | Kept — torrent → media site → direct file → probe |
| DRM | Not bypassed (yt-dlp refuses DRM formats; the UI says so) |

## Browser integration — `extension-chrome/`, `extension-firefox/`, `ytd/api/`

| Requirement | Status |
|---|---|
| Download with MDM (link / media / page) | Kept |
| Download selected links | New |
| Capture ordinary downloads | New, opt-in, pause → hand over → cancel only on success |
| Detect media, YouTube button & playlist picker | Kept |
| Send to a specific queue | New (extension options) |
| Choose folder / filename | API supports `save_dir`/`filename` (validated); in the app via the Add dialog |
| Authenticated local API, rate limit | Kept |
| Native Messaging | Investigated, not adopted (see ARCHITECTURE_UPGRADE.md §6) |
| Open MDM automatically | Not possible without native messaging; the popup reports "app not running" |

## UI — `ytd/gui/`

| Requirement | Status |
|---|---|
| Sidebar with categories, counts, Finished/Unfinished, Queues | New |
| Toolbar: New Download, Paste, Resume, Pause, Start/Stop Queue, Queues, Stop All, Delete, Settings (state-aware) | New |
| Table columns: name, type, size, downloaded, progress, status, speed, time left, connections, added, completed, source (+engine, queue) | New; sortable, resizable, hideable (right-click header) |
| Multi-select + batch actions | New (Ctrl/Shift, Ctrl+A) |
| Status bar: active, ↓/↑ speed, queued, limit | New |
| Dark / Light / AMOLED Black / System, accent colours, Mica title bar | New |
| High-DPI | CustomTkinter scaling; grid/sidebar scale their pixel metrics; vector icon font |
| Smart New Download (file / media / torrent / batch, duplicates) | Kept + batch + duplicates + queue/connections/checksum |
| Context menu: open, open with, folder, copy link/path, retry, pause, resume, restart, move to queue, priority, speed limit, properties, error, remove, delete file (confirmed) | New/kept |
| Details: progress graph, connections, segment map | Kept + new |

## History, settings, Windows integration

| Requirement | Status |
|---|---|
| SQLite history, search, filters (status/category/date), export/import, duplicates | New (`test_history_sqlite.py`) |
| Settings sections incl. Appearance, Bandwidth, Queues & Scheduler, Browser Integration, History & Storage, Updates | New |
| Safe reset (keeps downloads, history, extension pairing) | New |
| Installer: welcome, info, components, shortcuts, progress, finish, launch, GitHub link | New (`installer_test.ps1`) |
| Upgrade in place, data preserved, single uninstall entry | Kept/verified |
| Secure update check (GitHub + SHA-256) | New |
| Language settings | Not implemented — the app is English only |
