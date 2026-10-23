# Update security

How Master Download Manager decides that an update is genuine, and what it
does not protect against. For the user-facing description see
[UPDATING.md](UPDATING.md).

## Trust model

| Layer | What it proves | What it doesn't |
|---|---|---|
| HTTPS to `api.github.com` / `github.com` | The data came from GitHub, unmodified in transit | That the release was made by the author (anyone with push access to the repo could publish one) |
| Repository identity check | The release belongs to `enoluca22-dota2/master-download-manager` (its `html_url`) | — |
| `SHA256SUMS.txt` | The installer wasn't corrupted | Authenticity: it's published next to the installer, so whoever can replace one can replace both |
| **Signed manifest** (`update-manifest.json` + `update-manifest.sig`, Ed25519) | The release was approved by the holder of the release signing key, and names exactly which installer (name, URL, size, SHA-256) | That the author's machine wasn't compromised |
| Re-check before running | The file that runs is the file that was verified | — |

Only the signed manifest establishes authenticity. MDM installs an update
**only** when the signature verifies against the public key built into the
installed copy (`ytd/update_public_key.pem`, fingerprint `2a9109c7b297b34e`).
If any check fails, the update can still be *shown* (with the reason and an
*Open Release Page* button), but it is never downloaded and run by MDM.

## Checks, in order (`ytd/updater.py`)

1. Fetch the release from the GitHub API: `/releases/latest` (stable channel)
   or the highest version in `/releases` (beta channel). HTTPS only; size
   limits on every response.
2. `html_url` must be on `github.com/enoluca22-dota2/master-download-manager`.
3. Version from the tag (semantic versioning via `packaging`). Not newer than
   the installed version → "up to date". MDM never downgrades.
4. Download `update-manifest.json`, `update-manifest.sig` and
   `SHA256SUMS.txt` from the release assets.
5. Verify the Ed25519 signature (RFC 8032, pycryptodomex) over the exact
   manifest bytes.
6. In the manifest: `app` and `repository` match; `version` equals the tag;
   `installer.name` is an asset of this release; `installer.url` is that
   asset's download URL on `github.com`; `installer.size` equals the asset
   size; `installer.sha256` equals the entry in `SHA256SUMS.txt`.
7. After the user clicks *Update Now* (`ytd/update_install.py`): free-space
   check, download to `%LOCALAPPDATA%\MasterDownloadManager\updates\<version>\`
   as a `.part` file, size and SHA-256 check, then rename. A truncated or
   wrong file is deleted.
8. Back up settings, queues, scheduler and the history database (SQLite
   backup API).
9. Right before starting Setup: SHA-256 and size are checked again.
10. Setup runs unattended
    (`/SILENT /SUPPRESSMSGBOXES /NORESTART /SP- /MDMUPDATE=1 /WAITPID=<pid> /LOG=<file>`),
    waits up to 90 s for MDM to exit, upgrades in place and starts the new
    version with `--post-update`. If Setup fails, it restarts the old
    version.
11. The new version compares its version with the expected one, records the
    result, restores `settings.json` from the backup if it has become
    unreadable, and deletes the used installer.

A lock file (`updates\update.lock`, with the owner's process ID) prevents two
updates from running at once. A lock left by a crashed process is taken over.

## The signing key

* Ed25519 key pair created with `tools/release_signing.py init`.
* The **private key is never in the repository**. It is stored at
  `%APPDATA%\MasterDownloadManager-release\update-signing-key.dpapi`,
  encrypted with Windows DPAPI for the release author's Windows account.
* `release_signing.py export-backup <file>` writes a passphrase-encrypted
  copy (PBKDF2-HMAC-SHA512, 600,000 iterations, AES-256). Keep it offline.
* `python build.py release-assets` signs the manifest. `release_signing.py
  verify` checks it with the public key that ships in the app.

**If the private key is lost**, installed copies can no longer verify new
releases. Users then have to install one release manually that ships a new
public key. **If the private key leaks**, an attacker who can also publish a
release in the repository could push a malicious update. Response: publish a
release (signed with the old key while it is still yours) that ships a new
public key, and announce it in the README and release notes.

## What this does not cover

* The Windows publisher identity (see Authenticode below). The installer
  downloaded from the website is checked only by its SHA-256.
* A compromise of the author's PC or GitHub account together with the key.
* Releases that carry no signed manifest (4.0.0 and older). They are
  offered only as a link to the release page.
* Custom manifest URLs (Settings → Updates → Advanced). These aren't signed,
  so they are never installed automatically.

## Authenticode code signing

MDM 4.1.0 is **not Authenticode-signed**. `build.py` signs the program and
the Setup automatically when a certificate is configured, and leaves them
unsigned (and says so) when it isn't. It never creates a self-signed
certificate.

```powershell
$env:MDM_SIGN_CERT_THUMBPRINT = "<SHA-1 thumbprint of a certificate in your store>"
$env:MDM_SIGNTOOL = "C:\Program Files (x86)\Windows Kits\10\bin\<ver>\x64\signtool.exe"  # optional
python build.py all
```

This runs `signtool sign /sha1 <thumbprint> /fd SHA256 /tr <RFC 3161 timestamp> /td SHA256`
followed by `signtool verify /pa`. The certificate's private key stays in
the Windows certificate store or on the hardware token; it is never in the
repository or in build logs.

Options for the author, checked 2026-10-24. Prices and eligibility change,
so confirm them with the provider before buying:

| Option | Roughly | Notes |
|---|---|---|
| **Microsoft Artifact Signing** (formerly Trusted Signing) | ~US$9.99/month (Basic) | Microsoft's recommended option for apps distributed outside the Store. Identity validation takes a few business days. Per Microsoft's documentation, organizations in the USA, Canada, EU and UK, and **individual developers only in the USA and Canada**, are eligible. Signing runs through an Azure account and SignTool with the Artifact Signing dlib (needs a different `signtool` command line than the thumbprint form above). |
| OV code-signing certificate from a public CA | about US$200–500/year | Since 2023 the private key must live on a hardware token or cloud HSM. Available to individuals in more countries. SmartScreen reputation still builds up over time. |
| EV code-signing certificate | more | Usually requires a registered organization. Since 2024 it no longer gives instant SmartScreen reputation. |
| Stay unsigned | free | Users see SmartScreen warnings; SHA256SUMS and the signed update manifest still protect updates. |

Sources: Microsoft Learn,
[Code signing options for Windows app developers](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options),
and [Artifact Signing pricing](https://azure.microsoft.com/pricing/details/artifact-signing/).

What to decide: [LEGAL_REVIEW_REQUIRED.md](LEGAL_REVIEW_REQUIRED.md#3-code-signing).
