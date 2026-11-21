"""sealed answers: HPKE (RFC 9180) base mode, the suite the connect design
names: KEM 0x0020 (DHKEM(X25519, HKDF-SHA256)), KDF 0x0001 (HKDF-SHA256),
AEAD 0x0003 (ChaCha20-Poly1305).

copied, not imported, from MetaSync's node/metasync_node/meta/sealing.py
(origin/main 68b51614): seal(), open_sealed(), generate_reply_keypair() and
the key schedule. the zstd dictionary and the payload codec stay behind;
the connect answer is small JSON.

built from ``cryptography`` primitives rather than that library's own HPKE
module, as MetaSync's is: the module only exists in newer releases and its
single-shot API offers neither AAD nor a pinnable ephemeral key, and the
RFC 9180 A.2.1 vector in tests/test_connect_conformance.py needs both.

the output is ``enc || ciphertext``.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac

_KEM_ID = 0x0020
_KDF_ID = 0x0001
_AEAD_ID = 0x0003
_MODE_BASE = 0
_HASH_LEN = 32  # SHA-256
_KEY_LEN = 32  # ChaCha20-Poly1305
_NONCE_LEN = 12
_TAG_LEN = 16
_ENC_LEN = 32  # X25519 public key


def sealing_available() -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric.x25519 import (  # noqa: F401
            X25519PrivateKey,
        )

        return True
    except Exception:  # noqa: BLE001 - any import failure means unavailable
        return False


def _labeled_extract(salt: bytes, suite_id: bytes, label: bytes, ikm: bytes) -> bytes:
    return _hmac.new(
        salt or bytes(_HASH_LEN),
        b"HPKE-v1" + suite_id + label + ikm,
        hashlib.sha256,
    ).digest()


def _labeled_expand(
    prk: bytes, suite_id: bytes, label: bytes, info: bytes, length: int
) -> bytes:
    labeled_info = (
        length.to_bytes(2, "big") + b"HPKE-v1" + suite_id + label + info
    )
    out = b""
    block = b""
    counter = 1
    while len(out) < length:
        block = _hmac.new(
            prk, block + labeled_info + bytes([counter]), hashlib.sha256
        ).digest()
        out += block
        counter += 1
    return out[:length]


def _kem_encap(public_raw: bytes, ephemeral_private=None):
    """DHKEM Encapsulate: (enc, shared_secret). the ephemeral key is
    injectable only so the RFC 9180 vector can pin it; production callers
    let it be generated."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey,
        X25519PublicKey,
    )

    sk_e = ephemeral_private or X25519PrivateKey.generate()
    enc = sk_e.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    dh = sk_e.exchange(X25519PublicKey.from_public_bytes(public_raw))
    suite_id = b"KEM" + _KEM_ID.to_bytes(2, "big")
    kem_context = enc + public_raw
    eae_prk = _labeled_extract(b"", suite_id, b"eae_prk", dh)
    shared_secret = _labeled_expand(
        eae_prk, suite_id, b"shared_secret", kem_context, _HASH_LEN
    )
    return enc, shared_secret


def _kem_decap(private_raw: bytes, enc: bytes) -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey,
        X25519PublicKey,
    )

    sk_r = X25519PrivateKey.from_private_bytes(private_raw)
    public_raw = sk_r.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    dh = sk_r.exchange(X25519PublicKey.from_public_bytes(enc))
    suite_id = b"KEM" + _KEM_ID.to_bytes(2, "big")
    kem_context = enc + public_raw
    eae_prk = _labeled_extract(b"", suite_id, b"eae_prk", dh)
    return _labeled_expand(
        eae_prk, suite_id, b"shared_secret", kem_context, _HASH_LEN
    )


def _key_schedule(shared_secret: bytes, info: bytes):
    """base-mode key schedule: (AEAD key, base nonce)."""
    # the context suite_id (RFC 9180 section 5.1) is "HPKE" || kem_id ||
    # kdf_id || aead_id. the mode byte is only inside key_schedule_context
    suite_id = (
        b"HPKE"
        + _KEM_ID.to_bytes(2, "big")
        + _KDF_ID.to_bytes(2, "big")
        + _AEAD_ID.to_bytes(2, "big")
    )
    psk_id_hash = _labeled_extract(b"", suite_id, b"psk_id_hash", b"")
    info_hash = _labeled_extract(b"", suite_id, b"info_hash", info)
    key_schedule_context = bytes([_MODE_BASE]) + psk_id_hash + info_hash
    secret = _labeled_extract(shared_secret, suite_id, b"secret", b"")
    key = _labeled_expand(secret, suite_id, b"key", key_schedule_context, _KEY_LEN)
    base_nonce = _labeled_expand(
        secret, suite_id, b"base_nonce", key_schedule_context, _NONCE_LEN
    )
    return key, base_nonce


def generate_reply_keypair() -> tuple[bytes, bytes]:
    """a fresh ephemeral X25519 keypair: (private raw bytes, public raw bytes)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    sk = X25519PrivateKey.generate()
    private_raw = sk.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_raw = sk.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private_raw, public_raw


def seal(
    recipient_public_raw: bytes,
    info: bytes,
    plaintext: bytes,
    aad: bytes = b"",
    *,
    _ephemeral_private=None,
) -> bytes:
    """seal one payload: ``enc || ciphertext``. single-shot (sequence
    number 0), so the nonce is the base nonce itself."""
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

    enc, shared_secret = _kem_encap(recipient_public_raw, _ephemeral_private)
    key, nonce = _key_schedule(shared_secret, info)
    return enc + ChaCha20Poly1305(key).encrypt(nonce, plaintext, aad)


def open_sealed(
    recipient_private_raw: bytes, info: bytes, sealed: bytes, aad: bytes = b""
) -> bytes:
    """open a sealed payload. raises ValueError on any failure (bad shape,
    wrong key, wrong info, tampered ciphertext)."""
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

    if len(sealed) < _ENC_LEN + _TAG_LEN:
        raise ValueError("sealed payload too short")
    enc, ciphertext = sealed[:_ENC_LEN], sealed[_ENC_LEN:]
    try:
        shared_secret = _kem_decap(recipient_private_raw, enc)
        key, nonce = _key_schedule(shared_secret, info)
        return ChaCha20Poly1305(key).decrypt(nonce, ciphertext, aad)
    except Exception as err:  # every open failure is one class
        raise ValueError("sealed payload does not open") from err
