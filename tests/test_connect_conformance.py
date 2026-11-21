"""conformance vectors for the server identity, the request signing and the
sealing (PAIRING_SPEC.md 1, FOR_MUSE.md 5).

these are the contract with the connect service, pinned BYTE FOR BYTE as
literals, in the style of MetaSync's node/tests/test_meta_conformance.py.
they are not round-tripped through this package's own functions: a copy
that drifts (a label, a newline, a key schedule step) fails here instead of
every signature verifying against nothing in production.

the identity and signing literals were also produced by MetaSync's own
identity.py (origin/main 68b51614) with the label swapped in, so the copy
matches the original's construction.

vectors use fixed, obviously synthetic inputs: root secrets of 32 zero bytes
and bytes(range(32)), the nonce bytes(range(16)) as base64url, timestamp
1700000000.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from core.connect import sealing
from core.connect.identity import (
    SIGNING_INFO,
    ServerIdentity,
    b64url_decode,
    canonical_request_string,
)

ZERO_KEY = "00" * 32
RANGE_KEY = bytes(range(32)).hex()
NONCE = "AAECAwQFBgcICQoLDA0ODw"  # bytes(range(16)), base64url
BODY = b'{"code_hash":"x"}'


def _hkdf_sha256(ikm: bytes, info: bytes, length: int = 32) -> bytes:
    """RFC 5869 by hand, apart from the cryptography HKDF the copy uses."""
    prk = hmac.new(bytes(32), ikm, hashlib.sha256).digest()  # salt None = 32 zero bytes
    out, block, counter = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


# ── identity ─────────────────────────────────────────────────────────────

def test_the_label_is_soulsyncs_own():
    assert SIGNING_INFO == b"soulsync-connect-server-signing-v1"


def test_identity_vector_zero_key():
    ident = ServerIdentity(key=ZERO_KEY)
    assert ident.signing_seed.hex() == (
        "3cb3d79d19e5382cdab2c05092fe17b9a40da6aae2f66d9178ec19dd05188773")
    assert ident.public_key_bytes.hex() == (
        "8b285ede50b3f8cb7dc0e8268a1df2ba35b0cea75c21208cb10d8f79bd3edafe")
    assert ident.public_key == "iyhe3lCz-Mt9wOgmih3yujWwzqdcISCMsQ2Peb0-2v4"
    assert ident.lookup_id == (
        "ae0396a668c3a790a6e5270415d0d62aa3527ae5be87cfbee583db91ec46ca41")
    assert ident.fingerprint == "ae0396a668c3"


def test_identity_vector_range_key():
    ident = ServerIdentity(key=RANGE_KEY)
    assert ident.signing_seed.hex() == (
        "d21bbb8e7905de16861496a49879ca96e25136e5d6d094f5b1baf85acc17c458")
    assert ident.public_key_bytes.hex() == (
        "dd036eab4c5a0e25de3e1ff8ee724a87b5ff3de5c942dddb713cbb3e56236a90")
    assert ident.public_key == "3QNuq0xaDiXePh_47nJKh7X_PeXJQt3bcTy7PlYjapA"
    assert ident.lookup_id == (
        "ee767f6e8f8602fe277759e44a85cca92c1edcdfa62f789ac99a275b3c5234bd")
    assert ident.fingerprint == "ee767f6e8f86"


def test_the_seed_is_plain_hkdf_with_the_label():
    for key in (ZERO_KEY, RANGE_KEY):
        assert ServerIdentity(key=key).signing_seed == _hkdf_sha256(bytes.fromhex(key), SIGNING_INFO)


def test_the_lookup_id_is_sha256_of_the_raw_public_key():
    ident = ServerIdentity(key=RANGE_KEY)
    assert ident.lookup_id == hashlib.sha256(ident.public_key_bytes).hexdigest()
    assert ident.fingerprint == ident.lookup_id[:12]


def test_metasyncs_label_gives_another_key():
    # the same secret under the node's label is a different key: domain
    # separation, so a SoulSync key can never sign as a MetaSync node
    node_seed = _hkdf_sha256(bytes.fromhex(RANGE_KEY), b"metasync-sin-request-signing-v1")
    assert node_seed.hex() == "559feb6fe13201b8de1bd2284ed0f99e2918b4473935d5e85084c0e8a1a26fb5"
    assert node_seed != ServerIdentity(key=RANGE_KEY).signing_seed


def test_the_secret_never_prints():
    assert RANGE_KEY not in repr(ServerIdentity(key=RANGE_KEY))


# ── request signing ──────────────────────────────────────────────────────

def test_canonical_request_string_vector():
    assert canonical_request_string("post", "/v1/invites", 1700000000, NONCE, BODY) == (
        "POST\n"
        "/v1/invites\n"
        "1700000000\n"
        "AAECAwQFBgcICQoLDA0ODw\n"
        "c003c96fb64052baf27404db5fbeeb5e73f4ac2edf95bd183ca07d4e55b7b5c1"
    )


def test_canonical_request_string_empty_body():
    assert canonical_request_string("GET", "/v1/status", "1700000000", NONCE, b"").endswith(
        "\ne3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")


def test_signed_headers_vector():
    headers = ServerIdentity(key=RANGE_KEY).sign_request(
        "POST", "/v1/invites", BODY, timestamp=1700000000, nonce=NONCE)
    assert headers == {
        "X-Server-Id": "ee767f6e8f8602fe277759e44a85cca92c1edcdfa62f789ac99a275b3c5234bd",
        "X-Server-Signature": ("irVvr9oUNDwtQLoRnAhPVSEKvuMKxV20QL0cBAmdbK0EQaHv02spxcocetv"
                               "nGsT8S3rPXWOJV1fUQl4BlaaGDA"),
        "X-Server-Timestamp": "1700000000",
        "X-Server-Nonce": "AAECAwQFBgcICQoLDA0ODw",
    }


def test_the_signature_verifies_against_the_public_key():
    ident = ServerIdentity(key=RANGE_KEY)
    headers = ident.sign_request("POST", "/v1/invites", BODY, timestamp=1700000000, nonce=NONCE)
    signed = canonical_request_string("POST", "/v1/invites", 1700000000, NONCE, BODY)
    pub = Ed25519PublicKey.from_public_bytes(ident.public_key_bytes)
    pub.verify(b64url_decode(headers["X-Server-Signature"]), signed.encode())
    assert "=" not in headers["X-Server-Signature"]


# ── sealing (HPKE, RFC 9180 A.2.1 on SoulSync's copy) ────────────────────

# DHKEM(X25519, HKDF-SHA256), HKDF-SHA256, ChaCha20-Poly1305, base mode,
# one-shot with AAD "Count-0". the same literals as MetaSync's
# node/tests/test_meta_sealing.py
RFC_INFO = bytes.fromhex("4f6465206f6e2061204772656369616e2055726e")
RFC_SK_EM = bytes.fromhex("f4ec9b33b792c372c1d2c2063507b684ef925b8c75a42dbcbf57d63ccd381600")
RFC_PK_RM = bytes.fromhex("4310ee97d88cc1f088a5576c77ab0cf5c3ac797f3d95139c6c84b5429c59662a")
RFC_SK_RM = bytes.fromhex("8057991eef8f1f1af18f4a9491d16a1ce333f695d4db8e38da75975c4478e0fb")
RFC_ENC = bytes.fromhex("1afa08d3dec047a643885163f1180476fa7ddb54c6a8029ea33f95796bf2ac4a")
RFC_PT = b"Beauty is truth, truth beauty"
RFC_AAD = b"Count-0"
RFC_CT = bytes.fromhex(
    "1c5250d8034ec2b784ba2cfd69dbdb8af406cfe3ff938e131f0def8c8b60b4db2"
    "1993c62ce81883d2dd1b51a28"
)


def test_hpke_matches_rfc9180_test_vector():
    ephemeral = X25519PrivateKey.from_private_bytes(RFC_SK_EM)
    sealed = sealing.seal(RFC_PK_RM, info=RFC_INFO, plaintext=RFC_PT, aad=RFC_AAD,
                          _ephemeral_private=ephemeral)
    assert sealed[:32] == RFC_ENC, "encapsulated key differs from the RFC vector"
    assert sealed[32:] == RFC_CT, "ciphertext differs from the RFC vector"
    assert sealing.open_sealed(RFC_SK_RM, info=RFC_INFO, sealed=sealed, aad=RFC_AAD) == RFC_PT


def test_seal_open_round_trip_and_wrong_key_or_info_fails():
    private, public = sealing.generate_reply_keypair()
    info = bytes(32) + b"lookup"
    sealed = sealing.seal(public, info=info, plaintext=b"payload")
    assert sealing.open_sealed(private, info=info, sealed=sealed) == b"payload"
    other_private, _ = sealing.generate_reply_keypair()
    with pytest.raises(ValueError):
        sealing.open_sealed(other_private, info=info, sealed=sealed)
    with pytest.raises(ValueError):
        sealing.open_sealed(private, info=b"another", sealed=sealed)
    with pytest.raises(ValueError):
        sealing.open_sealed(private, info=info, sealed=sealed[:-1] + bytes([sealed[-1] ^ 1]))
    with pytest.raises(ValueError):
        sealing.open_sealed(private, info=info, sealed=b"short")


def test_sealing_is_available_here():
    assert sealing.sealing_available()
