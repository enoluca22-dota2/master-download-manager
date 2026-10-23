"""Build-time only: stage the external executables Master Download Manager
bundles (FFmpeg/FFprobe and the deno JavaScript runtime) into ./vendor.

The packaged application never downloads these — they ship inside the build.
This script runs on the *build* machine, pins exact versions, and verifies
SHA-256 checksums before anything is extracted.

    python tools/fetch_tools.py            # fetch/verify everything
    python tools/fetch_tools.py --check    # only report what's staged

Layout produced:
    vendor/ffmpeg/   ffmpeg.exe, ffprobe.exe, *.dll, LICENSE.txt, SOURCE.txt
    vendor/deno/     deno.exe, LICENSE.md, SOURCE.txt
    vendor/innosetup/  portable Inno Setup compiler (build tool only — never shipped)
"""

import argparse
import hashlib
import io
import os
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"

# FFmpeg: LGPL "shared" build (no GPL-only components; ffmpeg.exe/ffprobe.exe
# share the same DLLs, which keeps the bundle ~75 MB instead of ~330 MB).
FFMPEG = {
    "version": "n9.0.2-22-g46d8f462ee",
    "url": ("https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-10-01-13-06/"
            "ffmpeg-n9.0.2-22-g46d8f462ee-win64-lgpl-shared-9.0.zip"),
    "sha256": "2a41605c6c28455c7029e5057f77cda18838203fa09d40bed6e6cac14200e1f5",
    "source": ("FFmpeg source: https://ffmpeg.org/download.html (git tag n9.0.2 + 22 commits, "
               "revision 46d8f462ee). Build scripts: https://github.com/BtbN/FFmpeg-Builds "
               "(release autobuild-2026-10-01-13-06)."),
}

# Inno Setup (portable, from the Tools.InnoSetup NuGet package): compiles the
# installer on the build machine only.
INNO = {
    "version": "6.7.1",
    "url": "https://api.nuget.org/v3-flatcontainer/tools.innosetup/6.7.1/tools.innosetup.6.7.1.nupkg",
    "sha256": "00a5a07e8a4bd3a30364cb579d744677b37adfc22e365099cb01b8bd1e651828",
}

# deno: taken from the `deno` PyPI wheel already installed in the build venv
# (requirements.txt pins deno==2.9.7), so no separate download is needed.
DENO_VERSION = "2.9.7"
DENO_SOURCE = "deno source: https://github.com/denoland/deno (tag v2.9.7). Binary from the PyPI 'deno' wheel."


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def fetch_ffmpeg(force=False):
    dest = VENDOR / "ffmpeg"
    if (dest / "ffmpeg.exe").is_file() and (dest / "ffprobe.exe").is_file() and not force:
        print(f"ffmpeg: already staged in {dest}")
        return
    print(f"ffmpeg: downloading {FFMPEG['url']}")
    with urllib.request.urlopen(FFMPEG["url"], timeout=120) as resp:
        data = resp.read()
    digest = _sha256(data)
    if digest != FFMPEG["sha256"]:
        raise SystemExit(f"ffmpeg: checksum mismatch (got {digest}, expected {FFMPEG['sha256']}) — refusing to use it")
    print("ffmpeg: checksum OK")

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.infolist():
            name = member.filename
            parts = Path(name).parts
            # Only take bin/*.exe|*.dll and the top-level LICENSE; never trust
            # archive paths (no traversal possible: we write by basename).
            if len(parts) >= 3 and parts[-2] == "bin" and parts[-1].lower().endswith((".exe", ".dll")):
                if parts[-1].lower() == "ffplay.exe":
                    continue  # not needed by the app
                (dest / parts[-1]).write_bytes(zf.read(member))
            elif len(parts) == 2 and parts[-1].upper().startswith("LICENSE"):
                (dest / "LICENSE.txt").write_bytes(zf.read(member))
    (dest / "SOURCE.txt").write_text(
        f"FFmpeg {FFMPEG['version']} (LGPL build)\nDownloaded from: {FFMPEG['url']}\n"
        f"SHA-256 of archive: {FFMPEG['sha256']}\n{FFMPEG['source']}\n", encoding="utf-8")
    print(f"ffmpeg: staged {len(list(dest.iterdir()))} files in {dest}")


def fetch_inno(force=False):
    dest = VENDOR / "innosetup"
    if (dest / "ISCC.exe").is_file() and not force:
        print(f"innosetup: already staged in {dest}")
        return
    print(f"innosetup: downloading {INNO['url']}")
    with urllib.request.urlopen(INNO["url"], timeout=120) as resp:
        data = resp.read()
    digest = _sha256(data)
    if digest != INNO["sha256"]:
        raise SystemExit(f"innosetup: checksum mismatch (got {digest}) — refusing to use it")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.infolist():
            parts = Path(member.filename).parts
            if len(parts) >= 2 and parts[0] == "tools" and not member.is_dir() and ".." not in parts:
                target = dest.joinpath(*parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(member))
    print(f"innosetup: staged {dest / 'ISCC.exe'}")


def stage_deno():
    dest = VENDOR / "deno"
    try:
        import deno  # noqa: F401  (the PyPI wrapper package)
    except ImportError:
        raise SystemExit("deno: the 'deno' package is not installed in this Python. Run build.py setup first.")
    scripts = Path(sys.executable).parent
    candidates = [scripts / "deno.exe", scripts / "Scripts" / "deno.exe"]
    exe = next((c for c in candidates if c.is_file()), None)
    if exe is None:
        found = shutil.which("deno", path=str(scripts))
        exe = Path(found) if found else None
    if exe is None:
        raise SystemExit("deno: deno.exe not found next to this Python interpreter")
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exe, dest / "deno.exe")
    lic = _find_dist_license("deno")
    if lic:
        shutil.copy2(lic, dest / "LICENSE.md")
    (dest / "SOURCE.txt").write_text(f"deno {DENO_VERSION}\n{DENO_SOURCE}\n", encoding="utf-8")
    print(f"deno: staged {dest / 'deno.exe'}")


def _find_dist_license(dist_name):
    import importlib.metadata as md
    try:
        dist = md.distribution(dist_name)
    except md.PackageNotFoundError:
        return None
    for f in dist.files or []:
        if "licen" in f.name.lower() or "copying" in f.name.lower():
            return Path(dist.locate_file(f))
    return None


def check():
    ok = True
    for rel in ("ffmpeg/ffmpeg.exe", "ffmpeg/ffprobe.exe", "ffmpeg/LICENSE.txt", "deno/deno.exe", "innosetup/ISCC.exe"):
        path = VENDOR / rel
        print(f"{'OK     ' if path.is_file() else 'MISSING'} {path}")
        ok &= path.is_file()
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.check:
        sys.exit(0 if check() else 1)
    fetch_ffmpeg(force=args.force)
    stage_deno()
    fetch_inno(force=args.force)
    sys.exit(0 if check() else 1)


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
