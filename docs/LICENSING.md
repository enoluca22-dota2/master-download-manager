# Licensing

Master Download Manager involves four separate questions. Each has its own
answer, and none of them decides the others.

| Question | Current answer |
|---|---|
| Under what terms may people use the **program**? | No end-user license agreement has been adopted yet; see [Using the program](#using-the-program) |
| Is MDM's **own source code** open source? | **No.** Copyright (c) 2026 the Master Download Manager author, all rights reserved |
| What about the **third-party components** inside MDM? | Each stays under its own open-source license — see [Third-party software](#third-party-software) |
| Is the program **code-signed**? | **No** — see [Code signing](#code-signing) |

## Using the program

The author publishes Master Download Manager free of charge on the project's
[GitHub Releases page](https://github.com/enoluca22-dota2/master-download-manager/releases).
The author has not yet adopted a formal end-user license agreement. A draft
for the author's review is in [LEGAL_REVIEW_REQUIRED.md](LEGAL_REVIEW_REQUIRED.md);
**it is a draft and is not in force.**

Until terms are adopted:

* The Setup shows a **License Information** page (copyright, these facts,
  the third-party components). It does not ask you to accept a contract.
* The program is provided as it is, without any promise that it fits a
  particular purpose; you use it at your own risk, to the extent the law
  allows.
* Only download content you have the right to download. MDM does not
  support DRM-protected media and never circumvents copy protection.
* If you want to redistribute MDM (re-upload the installer, bundle it with
  other software), ask the author first through the
  [GitHub repository](https://github.com/enoluca22-dota2/master-download-manager/issues).

## The source code

The source code is public on GitHub so it can be read, reviewed and built
for personal evaluation. Being public does **not** make it open source: no
license file grants permission to copy, modify or redistribute MDM's own code,
so normal copyright applies (all rights reserved). Contributions and forks
beyond what GitHub's Terms of Service allow need the author's permission.

The author may choose an open-source license later. Until a `LICENSE` file is
added to the repository by the author, nothing here grants one.

## Third-party software

MDM includes open-source software: Python, yt-dlp, libtorrent, FFmpeg, Deno,
CustomTkinter, FastAPI and others — 61 components in 4.1.0. Their licenses are
not replaced by MDM's terms. Where to find them:

* **Help → View Open-Source Licenses** — name, author, license, version and
  source repository of every bundled component, with the full license texts,
  search, sorting and copy.
* `licenses\` in the installation folder (one folder per component, plus
  `inventory.json`), and `THIRD_PARTY_NOTICES.md` (what each license requires
  and how MDM complies).
* `third-party-licenses.zip` attached to each release.

The inventory is generated from the build itself by
`tools/collect_licenses.py`. A release cannot be built if a bundled component
is missing from the inventory, has an unknown license, a missing text, author
or source, or a version that differs from the build.

**LGPL components.** FFmpeg/FFprobe (LGPL-3.0-or-later) are separate,
unmodified executables in `tools\ffmpeg\`; `SOURCE.txt` there names the exact
build and its source. pystray (LGPL-3.0) ships as plain `.py` files. Both can
be replaced by the user.

**AB Download Manager.** Some techniques were adapted from AB Download
Manager (Apache-2.0) and re-implemented; no AB code, icons or branding are
included. MDM is not affiliated with AB Download Manager.

## Code signing

The 4.1.0 installer and program are **not code-signed**. Windows SmartScreen
may warn about them, and Windows shows "Unknown publisher". To check a
download, compare its SHA-256 with `SHA256SUMS.txt` on the release page.

Code signing is separate from the in-app update check. Updates are verified
with the project's own Ed25519 release key (see
[UPDATE_SECURITY.md](UPDATE_SECURITY.md)). That proves an update came from the
same publisher as the installed copy, but it is not a Windows publisher
identity. The build pipeline can sign with a real Authenticode certificate as
soon as the author has one; the options are listed in
[UPDATE_SECURITY.md](UPDATE_SECURITY.md#authenticode-code-signing).
