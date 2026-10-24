# Security

## Verifying downloads

Every release lists the SHA-256 of each file in `SHA256SUMS.txt`:

```powershell
Get-FileHash .\Master.Download.Manager.Setup.4.1.0.exe -Algorithm SHA256
```

Compare the result with the line for that file. Download only from
<https://github.com/enoluca22-dota2/master-download-manager/releases>.

**Windows code signing:** version 4.1.0 is not Authenticode-signed, so
SmartScreen may warn and Windows shows "Unknown publisher". When signed
releases start, the release notes will state the exact publisher name to
expect in the file's *Properties → Digital Signatures*.

## How in-app updates are protected

* Every release carries `update-manifest.json` and `update-manifest.sig`: the
  installer's name, download address, size and SHA-256, signed with the
  project's update key (Ed25519). Update key fingerprint:
  **`2a9109c7b297b34e`** (SHA-256 of the raw public key, first 16 hex
  digits).
* Master Download Manager installs an update only when that signature
  verifies with the key built into the installed program, the release comes
  from this repository, and the downloaded installer's size and SHA-256 match.
  It never downgrades, and installs nothing until you click *Update Now*.
* A release whose signature doesn't verify is shown as "Verification:
  FAILED — do not install".
* Your settings and history are backed up before an update. If the update
  fails, the previous version starts again.

## Reporting a vulnerability

Please don't post security problems publicly. Open an issue that only says
you have a security report and how to contact you, or use GitHub's private
vulnerability reporting for this repository if it is enabled. You'll get a
reply as soon as possible. Please include the version (Help → About) and the
steps to reproduce.
