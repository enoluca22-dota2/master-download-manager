# Troubleshooting

First stop for any problem: **Tools → View Application Logs**. The log
(`%LOCALAPPDATA%\MasterDownloadManager\logs\app.log`) never contains your
security token or cookies, so you can attach it to a bug report
(**Help → Report a Bug**).

## Installing

**"Windows protected your PC" (SmartScreen).** The installer isn't
code-signed yet. Check the file's SHA-256 against `SHA256SUMS.txt` on the
release page (see [INSTALLATION.md](INSTALLATION.md)). If it matches, click
*More info → Run anyway*.

**"Setup has detected that Master Download Manager is currently running."**
Exit MDM from the tray icon (right-click → *Exit*), then click *Retry*.

**The shortcut or file association I unticked is still there.** Run the Setup
again and untick it: removed components are cleaned up on reinstall.

## Downloads

**A download failed.** Double-click it. The details window shows the reason
in plain words, a suggestion, and (under *View technical details*) the exact
error.
*Retry* resumes where it stopped whenever the server supports it.

**YouTube or another media site stopped working.** Sites change often. The
media engine (yt-dlp) is updated with new MDM releases, so check
**Help → Check for Updates**. Age-restricted or members-only videos need
cookies: Settings → Authentication.

**A direct download is slow.** Settings → Downloads: connections per download
(MDM adds connections only while they make the download faster). Also check
that no speed limit is active (status bar, **Tools → Speed Limiter**).

**A torrent doesn't start.** It needs peers. Check Settings → Torrent (port,
DHT) and that your firewall allows MDM. Magnet links wait for metadata from
peers before the file list appears.

**"Not enough free disk space".** Choose another folder or free up space. MDM
checks before it starts.

## Browser extension

**The extension says it can't connect.**
1. Is MDM running? The extension can't start it.
2. Is the token in the extension's options the same as in MDM (**Tools →
   Browser Integration…**)? Paste it again and click *Save Connection*.
3. Is the port the same as in MDM (default 47821)? Another program may be
   using that port: change it in MDM, then in the extension.

**Browser downloads aren't captured.** Capture is off by default: turn it on
in Settings → Browser Integration and choose the file types. Downloads that
need your login cookies stay in the browser on purpose.

After an MDM update, reload the extension in `chrome://extensions`.

## Updates

See [UPDATING.md](UPDATING.md#if-something-goes-wrong). If an update didn't
complete, the message after the next start shows where the installer log is
(`%LOCALAPPDATA%\MasterDownloadManager\updates\install-<version>.log`).
Running the new Setup by hand from the Releases page always works and keeps
your data.

## Settings and data

**Settings look wrong after a crash.** MDM keeps a copy of a damaged settings
file (`settings.json.corrupt-<time>`) and starts with defaults. Settings →
General → *Reset all settings* also helps. It keeps downloads, history and
the browser pairing.

**Moving to a new PC.** **Tools → Export Settings** on the old PC, **Import
Settings** on the new one. Export/import the download list via the **File**
menu. The history database is
`%LOCALAPPDATA%\MasterDownloadManager\download_history.sqlite3`.

**Starting completely fresh.** Uninstall and answer *Yes* when asked about
your data, or delete `%LOCALAPPDATA%\MasterDownloadManager` while MDM is
closed. Downloaded files are never affected.
