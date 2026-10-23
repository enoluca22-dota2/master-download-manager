"""Build Master Download Manager.

Usage (from the project folder):
    python build.py setup       create .venv, install yt-dlp from ../yt-dlp-master + requirements, stage tools
    python build.py test        run the automated test suite
    python build.py exe         build dist/MasterDownloadManager/MasterDownloadManager.exe
    python build.py installer   build "dist/Master Download Manager Setup <version>.exe"
    python build.py extensions  zip the Chrome/Firefox extensions into dist/
    python build.py all         setup (if needed), test, exe, installer, extensions

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
    if not (YTDLP_SOURCE / "yt_dlp" / "version.py").is_file():
        sys.exit(f"yt-dlp source not found at {YTDLP_SOURCE}")
    # yt-dlp itself from the audited local source (no extras: its [default]
    # extra would pull in GPL mutagen, which this app deliberately excludes).
    run([py, "-m", "pip", "install", "--upgrade", str(YTDLP_SOURCE)])
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
    """extension-shared/ytd-client.js is the single source for both builds."""
    shared = ROOT / "extension-shared" / "ytd-client.js"
    for name in ("extension-chrome", "extension-firefox"):
        shutil.copy2(shared, ROOT / name / "ytd-client.js")


def exe():
    py = ensure_venv()
    run([py, ROOT / "tools" / "fetch_tools.py", "--check"])
    sync_extensions()
    run([py, ROOT / "build_icons.py"])
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
    _remove_conflicting_winrt_crt_dlls(APP_DIST)
    print(f"\nBuilt: {APP_DIST / 'MasterDownloadManager.exe'}")


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


def main():
    commands = {"setup": setup, "test": test, "exe": exe, "installer": installer, "extensions": extensions}
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
    else:
        commands[sys.argv[1]]()


if __name__ == "__main__":
    main()
