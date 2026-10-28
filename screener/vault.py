"""Encrypt/decrypt the private database so it can sit in a public repo.

The key comes from a passphrase (GitHub secret POSITIONS_KEY). Without it the file is unreadable.

Usage:
    python -m screener.vault encrypt private.db private.db.enc
    python -m screener.vault decrypt private.db.enc private.db
(passphrase read from the POSITIONS_KEY environment variable)
"""
import base64
import hashlib
import os
import sys

from cryptography.fernet import Fernet


def _fernet(passphrase):
    if not passphrase:
        raise SystemExit("POSITIONS_KEY is not set")
    key = base64.urlsafe_b64encode(hashlib.sha256(passphrase.encode()).digest())
    return Fernet(key)


def encrypt(src, dst, passphrase):
    with open(src, "rb") as f:
        data = _fernet(passphrase).encrypt(f.read())
    with open(dst, "wb") as f:
        f.write(data)


def decrypt(src, dst, passphrase):
    with open(src, "rb") as f:
        data = _fernet(passphrase).decrypt(f.read())
    with open(dst, "wb") as f:
        f.write(data)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("encrypt", "decrypt"):
        raise SystemExit(__doc__)
    {"encrypt": encrypt, "decrypt": decrypt}[sys.argv[1]](sys.argv[2], sys.argv[3], os.environ.get("POSITIONS_KEY"))
