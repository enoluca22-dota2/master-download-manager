"""Master Download Manager's own update mechanism (never yt-dlp's).

    Settings → Updates → "Check for updates" (always user-initiated)
        -> GET <update_manifest_url>         (HTTPS only)
        -> compare versions
        -> user chooses to download
        -> download installer, verify size + SHA-256 from the manifest
        -> user chooses to run it

Manifest format (JSON, published by whoever releases MDM):
    {
      "version": "3.1.0",
      "url": "https://downloads.example.com/MasterDownloadManager-Setup-3.1.0.exe",
      "sha256": "<64 hex chars>",
      "size": 123456789,
      "notes": "What's new ..."
    }

Nothing is downloaded or executed automatically. No update server is
configured by default, so the app never contacts one unless the user sets it.
"""

import hashlib
import ipaddress
import os
import re
from urllib.parse import urlparse

import requests
from packaging.version import InvalidVersion, Version

from . import __version__
from .logging_setup import get_logger

log = get_logger()

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
MAX_MANIFEST_BYTES = 64 * 1024
MAX_INSTALLER_BYTES = 2 * 1024 ** 3


class UpdateError(Exception):
    pass


def _is_loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def _require_secure(url, what):
    parsed = urlparse(url or "")
    if parsed.scheme == "https" and parsed.hostname:
        return
    if parsed.scheme == "http" and parsed.hostname and _is_loopback(parsed.hostname):
        return  # local testing only
    raise UpdateError(f"The {what} must be an https:// address.")


def check_for_update(manifest_url, current_version=__version__, timeout=15):
    """Returns (update_available: bool, manifest: dict). Raises UpdateError."""
    if not manifest_url:
        raise UpdateError("No update server is configured (Settings → Updates).")
    _require_secure(manifest_url, "update manifest URL")
    try:
        with requests.get(manifest_url, timeout=timeout, stream=True) as resp:
            resp.raise_for_status()
            raw = resp.raw.read(MAX_MANIFEST_BYTES + 1, decode_content=True)
    except requests.RequestException as e:
        raise UpdateError(f"Couldn't reach the update server ({e.__class__.__name__}).") from e
    if len(raw) > MAX_MANIFEST_BYTES:
        raise UpdateError("The update manifest is unreasonably large.")
    try:
        import json
        manifest = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise UpdateError("The update manifest isn't valid JSON.") from e
    manifest = validate_manifest(manifest)
    available = Version(manifest["version"]) > Version(current_version)
    return available, manifest


def validate_manifest(manifest):
    if not isinstance(manifest, dict):
        raise UpdateError("The update manifest has the wrong format.")
    try:
        Version(str(manifest.get("version", "")))
    except InvalidVersion as e:
        raise UpdateError("The update manifest has an invalid version number.") from e
    url = manifest.get("url", "")
    _require_secure(url, "installer download URL")
    if not _SHA256_RE.match(str(manifest.get("sha256", ""))):
        raise UpdateError("The update manifest has no valid SHA-256 checksum; refusing to use it.")
    size = manifest.get("size")
    if size is not None and (not isinstance(size, int) or size <= 0 or size > MAX_INSTALLER_BYTES):
        raise UpdateError("The update manifest has an invalid size.")
    return {
        "version": str(manifest["version"]),
        "url": url,
        "sha256": manifest["sha256"].lower(),
        "size": size,
        "notes": str(manifest.get("notes", ""))[:5000],
    }


def download_update(manifest, dest_dir, progress=None, cancel_event=None, timeout=30):
    """Download the installer and verify it. Returns the verified file path.
    A file that fails verification is deleted and never returned."""
    manifest = validate_manifest(manifest)
    os.makedirs(dest_dir, exist_ok=True)
    name = os.path.basename(urlparse(manifest["url"]).path) or "MasterDownloadManager-Setup.exe"
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    final = os.path.join(dest_dir, name)
    part = final + ".part"
    digest = hashlib.sha256()
    received = 0
    try:
        with requests.get(manifest["url"], stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            with open(part, "wb") as f:
                for chunk in resp.iter_content(256 * 1024):
                    if cancel_event is not None and cancel_event.is_set():
                        raise UpdateError("Update download cancelled.")
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > (manifest["size"] or MAX_INSTALLER_BYTES):
                        raise UpdateError("The installer is larger than the manifest says; refusing it.")
                    digest.update(chunk)
                    f.write(chunk)
                    if progress:
                        progress(received, manifest["size"])
        if manifest["size"] is not None and received != manifest["size"]:
            raise UpdateError("The installer size doesn't match the manifest; refusing it.")
        if digest.hexdigest() != manifest["sha256"]:
            raise UpdateError("The installer's SHA-256 checksum doesn't match the manifest; refusing it.")
        os.replace(part, final)
        log.info("Update %s downloaded and verified: %s", manifest["version"], final)
        return final
    except requests.RequestException as e:
        raise UpdateError(f"Downloading the update failed ({e.__class__.__name__}).") from e
    finally:
        if os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass
