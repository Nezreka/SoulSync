"""The sealed rendezvous answer — DESIGN-CONNECT v0 sections 3 and 6.

Wire definition (pinned here; the conformance vectors are generated
from this module):

* HPKE ``info`` = ``b"soulsync-connect-hpke-info-v1\\n"`` followed by the
  code_hash's lowercase hex as ASCII. (Amended Nov 21, 2026: info binds
  the code_hash ONLY. The lookup_id cannot be in info — the app does not
  know it until it opens the seal. It travels inside the payload instead,
  under the signature.)
* The plaintext is a JSON envelope ``{"payload": {...}, "signature":
  "<base64url>"}``, serialized with sorted keys and compact separators.
* The payload object:
    - ``v``: 1
    - ``lookup_id``: the server's lookup_id (hex)
    - ``code_hash``: the code_hash this answer responds to (hex)
    - ``fingerprint``: lookup_id[:12], for the human compare
    - ``server_pubkey``: the server's raw Ed25519 public key, base64url
    - ``cert_fingerprint``: lowercase hex SHA-256 of the server's TLS
      certificate (DER), which the app pins
    - ``addresses``: the candidate address list, server-built, fresh
      per answer
* ``signature`` is the server's Ed25519 signature over the payload's
  canonical bytes: ``json.dumps(payload, sort_keys=True,
  separators=(",", ":"))`` as UTF-8.

Opening verifies, in order: the seal opens under info for the expected
code_hash; the signature verifies against ``server_pubkey``; the
payload's ``lookup_id`` equals SHA-256 of that public key (the ID is
derived from the key, so it cannot be spoofed); the payload's
``code_hash`` matches the lookup; the fingerprint matches. Any failure
raises ValueError — one error class, like the sealing layer.
"""
from __future__ import annotations

import hashlib
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import sealing
from .identity import ServerIdentity, b64url_decode

_INFO_PREFIX = b"soulsync-connect-hpke-info-v1\n"


def hpke_info(code_hash_hex: str) -> bytes:
    return _INFO_PREFIX + code_hash_hex.encode("ascii")


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def build_payload(
    identity: ServerIdentity,
    code_hash_hex: str,
    addresses: list[str],
    cert_fingerprint: str,
) -> dict:
    return {
        "v": 1,
        "lookup_id": identity.lookup_id,
        "code_hash": code_hash_hex,
        "fingerprint": identity.fingerprint,
        "server_pubkey": identity.public_key,
        "cert_fingerprint": cert_fingerprint,
        "addresses": list(addresses),
    }


def build_sealed_answer(
    identity: ServerIdentity,
    code_hash_hex: str,
    addresses: list[str],
    cert_fingerprint: str,
    app_public_raw: bytes,
    *,
    _ephemeral_private=None,
) -> bytes:
    """Build the server's answer to one lookup: the signed payload,
    HPKE-sealed to the app's ephemeral key. Returns enc || ciphertext."""
    payload = build_payload(identity, code_hash_hex, addresses, cert_fingerprint)
    signature = identity.sign(_canonical(payload))
    envelope = {"payload": payload, "signature": signature}
    return sealing.seal(
        app_public_raw,
        info=hpke_info(code_hash_hex),
        plaintext=_canonical(envelope),
        _ephemeral_private=_ephemeral_private,
    )


def open_answer(app_private_raw: bytes, code_hash_hex: str, sealed: bytes) -> dict:
    """Open and fully verify one sealed answer. Returns the payload.
    Raises ValueError on any failure."""
    plaintext = sealing.open_sealed(
        app_private_raw, info=hpke_info(code_hash_hex), sealed=sealed
    )
    try:
        envelope = json.loads(plaintext.decode("utf-8"))
        payload = envelope["payload"]
        signature = b64url_decode(envelope["signature"])
        pubkey_raw = b64url_decode(payload["server_pubkey"])
    except Exception as err:
        raise ValueError("answer envelope is malformed") from err
    try:
        Ed25519PublicKey.from_public_bytes(pubkey_raw).verify(signature, _canonical(payload))
    except InvalidSignature as err:
        raise ValueError("answer signature does not verify") from err
    if hashlib.sha256(pubkey_raw).hexdigest() != payload.get("lookup_id"):
        raise ValueError("answer lookup_id does not match its public key")
    if payload.get("code_hash") != code_hash_hex:
        raise ValueError("answer is for a different code")
    if payload.get("fingerprint") != payload["lookup_id"][:12]:
        raise ValueError("answer fingerprint does not match its lookup_id")
    return payload
