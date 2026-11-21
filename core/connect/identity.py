"""the server's own identity and its request signing.

copied, not imported, from MetaSync's node/metasync_node/identity.py
(origin/main 68b51614). the construction is the same, the label and the
file name are SoulSync's own:

* a random 32-byte root secret, 64 lowercase hex chars, in
  ``<state dir>/server_key`` (mode 0600), made on first start;
* HKDF-SHA256 (salt None, length 32, info ``soulsync-connect-server-signing-v1``)
  over it gives the Ed25519 seed;
* the lookup ID is the SHA-256 hex of the raw public key, the fingerprint
  its first 12 chars.

the root secret never leaves the server (backups aside, see
core/connect/server_key.py). only the public key, the lookup ID and the
fingerprint are public.

the pinned vectors in tests/test_connect_conformance.py are the contract
with the connect service: change nothing here without them.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEY_FILE_NAME = "server_key"
_KEY_SHAPE = re.compile(r"^[0-9a-f]{64}$")
SIGNING_INFO = b"soulsync-connect-server-signing-v1"


def b64url_encode(data: bytes) -> str:
    """bytes as unpadded base64url."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    """padded or unpadded base64url."""
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def canonical_request_string(method: str, path: str, timestamp: str | int, nonce: str, body: bytes) -> str:
    """the exact canonical string signed for one request to the connect
    service: method, path, unix timestamp, nonce and the lowercase hex
    SHA-256 of the raw body, one per line."""
    if isinstance(body, str):
        body_bytes = body.encode("utf-8")
    else:
        body_bytes = bytes(body)
    return "\n".join((
        method.upper(),
        path,
        str(timestamp),
        nonce,
        hashlib.sha256(body_bytes).hexdigest(),
    ))


def is_valid_key(key: str) -> bool:
    return bool(_KEY_SHAPE.match(key or ""))


@dataclass(frozen=True)
class ServerIdentity:
    key: str  # hex root secret; never leaves the server

    def __repr__(self) -> str:  # never print the secret
        return f"ServerIdentity(fingerprint={self.fingerprint!r})"

    @property
    def _root_key_bytes(self) -> bytes:
        return bytes.fromhex(self.key)

    @property
    def signing_seed(self) -> bytes:
        """the domain-separated Ed25519 seed derived from the root secret."""
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=SIGNING_INFO,
        )
        return hkdf.derive(self._root_key_bytes)

    @property
    def signing_private_key(self) -> Ed25519PrivateKey:
        return Ed25519PrivateKey.from_private_bytes(self.signing_seed)

    @property
    def signing_public_key(self):
        return self.signing_private_key.public_key()

    @property
    def public_key_bytes(self) -> bytes:
        from cryptography.hazmat.primitives import serialization

        return self.signing_public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def public_key(self) -> str:
        """the Ed25519 public key, unpadded base64url."""
        return b64url_encode(self.public_key_bytes)

    @property
    def lookup_id(self) -> str:
        """the stable public id: SHA-256 of the raw Ed25519 public key."""
        return hashlib.sha256(self.public_key_bytes).hexdigest()

    @property
    def fingerprint(self) -> str:
        """the short form people compare by eye."""
        return self.lookup_id[:12]

    def sign(self, message: str | bytes) -> str:
        """sign bytes or text, the signature as unpadded base64url."""
        data = message.encode("utf-8") if isinstance(message, str) else bytes(message)
        return b64url_encode(self.signing_private_key.sign(data))

    def sign_request(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        *,
        timestamp: str | int | None = None,
        nonce: str | None = None,
    ) -> dict[str, str]:
        """the four server-signature headers for one request."""
        timestamp_value = str(int(time.time()) if timestamp is None else timestamp)
        nonce_value = nonce or b64url_encode(secrets.token_bytes(16))
        canonical = canonical_request_string(method, path, timestamp_value, nonce_value, body)
        return {
            "X-Server-Id": self.lookup_id,
            "X-Server-Signature": self.sign(canonical),
            "X-Server-Timestamp": timestamp_value,
            "X-Server-Nonce": nonce_value,
        }


def read_key_file(state_dir: str | Path) -> str | None:
    """the stored root secret, None when there is no file. a file that is
    there but not 64 hex chars raises: a damaged key must not be quietly
    replaced by a new identity."""
    file = Path(state_dir) / KEY_FILE_NAME
    if not file.exists():
        return None
    key = file.read_text(encoding="utf-8").strip()
    if not is_valid_key(key):
        raise ValueError(f"{KEY_FILE_NAME} file at {file} is corrupt (expected 64 hex chars)")
    return key


def write_key_file(state_dir: str | Path, key: str) -> Path:
    if not is_valid_key(key):
        raise ValueError("a server key is 64 lowercase hex chars")
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    file = state_dir / KEY_FILE_NAME
    # made 0600 from the start, never world-readable for a moment
    fd = os.open(str(file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(key + "\n")
    try:
        os.chmod(file, 0o600)
    except OSError:  # a share that refuses chmod keeps what it has
        pass
    return file


def load_or_create_identity(state_dir: str | Path) -> ServerIdentity:
    """the identity in ``state_dir``, made on first use."""
    key = read_key_file(state_dir)
    if key is None:
        key = secrets.token_hex(32)
        write_key_file(state_dir, key)
    return ServerIdentity(key=key)
