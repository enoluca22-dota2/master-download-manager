"""Master Download Manager's own update mechanism (never yt-dlp's).

Discovery (Help → Check for Updates, the startup/daily check, Settings → Updates):

    GitHub Releases of GITHUB_REPO
      stable channel: GET /repos/<repo>/releases/latest
      beta channel:   GET /repos/<repo>/releases (highest version, pre-releases included)
    -> the release must belong to GITHUB_REPO (html_url and asset URLs checked)
    -> version = the tag, compared as a semantic version; never an older one
    -> update-manifest.json + update-manifest.sig (Ed25519, see below)
    -> installer asset name, URL, size and SHA-256 must agree between the
       signed manifest, the release's asset list and SHA256SUMS.txt

Trust model:
    * SHA-256 from SHA256SUMS.txt (hosted next to the installer) detects
      corruption, but someone who can change the release could change both.
    * update-manifest.json is signed with an Ed25519 key that never leaves the
      release machine (DPAPI-protected, tools/release_signing.py); the public
      key ships inside MDM (ytd/update_public_key.pem). Only a release whose
      signed manifest verifies is "verified" and may be installed with one
      click. Older releases (4.0.0) have no signed manifest: they're shown,
      with a link to the release page, but never installed automatically.
    * Authenticode: the installer isn't code-signed yet; when it is, Windows
      checks it when it runs (see docs/UPDATE_SECURITY.md).

Nothing is downloaded or installed without the user's confirmation;
installation itself is in ytd/update_install.py.

Legacy: a custom manifest URL (Settings → Updates → Custom manifest) in the
3.x/4.0 format {version, url, sha256, size, notes} is still supported for
checking and downloading; such updates aren't signed and so aren't one-click.
"""

import base64
import dataclasses
import hashlib
import ipaddress
import json
import os
import re
from urllib.parse import urlparse

import requests
from packaging.version import InvalidVersion, Version

from . import __version__
from .config import RESOURCE_DIR
from .logging_setup import get_logger

log = get_logger()

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
GITHUB_REPO = "enoluca22-dota2/master-download-manager"
GITHUB_API = "https://api.github.com"
_INSTALLER_RE = re.compile(r"^Master[ ._-]Download[ ._-]Manager[ ._-]Setup[ ._-][0-9][0-9A-Za-z.\-]*\.exe$", re.I)
MANIFEST_ASSET = "update-manifest.json"
SIGNATURE_ASSET = "update-manifest.sig"
SUMS_ASSET = "SHA256SUMS.txt"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_INSTALLER_BYTES = 2 * 1024 ** 3
PUBLIC_KEY_FILE = os.path.join(RESOURCE_DIR, "update_public_key.pem")
if not os.path.isfile(PUBLIC_KEY_FILE):  # running from source
    PUBLIC_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "update_public_key.pem")


class UpdateError(Exception):
    pass


@dataclasses.dataclass
class UpdateInfo:
    version: str
    current: str
    available: bool
    published: str = ""
    notes: str = ""
    size: int = None
    url: str = ""
    sha256: str = ""
    release_url: str = ""
    prerelease: bool = False
    verified: bool = False  # signed manifest verified -> may be installed with one click
    reason: str = ""  # why it isn't verified (shown to the user)
    failed: bool = False  # signed information present but wrong: possibly tampered, never offer to install

    def manifest(self):
        return {"version": self.version, "url": self.url, "sha256": self.sha256, "size": self.size, "notes": self.notes}


# --------------------------------------------------------------- transport -- #
def _is_loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def _require_secure(url, what):
    parsed = urlparse(url or "")
    if parsed.scheme == "https" and parsed.hostname:
        return
    if parsed.scheme == "http" and parsed.hostname and _is_loopback(parsed.hostname) and _test_profile():
        return  # local testing only (portable/test profile)
    raise UpdateError(f"The {what} must be an https:// address.")


def _get_bytes(url, timeout, limit, what):
    _require_secure(url, what)
    try:
        with requests.get(url, timeout=timeout, stream=True,
                          headers={"Accept": "application/vnd.github+json", "User-Agent": "MasterDownloadManager"}) as r:
            r.raise_for_status()
            raw = r.raw.read(limit + 1, decode_content=True)
    except requests.RequestException as e:
        raise UpdateError(f"Couldn't reach GitHub ({e.__class__.__name__}).") from e
    if len(raw) > limit:
        raise UpdateError(f"The {what} is unreasonably large.")
    return raw


def _get_json(url, timeout, limit=MAX_MANIFEST_BYTES * 8):
    raw = _get_bytes(url, timeout, limit, "update information")
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise UpdateError("GitHub returned something that isn't JSON.") from e


def _get_text(url, timeout, limit=64 * 1024):
    return _get_bytes(url, timeout, limit, "checksum list").decode("utf-8", "replace")


# ------------------------------------------------------- test configuration -- #
def _test_profile():
    return bool(os.environ.get("MDM_DATA_DIR"))


def _source():
    """(api_base, repo, public_key_file). Overridable only for a portable/test
    profile (MDM_DATA_DIR set) so the update path can be tested end to end
    against a local fake GitHub; a normal installation always uses GitHub."""
    api, repo, key = GITHUB_API, GITHUB_REPO, PUBLIC_KEY_FILE
    if _test_profile():
        api = os.environ.get("MDM_UPDATE_API", api)
        repo = os.environ.get("MDM_UPDATE_REPO", repo)
        key = os.environ.get("MDM_UPDATE_PUBKEY_FILE", key)
    return api.rstrip("/"), repo, key


# --------------------------------------------------------------- signatures -- #
def load_public_key(path=None):
    from Cryptodome.PublicKey import ECC
    path = path or _source()[2]
    try:
        with open(path, "rt", encoding="ascii") as f:
            return ECC.import_key(f.read())
    except (OSError, ValueError) as e:
        raise UpdateError("The update verification key is missing from this installation.") from e


def verify_signature(data, signature_b64, key=None):
    """True only if `signature_b64` is a valid Ed25519 signature of `data`."""
    from Cryptodome.Signature import eddsa
    key = key or load_public_key()
    try:
        signature = base64.b64decode(signature_b64.strip(), validate=True)
        eddsa.new(key, "rfc8032").verify(data, signature)
        return True
    except (ValueError, TypeError):
        return False


def public_key_fingerprint(key=None):
    key = key or load_public_key()
    return hashlib.sha256(key.export_key(format="raw")).hexdigest()[:16]


# ----------------------------------------------------------- legacy manifest -- #
def check_for_update(manifest_url, current_version=__version__, timeout=15):
    """Custom (unsigned) manifest URL. Returns (update_available, manifest)."""
    if not manifest_url:
        raise UpdateError("No update server is configured (Settings → Updates).")
    raw = _get_bytes(manifest_url, timeout, MAX_MANIFEST_BYTES, "update manifest")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise UpdateError("The update manifest isn't valid JSON.") from e
    manifest = validate_manifest(manifest)
    return Version(manifest["version"]) > Version(current_version), manifest


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
    return {"version": str(manifest["version"]), "url": url, "sha256": manifest["sha256"].lower(), "size": size,
            "notes": str(manifest.get("notes", ""))[:5000]}


# --------------------------------------------------------------- GitHub path -- #
def _parse_sums(text):
    sums = {}
    for line in (text or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and _SHA256_RE.match(parts[0]):
            sums[parts[1].lstrip("*").strip()] = parts[0].lower()
    return sums


def _installer_asset(release):
    return next((a for a in release.get("assets") or []
                 if isinstance(a, dict) and _INSTALLER_RE.match(str(a.get("name", "")))), None)


def manifest_from_github_release(release, sums_text, allow_prerelease=False):
    """Manifest from a GitHub release + its SHA256SUMS.txt (checksum only, unsigned)."""
    if not isinstance(release, dict) or release.get("draft") or (release.get("prerelease") and not allow_prerelease):
        raise UpdateError("No stable release found.")
    version = str(release.get("tag_name") or "").lstrip("vV")
    asset = _installer_asset(release)
    if asset is None:
        raise UpdateError("The latest release has no Windows installer.")
    sha = _parse_sums(sums_text).get(asset["name"])
    if not sha:
        raise UpdateError("The release's SHA256SUMS.txt doesn't list the installer; refusing it.")
    return validate_manifest({"version": version, "url": asset.get("browser_download_url", ""), "sha256": sha,
                              "size": asset.get("size"), "notes": release.get("body") or ""})


def _check_release_identity(release, repo):
    html = str(release.get("html_url") or "")
    if not html.lower().startswith(f"https://github.com/{repo}/releases/".lower()):
        raise UpdateError("That release doesn't belong to the official Master Download Manager repository.")
    prefix = f"https://github.com/{repo}/releases/download/".lower()
    for asset in release.get("assets") or []:
        url = str(asset.get("browser_download_url") or "")
        host = urlparse(url).hostname or ""
        if not url.lower().startswith(prefix) and not (_is_loopback(host) and _test_profile()):
            raise UpdateError(f"Unexpected download location for {asset.get('name')}; refusing the release.")


def _pick_release(api, repo, channel, timeout):
    if channel == "beta":
        releases = _get_json(f"{api}/repos/{repo}/releases?per_page=30", timeout)
        candidates = []
        for r in releases if isinstance(releases, list) else []:
            if not isinstance(r, dict) or r.get("draft"):
                continue
            try:
                candidates.append((Version(str(r.get("tag_name") or "").lstrip("vV")), r))
            except InvalidVersion:
                continue
        if not candidates:
            raise UpdateError("No releases found.")
        return max(candidates, key=lambda c: c[0])[1]
    release = _get_json(f"{api}/repos/{repo}/releases/latest", timeout)
    if not isinstance(release, dict):
        raise UpdateError("GitHub returned an unexpected answer.")
    return release


def verify_signed_manifest(manifest_bytes, signature_text, release, sums, key=None):
    """Checks the signed manifest against itself, the release and SHA256SUMS.
    Returns the parsed manifest; raises UpdateError on any mismatch."""
    if not verify_signature(manifest_bytes, signature_text, key):
        raise UpdateError("The update's signature is invalid; it was not published with the official release key.")
    try:
        m = json.loads(manifest_bytes.decode("utf-8"))
    except ValueError as e:
        raise UpdateError("The signed update manifest isn't valid JSON.") from e
    tag_version = str(release.get("tag_name") or "").lstrip("vV")
    inst = m.get("installer") or {}
    if m.get("app") != "Master Download Manager" or str(m.get("repository", "")).lower() != _source()[1].lower():
        raise UpdateError("The signed manifest is for a different application or repository.")
    if str(m.get("version")) != tag_version:
        raise UpdateError(f"The signed manifest is for {m.get('version')}, but the release is {tag_version}.")
    asset = _installer_asset(release)
    if asset is None or asset.get("name") != inst.get("name"):
        raise UpdateError("The release's installer isn't the one named in the signed manifest.")
    if asset.get("browser_download_url") != inst.get("url"):
        raise UpdateError("The installer's download address differs from the signed manifest.")
    if asset.get("size") != inst.get("size"):
        raise UpdateError("The installer's size differs from the signed manifest.")
    sha = str(inst.get("sha256", "")).lower()
    if not _SHA256_RE.match(sha) or sums.get(inst["name"]) != sha:
        raise UpdateError("The installer's SHA-256 differs between the signed manifest and SHA256SUMS.txt.")
    return m


def check_github(repo=None, current_version=__version__, timeout=15, channel="stable"):
    """Backwards-compatible: (update_available, legacy manifest dict)."""
    info = check_release(current_version=current_version, timeout=timeout, channel=channel, repo=repo)
    return info.available, info.manifest()


def check_release(current_version=__version__, timeout=15, channel="stable", repo=None):
    api, default_repo, key_file = _source()
    repo = repo or default_repo
    release = _pick_release(api, repo, channel, timeout)
    if release.get("prerelease") and channel != "beta":
        raise UpdateError("No stable release found.")
    _check_release_identity(release, repo)
    version = str(release.get("tag_name") or "").lstrip("vV")
    try:
        latest = Version(version)
    except InvalidVersion as e:
        raise UpdateError(f"The latest release has an invalid version ({version!r}).") from e
    assets = {a.get("name"): a for a in release.get("assets") or [] if isinstance(a, dict)}
    if SUMS_ASSET not in assets:
        raise UpdateError("The latest release has no SHA256SUMS.txt; refusing to update without a checksum.")
    sums_text = _get_text(assets[SUMS_ASSET]["browser_download_url"], timeout)
    manifest = manifest_from_github_release(release, sums_text, allow_prerelease=(channel == "beta"))
    info = UpdateInfo(version=version, current=current_version, available=latest > Version(current_version),
                      published=str(release.get("published_at") or ""), notes=str(release.get("body") or "")[:20000],
                      size=manifest["size"], url=manifest["url"], sha256=manifest["sha256"],
                      release_url=str(release.get("html_url") or ""), prerelease=bool(release.get("prerelease")))
    if MANIFEST_ASSET in assets and SIGNATURE_ASSET in assets:
        try:
            manifest_bytes = _get_bytes(assets[MANIFEST_ASSET]["browser_download_url"], timeout, MAX_MANIFEST_BYTES,
                                        "update manifest")
            signature = _get_text(assets[SIGNATURE_ASSET]["browser_download_url"], timeout, 4096)
            verify_signed_manifest(manifest_bytes, signature, release, _parse_sums(sums_text),
                                   load_public_key(key_file))
            info.verified = True
        except UpdateError as e:
            info.reason = str(e)
            info.failed = True
            log.warning("Update %s: %s", version, e)
    else:
        info.reason = ("This release has no signed update information, so it can't be installed automatically. "
                       "Download it from the release page instead.")
    return info


def check(settings, current_version=__version__):
    """Check the configured source. Returns (update_available, manifest)."""
    if (settings.get("update_source") or "github") == "manifest":
        return check_for_update(settings.get("update_manifest_url", ""), current_version)
    return check_github(current_version=current_version, channel=settings.get("update_channel") or "stable")


# ----------------------------------------------------------------- download -- #
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
            raise UpdateError("The installer size doesn't match the manifest (the download was interrupted or "
                              "altered); refusing it.")
        if digest.hexdigest() != manifest["sha256"]:
            raise UpdateError("The installer's SHA-256 checksum doesn't match the manifest; refusing it.")
        os.replace(part, final)
        log.info("Update %s downloaded and verified: %s", manifest["version"], final)
        return final
    except requests.RequestException as e:
        raise UpdateError(f"Downloading the update failed ({e.__class__.__name__}). Check your connection "
                          "and try again.") from e
    finally:
        if os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()
