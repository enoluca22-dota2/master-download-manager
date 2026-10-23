# Updating Master Download Manager

## One-click update (from 4.1.0)

1. **Help → Check for Updates…** (or wait: MDM checks GitHub at most once a
   day while it runs, and tells you when there's something new).
2. The **Master Download Manager Update Available** window shows the installed
   version, the latest version, the release date, the download size and
   what's new. Buttons:
   * **Update Now** — downloads and verifies the update (with progress).
   * **Remind Me Later** — asks again about this version in 7 days.
   * **View Release Notes** — opens the release page on GitHub.
   * **Cancel**.
3. When the download is verified you see **Update Ready to Install**:
   * **Install and Restart** — MDM pauses running downloads, saves
     everything, closes, the installer upgrades it in place and the new
     version starts on its own and tells you the update succeeded.
   * **Later** — nothing is installed. The verified file is kept until a
     successful update removes it.

Your settings, download history, queues, unfinished downloads and the browser
pairing are kept. Before installing, MDM backs them up to
`%LOCALAPPDATA%\MasterDownloadManager\updates\backup-<version>-<time>` (the
last three backups are kept).

Nothing is ever installed without you clicking *Update Now*.

## What is checked

Every update must pass all of these, or it is not installed:

* it comes from the official GitHub repository
  (`enoluca22-dota2/master-download-manager`) over HTTPS;
* it is newer than the installed version (MDM never downgrades);
* the release has a manifest signed with the project's release key, and the
  signature matches the public key built into MDM;
* the installer's name, download address, size and SHA-256 match the signed
  manifest and the release's `SHA256SUMS.txt`;
* the downloaded file still has that SHA-256 right before it runs.

A release **without** a signed manifest (for example 4.0.0 and older) is
shown with an **Open Release Page** button instead of *Update Now*: you can
download and run it yourself after checking it. A release whose signed
information **doesn't verify** is shown as "Verification: FAILED — do not
install", with only *Close* and *Report a Problem*: it may have been tampered
with. Details for the technically
curious: [UPDATE_SECURITY.md](UPDATE_SECURITY.md).

## If something goes wrong

* **The check fails** ("Couldn't reach GitHub") — nothing changes; try again
  later. The update check doesn't use the download proxy from Settings →
  Network; behind a proxy it follows the standard `HTTPS_PROXY` environment
  variable.
* **The download fails or is corrupt** — it's discarded and you can retry.
* **The installation fails** — Setup restarts your existing version, which is
  still in place; the next start shows "The update didn't complete" with the
  location of the installer log. If your settings file was damaged, it is
  restored from the backup automatically.
* **Not enough disk space** — MDM says how much is needed before downloading.

## Settings → Updates

* **Automatically check for updates** (on by default; at most once a day).
* **Also check every time the app starts.**
* **Channel** — *Stable* or *Beta* (Beta also offers pre-releases).
* **Check Now**, and the current version, the last check and its result.
* *Advanced:* a custom manifest URL, for people who publish their own builds.
  Such updates aren't signed, so they are never installed automatically.

## Updating from 4.0.0 or older

Those versions can't install updates themselves. Download
`Master.Download.Manager.Setup.4.1.0.exe` from the
[Releases page](https://github.com/enoluca22-dota2/master-download-manager/releases),
check it against `SHA256SUMS.txt`, and run it. It upgrades in place and keeps
your data.

## Command line

`MasterDownloadManager.exe --update-now` opens the update check right away;
add `--yes` to install a verified update without further clicks.
