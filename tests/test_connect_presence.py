"""the presence client's protocol logic, driven directly (no sockets):
welcome registers the live codes, a lookup for a live code is answered
with a sealed answer the app can open, everything else fails the same
way, and a displaced close stands the client down."""
from __future__ import annotations

import json

from core.connect import answer, sealing
from core.connect.identity import ServerIdentity, b64url_decode, b64url_encode
from core.connect.presence import PresenceClient
from core.connect.rendezvous import build_address_list, dns_id

LIVE_HASH = "ab" * 32


def make_client() -> PresenceClient:
    identity = ServerIdentity(key=bytes([3] * 32).hex())
    return PresenceClient(
        "https://connect.ssync.net",
        identity,
        registrations_provider=lambda: [(LIVE_HASH, 540)],
        is_live=lambda code_hash: code_hash == LIVE_HASH,
        address_provider=lambda observed: build_address_list(
            identity, observed_public_ip=observed, lan_ips=["192.168.1.50"], port=8008
        ),
        cert_fingerprint_provider=lambda: "ef" * 32,
    )


class FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


def test_welcome_registers_live_codes():
    client = make_client()
    fake = FakeWS()
    client._ws = fake  # noqa: SLF001 - driving the protocol directly
    client._dispatch({"type": "welcome", "observed_ip": "203.0.113.7"})  # noqa: SLF001
    assert client.observed_public_ip == "203.0.113.7"
    assert fake.sent == [{"type": "register", "code_hash": LIVE_HASH, "ttl_seconds": 540}]


def test_lookup_answers_live_code_with_openable_seal():
    client = make_client()
    app_sk, app_pk = sealing.generate_reply_keypair()
    reply = client.handle_lookup(
        {"request_id": "r1", "code_hash": LIVE_HASH, "reply_key": b64url_encode(app_pk)}
    )
    assert reply["type"] == "answer"
    payload = answer.open_answer(app_sk, LIVE_HASH, b64url_decode(reply["sealed"]))
    assert payload["lookup_id"] == client._identity.lookup_id  # noqa: SLF001
    assert payload["cert_fingerprint"] == "ef" * 32
    did = dns_id(client._identity)  # noqa: SLF001
    assert payload["addresses"] == [f"https://192-168-1-50.{did}.connect.ssync.net:8008"]


def test_lookup_failures_are_all_the_same():
    client = make_client()
    _sk, app_pk = sealing.generate_reply_keypair()
    unknown = client.handle_lookup({"request_id": "r2", "code_hash": "00" * 32, "reply_key": b64url_encode(app_pk)})
    assert unknown == {"type": "lookup_failed", "request_id": "r2"}
    bad_key = client.handle_lookup({"request_id": "r3", "code_hash": LIVE_HASH, "reply_key": "!!!"})
    assert bad_key == {"type": "lookup_failed", "request_id": "r3"}


def test_displaced_stands_down():
    client = make_client()
    client._on_close(None, 4000, "displaced")  # noqa: SLF001
    assert client._displaced is True  # noqa: SLF001
