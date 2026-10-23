# Test report — Master Download Manager 4.0.0

Machine: Windows 11 Pro 10.0.26200, 12 logical cores, Python 3.12.10.
Date: 2026-10-23. Every number below was measured in this session; nothing is
estimated. "Packaged" means the PyInstaller build in `dist/` or the installed
app, not the Python source.

## 1. Summary

| Suite | Target | Result |
|---|---|---|
| Baseline before any change (3.0.0) | source | **292 / 292 passed** |
| Automated tests (`pytest tests`) | source | **352 / 352 passed** (final run) |
| GUI smoke (`tools/gui_smoke.py`) | real window, source | **25 / 25** |
| Real-world downloads (`tools/real_world_test.py`) | packaged 4.0.0, internet | **22 / 22** |
| Extension regression (`tools/browser_extension_test.py`) | packaged app + Chromium + YouTube | **15 / 15** |
| Extension 4.0 features (`tools/browser_capture_test.py`) | packaged app + Chromium | **8 / 8** |
| Installer lifecycle (`tools/installer_test.ps1`) | isolated installer variant | **23 / 23** |
| Queue load / churn (`tools/perf_queue.py`) | source engine | **6 / 6** |
| Real upgrade 3.0.0 → 4.0.0 of the installed app | release installer | **passed** (§6) |

## 2. Automated tests (352)

New in 4.0 (60 tests): `test_http_upgrades.py` (dynamic splitting, adaptive
connections, checksum, Last-Modified, global limit, retry reset),
`test_queues_scheduler.py` (queue CRUD/persistence, per-queue concurrency,
priority + aging, scheduled starts, completion events, DST/sleep/catch-up,
speed profiles, recurring downloads), `test_history_sqlite.py` (migration
from 3.x JSON, search/filters, export/import incl. CSV formula guard, 20k
rows), `test_api_v4.py` (auth on new endpoints, capture gating and probing,
batch validation, queues, checksum/priority/connections validation),
`test_extension_logic.py` (capture decision run in Node; manifests' permissions),
`test_settings.py` additions (BOM, corrupt file backup), router manifests.

Changed existing tests (intentional behaviour changes, not weakened checks):

| Test | Change | Why |
|---|---|---|
| `test_history_capped_at_max_entries` | patches the cap to 25 instead of inserting 2,010 rows | cap raised from 2,000 to 100,000 (SQLite) |
| `test_segmented_download_is_byte_exact`, `test_idle_connection_splits…`, `test_queue_connection_limit…` | set `http_adaptive_connections=False` | they test the fixed multi-connection mechanism; adaptive mode has its own tests |

### Flaky test observed

`tests/test_torrent_integration.py` (pre-existing, unchanged) failed in 2 of 7
full-suite runs — both times while another CPU-heavy job ran in parallel
(concurrent test runs / PyInstaller). The tests use fixed 10–90 s timeouts
against rate-limited local seeders. Run alone on an idle machine:
**5 / 5 runs × 26 tests passed**; all full-suite runs after 19:20 passed. Classified as
load-sensitive test timing, not a product defect.

## 3. Real-world downloads (packaged 4.0.0, internet)

All 22 checks passed: python.org zip intact; HTTP pause at 7.9 MB of 27.0 MB
and resume to the exact size; cancel removes partial files; 404 explained and
retryable; YouTube (Big Buck Bunny, CC-BY) formats listed with no missing-JS
warning, 360p merged to MP4, MP3 128 kbps; Sintel 29 subtitle languages, SRT
download and MKV embedding; Blender playlist (17 items) items 1–2 in order;
Debian 13.7 netinst `.torrent` URL parsed (792 MB), 13 peers at 5.5 MB/s,
pause and resume; "send link" auto-detects HTTP.

## 4. Performance (measured)

### 4.1 Connections (`tools/benchmark_connections.py --internet --repeat 3`, medians)

| Scenario | 1 | 2 | 4 | 8 | adaptive ≤ 8 (default) |
|---|---|---|---|---|---|
| python.org CDN, 26 MB | **1.82 s** | — | 2.07 s | 2.36 s | **1.85 s** |
| every connection capped ~2 MB/s, 24 MB | 12.88 s | 6.63 s | 3.37 s | **2.36 s** | 5.12 s |
| localhost, unthrottled, 48 MB* | 0.19 s | — | 0.43 s | 0.93 s | — |

Dynamic splitting (4 connections, 24 MB, one slow quarter): **2.10 s on vs
3.36 s off (1.60× faster)**.

\* The unthrottled-localhost row is limited by the single-process Python test
server, so it shows overhead, not real-world behaviour.

Conclusions stated in the app and docs: more connections are **not** always
faster. On a fast CDN one connection was best and a fixed 4 was 14% slower in this run (28% in an earlier run)
(8: 30% slower); against servers that cap each connection, 8 connections were
5.5× faster than one. The adaptive default matched the single connection on
the CDN and was 2.5× faster than one on the capped server, but slower than a
fixed 8 there on this short file (its ramp-up takes a few seconds).

### 4.2 UI and data

| Measurement | Result |
|---|---|
| 3,007-row download list: full rebuild | 71–73 ms |
| 3,007-row list: scroll redraw | 10–12 ms per frame |
| SQLite history, 20,000 rows: insert | 162 ms |
| SQLite history, 20,000 rows: search + filter + full read | 83 ms |
| Installed 4.0.0 idle (30 s, window open, nothing downloading) | 0.05 % of one core; 96 MB working set, 72 MB private; 14 threads |

### 4.3 Queue load and lifecycle (`tools/perf_queue.py`)

| Check | Result |
|---|---|
| 50 × 1 MB queued, max 10 concurrent | all byte-exact in 0.32 s (queued in 68 ms); peak concurrent ≤ 10 |
| Threads after the queue drains | 0 job/connection threads left |
| 30 pause/resume cycles on one 8 MB download | completed byte-exact (12.2 s) |
| 20 cancels mid-download | all cancelled, no partial files left |
| Threads after churn | none left (no zombie workers) |

## 5. Installer (`tools/installer_test.ps1`, isolated variant)

23/23: silent clean install (11 s); program, ffmpeg, deno, extension files,
licenses (incl. AB Download Manager's) present; Start-menu and desktop
shortcuts; one uninstall entry (4.0.0) with GitHub links; the **installed**
exe starts, its API answers and it downloads a 12 MB file byte-exact; history
database created; re-install with "program only" removes extension files and
the unticked desktop shortcut and keeps one uninstall entry and the user data;
silent uninstall removes program files (0 leftovers), shortcuts and the
uninstall entry, and keeps user data and downloaded files.

Wizard visuals checked: install-mode chooser and welcome page render in the
dark "windows11" style with MDM artwork. The remaining pages were exercised
through the silent install, not clicked through.

## 6. Real upgrade of the installed app (3.0.0 → 4.0.0)

Before: data folder backed up to `_backups/MDM-userdata-before-4.0-upgrade-2026-10-23`,
app idle (0 jobs), state fingerprinted.

| Check | Result |
|---|---|
| Release installer, silent, over 3.0.0 | exit 0 in 13 s; Setup closed and restarted MDM itself |
| Installed version / uninstall entries | 4.0.0 / exactly one |
| API token (extension pairing), port, download folder, theme | unchanged |
| `download_history.json` | byte-identical (kept as backup) |
| History in SQLite | 3 of 3 entries migrated, order kept |
| Shortcuts | Start menu + desktop kept; duplicate legacy "— Licenses" shortcut removed |
| App after upgrade | 4.0.0 running, API up on the same port, Main queue running |

## 7. Bugs found by testing and fixed before release

1. `settings.json` with a UTF-8 BOM was unreadable → defaults and a new token
   were saved over the user's file (found by the installer test). Fixed:
   `utf-8-sig` everywhere + backup of unreadable settings; regression tests added.
2. Installer: associations matched by exe name only, so another copy could
   remove this install's magnet/.torrent registration. Fixed (full path).
3. Installer: re-install with fewer components/shortcuts left them behind;
   empty extension folders created. Fixed and covered by the lifecycle test.
4. Installer script without BOM → non-ASCII text would be mangled. Fixed.
5. Default 4 fixed connections were 14–28% slower than 1 on a fast CDN (two runs). Fixed
   with adaptive connections (benchmark §4.1).
6. Batch list not shown, stale accent colour in dialogs, lost ttk styling,
   dialog title bars — found in screenshots, fixed, re-verified.

## 8. Not tested / needs manual validation

| Item | Why |
|---|---|
| Firefox extension in a real Firefox | Firefox isn't installed on this PC (environment). Syntax-checked and logic-tested in Node; same code as the Chrome build except the manifest/background loader |
| Clicking the real right-click menu entries | browsers don't expose extension context menus to automation; the menu handlers' functions were tested directly |
| Sleep / hibernate / shutdown actually executing | not triggered on this PC; the countdown and cancel path are tested, `power.perform` uses fixed Windows APIs |
| Real PC sleep/wake during a scheduled queue | simulated with clock intervals in unit tests |
| High-DPI at 150–200 % scaling | tested at this PC's scaling only; layout metrics scale with CustomTkinter's factor |
| Edge / Brave | same Chromium extension; not run separately |
| Clean install on a PC that never had MDM | covered by the isolated variant on this PC, not on a fresh machine |
| Code signing | no signing certificate is configured; artifacts are unsigned |
