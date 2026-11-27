"""The presence client — DESIGN-CONNECT v0 sections 3 and 4.

SoulSync's persistent outgoing connection to connect.ssync.net, run in
a background thread (websocket-client). It:

* authenticates with the server's own Ed25519 identity (signed headers
  over a single-use challenge nonce, exactly as the service verifies);
* says hello, negotiates protocol version 1, and learns its observed
  public IP from the welcome;
* registers every live invite code (by code_hash, from the device
  invite store), and re-registers them all on every reconnect —
  registrations are connection-scoped;
* answers lookups: for a code that is still live it builds the sealed
  answer (address list built fresh, certificate fingerprint from the
  provider) and relays it; for anything else it answers lookup_failed,
  and the service turns that into the same generic error as every
  other failure;
* reports redemptions so the service drops the registration at once;
* reconnects with backoff (1s doubling to 60s, with jitter) — except
  when displaced: if another connection for the same lookup_id takes
  over, this copy stands down for good and logs why (a restored backup
  running alongside the original is the usual cause).

The invite store is reached through two callables so this module
stays independent of the database: ``registrations_provider()`` ->
[(code_hash, ttl_seconds)] and ``is_live(code_hash)`` -> bool.
:mod:`core.connect.rendezvous` wires them to the device invite codes.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time

import requests
import websocket

from . import answer as connect_answer
from .identity import ServerIdentity, b64url_decode, b64url_encode

log = logging.getLogger("soulsync.connect.presence")

PROTOCOL_VERSIONS = [1]
DISPLACED_CLOSE_CODE = 4000


class PresenceClient:
    def __init__(
        self,
        service_url: str,
        identity: ServerIdentity,
        *,
        registrations_provider,
        is_live,
        address_provider,
        cert_fingerprint_provider,
    ):
        """service_url is the https base (e.g. https://connect.ssync.net).
        address_provider(observed_public_ip) -> list[str];
        cert_fingerprint_provider() -> lowercase hex SHA-256 of the
        server's TLS certificate."""
        self._http_base = service_url.rstrip("/")
        self._ws_base = "wss://" + self._http_base.split("://", 1)[1]
        self._identity = identity
        self._registrations_provider = registrations_provider
        self._is_live = is_live
        self._address_provider = address_provider
        self._cert_fingerprint_provider = cert_fingerprint_provider
        self.observed_public_ip: str | None = None
        self._ws: websocket.WebSocketApp | None = None
        self._send_lock = threading.Lock()
        self._stopped = threading.Event()
        self._displaced = False
        self._thread: threading.Thread | None = None
        # for Settings > Devices: is the service reachable right now, and if
        # not, the last reason in words (#2328)
        self.connected = False
        self.last_error: str | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_loop, name="connect-presence", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._ws is not None:
            self._ws.close()

    def _run_loop(self) -> None:
        backoff = 1.0
        while not self._stopped.is_set():
            try:
                self._connect_once()
            except Exception as exc:  # noqa: BLE001 - the loop must survive any transport failure
                self.connected = False
                self.last_error = _words(exc)
                log.exception("connect presence: connection attempt failed")
            if self._displaced:
                log.error(
                    "connect presence: another copy of this server connected and displaced this one; "
                    "standing down. is a restored backup running?"
                )
                return
            if self._stopped.is_set():
                return
            time.sleep(backoff + random.uniform(0, backoff / 2))
            backoff = min(backoff * 2, 60.0)

    def _connect_once(self) -> None:
        resp = requests.get(f"{self._http_base}/v1/presence/challenge", timeout=10)
        resp.raise_for_status()
        nonce = resp.json()["nonce"]
        headers = self._identity.sign_request("GET", "/v1/presence", b"", nonce=nonce)
        headers["X-Server-Pubkey"] = self._identity.public_key
        header_lines = [f"{k}: {v}" for k, v in headers.items()]
        self._ws = websocket.WebSocketApp(
            f"{self._ws_base}/v1/presence",
            header=header_lines,
            on_open=self._on_open,
            on_message=self._on_message,
            on_close=self._on_close,
            on_error=self._on_error,
        )
        self._ws.run_forever(ping_interval=30, ping_timeout=10)

    # -- websocket callbacks -------------------------------------------------

    def _on_open(self, ws) -> None:
        self._send({"type": "hello", "versions": PROTOCOL_VERSIONS})

    def _on_close(self, ws, code, reason) -> None:
        self.connected = False
        if code == DISPLACED_CLOSE_CODE:
            self._displaced = True

    def _on_error(self, ws, error) -> None:
        self.connected = False
        self.last_error = _words(error)
        log.warning("connect presence: websocket error: %s", error)

    def _on_message(self, ws, message: str) -> None:
        try:
            data = json.loads(message)
        except ValueError:
            return
        self._dispatch(data)

    # -- protocol ---------------------------------------------------------

    def _dispatch(self, data: dict) -> None:
        mtype = data.get("type")
        if mtype == "welcome":
            self.observed_public_ip = data.get("observed_ip")
            self.connected = True
            self.last_error = None
            self._register_all()
        elif mtype == "lookup":
            self._send(self.handle_lookup(data))
        elif mtype == "error":
            log.warning("connect presence: service error: %s", data.get("error", {}).get("code"))

    def _register_all(self) -> None:
        for code_hash_hex, ttl_seconds in self._registrations_provider():
            self._send({"type": "register", "code_hash": code_hash_hex, "ttl_seconds": ttl_seconds})

    def handle_lookup(self, data: dict) -> dict:
        """Build the reply to one lookup prompt: a sealed answer for a
        live code, lookup_failed for anything else."""
        request_id = data.get("request_id", "")
        failed = {"type": "lookup_failed", "request_id": request_id}
        code_hash_hex = data.get("code_hash", "")
        if not self._is_live(code_hash_hex):
            return failed
        try:
            app_public = b64url_decode(data.get("reply_key", ""))
            if len(app_public) != 32:
                return failed
        except Exception:  # noqa: BLE001 - a malformed key is a failed lookup
            return failed
        sealed = connect_answer.build_sealed_answer(
            self._identity,
            code_hash_hex,
            self._address_provider(self.observed_public_ip),
            self._cert_fingerprint_provider(),
            app_public,
        )
        return {"type": "answer", "request_id": request_id, "sealed": b64url_encode(sealed)}

    def state(self) -> dict:
        """where this connection stands, for Settings > Devices."""
        if self._displaced:
            return {"state": "displaced", "error": None}
        if self.connected:
            return {"state": "connected", "error": None}
        return {"state": "connecting", "error": self.last_error}

    # -- outbound API used by the rest of SoulSync ---------------------------

    def register_code(self, code_hash_hex: str, ttl_seconds: int) -> None:
        """Register one freshly minted code. If the connection is down
        this is a no-op by design: the reconnect re-registers every
        live code from the store."""
        self._send({"type": "register", "code_hash": code_hash_hex, "ttl_seconds": ttl_seconds})

    def report_redeemed(self, code_hash_hex: str) -> None:
        self._send({"type": "redeemed", "code_hash": code_hash_hex})

    def _send(self, payload: dict) -> None:
        ws = self._ws
        if ws is None:
            return
        with self._send_lock:
            try:
                ws.send(json.dumps(payload))
            except Exception:  # noqa: BLE001 - a dead socket is the reconnect loop's problem
                log.debug("connect presence: send failed (connection down?)", exc_info=True)


def _words(exc) -> str:
    """an error as one short line, never a traceback."""
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text[:200]
