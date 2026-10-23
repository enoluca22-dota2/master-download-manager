"""License inventory for the Master Download Manager build: collection and
release gate.

    .venv\\Scripts\\python tools/collect_licenses.py            # collect texts + licenses/inventory.json
    .venv\\Scripts\\python tools/collect_licenses.py --verify   # compare with the packaged build (fails on problems)

Collect (run before PyInstaller, inside the build venv so versions match):
  * every Python distribution in CATALOG: version, author and repository from
    its own package metadata (curated values only where the metadata lacks
    them), its license files from its dist-info (or a verbatim upstream copy in
    tools/license_texts/ when the wheel ships none)
  * the native libraries the Python runtime bundles, FFmpeg and deno
  * the SPDX identifier comes from the catalog and is checked against the
    actual license text (classify_text) — package metadata is sometimes wrong
    (e.g. CustomTkinter's classifier says CC0, its LICENSE is MIT)

Verify (run after PyInstaller; build.py does both): the set of distributions
whose modules PyInstaller actually bundled (from its PYZ/COLLECT tables) must
equal the Python components in the inventory; every component needs a
license text, author, version, repository and a known SPDX license matching
its text; dist-info versions inside the build must match; every license file
listed must exist in the packaged licenses folder. Any problem exits non-zero.
"""

import argparse
import ast
import datetime
import email.header
import importlib.metadata as md
import json
import re
import shutil
import ssl
import sqlite3
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "licenses"
MANUAL = Path(__file__).resolve().parent / "license_texts"
BUILD_DIR = ROOT / "build" / "pyinstaller" / "MasterDownloadManager"
DIST_DIR = ROOT / "dist" / "MasterDownloadManager"

# ---------------------------------------------------------------- catalog -- #
# Python distributions that may be bundled. spdx: verified against the text.
# repo/author: only when the package metadata doesn't provide them (sources:
# the project's PyPI page / repository). extra_texts: verbatim upstream texts
# from tools/license_texts for code statically included in the wheel.
CATALOG = {
    "annotated-doc": {"spdx": "MIT"},
    "annotated-types": {"spdx": "MIT"},
    "anyio": {"spdx": "MIT"},
    "brotli": {"spdx": "MIT"},
    "certifi": {"spdx": "MPL-2.0", "extra_texts": ["MPL-2.0.txt"]},
    "charset-normalizer": {"spdx": "MIT", "repo": "https://github.com/jawah/charset_normalizer"},
    "click": {"spdx": "BSD-3-Clause"},
    "customtkinter": {"spdx": "MIT", "repo": "https://github.com/TomSchimansky/CustomTkinter",
                      "note": "Package metadata says CC0; the shipped LICENSE file is MIT."},
    "darkdetect": {"spdx": "BSD-3-Clause"},
    "fastapi": {"spdx": "MIT"},
    "h11": {"spdx": "MIT"},
    "idna": {"spdx": "BSD-3-Clause", "repo": "https://github.com/kjd/idna"},
    "libtorrent": {"spdx": "BSD-3-Clause AND BSL-1.0 AND Apache-2.0", "repo": "https://github.com/arvidn/libtorrent",
                   "extra_texts": ["libtorrent-LICENSE.txt", "boost-LICENSE_1_0.txt", "openssl-LICENSE.txt"],
                   "note": "The wheel statically links Boost (BSL-1.0) and OpenSSL 3 (Apache-2.0)."},
    "opentelemetry-api": {"spdx": "Apache-2.0", "repo": "https://github.com/open-telemetry/opentelemetry-python"},
    "packaging": {"spdx": "Apache-2.0 OR BSD-2-Clause"},
    "pillow": {"spdx": "MIT-CMU", "repo": "https://github.com/python-pillow/Pillow"},
    "pycryptodomex": {"spdx": "BSD-2-Clause AND LicenseRef-Public-Domain",
                      "note": "Partly public domain, partly BSD-2-Clause (see LICENSE.rst)."},
    "pydantic": {"spdx": "MIT"},
    "pydantic_core": {"spdx": "MIT", "repo": "https://github.com/pydantic/pydantic-core"},
    "pystray": {"spdx": "LGPL-3.0-only",
                "note": "Shipped as plain, replaceable .py files (LGPL requirement). Metadata: LGPLv3."},
    "pywin32": {"spdx": "BSD-3-Clause",
                "note": "MDM bundles pywin32's win32, win32com and Pythonwin (win32ui) parts, which are "
                        "BSD-3-Clause (Mark Hammond, Greg Stein). The other texts pywin32 ships (MIT, PSF, "
                        "LGPL-2.1 for adodbapi, Scintilla) are included as distributed; adodbapi isn't bundled."},
    "requests": {"spdx": "Apache-2.0"},
    "six": {"spdx": "MIT"},
    "starlette": {"spdx": "BSD-3-Clause"},
    "typing-inspection": {"spdx": "MIT"},
    "typing_extensions": {"spdx": "PSF-2.0", "repo": "https://github.com/python/typing_extensions"},
    "urllib3": {"spdx": "MIT", "repo": "https://github.com/urllib3/urllib3"},
    "uvicorn": {"spdx": "BSD-3-Clause", "repo": "https://github.com/Kludex/uvicorn"},
    "websockets": {"spdx": "BSD-3-Clause", "repo": "https://github.com/python-websockets/websockets"},
    "win11toast": {"spdx": "MIT"},
    "yt-dlp": {"spdx": "Unlicense"},
    "yt-dlp-ejs": {"spdx": "Unlicense AND MIT AND ISC", "repo": "https://github.com/yt-dlp/ejs",
                   "note": "Its solver script bundles meriyah (ISC) and astring (MIT); their license header is "
                           "extracted verbatim from the shipped lib.min.js."},
}
# pywinrt ships many tiny distributions (winrt-runtime + one per Windows
# namespace), all from the same repository under the same license.
WINRT = {"spdx": "MIT", "repo": "https://github.com/pywinrt/pywinrt", "author": "pywinrt contributors",
         "extra_texts": ["pywinrt-LICENSE.txt"]}

# Native code bundled by the Python 3.12.10 Windows runtime. Versions: CPython
# v3.12.10 PCbuild/get_externals.bat and the runtime itself (see SOURCES.txt).
NATIVE = [
    {"id": "openssl", "name": "OpenSSL", "author": "The OpenSSL Project Authors", "spdx": "Apache-2.0",
     "version": lambda: ssl.OPENSSL_VERSION.split()[1], "repo": "https://github.com/openssl/openssl",
     "texts": ["openssl-3.0.16-LICENSE.txt"], "bundled_as": "libcrypto-3.dll, libssl-3.dll (Python's ssl/hashlib)"},
    {"id": "sqlite", "name": "SQLite", "author": "D. Richard Hipp and SQLite contributors", "spdx": "blessing",
     "version": lambda: sqlite3.sqlite_version, "repo": "https://sqlite.org/src",
     "texts": ["sqlite-public-domain.txt"], "bundled_as": "sqlite3.dll"},
    {"id": "tcl", "name": "Tcl", "author": "Regents of the University of California, Sun Microsystems, et al.",
     "spdx": "TCL", "version": lambda: _tcl_version(), "repo": "https://github.com/tcltk/tcl",
     "texts": ["tcl-8.6.15-license.terms"], "bundled_as": "tcl86t.dll + _tcl_data"},
    {"id": "tk", "name": "Tk", "author": "Regents of the University of California, Sun Microsystems, et al.",
     "spdx": "TCL", "version": lambda: _tcl_version(), "repo": "https://github.com/tcltk/tk",
     "texts": ["@python:tk8.6/license.terms"], "bundled_as": "tk86t.dll + _tk_data"},
    {"id": "zlib", "name": "zlib", "author": "Jean-loup Gailly and Mark Adler", "spdx": "Zlib",
     "version": lambda: zlib.ZLIB_RUNTIME_VERSION, "repo": "https://github.com/madler/zlib",
     "texts": ["zlib-1.3.1-LICENSE.txt"], "bundled_as": "inside python312.dll"},
    {"id": "libffi", "name": "libffi", "author": "Anthony Green, Red Hat, Inc and others", "spdx": "MIT",
     "version": lambda: "3.4.4", "repo": "https://github.com/libffi/libffi",
     "texts": ["libffi-3.4.4-LICENSE.txt"], "bundled_as": "libffi-8.dll (ctypes)"},
    {"id": "expat", "name": "Expat", "author": "Thai Open Source Software Center Ltd, Clark Cooper, Expat maintainers",
     "spdx": "MIT", "version": lambda: _expat_version(), "repo": "https://github.com/libexpat/libexpat",
     "texts": ["expat-2.7.1-COPYING.txt"], "bundled_as": "pyexpat.pyd"},
    {"id": "bzip2", "name": "bzip2", "author": "Julian Seward", "spdx": "bzip2-1.0.6",
     "version": lambda: "1.0.8", "repo": "https://gitlab.com/bzip2/bzip2",
     "texts": ["bzip2-1.0.8-LICENSE.txt"], "bundled_as": "_bz2.pyd"},
    {"id": "xz", "name": "XZ Utils (liblzma)", "author": "Lasse Collin and XZ Utils contributors",
     "spdx": "LicenseRef-Public-Domain", "version": lambda: "5.2.5", "repo": "https://github.com/tukaani-project/xz",
     "texts": ["xz-5.2.5-COPYING.txt"], "bundled_as": "_lzma.pyd"},
]

BINARIES = [
    {"id": "ffmpeg", "name": "FFmpeg / FFprobe", "author": "The FFmpeg developers", "spdx": "LGPL-3.0-or-later",
     "repo": "https://git.ffmpeg.org/ffmpeg.git", "vendor": "ffmpeg", "bundled_as": "tools/ffmpeg (separate executables + DLLs)",
     "note": "BtbN LGPL shared build (built with --enable-version3; no GPL or non-free parts). Source location in SOURCE.txt."},
    {"id": "deno", "name": "Deno", "author": "the Deno authors", "spdx": "MIT", "repo": "https://github.com/denoland/deno",
     "vendor": "deno", "bundled_as": "tools/deno/deno.exe (runs yt-dlp's YouTube scripts, sandboxed)"},
]

PYTHON = {"id": "python", "name": "Python", "author": "Python Software Foundation", "spdx": "PSF-2.0",
          "repo": "https://github.com/python/cpython", "bundled_as": "python312.dll + standard library",
          "note": "LICENSE.txt also carries the terms for the Microsoft Visual C++ runtime DLLs shipped with Python."}
MSVC = {"id": "msvc-runtime", "name": "Microsoft Visual C++ Runtime / Universal CRT", "author": "Microsoft Corporation",
        "spdx": "LicenseRef-Microsoft-Distributable-Code", "repo": "",
        "homepage": "https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files",
        "bundled_as": "VCRUNTIME140*.dll, MSVCP140.dll, ucrtbase.dll, api-ms-win-*.dll",
        "note": "Redistributed under the 'Distributable Code' conditions included in Python's LICENSE.txt."}

# --------------------------------------------------------- classification -- #
SPDX_KNOWN = {"MIT", "MIT-CMU", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "LGPL-3.0-only", "LGPL-3.0-or-later",
              "MPL-2.0", "Unlicense", "PSF-2.0", "BSL-1.0", "ISC", "Zlib", "TCL", "bzip2-1.0.6", "blessing",
              "LicenseRef-Public-Domain", "LicenseRef-Microsoft-Distributable-Code"}
# identifiers that are notices rather than a classifiable license text
UNCHECKED = {"blessing", "LicenseRef-Public-Domain", "LicenseRef-Microsoft-Distributable-Code"}


def classify_text(text):
    """SPDX identifiers whose license text appears in `text` (marker phrases)."""
    t = re.sub(r"\s+", " ", text)
    found = set()
    if "Permission is hereby granted, free of charge" in t:
        found.add("MIT")
    if re.search(r"Permission to use, copy, modify,? and distribute this software and its", t):
        found.add("MIT-CMU")
    if "Apache License" in t and "Version 2.0" in t:
        found.add("Apache-2.0")
    if "Redistribution and use in source and binary forms" in t:
        third = re.search(r"Neither the name|names of (its|the|his) contributors|endorse or promote", t)
        found.add("BSD-3-Clause" if third else "BSD-2-Clause")
    if "GNU LESSER GENERAL PUBLIC LICENSE" in t and "Version 3" in t:
        found.update({"LGPL-3.0-only", "LGPL-3.0-or-later"})
    if re.search(r"Mozilla Public License,? (?:[Vv]ersion|v\.) 2\.0", t):
        found.add("MPL-2.0")
    if "This is free and unencumbered software released into the public domain" in t:
        found.add("Unlicense")
    if re.search(r"PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2|Python Software Foundation License", t, re.I):
        found.add("PSF-2.0")
    if "Boost Software License - Version 1.0" in t:
        found.add("BSL-1.0")
    if re.search(r"Permission to use, copy, modify, and(/or)? distribute this software for any purpose with or without fee", t):
        found.add("ISC")
    if "This software is provided 'as-is', without any express or implied warranty" in t:
        found.add("Zlib")
    if 'This program, "bzip2", the associated library "libbzip2"' in t:
        found.add("bzip2-1.0.6")
    if "Regents of the University of California, Sun Microsystems" in t:
        found.add("TCL")
    return found


def spdx_ids(expression):
    return {p.strip() for p in re.split(r"\s+(?:AND|OR|WITH)\s+|[()]", expression) if p.strip()}


# ------------------------------------------------------------------ helpers -- #
def _tcl_version():
    import tkinter
    return tkinter.Tcl().eval("info patchlevel")


def _expat_version():
    import pyexpat
    return pyexpat.EXPAT_VERSION.replace("expat_", "")


def _decode(value):
    if not value:
        return ""
    return str(email.header.make_header(email.header.decode_header(value)))


def metadata_author(meta):
    """Author names from package metadata (emails stripped), else maintainer."""
    for key in ("Author", "Author-email", "Maintainer", "Maintainer-email"):
        raw = _decode(meta.get(key))
        if not raw:
            continue
        names = [re.sub(r"<[^>]*>", "", part).strip().strip('"') for part in raw.split(",")]
        names = [n for n in names if n and "@" not in n]
        if names:
            return ", ".join(dict.fromkeys(names))
    return ""


def metadata_repo(meta):
    urls = {}
    for entry in meta.get_all("Project-URL") or []:
        label, _, url = entry.partition(",")
        urls[label.strip().lower()] = url.strip()
    for label in ("source", "source code", "repository", "code", "github", "homepage"):
        if label in urls and re.search(r"github\.com|gitlab\.com|git\.", urls[label]):
            return re.sub(r"(/tree/.*|/blob/.*)$", "", urls[label].rstrip("/"))
    home = meta.get("Home-page") or ""
    return home.rstrip("/") if re.search(r"github\.com|gitlab\.com", home) else ""


def dist_license_files(dist):
    for f in dist.files or []:
        s = str(f).replace("\\", "/").lower()
        if ".dist-info/" in s and any(k in f.name.lower() for k in ("licen", "copying", "notice")):
            yield Path(dist.locate_file(f))


def python_dists():
    names = dict(CATALOG)
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if name and name.lower().startswith("winrt-"):
            names[name] = dict(WINRT)
    return names


def ejs_bundled_header():
    """The license header the yt-dlp-ejs build embeds in the shipped solver script."""
    import yt_dlp_ejs
    js = Path(yt_dlp_ejs.__file__).parent / "yt" / "solver" / "lib.min.js"
    m = re.search(r"/\*!.*?\*/", js.read_text(encoding="utf-8"), re.S)
    if not m:
        return "", []
    text = "\n".join(line.strip().lstrip("*").rstrip()[1:] if line.strip().startswith("* ") else
                     line.strip().lstrip("/*!").rstrip("*/") for line in m.group(0).splitlines())
    deps = []
    for chunk in text.split("---"):
        fields = dict(re.findall(r"^(Name|Version|License|Repository|Author): (.+)$", chunk, re.M))
        if "Name" in fields:
            body = re.split(r"^Author: .+$", chunk, maxsplit=1, flags=re.M)[-1].strip()
            fields["text"] = body
            deps.append(fields)
    return text, deps


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


# ------------------------------------------------------------------ collect -- #
def collect(app_version):
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    comps = []

    def add(entry, files):
        folder = OUT / entry["id"]
        folder.mkdir(exist_ok=True)
        rel = []
        for src in files:
            src = Path(src)
            if not src.is_file():
                continue  # reported by check_entries as a missing text
            dest = folder / src.name
            if dest.exists():
                dest = folder / f"{src.parent.name}-{src.name}"
            shutil.copy2(src, dest)
            rel.append(dest.relative_to(OUT).as_posix())
        entry["license_files"] = rel
        comps.append(entry)

    for name, cat in sorted(python_dists().items(), key=lambda kv: kv[0].lower()):
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue  # not installed in this venv, so it can't be bundled either
        meta = dist.metadata
        files = list(dist_license_files(dist)) + [MANUAL / t for t in cat.get("extra_texts", [])]
        add({"id": f"{meta['Name']}-{meta['Version']}", "name": meta["Name"], "kind": "python-package",
             "version": meta["Version"], "author": cat.get("author") or metadata_author(meta),
             "license": cat["spdx"], "repository": cat.get("repo") or metadata_repo(meta),
             "homepage": meta.get("Home-page") or "", "bundled_as": "Python package inside _internal",
             "note": cat.get("note", "")}, files)

    header, deps = ejs_bundled_header()
    if header:
        ejs = next(c for c in comps if c["name"] == "yt-dlp-ejs")
        dest = OUT / ejs["id"] / "bundled-dependencies-header.txt"
        dest.write_text(header, encoding="utf-8")
        ejs["license_files"].append(dest.relative_to(OUT).as_posix())
        for d in deps:
            cid = f"{d['Name']}-{d['Version']}"
            (OUT / cid).mkdir(exist_ok=True)
            (OUT / cid / "LICENSE.txt").write_text(d["text"] + "\n", encoding="utf-8")
            repo = re.sub(r"^git\+|\.git$", "", d.get("Repository", ""))
            comps.append({"id": cid, "name": d["Name"], "kind": "bundled-javascript", "version": d["Version"],
                          "author": re.sub(r"\s*[<(].*$", "", d.get("Author", "")).strip(), "license": d["License"],
                          "repository": repo, "homepage": "", "bundled_as": "inside yt-dlp-ejs's lib.min.js",
                          "note": "License header taken verbatim from the shipped file.",
                          "license_files": [f"{cid}/LICENSE.txt"]})

    py_version = sys.version.split()[0]
    add(dict(PYTHON, id=f"python-{py_version}", kind="runtime", version=py_version, license=PYTHON["spdx"],
             repository=PYTHON["repo"], homepage="https://www.python.org"), [Path(sys.base_prefix) / "LICENSE.txt"])
    add(dict(MSVC, kind="runtime", version="as shipped with Python " + py_version, license=MSVC["spdx"],
             repository=""), [Path(sys.base_prefix) / "LICENSE.txt"])
    for n in NATIVE:
        files = []
        for t in n["texts"]:
            if t.startswith("@python:"):
                files.append(Path(sys.base_prefix) / "tcl" / t.split(":", 1)[1])
            else:
                files.append(MANUAL / t)
        add({"id": n["id"], "name": n["name"], "kind": "native-library", "version": n["version"](),
             "author": n["author"], "license": n["spdx"], "repository": n["repo"], "homepage": "",
             "bundled_as": n["bundled_as"], "note": f"Bundled by the Python {py_version} Windows runtime."}, files)
    for b in BINARIES:
        src = ROOT / "vendor" / b["vendor"]
        source_txt = (src / "SOURCE.txt").read_text(encoding="utf-8") if (src / "SOURCE.txt").exists() else ""
        m = re.search(r"(?:FFmpeg|deno)\s+(\S+)", source_txt)
        add({"id": b["id"], "name": b["name"], "kind": "executable", "version": m.group(1) if m else "",
             "author": b["author"], "license": b["spdx"], "repository": b["repo"], "homepage": "",
             "bundled_as": b["bundled_as"], "note": b.get("note", "")},
            [f for f in src.iterdir() if f.is_file() and f.suffix.lower() in (".txt", ".md")] if src.is_dir() else [])

    add({"id": "ab-download-manager", "name": "AB Download Manager (design reference)", "kind": "design-reference",
         "version": "1.11.1", "author": "AmirHossein Abdolmotallebi", "license": "Apache-2.0",
         "repository": "https://github.com/amir1376/ab-download-manager", "homepage": "https://abdownloadmanager.com",
         "bundled_as": "not bundled - techniques adapted, no code copied",
         "note": "Credited for the download-engine, queue and UI techniques MDM adapted."},
        [MANUAL / "ab-download-manager-LICENSE.txt"])

    inventory = {"schema": 2, "app": "Master Download Manager", "app_version": app_version,
                 "generated": datetime.datetime.now().isoformat(timespec="seconds"), "components": comps}
    (OUT / "inventory.json").write_text(json.dumps(inventory, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Collected {len(comps)} components into {OUT}")
    return check_entries(inventory, OUT)


# ------------------------------------------------------------------- verify -- #
def check_entries(inventory, licenses_dir):
    problems = []
    for c in inventory["components"]:
        label = f"{c['name']} {c['version']}"
        for field in ("author", "version", "license"):
            if not c.get(field):
                problems.append(f"{label}: missing {field}")
        if not (c.get("repository") or c.get("homepage")):
            problems.append(f"{label}: missing source reference")
        ids = spdx_ids(c.get("license", ""))
        unknown = ids - SPDX_KNOWN
        if unknown:
            problems.append(f"{label}: unknown license id(s) {sorted(unknown)}")
        if not c.get("license_files"):
            problems.append(f"{label}: no license text")
            continue
        texts = ""
        for rel in c["license_files"]:
            p = licenses_dir / rel
            if not p.is_file():
                problems.append(f"{label}: license file missing: {rel}")
            else:
                texts += p.read_text(encoding="utf-8", errors="replace") + "\n"
        detected = classify_text(texts)
        expected = ids - UNCHECKED
        ok = bool(expected & detected) if " OR " in c["license"] else expected <= detected
        if expected and not ok:
            problems.append(f"{label}: declared {c['license']} but its text matches {sorted(detected) or 'no known license'}")
    return problems


def bundled_distributions(build_dir):
    """{normalized dist name: evidence} for everything PyInstaller bundled."""
    names = set()

    def walk(node):
        if isinstance(node, (list, tuple)):
            if len(node) == 3 and all(isinstance(x, str) for x in node):
                names.add(node[0])
            else:
                for x in node:
                    walk(x)
    for toc in ("PYZ-00.toc", "COLLECT-00.toc"):
        walk(ast.literal_eval((build_dir / toc).read_text(encoding="utf-8")))
    pkg_map = md.packages_distributions()
    dists = {}
    for n in names:
        first = n.replace("\\", "/").split("/")[0]
        if first.endswith(".dist-info"):
            dists.setdefault(norm(re.sub(r"-[^-]+\.dist-info$", "", first)), set()).add(first)
            continue
        top = first.split(".")[0]
        for d in pkg_map.get(top, []):
            dists.setdefault(norm(d), set()).add(top)
    return dists


def verify(build_dir=BUILD_DIR, dist_dir=DIST_DIR):
    problems = []
    lic_dir = dist_dir / "licenses"
    inv_path = lic_dir / "inventory.json"
    if not inv_path.is_file():
        return [f"{inv_path} is missing from the build"]
    inventory = json.loads(inv_path.read_text(encoding="utf-8"))
    source_inv = ROOT / "licenses" / "inventory.json"
    if dist_dir == DIST_DIR and source_inv.is_file() and source_inv.read_bytes() != inv_path.read_bytes():
        problems.append("the build's licenses/inventory.json differs from the repository's (stale build or edit)")
    problems += check_entries(inventory, lic_dir)
    claimed = {norm(c["name"]): c for c in inventory["components"] if c["kind"] == "python-package"}
    bundled = bundled_distributions(build_dir)
    for d in sorted(set(bundled) - set(claimed)):
        problems.append(f"bundled but not in the inventory: {d} (evidence: {sorted(bundled[d])[:3]})")
    for d in sorted(set(claimed) - set(bundled)):
        problems.append(f"in the inventory but not bundled: {d}")
    for d, c in claimed.items():
        try:
            installed = md.version(c["name"])
        except md.PackageNotFoundError:
            problems.append(f"{d}: not installed in the build environment")
            continue
        if installed != c["version"]:
            problems.append(f"{d}: inventory says {c['version']}, build environment has {installed}")
        for info in (dist_dir / "_internal").glob("*.dist-info"):
            name, _, ver = info.name[:-len(".dist-info")].rpartition("-")
            if norm(name) == d and ver != c["version"]:
                problems.append(f"{d}: packaged dist-info is {ver}, inventory says {c['version']}")
    markers = {"openssl": "_internal/libcrypto-3.dll", "sqlite": "_internal/sqlite3.dll", "tcl": "_internal/tcl86t.dll",
               "tk": "_internal/tk86t.dll", "libffi": "_internal/libffi-8.dll", "bzip2": "_internal/_bz2.pyd",
               "xz": "_internal/_lzma.pyd", "expat": "_internal/pyexpat.pyd", "zlib": "_internal/python312.dll",
               "ffmpeg": "tools/ffmpeg/ffmpeg.exe", "deno": "tools/deno/deno.exe"}
    ids = {c["id"] for c in inventory["components"]}
    for cid, marker in markers.items():
        present = (dist_dir / marker).exists()
        if present and cid not in ids:
            problems.append(f"{marker} is shipped but has no inventory entry")
        if cid in ids and not present:
            problems.append(f"{cid} is listed but {marker} isn't in the build")
    return problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="check the packaged build instead of collecting")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.verify:
        problems = verify()
    else:
        sys.path.insert(0, str(ROOT))
        from ytd import __version__
        problems = collect(__version__)
    for p in problems:
        print(f"LICENSE GATE: {p}")
    if problems:
        print(f"{len(problems)} license problem(s) - release blocked.")
        sys.exit(1)
    print("License gate passed." if args.verify else "Inventory complete.")


if __name__ == "__main__":
    main()
