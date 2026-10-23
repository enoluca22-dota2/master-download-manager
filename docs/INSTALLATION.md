# Installing Master Download Manager

## Requirements

* Windows 10 or 11, 64-bit.
* About 450 MB of free disk space for the program (the Setup shows the exact
  figure for the components you choose).
* No administrator rights needed for the default per-user install.

## Download

Get `Master.Download.Manager.Setup.<version>.exe` from the
[Releases page](https://github.com/enoluca22-dota2/master-download-manager/releases).
To check the file, compare its SHA-256 with the release's `SHA256SUMS.txt`:

```powershell
Get-FileHash "Master.Download.Manager.Setup.4.1.0.exe" -Algorithm SHA256
```

The installer and the program are **not code-signed** yet (see
[LICENSING.md](LICENSING.md#code-signing)). Windows SmartScreen may show
"Windows protected your PC". Click *More info* → *Run anyway* only if you
downloaded the file from the official Releases page and its checksum matches.

## The Setup wizard

1. **Welcome** — what will be installed.
2. **License Agreement** — copyright notice, the terms under which the program
   is provided, and the third-party open-source components it contains. You
   are not asked to accept a contract on this page (see
   [LICENSING.md](LICENSING.md)).
3. **Choose Components** — each item shows a description and the space it
   needs:
   * **Master Download Manager** (required) — the program, its download
     engines, FFmpeg, documentation and licenses.
   * **Start Menu shortcut**
   * **Desktop shortcut**
   * **Start with Windows** — starts minimized in the notification area when
     you sign in. Off by default.
   * **Browser Integration** — copies the Chrome/Edge/Firefox extension
     folders into the installation (`extensions\`). Loading them into a
     browser is a separate step: [BROWSER_INTEGRATION.md](BROWSER_INTEGRATION.md).
   * **File Associations** (off by default):
     * *.torrent files* — adds MDM to "Open with" and to Windows Default apps.
       Your current default app for `.torrent` is kept; Windows lets you
       choose.
     * *magnet: links* — opens magnet links in MDM. The previous handler is
       backed up and restored when you uninstall.
4. **Installation Location** — new installs only. The default is
   `%LOCALAPPDATA%\Programs\Master Download Manager`. Upgrades keep the folder
   that is already in use.
5. **Installing** — real progress; *Show details* lists every file.
6. **Completing** — *Run Master Download Manager* (ticked) and *Open GitHub
   Project*.

After installing you have one entry in *Settings → Apps → Installed apps*
with the version number and an uninstaller.

## Upgrading

Running a newer Setup over an existing installation upgrades it in place:
same folder, same Installed Apps entry, and your settings, download history,
queues, unfinished downloads and browser-extension pairing are kept. Your
component choices from the last install are pre-selected. If MDM is running,
Setup asks to close it first.

The easiest way to upgrade is **Help → Check for Updates** in the app (see
[UPDATING.md](UPDATING.md)).

## Silent install (administrators)

```
Master.Download.Manager.Setup.4.1.0.exe /SILENT /COMPONENTS="app,startmenu,desktop"
```

`/VERYSILENT` hides the progress window; `/DIR="path"` changes the folder;
`/LOG="file"` writes a log. `/ALLUSERS` (as administrator) installs for all
users under Program Files. One-click updates of such an installation need
administrator rights, so the per-user default is recommended. Component names: `app`, `startmenu`, `desktop`,
`autostart`, `browser`, `assoc\torrent`, `assoc\magnet`.

## Where things are

| What | Where |
|---|---|
| Program | `%LOCALAPPDATA%\Programs\Master Download Manager` (default) |
| Settings, history, queues, logs | `%LOCALAPPDATA%\MasterDownloadManager` |
| Downloads | the folder you choose (default: your Downloads folder) |
| Documentation | `docs\` in the program folder, and Help → Documentation |
| Licenses | `licenses\`, `THIRD_PARTY_NOTICES.md`, Help → View Open-Source Licenses |

To remove the program see [UNINSTALLATION.md](UNINSTALLATION.md).
