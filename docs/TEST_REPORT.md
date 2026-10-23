# Test report — Master Download Manager 4.1.0

Machine: Windows 11 Pro 10.0.26200, Python 3.12.10. Date: 2026-10-24.
Every result below was produced in this session. "Packaged" means the
PyInstaller build in `dist/` or the installed app, not the Python source.
The 4.0.0 report is in the git history (`git show v4.0.0:docs/TEST_REPORT.md`).

## 1. Summary

| Suite | Target | Result |
|---|---|---|
| Automated tests (`pytest tests`) | source | **373 / 373 passed** (final run, 88 s) |
| 4.1 GUI checks (`tools/gui_smoke41.py`) | real window, source | **46 / 46** (3 consecutive runs green) |
| 4.0 GUI regression (`tools/gui_smoke.py`) | real window, source | **25 / 25** |
| License release gate (`collect_licenses.py --verify`) | packaged build | **passes**; blocks 5 / 5 injected faults and a stale build |
| Installer lifecycle (`tools/installer_test.ps1`) | isolated installer variant, final build | **27 / 27** |
| Setup wizard + uninstaller UI (`tools/installer_wizard_test.py`) | isolated variant, final build | **14 / 14** |
| One-click update, end to end (`tools/update_e2e_test.py`) | real installers, installed app, local fake GitHub | **22 / 22** |
| Signed manifest of the release vs. key bundled in the app | `dist/` | **verifies** |
| Real upgrade 4.0.0 → 4.1.0 of the installed app | release installer, wizard | **passed** (§7) |
| Real-world downloads (`tools/real_world_test.py`) | packaged 4.1.0, internet | **22 / 22** |
| Extension regression (`tools/browser_extension_test.py`) | packaged 4.1.0 + Chromium + YouTube | **15 / 15** |
| Extension capture (`tools/browser_capture_test.py`) | packaged 4.1.0 + Chromium | **8 / 8** |
| Queue load / churn (`tools/perf_queue.py`) | source engine | **6 / 6** |

## 2. Automated tests (373)

New in 4.1 — `tests/test_update41.py` (19 tests, against a local fake GitHub,
no internet):

* no update when the latest equals the installed version; newer stable
  release found and verified (size, notes, date);
* an **older release is never offered** (no downgrade);
* **beta** releases only on the beta channel;
* **signature from another key** → shown, not installable;
* release **without a signed manifest** → shown, `prepare` refuses it;
* manifest SHA-256 ≠ `SHA256SUMS.txt` → not verified;
* **missing installer asset** → error; release from **another repository**
  → refused; release **without `html_url`** → refused;
* **plain `http://`** refused outside a test profile; GitHub unreachable →
  clean error;
* **interrupted download** (connection dropped halfway) and **corrupt
  download** (same size, other bytes) → refused, nothing left behind;
* `prepare` backs up settings and the SQLite history (backup API) and
  releases the update lock; `launch` passes `/SILENT … /MDMUPDATE=1
  /WAITPID=<pid> /LOG=…`; an installer **modified after verification** is
  refused;
* a **second update attempt** while another process holds the lock is
  refused; a stale lock from a dead process is taken over;
* post-update check: success, failure (reported once), **damaged settings
  restored from the backup**;
* not enough disk space → clear error; the shipped public key loads.

Flaky run observed: one full run had 3 torrent-integration failures
(including `test_pause_resume_and_cancel_torrent`) and took 247 s instead of
~88 s; the same tests passed alone and in the next full run. They depend on
local peer timing under load; no torrent code changed in 4.1.

## 3. GUI (real window)

`tools/gui_smoke41.py` (46 checks, screenshots):

* menu bar File / Tasks / Tools / Help; **all 65 menu entries are wired or
  deliberately disabled** (0 without an action); 18 of them invoked, each
  opens its window (Add Multiple URLs, History, Queue Manager, Scheduler,
  Check File Integrity, Logs, the four guides, Licenses, Notices, Source
  Code Information, What's New, Changelog, About);
* status bar shows the central version; About shows version and copyright;
* Licenses window: 61 components, search filters, sort toggles (natural
  version order), every component has a license text, details window;
* bundled docs readable; What's New shows the 4.1.0 section;
* update dialog against the fake GitHub: "up to date"; "Update Available"
  with exactly *Update Now / Remind Me Later / View Release Notes / Cancel*;
  download + verification → "Update Ready to Install"; *Install and
  Restart* hands over; **bad signature** → "Verification: FAILED — do not
  install", only *Close / Report a Problem*; **unsigned** release → *Open
  Release Page*, no *Update Now*; *Remind Me Later* snoozes that version;
  *View Release Notes* opens the release page (browser calls are captured,
  nothing is opened);
* every Settings section opens; Settings → Updates shows version, last
  check and status.

`tools/gui_smoke.py` (4.0 regression, 25 checks) still passes, including
the 3,007-row list: rebuild 71 ms, scroll 12.1 ms per frame.

## 4. License gate

`tools/collect_licenses.py --verify` runs inside `build.py exe` and compares
the inventory with PyInstaller's actual output (`PYZ-00.toc`,
`COLLECT-00.toc`, dist-info folders, native DLL markers). Negative test on the
packaged build: removing `requests` from the inventory, a wrong Pillow
version, an unknown license id for OpenSSL and an empty author for Deno gave
**5 findings, exit 1 ("release blocked")**; an inventory that differs from the
repository's (stale build) is also blocked. Restored → passes.

## 5. Installer

`tools/installer_test.ps1` (isolated variant: own name, AppId, mutex and data
folder), **27 / 27**: clean install with components
`app,startmenu,desktop,browser`; program, FFmpeg, deno, extensions, licenses,
notices, docs, license inventory and update key installed; no
Start-with-Windows shortcut unless chosen; one Installed Apps entry with
version 4.1.0 and links; the installed app starts and downloads a file
byte-exact; re-install with fewer components removes the extension files and
the desktop shortcut, still one entry, data untouched; silent uninstall
removes program, shortcuts and entry and keeps data and downloaded files.

`tools/installer_wizard_test.py` drives the real wizard (Win32 messages,
screenshots), **14 / 14**: opens directly on **Welcome** (no "for me / all
users" question), **License Agreement** (information, no acceptance
checkbox), **Choose Components** (descriptions, space required),
**Installation Location** (new install; last page, *Install* button),
**Installing** with *Show details* (file list), **Completing** with *Run*
(ticked; the app started) and *Open the GitHub project page* (unticked).
Interactive uninstall: the data question appears with **No** as the default
button (screenshot); answering **Yes** removed MDM's settings, queue,
history, corrupt-settings copies, logs and update backups, and **kept** a
`my downloads\movie.mkv` and `notes.txt` placed in the data folder.

Upgrade from 4.0.x: shortcut choices stored as 4.0 *tasks* carry over to the
4.1 *components*. Checked with an isolated install whose registry says
"Start Menu yes, Desktop no": the Components page showed Desktop unticked,
Start Menu and Browser ticked.

## 6. One-click update, end to end

`tools/update_e2e_test.py` with real installers (test variant 4.1.0 → 4.2.0,
plus a 4.3.0 built to fail), a throw-away signing key and a local fake
GitHub, **22 / 22**:

1. 4.1.0 installed silently and running; a real download completed.
2. Closed normally; started with `--update-now --yes`: it found 4.2.0,
   verified the signed manifest, downloaded and checked it, backed up its
   state and exited (5 s).
3. Setup waited for the PID, upgraded in place and started 4.2.0: **4.2.0
   answering 21 s after the update began**.
4. Installed Apps entry 4.2.0 (one entry); result "updated from 4.1.0 to
   4.2.0"; installer log present; downloaded installer removed; backup
   present; settings (token, custom values) unchanged; the earlier download
   still listed through the API with the same token (browser pairing kept);
   history rows unchanged; downloaded file byte-identical; same folder.
5. Started again: already up to date, no second update.
6. **Failing update** (4.3.0 Setup fails before installing): 4.2.0 started
   again **7 s** later and reported "The update to 4.3.0 didn't complete;
   version 4.2.0 is still installed and your data is unchanged"; Installed
   Apps entry still 4.2.0; settings unchanged.

## 7. Real upgrade of the installed app (4.0.0 → 4.1.0)

Before: backups of the data folder and of the 4.0.0 program folder in
`_backups/` (not in git). MDM 4.0.0 was no longer running at that point; its
data had been saved cleanly at 01:20:48. The logs don't show why it exited
(4.0 doesn't log exits, and none of the test installers' logs show them
closing applications).

`Master Download Manager Setup 4.1.0.exe` was run through the wizard
(Welcome → License → Components, preselected as before: program, Start Menu,
Desktop, Browser → Install → Finish with *Run*):

| Check | Result |
|---|---|
| Installed Apps: one entry, version 4.1.0, same folder | pass |
| Components recorded `app,startmenu,desktop,browser` (from 4.0's tasks) | pass |
| 4.1.0 started by *Finish*; API answers with the existing token | pass |
| Earlier settings (61 keys) | unchanged except `update_last_check`: 4.1 ran its first automatic check, "Up to date (4.1.0)", against the real GitHub (latest stable was 4.0.0) |
| History rows | 3 → 3 |
| Desktop and Start Menu shortcuts | present |
| `magnet:` handler (Ant Download Manager, the user's choice) | untouched |
| Docs, license inventory, update key, extensions 4.1.0 | installed |

## 8. Real-world and browser tests (packaged 4.1.0, isolated profile)

`tools/real_world_test.py` **22 / 22**: direct HTTP (python.org zip intact;
pause at 8 MB, resume to the exact 26,964,224 bytes; cancel removes the
partial file; 404 gives a readable reason and can be retried); YouTube (Big
Buck Bunny: 19 real formats, no "missing JavaScript runtime" warning, 360p
video merged to MP4, MP3 128 kbps); Sintel subtitles (29 languages, SRT,
embedded in MKV); playlist (17 items; items 1–2 in order into a playlist
folder); Debian netinst torrent (8 peers, 6.9 MB/s, pause, resume); "send
link" auto-detects HTTP.

`tools/browser_extension_test.py` **15 / 15** (connection, YouTube player
button, drag and remembered position, real formats, playlist tab, context
menu, token mismatch). `tools/browser_capture_test.py` **8 / 8** (capture of
chosen types, browser copy cancelled, other types left alone, selected
links in one batch, browser keeps the download while MDM is closed).

The browser test tools crashed on the first try when printing "✓" to a
cp1252 console; rerun with `PYTHONIOENCODING=utf-8`. That's a test-tool
issue, not an app issue.

## 9. Bugs found by testing and fixed before release

| Found by | Bug | Fix |
|---|---|---|
| GUI checks (hang) | Python's cyclic garbage collector ran in a background thread (update check) and freed a Tk font there → that thread deadlocked; the update dialog stayed on "Checking…" | automatic GC off; collection runs on the Tk thread every second (`app._collect_garbage`) |
| GUI checks | Settings → Licenses crashed with the new inventory format | shared inventory loader |
| GUI checks | callbacks bound with `lambda e:` were sometimes called without an event (TypeError) | `lambda e=None:` |
| Screenshot review | Licenses search box showed no placeholder; versions sorted as text | no `textvariable`; natural sort |
| Screenshot review | bad-signature releases offered "Open Release Page" as the main action | "FAILED — do not install", *Report a Problem* |
| Wizard test | "Select Setup Install Mode" dialog before Welcome; all-users installs would need elevation for updates | per-user in the wizard; `/ALLUSERS` only on the command line |
| Wizard test | Location page before Components; an extra "Information" page after installing; a separate "Ready" page; duplicated "Click Next/Finish" lines | own Location page after Components, *Install* on the last page, texts fixed |
| Code review | uninstall "remove data" deleted the whole data folder (anything a user put there) | removes only MDM's own files and folders |
| Code review | Setup failing before copying files wouldn't restart the old version | relaunch folder read from the registry at start |
| Code review | 4.0 users' "no desktop shortcut" choice would be lost on upgrade | tasks carried over to components |
| Code review | loopback `http://` update URLs accepted outside test profiles; release without `html_url` skipped the repository check | both refused |
| Installer test | stale paths to license files after the inventory rework | fixed in docs and test |
| Packaging review | `_backups/` (personal data) was not git-ignored | ignored |
| Docs review | THIRD_PARTY_NOTICES listed outdated versions (e.g. FastAPI 0.142.2 vs 0.161.1 bundled) and pywin32 as PSF | synced with the inventory |

## 10. Not tested / limits

* Authenticode: the build is unsigned (no certificate); the signing hook
  wasn't exercised.
* The in-app update from the real GitHub can be exercised only once a
  version after 4.1.0 exists; it was tested against a local fake GitHub with
  real installers.
* Upgrading an **all-users** (`/ALLUSERS`) installation; behind a proxy; on
  Windows 10.
* Setup killed mid-copy (power loss): the next start reports "didn't
  complete"; files may be mixed until Setup is run again.
