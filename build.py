"""Build Master Download Manager.

Usage (from the project folder):
    python build.py setup       create .venv, install yt-dlp (../yt-dlp-master if present, else the pinned
                                PyPI release) + requirements, stage tools
    python build.py test        run the automated test suite
    python build.py exe         build dist/MasterDownloadManager/MasterDownloadManager.exe
    python build.py installer   build "dist/Master Download Manager Setup <version>.exe"
    python build.py extensions  zip the Chrome/Firefox extensions into dist/
    python build.py checksums   write dist/SHA256SUMS.txt for the release assets
    python build.py release-assets  signed update manifest, release notes, licenses zip, checksums
    python build.py all         setup (if needed), test, exe, installer, extensions, release-assets

Optional Authenticode signing: set MDM_SIGN_CERT_THUMBPRINT (a code-signing
certificate in the Windows certificate store) and, if signtool.exe isn't on
PATH, MDM_SIGNTOOL. The exe and the installer are then signed before any
checksum is computed. Without it the build is unsigned (and says so).

Everything after `setup` runs with .venv's Python, so the bundle contains
exactly the audited, pinned dependencies — never whatever happens to be
installed globally. Internet is only needed by `setup` (PyPI + the pinned
FFmpeg/Inno Setup archives, verified by SHA-256); the built app never
downloads code at runtime.
"""

import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VENV_PY = VENV / "Scripts" / "python.exe"
YTDLP_SOURCE = ROOT.parent / "yt-dlp-master"
# Audited release used when the local source tree isn't present (same version
# the 3.x builds shipped; Unlicense). Bump deliberately, after testing YouTube.
YTDLP_PINNED = "yt-dlp==2026.08.19"
DIST = ROOT / "dist"
APP_DIST = DIST / "MasterDownloadManager"


def run(args, **kw):
    print("+", " ".join(str(a) for a in args))
    result = subprocess.run([str(a) for a in args], cwd=ROOT, **kw)
    if result.returncode != 0:
        sys.exit(result.returncode)


def app_version():
    text = (ROOT / "ytd" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def ensure_venv():
    if not VENV_PY.is_file():
        run([sys.executable, "-m", "venv", VENV])
        run([VENV_PY, "-m", "pip", "install", "--upgrade", "pip"])
    return VENV_PY


def setup():
    py = ensure_venv()
    # yt-dlp without extras: its [default] extra would pull in GPL mutagen,
    # which this app deliberately excludes.
    if (YTDLP_SOURCE / "yt_dlp" / "version.py").is_file():
        run([py, "-m", "pip", "install", "--upgrade", str(YTDLP_SOURCE)])
    else:
        run([py, "-m", "pip", "install", YTDLP_PINNED])
    run([py, "-m", "pip", "install", "-r", ROOT / "requirements.txt"])
    run([py, ROOT / "tools" / "fetch_tools.py"])


def test():
    py = ensure_venv()
    env = dict(os.environ, MDM_DATA_DIR=str(ROOT / "build" / "test-data"))
    run([py, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"], env=env)


def _write_version_file(version):
    nums = (version.split(".") + ["0", "0", "0", "0"])[:4]
    tup = ", ".join(str(int(re.sub(r"\D", "", n) or 0)) for n in nums)
    text = (ROOT / "version_info.txt").read_text(encoding="utf-8")
    text = re.sub(r"filevers=\([^)]*\)", f"filevers=({tup})", text)
    text = re.sub(r"prodvers=\([^)]*\)", f"prodvers=({tup})", text)
    text = re.sub(r"StringStruct\('FileVersion', '[^']*'\)", f"StringStruct('FileVersion', '{version}')", text)
    text = re.sub(r"StringStruct\('ProductVersion', '[^']*'\)", f"StringStruct('ProductVersion', '{version}')", text)
    (ROOT / "version_info.txt").write_text(text, encoding="utf-8")


def sync_extensions():
    """extension-shared/ytd-client.js is the single source for both builds, and
    the extensions carry the app's version (ytd/__init__.py is the only place
    a version number is written by hand)."""
    import json
    shared = ROOT / "extension-shared" / "ytd-client.js"
    version = app_version()
    for name in ("extension-chrome", "extension-firefox"):
        shutil.copy2(shared, ROOT / name / "ytd-client.js")
        manifest_path = ROOT / name / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("version") != version:
            manifest["version"] = version
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def exe():
    py = ensure_venv()
    run([py, ROOT / "tools" / "fetch_tools.py", "--check"])
    sync_extensions()
    run([py, ROOT / "build_icons.py"])
    run([py, ROOT / "tools" / "make_installer_art.py"])
    run([py, ROOT / "tools" / "collect_licenses.py"])
    _write_version_file(app_version())
    run([py, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", DIST, "--workpath", ROOT / "build" / "pyinstaller",
         ROOT / "MasterDownloadManager.spec"])

    # Bundled executables next to the program (user-replaceable, as the LGPL expects for FFmpeg).
    tools_dir = APP_DIST / "tools"
    if tools_dir.exists():
        shutil.rmtree(tools_dir)
    shutil.copytree(ROOT / "vendor" / "ffmpeg", tools_dir / "ffmpeg")
    shutil.copytree(ROOT / "vendor" / "deno", tools_dir / "deno")
    # Browser extensions, licenses and notices at the top level where users can find them.
    ext_dir = APP_DIST / "extensions"
    if ext_dir.exists():
        shutil.rmtree(ext_dir)
    shutil.copytree(ROOT / "extension-chrome", ext_dir / "chrome")
    shutil.copytree(ROOT / "extension-firefox", ext_dir / "firefox")
    if (APP_DIST / "licenses").exists():
        shutil.rmtree(APP_DIST / "licenses")
    shutil.copytree(ROOT / "licenses", APP_DIST / "licenses")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", APP_DIST / "THIRD_PARTY_NOTICES.md")
    # Offline copies for Help -> What's New / Changelog / guides.
    docs_dir = APP_DIST / "docs"
    if docs_dir.exists():
        shutil.rmtree(docs_dir)
    docs_dir.mkdir()
    shutil.copy2(ROOT / "CHANGELOG.md", docs_dir / "CHANGELOG.md")
    for name in BUNDLED_DOCS:
        shutil.copy2(ROOT / "docs" / name, docs_dir / name)
    _remove_conflicting_winrt_crt_dlls(APP_DIST)
    # Release gate: the license inventory must match what PyInstaller bundled.
    run([py, ROOT / "tools" / "collect_licenses.py", "--verify"])
    authenticode_sign(APP_DIST / "MasterDownloadManager.exe")
    print(f"\nBuilt: {APP_DIST / 'MasterDownloadManager.exe'}")


BUNDLED_DOCS = ("USER_GUIDE.md", "INSTALLATION.md", "BROWSER_INTEGRATION.md", "TROUBLESHOOTING.md", "UPDATING.md",
                "LICENSING.md")


def authenticode_sign(path):
    """Sign with a real code-signing certificate when one is configured;
    otherwise leave the file unsigned and say so (never self-sign)."""
    thumbprint = os.environ.get("MDM_SIGN_CERT_THUMBPRINT")
    if not thumbprint:
        print(f"Authenticode: not configured - {path.name} is unsigned.")
        return False
    signtool = os.environ.get("MDM_SIGNTOOL") or shutil.which("signtool") or "signtool"
    run([signtool, "sign", "/sha1", thumbprint, "/fd", "SHA256", "/tr", "http://timestamp.digicert.com",
         "/td", "SHA256", "/d", "Master Download Manager", path])
    run([signtool, "verify", "/pa", path])
    return True


def _remove_conflicting_winrt_crt_dlls(out_dir):
    """win11toast's winrt-* packages each bundle an older MSVC++ runtime under
    _internal/winrt/. If that copy is found first, libtorrent's extension
    crashes the whole process (reproduced; faulting module
    _internal\\winrt\\MSVCP140.dll). Keep only the root copy."""
    winrt_dir = out_dir / "_internal" / "winrt"
    if not winrt_dir.is_dir():
        return
    removed = []
    for name in ("MSVCP140.dll", "VCRUNTIME140.dll", "VCRUNTIME140_1.dll", "msvcp140.dll", "vcruntime140.dll",
                 "vcruntime140_1.dll"):
        dup = winrt_dir / name
        if dup.is_file() and (out_dir / "_internal" / name).is_file():
            dup.unlink()
            removed.append(name)
    if removed:
        print(f"Removed duplicate CRT DLLs from _internal/winrt/: {', '.join(removed)}")


def installer():
    iscc = ROOT / "vendor" / "innosetup" / "ISCC.exe"
    if not iscc.is_file():
        sys.exit("Inno Setup compiler not staged. Run: python build.py setup")
    if not (APP_DIST / "MasterDownloadManager.exe").is_file():
        sys.exit("Build the executable first: python build.py exe")
    version = app_version()
    run([iscc, f"/DAppVersion={version}", f"/DSourceDir={APP_DIST}", ROOT / "installer" / "MasterDownloadManager.iss"])
    authenticode_sign(DIST / f"Master Download Manager Setup {version}.exe")
    print(f"\nBuilt: {DIST / f'Master Download Manager Setup {version}.exe'}")


def extensions():
    sync_extensions()
    DIST.mkdir(exist_ok=True)
    for name in ("extension-chrome", "extension-firefox"):
        zip_path = DIST / f"MasterDownloadManager-{name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in (ROOT / name).rglob("*"):
                if file.is_file():
                    zf.write(file, file.relative_to(ROOT / name))
        print(f"Wrote {zip_path}")


GITHUB_REPO = "enoluca22-dota2/master-download-manager"


def release_notes(version):
    """The CHANGELOG section for this version."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(version)}\b.*?(?=^## |\Z)", text, re.M | re.S)
    if not m:
        sys.exit(f"CHANGELOG.md has no section for {version}")
    return m.group(0).strip() + "\n"


def release_assets():
    """Everything a GitHub release needs besides the installer itself: the
    signed update manifest (one-click updates), release notes, the license
    bundle and SHA256SUMS.txt over all of it."""
    import datetime
    import hashlib
    import json
    py = ensure_venv()
    version = app_version()
    setup_exe = DIST / f"Master Download Manager Setup {version}.exe"
    if not setup_exe.is_file():
        sys.exit("Build the installer first: python build.py installer")
    asset_name = setup_exe.name.replace(" ", ".")  # GitHub's name for the uploaded file
    manifest = {
        "app": "Master Download Manager", "repository": GITHUB_REPO, "version": version,
        "channel": "beta" if re.search(r"[a-z]", version) else "stable",
        "published": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "installer": {"name": asset_name,
                      "url": f"https://github.com/{GITHUB_REPO}/releases/download/v{version}/{asset_name}",
                      "size": setup_exe.stat().st_size,
                      "sha256": hashlib.sha256(setup_exe.read_bytes()).hexdigest()},
        "minimum_version": "4.0.0",
    }
    manifest_path = DIST / "update-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    run([py, ROOT / "tools" / "release_signing.py", "sign", manifest_path])
    run([py, ROOT / "tools" / "release_signing.py", "verify", manifest_path])
    sig = DIST / "update-manifest.sig"
    if sig.exists():
        sig.unlink()
    (DIST / "update-manifest.json.sig").rename(sig)
    (DIST / "release-notes.md").write_text(release_notes(version), encoding="utf-8")
    with zipfile.ZipFile(DIST / "third-party-licenses.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        for f in (APP_DIST / "licenses").rglob("*"):
            if f.is_file():
                zf.write(f, Path("licenses") / f.relative_to(APP_DIST / "licenses"))
        zf.write(ROOT / "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md")
        zf.write(ROOT / "docs" / "LICENSING.md", "LICENSING.md")
    checksums(extra=["update-manifest.json", "update-manifest.sig", "release-notes.md", "third-party-licenses.zip"])


def checksums(extra=()):
    """SHA256SUMS.txt for the release: the in-app updater refuses an
    installer that isn't listed here with a matching hash."""
    import hashlib
    version = app_version()
    names = [f"Master Download Manager Setup {version}.exe", "MasterDownloadManager-extension-chrome.zip",
             "MasterDownloadManager-extension-firefox.zip", *extra]
    lines = []
    for name in names:
        path = DIST / name
        if not path.is_file():
            sys.exit(f"Missing release asset: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        # GitHub replaces spaces in asset names with dots
        lines.append(f"{digest}  {name.replace(' ', '.')}")
        print(f"{digest}  {name}")
    # LF line endings, so `sha256sum -c SHA256SUMS.txt` works as is
    (DIST / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {DIST / 'SHA256SUMS.txt'}")


def main():
    commands = {"setup": setup, "test": test, "exe": exe, "installer": installer, "extensions": extensions,
                "checksums": checksums, "release-assets": release_assets}
    if len(sys.argv) < 2 or sys.argv[1] not in (*commands, "all"):
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "all":
        if not VENV_PY.is_file():
            setup()
        test()
        exe()
        installer()
        extensions()
        release_assets()
    else:
        commands[sys.argv[1]]()


if __name__ == "__main__":
    main()
