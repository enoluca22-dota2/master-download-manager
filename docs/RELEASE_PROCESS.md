# Release process

For the maintainer. Every step runs on the release PC, which holds the
update signing key. Never publish a build that failed any step.

## 0. Once per machine

```powershell
python build.py setup                                   # .venv (Python 3.12), pinned deps, FFmpeg/deno/Inno Setup
.venv\Scripts\python tools\release_signing.py init      # only if no key exists yet; never overwrite
.venv\Scripts\python tools\release_signing.py export-backup <offline file>
```

`init` writes `ytd/update_public_key.pem`. Commit that file. **The private
key never goes into the repository.**

## 1. Version

The only version source is `ytd/__init__.py` (`__version__`). `build.py`
passes it to the installer (`/DAppVersion`), copies it into both extension
manifests, names the assets and writes the update manifest. The Setup script
refuses to compile without it.

* Stable: `4.1.0`. Beta: `4.2.0b1` (published as a GitHub *pre-release*;
  only users on the Beta channel are offered it).
* Add a `## <version> — <date>` section at the top of `CHANGELOG.md`. It
  becomes `release-notes.md`, the release body and *What's New*.

## 2. Build and gates

```powershell
python build.py all
```

This runs, in order, stopping at the first failure:

1. the automated tests (`pytest tests`);
2. PyInstaller → `dist/MasterDownloadManager/`;
3. **license gate** — `tools/collect_licenses.py --verify` compares the
   inventory with what PyInstaller actually bundled (missing or extra
   components, versions, license texts, authors, sources, unknown licenses);
4. Authenticode signing of the exe, if `MDM_SIGN_CERT_THUMBPRINT` is set
   (otherwise it prints "unsigned");
5. the installer (Inno Setup), signed the same way;
6. extension zips;
7. `release-assets`: `update-manifest.json` (version, exact asset name and
   URL, size, SHA-256), its Ed25519 signature `update-manifest.sig` (and
   verification with the shipped public key), `release-notes.md`,
   `third-party-licenses.zip`, `SHA256SUMS.txt` over all assets.

If dependencies changed, run `tools/collect_licenses.py` (collect mode)
first, review the diff of `licenses/inventory.json`, and add any new
component to its catalog with a verified license.

## 3. Test the build

* `tools/gui_smoke.py` and `tools/gui_smoke41.py` — the real window
  (screenshots).
* `tools/installer_test.ps1` with the isolated test variant of the installer
  (see the script header).
* `tools/real_world_test.py` against the packaged app.
* `tools/browser_extension_test.py`, `tools/browser_capture_test.py`.
* Upgrade test: install the previous release, then the new Setup over it.
  Settings, history, queues and shortcuts must survive.
* Updater end to end: see `docs/TEST_REPORT.md` (local fake GitHub, test key).

Record the results in `docs/TEST_REPORT.md`.

## 4. Publish

```powershell
git status                     # clean; no secrets, no personal data
git tag v4.1.0
git push origin main v4.1.0
gh release create v4.1.0 --title "Master Download Manager 4.1.0" --notes-file dist\release-notes.md `
  "dist\Master Download Manager Setup 4.1.0.exe" dist\SHA256SUMS.txt dist\update-manifest.json `
  dist\update-manifest.sig dist\release-notes.md dist\third-party-licenses.zip `
  dist\MasterDownloadManager-extension-chrome.zip dist\MasterDownloadManager-extension-firefox.zip
```

Add `--prerelease` for a beta. GitHub renames spaces in asset names to dots
(`Master.Download.Manager.Setup.4.1.0.exe`). The manifest already uses that
name.

## 5. Verify the published release

* Download every asset from GitHub and compare it with `SHA256SUMS.txt` and
  the local `dist/` files.
* `release_signing.py verify` the downloaded manifest.
* From an installed previous version: **Help → Check for Updates** must show
  the new version as verified, with *Update Now*.

## Never

* Publish a manifest for an installer that wasn't built, tested and
  checksummed in this run.
* Commit the private key, a `.pfx`, passwords or tokens. Signing secrets are
  passed only as environment variables or stay in the certificate store.
* Re-use a tag or replace assets of a published release. Make a new version
  instead (installed copies may already have verified the old manifest).
