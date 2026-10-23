# Master Download Manager — Project Audit

Audit date: 2026-10-03. Scope: `YTd-w-v1.2` (the existing application, which
becomes Master Download Manager) and `yt-dlp-master` (upstream yt-dlp source,
used as the media-capability base). Everything below was established by
reading source and running code on this machine, not from READMEs alone.

A backup of `YTd-w-v1.2` as found (excluding `build/` and `dist/`) was saved
to `New folder/_backups/YTd-w-v1.2-before-MDM-2026-10-03.tar.gz` before any
change, because the repository holds ~1,800 lines of uncommitted work plus
untracked `ytd/`, `tests/` and extension folders.

---

## 1. YTD-w-v1.2 — current state

### 1.1 Overview

| Item | Finding |
|---|---|
| Product name in code | "ENAGRIO — Universal Download Manager" (window title, tray, API, extension) |
| Language / runtime | Python 3.12 (Microsoft Store build on this PC) |
| Entry point | `youtube_downloader.py` → `YTDApp(ctk.CTk())`, `--minimized` flag for tray start |
| UI framework | CustomTkinter 6.0 + a `ttk.Treeview` for the download list |
| UI architecture | One 1,465-line module: toolbar, left category rail, Treeview list, status bar, Add dialog (YouTube / Torrent tabs), details window, settings dialog, clipboard prompt |
| Design system | `ytd/gui/theme.py` (palette, radii, glass widget factories), `effects.py` (atmosphere image, dither glass, spinner, toasts), `backdrop.py` (Windows 11 Mica via DWM) |
| Core package | `ytd/` — framework-agnostic, thread-safe |
| Tests | 131 pytest tests, **all passing** at audit time (8.6 s, no network) |
| Version | `ytd.__version__ = "2.0.0"` |
| Git | 1 commit; most current work uncommitted |

### 1.2 Architecture (as found)

```
youtube_downloader.py (GUI, Tk main thread)
        │ subscribe(callback) — events marshalled with root.after()
        ▼
ytd/engine.py  DownloadManager ──── dispatcher thread ──► one worker thread per job
        │  find_provider(url)
        ▼
ytd/providers/  MediaProvider interface
   ├─ youtube.py  YouTubeProvider  → yt_dlp.YoutubeDL (library API, in-process)
   └─ torrent.py  TorrentProvider  → libtorrent session per job
ytd/api/server.py  FastAPI on 127.0.0.1:47821 (uvicorn daemon thread) ── same DownloadManager
ytd/settings_store.py (settings.json) · ytd/history.py (download_history.json)
ytd/logging_setup.py · notifications.py (win11toast) · tray.py (pystray) · startup.py
extension-chrome/ extension-firefox/ (MV3) → local API with X-Enagrio-Token
```

The provider/engine split is sound and is kept: every provider implements
`supports / detect / resolve_format_selection / run_download(job, hook)` and
reports progress through the same hook dict shape, so the engine, API and
GUI are engine-agnostic. This is the foundation the new engines plug into.

### 1.3 Components

**Queue / download engine (`ytd/engine.py`)** — in-memory list of
`DownloadJob`; dispatcher wakes on an Event and starts jobs up to
`max_concurrent_downloads`; pause/resume/cancel via per-job Events checked in
the progress hook (raising `PauseRequested` / `DownloadCancelled`); manual
retry; automatic retry with exponential backoff for transient errors;
rolling 8-sample speed and ETA; disk-space precheck (100 MB floor);
move up/down; pause/resume/cancel all; `clear_finished`. Statuses:
Queued, Downloading, Paused, Completed, Failed, Cancelled.

**YouTube (`ytd/providers/youtube.py`)** — URL regex limited to
youtube.com/youtu.be. `detect()` collapses real formats into resolution tiers
(only tiers that exist), offers MP3 128–320 re-encodes, "source audio",
best thumbnail, manual subtitles. `build_download_opts()` maps a job to
yt-dlp options (`bestvideo[height<=H]+bestaudio`, mp4 merge, MP3 via
`FFmpegExtractAudio`, `continuedl`, 10 MB `http_chunk_size`, 8 concurrent
fragments). `detect_playlist()` uses flat extraction.

**Torrent (`ytd/providers/torrent.py`)** — real libtorrent 2.1.1:
magnet (`parse_magnet_uri`, DHT) and local `.torrent` files; 45 s metadata
timeout; per-file selection (one file or all); progress from
`total_wanted_done`; pause = tear down session, keep data (libtorrent
re-checks on resume); cancel keeps partial data.

**HTTP/direct downloader** — **does not exist.** Any non-YouTube http(s)
URL is rejected with "This media cannot be downloaded".

**Local API (`ytd/api`)** — `/health`, `/version`, `/connection`,
`/media/detect`, `/formats`, `/playlist/detect`, `/downloads` CRUD +
pause/resume/cancel/retry, `/system/disk-space`, `/settings`. Bound to
127.0.0.1, peer address re-checked, token header, CORS only for
`chrome-extension://` / `moz-extension://`, 240 req/min rate limit.

**Browser integration (`extension-chrome`, `extension-firefox`)** —
`content.js` (1,083 lines, identical in both builds) injects a draggable
"Download video" button *inside the YouTube player* (works in
fullscreen/theater); position persisted as a fraction of the player size;
"×" dismisses for the browser session (stored via the background script in
`storage.session`); SPA-aware (`yt-navigate-finish`, popstate, `<title>`
observer, de-duplication, re-homing on player swap); free-floating,
draggable format popup with remembered position; Video/Playlist tabs when
the URL has `list=`; playlist tab lists entries with checkboxes and queues
one job per checked entry; connection check distinguishes "app not
running" from "token mismatch"; stale-extension-context detection; per-site
blocklist; toolbar popup shows connection, site status, last 5 downloads.
The full `location.href` (including `list=` and `index=`) is what gets sent.

**Settings** — `settings.json` beside the script/exe; token generated and
persisted on first run. **History** — `download_history.json`, newest
first, capped at 200, rows seeded into the main list at startup.

**FFmpeg** — not bundled; `shutil.which("ffmpeg")` or a settings path.
The app warns when missing.

**Packaging** — `build.py` (PyInstaller `--onedir --windowed`, name
"Enagrio", `--collect-all yt_dlp/libtorrent/customtkinter`, deletes the
duplicate winrt CRT DLLs that crash libtorrent). `YTD.spec` is stale (name
YTD, icon path from another folder). `dist/YTD/` is an older build.
No installer. **Updater** — none. **Icons** — `enagrio.ico`,
`build_icons.py` generates app + extension icons with Pillow.

### 1.4 Existing bugs and gaps found

| # | Problem | Impact |
|---|---|---|
| B1 | `refresh_list()` deletes and rebuilds every Treeview row on **every** engine event (progress fires many times/s per job) | UI lag/freeze with many or long playlists |
| B2 | A history "Failed" entry is written on every auto-retry attempt, even if a later attempt succeeds | Misleading history |
| B3 | `job.resumable = d.get("fragment_index") is None or True` — always True | Dead logic |
| B4 | Remote `https://…/x.torrent` URLs are routed to the torrent provider, which only opens local files | Always fails ("Torrent file not found") |
| B5 | Only YouTube URLs accepted; the other ~1,750 yt-dlp extractors are unreachable | Large missing capability |
| B6 | No JS runtime available to yt-dlp — verified: yt-dlp warns *"YouTube extraction without a JS runtime has been deprecated, and some formats may be missing"* | Fragile YouTube support |
| B7 | FFmpeg not bundled | Merging/MP3 fail on a clean PC |
| B8 | settings/history/log written beside the exe | Fails when installed under Program Files |
| B9 | Queue is memory-only | Unfinished downloads lost on restart |
| B10 | Cancelling a media download leaves `.part`/`.ytdl` files | Disk clutter |
| B11 | `extension-shared/ytd-client.js` out of sync with the copies in each extension | Maintenance trap |
| B12 | Build pulls in the global site-packages (mypy, numpy, cryptography in `dist/YTD/_internal`) | Bloated, unreproducible bundle |
| B13 | "Start with Windows" shortcut targets `python.exe youtube_downloader.py` even in the frozen build | Broken in packaged app |
| B14 | Log redaction replaces any whole line containing "token"/"cookie"…, but does not redact secrets inside URLs | Hides useful lines; misses real secrets |
| B15 | `schemas.py` claims save paths are restricted; `save_dir` is passed through unvalidated | Doc/behaviour mismatch |
| B16 | Windows reserved names (CON, NUL, COM1…) not handled by filename sanitizer | Rare failed writes |
| B17 | GUI "This is a playlist" downloads the whole playlist as one job (progress resets per video, one history row) | Poor playlist UX |

### 1.5 Must-not-break features

YouTube detect/download (video tiers, MP3, source audio, thumbnail,
subtitles); torrent magnet + `.torrent` with file selection; queue with
concurrency, pause/resume/cancel/retry/reorder; local API contract used by
the extension; extension button injection, dragging, remembered positions,
dismiss, playlist tab, `list=`/`index=` preservation, connection
diagnostics; tray, notifications, clipboard monitor, start-with-Windows;
glass theme / Mica; settings + history persistence; the libtorrent-before-
win11toast DLL load order (a real crash fix).

---

## 2. yt-dlp-master — architecture study

Version **2026.08.19** (`yt_dlp/version.py`), identical to the yt-dlp
already installed in this Python, so the audited source is exactly what
ships. License: **Unlicense** (public domain dedication).

| Area | How it works (source) |
|---|---|
| Core | `YoutubeDL.py`: params dict → `extract_info()` → `process_ie_result()` → format selection → `dl()` → post-processors. Fully usable as a library; progress via `progress_hooks`/`postprocessor_hooks`; logging via a `logger` object |
| Extractors | `extractor/` — 1,751 `InfoExtractor` classes; `suitable(url)` regex match; `GenericIE` last (direct links, embeds, HTML5 media). Plugin system (`plugins.py`) loads extra extractors/PPs from plugin dirs |
| URL detection | iterate `gen_extractor_classes()`, first `suitable()` wins; measured ≈60 ms/URL here |
| Format selection | selector mini-language (`bv*+ba/b`, filters `[height<=720]`, `/` fallbacks), `format_sort` (e.g. `vcodec:h264`, `res`, `abr`), `check_formats` |
| Playlists / channels | `_type: playlist`, `entries` generator, `extract_flat='in_playlist'` for fast listing, `playlist_items` ("1,3,5-7"), `lazy_playlist`; YouTube channels/tabs via `youtube:tab` |
| Subtitles | `subtitles` (manual) and `automatic_captions`; `writesubtitles`, `writeautomaticsub`, `subtitleslangs`, `subtitlesformat`; `FFmpegSubtitlesConvertor` (srt/vtt/ass/lrc), `FFmpegEmbedSubtitle` (mp4/mov/m4a/webm/mkv/mka) |
| Thumbnails / metadata / chapters | `writethumbnail`, `EmbedThumbnail` (ffmpeg/mutagen), `FFmpegMetadata` (tags, chapters, info.json attach) |
| Sections | `download_ranges` + `force_keyframes_at_cuts` |
| Post-processing | `FFmpegExtractAudio` (mp3/aac/m4a/opus/vorbis/flac/alac/wav), `FFmpegMerger`, `FFmpegVideoRemuxer`/`Convertor`, fixups, `SponsorBlock`, `ModifyChapters`, `Exec` |
| Downloaders | `downloader/`: native HTTP (resume via Range, `http_chunk_size`), fragment base (HLS/DASH/ISM, `concurrent_fragment_downloads`, `fragment_retries`, resume via `.ytdl` state), ffmpeg/rtmp/websocket/external (aria2c etc.) |
| Network | `networking/`: request director with urllib/requests/curl_cffi/websockets handlers, proxies (http/socks), impersonation, `socket_timeout`, `source_address` |
| Rate limiting / retries | `ratelimit`, `throttledratelimit`, `retries`, `fragment_retries`, `extractor_retries`, `retry_sleep_functions` |
| Auth / cookies | `cookiefile`, `cookiesfrombrowser` (chrome, edge, brave, chromium, opera, vivaldi, whale, firefox, safari), username/password/netrc per extractor |
| JS requirements | YouTube "n"/signature challenges solved by EJS scripts run in an external JS runtime (`extractor/youtube/jsc`). Core solver is vendored in yt-dlp; the lib script comes from the `yt-dlp-ejs` package; runtime: deno (default), node, bun, quickjs. `remote_components` (`ejs:github`, `ejs:npm`) would download scripts at runtime — **must stay disabled** |
| Errors | `DownloadError`, `ExtractorError` (`expected`), `GeoRestrictedError`, `UnavailableVideoError`, `DownloadCancelled`, etc. |
| Updates | `update.py` self-updates from GitHub releases — **not used** (library usage never invokes it) |
| Cache | `cachedir` (signature/player cache) — will be pointed at MDM's own data dir |

---

## 3. Integration strategy (decision)

**Chosen: Option A — integrate the yt-dlp package internally, built from the
audited local `yt-dlp-master` source, behind MDM's own Media Engine
adapter.**

Why not the others:

- **Vendoring a subset (B/C)**: extractors share `utils`, `networking`,
  `downloader`, `postprocessor`, and the JS-challenge machinery; carving out
  a subset means maintaining a fork of ~250k lines with no way to take
  upstream extractor fixes (sites change weekly). Copying the whole tree
  into `ytd/` is exactly the "blind copy" the brief rules out.
- **Subprocess wrapper around a yt-dlp.exe (D)**: loses structured progress,
  in-process cancellation and info dicts; adds another updater and identity.

What Option A means concretely:

1. The build installs yt-dlp **from the local `yt-dlp-master` folder** into
   an isolated build virtualenv and PyInstaller bundles it. Nothing is
   fetched from GitHub at runtime; the user never installs Python or pip.
2. All yt-dlp usage stays behind `ytd/providers/` (the Media Engine), so the
   rest of the app sees MDM's own `MediaInfo` / `FormatInfo` / `DownloadJob`.
3. YouTube JS challenges: bundle `yt-dlp-ejs` (Unlicense) and a `deno`
   binary (MIT), pass `js_runtimes={'deno': {'path': <bundled>}}` and
   `remote_components=[]`.
4. FFmpeg/FFprobe bundled as separate executables from an LGPL build,
   passed via `ffmpeg_location`; settings override and PATH fallback kept.
5. yt-dlp's updater, CLI and config files are never used; `cachedir` and
   all output go to MDM-controlled paths.

### Target architecture

```
Master Download Manager (master_download_manager.py → ytd/gui)
│
├── Application core: ytd/engine.py DownloadManager (unified queue, statuses,
│   persistence, retry policy, history hooks)  +  ytd/router.py (engine detection)
├── Engines (ytd/providers/, one interface)
│   ├── media.py    Media Engine (yt-dlp: extractors, formats, playlists,
│   │               subtitles, metadata, post-processing)  — evolves youtube.py
│   ├── torrent.py  Torrent Engine (libtorrent; .torrent file/URL, magnet)
│   └── http.py     Direct HTTP/HTTPS Engine (new)
├── Local API (ytd/api) → browser extensions (preserved, rebranded)
├── History / Library, Settings, Logging (per-subsystem files), Security helpers
├── Tools resolution (bundled ffmpeg/ffprobe/deno)
└── Packaging (build.py, clean venv, PyInstaller, installer) / Updates (ytd/updater.py)
```

## 4. Proposed changes by stage

| Stage | Changes |
|---|---|
| 3 Media | Generalise YouTube provider to any yt-dlp extractor; real format table + presets (only existing heights) + Best/Worst/Custom selector; audio extraction to formats the bundled ffmpeg can encode; containers/codec preference; subtitles (manual + auto, multi-language, srt/vtt/ass, embed); thumbnail/metadata/chapter embedding; playlist/channel listing with item selection; cookies (browser/file); network options; bundled ffmpeg/deno resolution |
| 4 Queue | `engine` field per job; Pending/Preparing/Downloading/Processing/Completed/Failed/Paused/Cancelled/Retrying(/Seeding); history only on final outcome; retry-failed, clear-completed, move top/bottom; queue persistence; temp-file cleanup on cancel; result path tracking |
| 5 Torrent | keep engine; add remote `.torrent` URLs, multi-file selection, seeds/peers, rate/connection limits, optional seeding to a ratio |
| 6 HTTP | new engine: streaming, Range resume with validators, retries, redirects, Content-Disposition names, disk checks, pause/cancel, speed limit, multi-segment for large range-capable files |
| 7 UI | rebrand to Master Download Manager; smart Add dialog (auto engine detection); unified list with engine column and incremental updates; filters Active/Completed/Failed + Library categories; details with technical error view/copy |
| 8 Browser | keep content script behaviour; rebrand; accept old and new token header; context-menu "Download with MDM" for links/magnets/pages; single source for the shared client |
| 9 Settings/History | sectioned settings window with only implemented options; library categories, open file/folder, redownload, remove (file deletion only on explicit request); updater with manifest + SHA-256 verification, user-initiated |
| 10 Packaging | clean build venv, yt-dlp from local source, bundled tools, licenses folder, `MasterDownloadManager.exe`, installer |
| 11–12 | automated + real-world tests, final audit |

## 5. Risks

- **YouTube changes** break extraction regularly; mitigated by building from
  a newer yt-dlp source and shipping an MDM update (no runtime GitHub fetch).
- **Browser cookies**: Chromium-based browsers lock/encrypt their cookie DB
  (app-bound encryption in recent Chrome) — reading may fail while the
  browser runs; Firefox and cookie files are reliable.
- **BitTorrent** may be blocked by the network; magnet metadata needs DHT/peers.
- **Bundle size**: ffmpeg + ffprobe + deno add roughly 150–250 MB.
- **MSVC runtime DLL conflict** between libtorrent and winrt (known, fix kept).
- **Microsoft Store Python** on this PC: venv/PyInstaller must be validated.
- **Legal**: downloading is only appropriate for content the user may
  download; MDM does not circumvent DRM (yt-dlp refuses DRM formats).

## 6. Licensing considerations (summary — full detail in THIRD_PARTY_NOTICES.md)

yt-dlp and yt-dlp-ejs are Unlicense (no attribution required; credited
anyway). FFmpeg LGPL/GPL build → ship license text + source location,
invoked as a separate process. libtorrent BSD-3 (+ Boost BSL-1.0,
OpenSSL Apache-2.0 inside the wheel). pystray is LGPL-3.0 — shipped as an
unmodified, replaceable module in the onedir build. certifi is MPL-2.0.
deno MIT. Python PSF-2.0. All texts are collected into `licenses/` and
bundled with the app.
