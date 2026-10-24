# Master Download Manager

A Windows download manager for **direct file downloads** (multi-connection,
resumable), **media sites** (video, audio, playlists on YouTube and about
1,700 other sites) and **BitTorrent** (magnet links and `.torrent` files),
with queues, a scheduler, speed limits and a browser extension.

This repository is the official **download and update page**. It holds the
installers, release notes, signed update information, documentation and the
licenses of the open-source components used. The program's source code is
proprietary and is not published here.

## Download

Get **`Master.Download.Manager.Setup.<version>.exe`** from
[Releases](https://github.com/enoluca22-dota2/master-download-manager/releases/latest).

* Windows 10 or 11, 64-bit; per-user install, no administrator rights needed.
* Check the file against the release's `SHA256SUMS.txt`:
  `Get-FileHash .\Master.Download.Manager.Setup.4.1.0.exe -Algorithm SHA256`
* The installer is not code-signed yet, so Windows SmartScreen may ask for
  confirmation (see [SECURITY.md](SECURITY.md)).

Installing over an earlier version keeps your settings, download history and
queues. From 4.1.0 on, **Help → Check for Updates** updates in one click and
only installs updates whose signed update information verifies.

## Documentation

* [User Guide](docs/USER_GUIDE.md)
* [Installation](docs/INSTALLATION.md), [Updating](docs/UPDATING.md),
  [Uninstallation](docs/UNINSTALLATION.md)
* [Browser Integration](docs/BROWSER_INTEGRATION.md)
* [Troubleshooting](docs/TROUBLESHOOTING.md)
* [Changelog](CHANGELOG.md)

## Support

Report a problem or suggest a feature in
[Issues](https://github.com/enoluca22-dota2/master-download-manager/issues).
Security problems: see [SECURITY.md](SECURITY.md).

## License

Copyright © 2026 the Master Download Manager author. All rights reserved.
Master Download Manager is **proprietary software**; see [LICENSE.md](LICENSE.md)
and [docs/LICENSING.md](docs/LICENSING.md).

It includes open-source components that remain under their own licenses:
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), the [licenses](licenses)
folder, and Help → View Open-Source Licenses in the program.

Only download content you have the right to download. DRM-protected media is
not supported.
