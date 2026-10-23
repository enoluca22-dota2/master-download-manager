# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Master Download Manager. Build with:  python build.py exe
# (build.py runs this from the isolated .venv and then adds tools/, extensions/
# and licenses next to the executable.)

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

datas = [
    ("mdm.ico", "."),
    ("mdm.png", "."),
    ("licenses", "licenses"),
    ("THIRD_PARTY_NOTICES.md", "."),
    ("ytd/update_public_key.pem", "."),  # verifies signed update manifests
]
binaries = []
hiddenimports = [
    "win11toast", "pystray._win32",
    # uvicorn picks these at runtime by name
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.loops.asyncio", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
]
hiddenimports += collect_submodules("winrt")

for package in ("customtkinter", "libtorrent"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

# YouTube JS-challenge solver scripts (vendored in yt-dlp + the yt-dlp-ejs package).
datas += collect_data_files("yt_dlp", includes=["**/*.js"])
datas += collect_data_files("yt_dlp_ejs", includes=["**/*.js"])

a = Analysis(
    ["master_download_manager.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # GPL-licensed: deliberately not bundled (see THIRD_PARTY_NOTICES.md)
        "mutagen",
        # development / test only
        "pytest", "_pytest", "httpx", "httpcore", "pip", "setuptools", "pkg_resources", "PyInstaller",
        "tkinter.test", "unittest.mock", "pydoc_data", "lib2to3", "numpy", "mypy",
    ],
    noarchive=False,
    optimize=0,
    # LGPL-3.0 pystray ships as plain .py files (not inside the PYZ archive)
    # so it can be inspected and replaced, as the LGPL requires.
    module_collection_mode={"pystray": "py"},
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MasterDownloadManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["mdm.ico"],
    version="version_info.txt",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MasterDownloadManager",
)
