"""
Creates a Desktop shortcut that launches Master Download Manager from source
(for the packaged app, the installer creates shortcuts).
Run this ONCE:
    python create_desktop_shortcut.py

Works on Windows, macOS, and Linux.
"""

import os
import sys
import stat
import platform

APP_DIR = os.path.dirname(os.path.abspath(__file__))
APP_SCRIPT = os.path.join(APP_DIR, "master_download_manager.py")
APP_ICON_FILE = os.path.join(APP_DIR, "mdm.ico")
DESKTOP_DIR = os.path.join(os.path.expanduser("~"), "Desktop")


def create_windows_shortcut():
    # Try the proper .lnk shortcut first (needs pywin32)
    try:
        import win32com.client  # type: ignore
        shortcut_path = os.path.join(DESKTOP_DIR, "Master Download Manager.lnk")
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(shortcut_path)
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        shortcut.Targetpath = pythonw if os.path.exists(pythonw) else sys.executable
        shortcut.Arguments = f'"{APP_SCRIPT}"'
        shortcut.WorkingDirectory = APP_DIR
        # Prefer the optional project icon when it is a real file.  Falling
        # back to Python's icon keeps shortcut creation dependable before an
        # icon asset has been designed and added to the project.
        shortcut.IconLocation = APP_ICON_FILE if os.path.isfile(APP_ICON_FILE) else sys.executable
        shortcut.save()
        print(f"Desktop shortcut created: {shortcut_path}")
        return
    except ImportError:
        pass  # fall back below

    # Fallback: a .bat launcher (double-click still works, just shows a console briefly)
    bat_path = os.path.join(DESKTOP_DIR, "Master Download Manager.bat")
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    launcher = pythonw if os.path.exists(pythonw) else sys.executable
    with open(bat_path, "w") as f:
        f.write(f'@echo off\r\n"{launcher}" "{APP_SCRIPT}"\r\n')
    print(f"Desktop launcher created: {bat_path}")
    print("(Tip: run 'pip install pywin32' and re-run this script for a proper icon+shortcut.)")


def create_mac_launcher():
    command_path = os.path.join(DESKTOP_DIR, "Master Download Manager.command")
    with open(command_path, "w") as f:
        f.write(f'#!/bin/bash\ncd "{APP_DIR}"\n"{sys.executable}" "{APP_SCRIPT}"\n')
    st = os.stat(command_path)
    os.chmod(command_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"Desktop launcher created: {command_path}")
    print("Double-click it to run (macOS may ask you to confirm the first time via right-click > Open).")


def create_linux_launcher():
    desktop_file = os.path.join(DESKTOP_DIR, "master-download-manager.desktop")
    content = f"""[Desktop Entry]
Type=Application
Name=Master Download Manager
Comment=Media, torrent and file download manager
Exec="{sys.executable}" "{APP_SCRIPT}"
Path={APP_DIR}
Terminal=false
Categories=Utility;
"""
    with open(desktop_file, "w") as f:
        f.write(content)
    st = os.stat(desktop_file)
    os.chmod(desktop_file, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"Desktop launcher created: {desktop_file}")
    print("If it doesn't run on double-click, right-click it and choose 'Allow Launching' or 'Trust'.")


def main():
    os.makedirs(DESKTOP_DIR, exist_ok=True)
    system = platform.system()
    if system == "Windows":
        create_windows_shortcut()
    elif system == "Darwin":
        create_mac_launcher()
    else:
        create_linux_launcher()


if __name__ == "__main__":
    main()
