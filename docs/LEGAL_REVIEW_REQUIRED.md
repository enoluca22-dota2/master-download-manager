# Legal review required — decisions for the author

> **Status: DRAFT FOR REVIEW. Nothing in this file is in force.** It was
> prepared while building 4.1.0 so that the author (the copyright holder) can
> decide. It is not legal advice. Consider having a lawyer review any terms
> before adopting them, especially for your country.

Facts as of 4.1.0:

* MDM's own code: copyright (c) 2026 the author, **all rights reserved**. No
  `LICENSE` file, so no open-source license has been granted.
* The installer shows an informational *License Information* page
  (`installer/license_information.txt`). It doesn't require the user to accept
  anything.
* 61 bundled third-party components keep their own licenses (Help → View
  Open-Source Licenses). Your terms cannot override them.
* Not code-signed. Updates are signed with the project's own Ed25519 key.

## 1. Product terms (EULA) — decide whether to adopt one

Options:

* **A. Keep the informational page** (current). It's simple, and users aren't
  bound to anything beyond copyright law and the third-party licenses.
* **B. Adopt the draft EULA below** (after review). Setup would then use
  `LicenseFile=` with "I accept" instead of the information page.
* **C. Open-source the program** (see §2). In that case the open-source
  license is the product license and no separate EULA is needed.

### Draft end-user license agreement (NOT IN FORCE)

```
MASTER DOWNLOAD MANAGER - END-USER LICENSE AGREEMENT (DRAFT)

Copyright (c) 2026 [AUTHOR'S LEGAL NAME]. All rights reserved.

1. Grant. You may install and use Master Download Manager ("the Software")
   on any number of devices you own or control, free of charge, for personal
   or internal business purposes.

2. Restrictions. You may not sell the Software, or redistribute it except in
   the unmodified form published at
   https://github.com/enoluca22-dota2/master-download-manager/releases, with
   this agreement. You may not remove copyright or license notices.

3. Third-party software. The Software includes open-source components that
   are licensed to you under their own licenses (listed in the program under
   Help -> View Open-Source Licenses and in THIRD_PARTY_NOTICES.md). Nothing
   in this agreement limits your rights under those licenses.

4. Your responsibility. You are responsible for what you download. Use the
   Software only for content you have the right to download. The Software
   does not support and must not be used to circumvent DRM or other
   technical protection measures.

5. Updates. The Software can check for updates on GitHub and install them
   when you choose to. [Describe data sent: only standard HTTPS requests to
   GitHub; no personal data or telemetry is collected.]

6. No warranty. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY
   KIND, EXPRESS OR IMPLIED, INCLUDING MERCHANTABILITY, FITNESS FOR A
   PARTICULAR PURPOSE AND NON-INFRINGEMENT, TO THE EXTENT PERMITTED BY LAW.

7. Limitation of liability. TO THE EXTENT PERMITTED BY LAW, THE AUTHOR IS NOT
   LIABLE FOR ANY DAMAGES ARISING FROM THE USE OF OR INABILITY TO USE THE
   SOFTWARE. [Check: consumer-protection law in your country may not allow
   excluding all liability.]

8. Termination. This agreement ends automatically if you breach it; you must
   then uninstall the Software.

9. Governing law. [COUNTRY / STATE]. [Courts of ...]
```

Placeholders in `[brackets]` need the author's details. In particular, §6–7
must be checked against consumer law where you and your users live.

## 2. Source code license — decide whether to open-source

The code is public but not open source. Options:

| Choice | Effect |
|---|---|
| Keep all rights reserved (current) | Others may read it and fork it on GitHub (GitHub ToS), but not reuse it. |
| MIT / BSD-2-Clause / Apache-2.0 | Permissive: anyone may reuse it, also commercially. Apache-2.0 adds a patent grant. |
| GPL-3.0 / AGPL-3.0 | Copyleft: modified versions must stay open. Check compatibility: everything bundled today is GPL-3-compatible (LGPL, MIT, BSD, Apache-2.0, MPL-2.0, PSF). |
| Source-available (e.g. PolyForm Noncommercial) | Readable and reusable for non-commercial use only. Not "open source". |

If you choose one, add a `LICENSE` file yourself (or ask for it to be added)
and update `docs/LICENSING.md`, `installer/license_information.txt`, the About
window text and `THIRD_PARTY_NOTICES.md`. **Nothing has been chosen on your
behalf.**

## 3. Code signing

See [UPDATE_SECURITY.md](UPDATE_SECURITY.md#authenticode-code-signing) for
options and costs. To decide: buy a certificate or Artifact Signing
subscription (if eligible), or stay unsigned. The build already supports
signing once `MDM_SIGN_CERT_THUMBPRINT` is set.

## 4. Release signing key — action needed

The update signing key exists only on the release PC (DPAPI-protected). Make
an offline backup now, or a lost Windows profile or PC will break one-click
updates for every installed copy:

```powershell
.venv\Scripts\python tools\release_signing.py export-backup E:\mdm-update-key-backup.pem
```

Store the file and its passphrase separately (for example a USB stick plus a
password manager).

## 5. Publisher name

The installer shows the product name, "Master Download Manager", as
publisher. It doesn't name a person or company, because no legal entity was
given. If you want your name or a company name shown, say so. A code-signing
certificate would show the validated name anyway.

## 6. Other checks

* **Trademarks**: "Master Download Manager" hasn't been checked against
  existing trademarks.
* **Privacy**: MDM sends no telemetry. Update checks are plain HTTPS requests
  to GitHub (GitHub sees the IP address). A short privacy note on the
  README/release page may be useful.
* **AB Download Manager**: attribution is given (Apache-2.0); no code is
  copied. Keep it this way, or comply with Apache-2.0 §4 if code is ever
  reused.
* **FFmpeg (LGPL)**: if the upstream source links in `tools\ffmpeg\SOURCE.txt`
  disappear, host a copy of the matching source.
