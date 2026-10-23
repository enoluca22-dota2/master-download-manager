"""Compatibility launcher: older shortcuts point at youtube_downloader.py.
The application is now Master Download Manager (master_download_manager.py)."""

import sys

from master_download_manager import main

if __name__ == "__main__":
    sys.exit(main())
