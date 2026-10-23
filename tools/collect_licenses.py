"""Collect the license texts of every third-party component bundled into the
Master Download Manager build into ./licenses (and from there into the
packaged app). Run inside the build venv so the texts match the exact
versions PyInstaller bundles:

    .venv\\Scripts\\python tools/collect_licenses.py

Nothing here is hand-typed: texts come from each package's own dist-info,
from the staged vendor tools, from the Python installation, or (for the few
wheels that ship no license file) from tools/license_texts/, which holds
verbatim copies of the upstream license files.
"""

import importlib.metadata as md
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "licenses"
MANUAL = Path(__file__).resolve().parent / "license_texts"

# Python distributions that end up inside the packaged app (runtime closure).
# Test/build-only packages (pytest, httpx, pyinstaller, pip, ...) are excluded
# because they are never shipped.
RUNTIME_DISTRIBUTIONS = [
    "yt-dlp", "yt-dlp-ejs", "deno",
    "customtkinter", "darkdetect", "packaging", "pillow",
    "pywin32", "pystray", "six", "win11toast", "winrt-runtime",
    "fastapi", "starlette", "pydantic", "pydantic_core", "annotated-types", "annotated-doc",
    "typing_extensions", "typing-inspection", "anyio", "idna", "h11", "click", "colorama",
    "uvicorn", "opentelemetry-api",
    "requests", "urllib3", "certifi", "charset-normalizer", "brotli", "pycryptodomex", "websockets",
    "libtorrent",
]

# Wheels that ship no license file: verbatim upstream texts kept in tools/license_texts.
MANUAL_TEXTS = {
    "libtorrent": ["libtorrent-LICENSE.txt", "boost-LICENSE_1_0.txt", "openssl-LICENSE.txt"],
    "winrt-runtime": ["pywinrt-LICENSE.txt"],
}


def _dist_license_files(dist):
    for f in dist.files or []:
        lowered = str(f).lower()
        if not (".dist-info" in lowered):
            continue
        name = f.name.lower()
        if any(key in name for key in ("licen", "copying", "notice", "authors")):
            yield Path(dist.locate_file(f))


def collect():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    inventory = []

    for name in RUNTIME_DISTRIBUTIONS:
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            print(f"warning: {name} not installed; skipped")
            continue
        meta = dist.metadata
        target = OUT / f"{meta['Name']}-{meta['Version']}"
        target.mkdir()
        copied = []
        for src in _dist_license_files(dist):
            dest = target / src.name
            if dest.exists():
                dest = target / f"{src.parent.name}-{src.name}"
            shutil.copy2(src, dest)
            copied.append(dest.name)
        for manual in MANUAL_TEXTS.get(name, []):
            shutil.copy2(MANUAL / manual, target / manual)
            copied.append(manual)
        if not copied:
            print(f"warning: no license text found for {name}")
        license_expr = meta.get("License-Expression") or (meta.get("License") or "").splitlines()[0:1]
        if isinstance(license_expr, list):
            license_expr = license_expr[0] if license_expr else ""
        inventory.append({"name": meta["Name"], "version": meta["Version"],
                          "license": license_expr, "files": copied})

    # Python runtime + Tcl/Tk (bundled by PyInstaller).
    py = OUT / f"Python-{sys.version.split()[0]}"
    py.mkdir()
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", py / "LICENSE.txt")
    for terms in Path(sys.base_prefix, "tcl").glob("*/license.terms"):
        shutil.copy2(terms, py / f"{terms.parent.name}-license.terms")
    inventory.append({"name": "Python (incl. Tcl/Tk, OpenSSL, libffi, SQLite, zlib, bzip2, xz)",
                      "version": sys.version.split()[0], "license": "PSF-2.0 (+ component licenses in LICENSE.txt)",
                      "files": [p.name for p in py.iterdir()]})

    # Executables staged by tools/fetch_tools.py.
    for tool in ("ffmpeg", "deno"):
        src = ROOT / "vendor" / tool
        if not src.is_dir():
            print(f"warning: vendor/{tool} not staged; run tools/fetch_tools.py")
            continue
        target = OUT / f"{tool}-binary"
        target.mkdir()
        for f in src.iterdir():
            if f.suffix.lower() in (".txt", ".md") and f.is_file():
                shutil.copy2(f, target / f.name)
        inventory.append({"name": f"{tool} (executable)", "version": "see SOURCE.txt",
                          "license": "LGPL-3.0-or-later" if tool == "ffmpeg" else "MIT",
                          "files": [p.name for p in target.iterdir()]})

    (OUT / "inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    print(f"Collected licenses for {len(inventory)} components into {OUT}")


if __name__ == "__main__":
    os.chdir(ROOT)
    collect()
