"""Update-manifest signing for Master Download Manager releases (Ed25519).

    .venv\\Scripts\\python tools/release_signing.py init             # once: create the key pair
    .venv\\Scripts\\python tools/release_signing.py sign FILE        # writes FILE.sig (base64)
    .venv\\Scripts\\python tools/release_signing.py verify FILE      # checks FILE.sig with the shipped public key
    .venv\\Scripts\\python tools/release_signing.py export-backup OUT   # passphrase-protected backup of the private key
    .venv\\Scripts\\python tools/release_signing.py fingerprint

The private key never enters the repository. It is stored at KEY_FILE,
encrypted with Windows DPAPI for the current Windows user (only that account
on this PC can decrypt it). The public key is written to
ytd/update_public_key.pem and ships inside the app.

Losing the private key means installed copies can no longer verify new
releases: they only offer "Open Release Page", and users have to install one
release by hand (one that ships a new public key) before one-click updates
work again. Keep the export-backup file somewhere safe and offline. See
docs/UPDATE_SECURITY.md.
"""

import base64
import getpass
import os
import sys
from pathlib import Path

from Cryptodome.PublicKey import ECC
from Cryptodome.Signature import eddsa

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_KEY = ROOT / "ytd" / "update_public_key.pem"
KEY_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "MasterDownloadManager-release"
KEY_FILE = KEY_DIR / "update-signing-key.dpapi"


def _protect(data):
    import win32crypt
    return win32crypt.CryptProtectData(data, "MDM update signing key", None, None, None, 0)


def _unprotect(blob):
    import win32crypt
    return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]


def load_private_key():
    if not KEY_FILE.is_file():
        sys.exit(f"No signing key at {KEY_FILE}. Run: python tools/release_signing.py init")
    return ECC.import_key(_unprotect(KEY_FILE.read_bytes()).decode("ascii"))


def fingerprint(key):
    import hashlib
    return hashlib.sha256(key.public_key().export_key(format="raw")).hexdigest()[:16]


def init():
    if KEY_FILE.exists():
        sys.exit(f"A signing key already exists at {KEY_FILE}; refusing to overwrite it.")
    key = ECC.generate(curve="ed25519")
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(_protect(key.export_key(format="PEM").encode("ascii")))
    PUBLIC_KEY.write_text(key.public_key().export_key(format="PEM") + "\n", encoding="ascii")
    print(f"Private key (DPAPI-protected): {KEY_FILE}")
    print(f"Public key (ships in the app): {PUBLIC_KEY}")
    print(f"Fingerprint: {fingerprint(key)}")


def sign(path):
    key = load_private_key()
    data = Path(path).read_bytes()
    sig = base64.b64encode(eddsa.new(key, "rfc8032").sign(data)).decode("ascii")
    pub = ECC.import_key(PUBLIC_KEY.read_text(encoding="ascii"))
    if pub.export_key(format="raw") != key.public_key().export_key(format="raw"):
        sys.exit("The private key doesn't match ytd/update_public_key.pem; refusing to sign.")
    Path(str(path) + ".sig").write_text(sig + "\n", encoding="ascii")
    print(f"Signed {path} -> {path}.sig (key {fingerprint(key)})")


def verify(path):
    pub = ECC.import_key(PUBLIC_KEY.read_text(encoding="ascii"))
    sig = base64.b64decode(Path(str(path) + ".sig").read_text(encoding="ascii").strip())
    try:
        eddsa.new(pub, "rfc8032").verify(Path(path).read_bytes(), sig)
    except ValueError:
        sys.exit("Signature INVALID")
    print("Signature valid")


def export_backup(out):
    key = load_private_key()
    passphrase = getpass.getpass("Backup passphrase (min 12 characters): ")
    if len(passphrase) < 12 or passphrase != getpass.getpass("Repeat: "):
        sys.exit("Passphrases differ or are too short.")
    pem = key.export_key(format="PEM", passphrase=passphrase, protection="PBKDF2WithHMAC-SHA512AndAES256-CBC",
                         prot_params={"iteration_count": 600_000})
    Path(out).write_text(pem, encoding="ascii")
    print(f"Encrypted backup written to {out}")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "init":
        init()
    elif cmd == "sign" and args:
        sign(args[0])
    elif cmd == "verify" and args:
        verify(args[0])
    elif cmd == "export-backup" and args:
        export_backup(args[0])
    elif cmd == "fingerprint":
        print(fingerprint(load_private_key()))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
