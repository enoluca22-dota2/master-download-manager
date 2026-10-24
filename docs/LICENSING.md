# Licensing

Master Download Manager is **proprietary software**.

    Master Download Manager
    Copyright © 2026 the Master Download Manager author. All rights reserved.
    Proprietary software. Use, copying, modification, reverse engineering and
    redistribution are governed by the applicable license terms and by
    mandatory law. Third-party components remain subject to their own licenses.

Four separate things are involved:

| Question | Answer |
|---|---|
| Who owns Master Download Manager? | Its author (the copyright holder). The program's own code is **not** open source, and no license to copy, modify or redistribute it is granted. |
| Under what terms may I use it? | It is offered free of charge on the official download page. A formal End User License Agreement (EULA) is being prepared; until one is adopted, the program is provided as it is, and the points below apply. |
| What about the open-source parts inside it? | Each third-party component stays under its own license. Those licenses are **not** replaced or limited by MDM's terms. |
| Is the installer digitally signed? | Not yet (see [Code signing](#code-signing)). Check downloads against `SHA256SUMS.txt`. |

## Using the program

* Download it only from the official page:
  <https://github.com/enoluca22-dota2/master-download-manager/releases>.
* You may install and use it on your own devices.
* Don't sell it, re-upload it or bundle it with other software without the
  author's written permission. Ask through the
  [issue tracker](https://github.com/enoluca22-dota2/master-download-manager/issues).
* The program is provided as it is, without any promise that it fits a
  particular purpose, to the extent the law allows. Rights you have under
  mandatory consumer-protection law are not affected.
* Only download content you have the right to download. MDM does not
  support DRM-protected media and never circumvents copy protection.

The source code of Master Download Manager is not published. Earlier
development snapshots that were visible on GitHub (up to version 4.1.0) were
never licensed for reuse; they remain all rights reserved.

## Third-party software

MDM includes open-source software: Python, yt-dlp, libtorrent, FFmpeg, Deno,
CustomTkinter, FastAPI and others (61 components in 4.1.0). Where to find the
details:

* **Help → View Open-Source Licenses**: name, author, license, version and
  source of every bundled component, with the full license texts;
* `licenses\` and `THIRD_PARTY_NOTICES.md` in the installation folder;
* `third-party-licenses.zip` attached to every release.

**LGPL components.** FFmpeg/FFprobe (LGPL-3.0-or-later) are separate,
unmodified executables and DLLs in `tools\ffmpeg\`, and pystray (LGPL-3.0)
ships as plain `.py` files. You may replace them with your own versions, and
reverse-engineer the program as far as needed to debug such replacements.
Nothing in MDM's terms restricts these rights. Their source code: see
`tools\ffmpeg\SOURCE.txt` and Help → View Open-Source Licenses.

**AB Download Manager.** Some techniques were adapted from AB Download
Manager (Apache-2.0) and re-implemented; no AB code, icons or branding are
included. MDM is not affiliated with AB Download Manager.

## Code signing

The installer and program of version 4.1.0 are **not** Authenticode-signed, so
Windows SmartScreen may warn and Windows shows "Unknown publisher". To check a
download, compare its SHA-256 with `SHA256SUMS.txt` on the release page.

In-app updates are protected separately: every release carries a manifest
signed with the project's update key (Ed25519, fingerprint
`2a9109c7b297b34e`), and MDM installs an update only if that signature, the
size and the SHA-256 all match. See [SECURITY.md](../SECURITY.md).
