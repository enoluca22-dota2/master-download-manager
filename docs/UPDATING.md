# Updating Master Download Manager

## One-click update (from 4.1.0)

1. **Help → Check for Updates…** (or wait: MDM checks automatically - once a
   day by default - and tells you when there's something new).
2. The **Master Download Manager Update Available** window shows the installed
   version, the latest version, the release date, the download size and
   what's new. Buttons:
   * **Download Update** — downloads and verifies the update (with progress;
     *Cancel* stops it, *Try again* restarts it).
   * **Remind Me Later** — asks again about this version in 7 days.
   * **View Release Notes** — opens the release page on GitHub.
   * **Cancel**.
3. When the download is verified you see **Update Ready to Install**:
   * **Install Update** — if downloads are running, MDM asks first; then it
     pauses them, saves everything, closes, the installer upgrades it in place
     and the new version starts on its own and tells you the update succeeded.
   * **Cancel** — nothing is installed. The verified file is kept:
     **Settings → Updates → Install Downloaded Update** installs it later
     (it is checked again first).

Your settings, download history, queues, unfinished downloads, download
folders and the browser pairing are kept. Before installing, MDM backs them up
to `%LOCALAPPDATA%\MasterDownloadManager\updates\backup-<version>-<time>` (the
last three backups are kept).

Nothing is ever installed without you clicking *Install Update*.

## Where updates come from

**Settings → Updates → Update source**:

* **Public GitHub releases** (default) — the public download page
  `enoluca22-dota2/master-download-manager`. No account needed.
* **Private GitHub releases (token)** — a private repository that holds only
  installers (`enoluca22-dota2/mdm-private-releases` by default). Use this on
  your own PCs when a version is released privately:
  1. On GitHub: *Settings → Developer settings → Personal access tokens →
     Fine-grained tokens → Generate new token*. Repository access: **Only
     select repositories → mdm-private-releases**. Permissions: **Contents:
     Read-only**. Choose an expiry date.
  2. In MDM: choose *Private GitHub releases (token)*, paste the token, click
     **Save token**, then **Save**.
  The token is encrypted for your Windows account (Windows DPAPI) in
  `updates\github-token.dpapi`; it is never written to settings.json, never
  exported with your settings and only ever sent to `api.github.com`. *Remove*
  deletes it. When it expires, MDM says so - paste a new one.
* **Custom manifest URL** — for people who publish their own builds. Such
  updates aren't signed, so they are never installed automatically.

Every PC keeps its own settings, history and download folders; only the
program version is updated.

## What is checked

Every update must pass all of these, or it is not installed:

* it comes from the configured GitHub repository over HTTPS;
* it is newer than the installed version (MDM never downgrades);
* the release has a manifest signed with the project's release key, and the
  signature matches the public key built into MDM; the manifest names the
  same repository, version, installer name, address, size and SHA-256 as the
  release and its `SHA256SUMS.txt`, and is for x64 Windows;
* the downloaded file has exactly that size and SHA-256 before it gets its
  final name, and still has it right before it runs;
* a release that says it needs a newer starting version (`minimum_version`)
  is only offered as a manual download.

A release **without** a signed manifest (for example 4.0.0 and older) is
shown with an **Open Release Page** button instead of *Download Update*: you
can download and run it yourself after checking it. A release whose signed
information **doesn't verify** is shown as "Verification: FAILED — do not
install", with only *Close* and *Report a Problem*: it may have been tampered
with. Details: [UPDATE_SECURITY.md](UPDATE_SECURITY.md).

## If something goes wrong

* **The check fails** — MDM says why (no internet, GitHub unavailable, GitHub's
  request limit reached and when to retry, the token was rejected or lacks
  access) and nothing changes. The update check doesn't use the download proxy
  from Settings → Network; behind a proxy it follows the standard
  `HTTPS_PROXY` environment variable.
* **The download fails, is interrupted or corrupt** — the partial file is
  discarded and you can retry. Closing MDM during a download cancels it safely.
* **The installation fails** — Setup restarts your existing version, which is
  still in place; the next start shows "The update didn't complete" with the
  location of the installer log. If your settings file was damaged, it is
  restored from the backup automatically. You can also run the previous
  installer from the release page again: it installs over the top and keeps
  your data.
* **Not enough disk space** — MDM says how much is needed before downloading.

## Settings → Updates

* **Automatically check for updates** (on by default) and **Check every**
  6 hours / day / week.
* **Also check every time the app starts.**
* **Channel** — *Stable* or *Beta* (Beta also offers pre-releases).
* **Check for Updates**, the current version, the last successful check and
  its result.
* **View Release Notes** (the latest release found) and **Install Downloaded
  Update** (only when a verified download is waiting).
* **Update source** — see above.

## Updating from 4.3.0 or older to a private release

Versions before 4.4.0 can't read private releases. Once: sign in to GitHub,
open the private release page, download `Master.Download.Manager.Setup.<version>.exe`,
check it against `SHA256SUMS.txt` (`Get-FileHash <file>` in PowerShell), close
MDM and run it. It upgrades in place and keeps your data. From then on, set
up the token as above.

## Updating from 4.0.0 or older

Those versions can't install updates themselves. Download the latest
installer from the
[Releases page](https://github.com/enoluca22-dota2/master-download-manager/releases),
check it against `SHA256SUMS.txt`, and run it. It upgrades in place and keeps
your data.

## Command line

`MasterDownloadManager.exe --update-now` opens the update check right away;
add `--yes` to install a verified update without further clicks.
