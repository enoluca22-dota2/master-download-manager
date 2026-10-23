# Release report — Master Download Manager 4.0.0

| | |
|---|---|
| Version | **4.0.0** (previous: 3.0.0). Major bump: new UI, queue/scheduler model, SQLite history, new API endpoints |
| Repository | https://github.com/enoluca22-dota2/master-download-manager (public, created for this release; branch `main`) |
| Release | https://github.com/enoluca22-dota2/master-download-manager/releases/tag/v4.0.0 (tag `v4.0.0`, not draft, not pre-release) |
| Source of the build | tree of commit `3a18ebb`; `v4.0.0` points at `e7b5e15`, which only adds docs and a test fixture — no bundled file differs (`git diff --stat 3a18ebb e7b5e15 -- ytd extension-* installer licenses …` is empty) |
| Code signing | **Not signed** — no signing certificate is configured on this machine. Users see SmartScreen; integrity is provided by SHA-256 |

## Assets

| Asset | Size | SHA-256 |
|---|---|---|
| `Master.Download.Manager.Setup.4.0.0.exe` | 111,315,847 B | `bb5bd08a9d385b786cd9abe0e0a57ea28309638a52745435d15867eec91471e4` |
| `MasterDownloadManager-extension-chrome.zip` | 40,973 B | `0f0ecf2309b163090a442eac88eea1e4b0d515fefc6e2d9708844e1df4726265` |
| `MasterDownloadManager-extension-firefox.zip` | 40,808 B | `41468d85a2cd42873759c671393b3430cc622f0a5fcbb1cc5882f97b49900543` |
| `SHA256SUMS.txt` | 328 B | lists the three files above under their GitHub (dotted) names |

The installer published is **the same file** that performed the verified
3.0.0 → 4.0.0 upgrade on the development PC (`TEST_REPORT.md` §6).

## Post-publication verification

| Check | Result |
|---|---|
| Release page and asset list via the GitHub API | 4 assets, names and sizes as above; GitHub's own digests equal the local SHA-256s |
| Public download URL of the installer | downloaded, SHA-256 `bb5bd08a…` — identical |
| `SHA256SUMS.txt` downloaded from the release | lists `Master.Download.Manager.Setup.4.0.0.exe` with `bb5bd08a…` |
| In-app updater (`ytd/updater.py`, GitHub source) as version 3.9.0 | finds 4.0.0 in 1.2 s, downloads it, SHA-256 verified |
| Same, as 4.0.0 | "up to date" |
| Same, with a tampered hash | refused ("checksum doesn't match … refusing it") |

### Update path from the installed 3.0.0

MDM 3.0.0's updater only supported a custom manifest URL and none was ever
configured, so **3.0.0 cannot discover 4.0.0 by itself**. 3.x installs are
upgraded by running the 4.0.0 installer (verified on this PC: settings, token,
history and shortcuts preserved). From 4.0.0 on, Settings → Updates checks
GitHub Releases (manual, or weekly if enabled).

## Release checklist

| Step | Done |
|---|---|
| Current version determined (3.0.0) and new version chosen (4.0.0) | ✓ |
| Version updated consistently: `ytd/__init__.py`, installer default, extension manifests, exe version resource (generated) | ✓ |
| Changelog and release notes | ✓ `CHANGELOG.md`, release body |
| Working tree contains only intended changes; no settings, history, logs or secrets tracked; personal data removed from a test fixture (history rewritten before the first push) | ✓ |
| Mandatory checks: 354 automated, 25 GUI, 22 real-world, 15 + 8 browser, 23 installer, 6 load, real upgrade | ✓ (`TEST_REPORT.md`) |
| Commit and push to the project remote | ✓ `main` |
| Production app + installer built | ✓ |
| Code signing | ✗ no credentials configured (stated, not claimed) |
| SHA-256 checksums | ✓ |
| GitHub Release with installer, extension packages, checksums | ✓ |
| Release URLs and assets verified | ✓ |
| Update discovery/installation verified | ✓ from 4.0.0 onwards; 3.0.0 has no discovery (see above) |
| Updating preserves data and settings | ✓ verified on the real install |

## Not published

* Browser-store listings (Chrome Web Store / Firefox Add-ons): they need
  developer accounts and store review; the extensions ship as zips and in the
  app's `extensions` folder.
* The local branch `legacy-ytd` (the pre-MDM commit, which contains a personal
  download history file) was deliberately **not** pushed.
