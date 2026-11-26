"""the sealed rendezvous answer, pinned against the shared conformance
vectors (DESIGN-CONNECT v0 sections 3 and 6; the same fixed inputs as
tests/test_connect_conformance.py: root secret bytes(range(32)), the
test code K7M9P2X4Q8RTW3ZA).

the sealed bytes are a literal, not a round-trip: a drift in the HPKE
info binding, the envelope shape or the signature's canonical bytes
fails here instead of in an app that cannot open the answer.
"""
from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from core.connect import answer
from core.connect.identity import ServerIdentity

RANGE_KEY = bytes(range(32)).hex()
LOOKUP_ID = "ee767f6e8f8602fe277759e44a85cca92c1edcdfa62f789ac99a275b3c5234bd"
CODE_HASH = "f6bae887878e96e428a1474ff1ce607b038e1a689dab35a7c72840b3fb610586"
APP_PRIVATE_HEX = "101112131415161718191a1b1c1d1e1f202122232425262728292a2b2c2d2e2f"
APP_PUBLIC_HEX = "d89e3bad79437dbed9f843418304f460ff05c7fe81fe4a9577a804cb9367ff66"
EPHEMERAL_HEX = "404142434445464748494a4b4c4d4e4f505152535455565758595a5b5c5d5e5f"
ADDRESSES = [
    "https://203-0-113-7.ee767f6e8f8602fe277759e44a85cca92c1edcdfa62f789ac99a275b3c5234bd.connect.ssync.net",
    "https://192-168-1-50.ee767f6e8f8602fe277759e44a85cca92c1edcdfa62f789ac99a275b3c5234bd.connect.ssync.net",
    "http://192.168.1.50:8008",
]
CERT_FINGERPRINT = "ab" * 32
SEALED_HEX = (
    "79a631eede1bf9c98f12032cdeadd0e7a079398fc786b88cc846ec89af85a51a"
    "07eb4419c2827b51cdcb705b4e6cebe706180b40d171d23e8162c2f85aaf39d1"
    "aa2efc54081d4105b1bfd72fdc61bbb3b3e2f9b9445fc92600ffd1a508ed7dfc"
    "527db6f32b6537e94045f42b52563a6bb72d54687483a3b101ce081d484528b6"
    "47192a7974049034de7437a91d3184231b808dcfba4d0d86d91fd182d59660bd"
    "0c1f37d9aa587e872d849596f5d867cea1479657d3399c7158b5cc9263ebe72f"
    "14f8080bad381f74d8f502aeb45ef75f57e4f1d772a3cb1f4162919c4930c81d"
    "2db482abc6a1f4b7c2670158979644341b1f573b2df5e5ee9f8c536d58fd0082"
    "ba6ed01b13e9f589626e441c2d77f5b58611d22c9896acd86f4716d4316a6319"
    "e7dd16f1f5f2c09b8df075e8cbd0ee61be9821dadd1e0882635e288441c2b42e"
    "cd766f9d8777bb51fe9272239533566f128bc107263801a33e91ffd8597d3667"
    "aacf2831ddad8aea83b9a0ff912c11861a00985990f0dca4b00ade34a790a6b1"
    "7bb8d8252e2f036864c0bdef76f529b5d0169f98a4203f6b5f2fe360254fc26f"
    "cd7a26dce9dcf1a163324ff926c498011677dbbd631fb8177a95145398fb65b8"
    "fb8e5f27f3811f941c4c8d3dbf07215d0b59fe6f640b2629f74712267e172179"
    "9d90405133eb3050398046f6eaeebca87fa63d8334b44110c952144466108be7"
    "2bdcb50e869e18d442ae76f9b1dfc9d412bfcc7e401c208c43812a2b91809cf3"
    "72e8b414ecd112a86b1fb2e07a559b93e8c706399271dc6b78b9616346c823fe"
    "41288a12c6ebb24cd46c2c38aa65dee56e27205abbcdd0274b7b9244ada896e2"
    "05cf69393b00c3b12e50a80250b4a5fd2afc1b15a2d6c3a769d700643b447feb"
    "ac80983e4a8b6bfa45f2d4dd063d9bc02079438dff9ebcdfef581ce2bc9d9d3a"
    "6baa9812c3b4f7d6b9edff4f9990165fb2a7c96e92508aef2c84f2f0dfce0079"
    "a6d0c79530c0d98fbcd18d38675dc7ebb356603728b8864433e32f9ad15597d"
    "f334696d6502b37a6b66145444816f86118573b"
)


def test_sealed_answer_matches_the_vector():
    ident = ServerIdentity(key=RANGE_KEY)
    assert ident.lookup_id == LOOKUP_ID
    ephemeral = X25519PrivateKey.from_private_bytes(bytes.fromhex(EPHEMERAL_HEX))
    sealed = answer.build_sealed_answer(
        ident, CODE_HASH, ADDRESSES, CERT_FINGERPRINT,
        bytes.fromhex(APP_PUBLIC_HEX), _ephemeral_private=ephemeral,
    )
    assert sealed.hex() == SEALED_HEX
    payload = answer.open_answer(bytes.fromhex(APP_PRIVATE_HEX), CODE_HASH, sealed)
    assert payload["lookup_id"] == LOOKUP_ID
    assert payload["fingerprint"] == "ee767f6e8f86"
    assert payload["cert_fingerprint"] == CERT_FINGERPRINT
    assert payload["addresses"] == ADDRESSES


def test_sealed_answer_tamper_and_wrong_code_fail():
    sealed = bytearray(bytes.fromhex(SEALED_HEX))
    sealed[-1] ^= 1
    with pytest.raises(ValueError):
        answer.open_answer(bytes.fromhex(APP_PRIVATE_HEX), CODE_HASH, bytes(sealed))
    with pytest.raises(ValueError):
        answer.open_answer(bytes.fromhex(APP_PRIVATE_HEX), "00" * 32, bytes.fromhex(SEALED_HEX))
