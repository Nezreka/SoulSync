"""Certificates — DESIGN-CONNECT v0 section 5.

The server holds a Let's Encrypt wildcard for ``*.<dns_id>`` only,
earned through the connect service's DNS-01 broker: SoulSync never
touches the parent zone, it signs broker requests with its server
identity and the service publishes the TXT under the server's own
name.

Pieces:

* :class:`CertificateStore` — the cert key + chain on disk, the
  fingerprint the sealed answers pin (SHA-256 of the leaf's DER), and
  the known-serials set the CT watch checks against.
* :class:`Dns01Broker` — the signed broker client (create/delete one
  challenge TXT).
* :class:`AcmeClient` — a small RFC 8555 client (ES256 account key,
  POST-as-GET, nonce tracking) driving order → challenge → finalize.
* :func:`ensure_certificate` — renew when under 30 days remain (or no
  cert exists), otherwise a no-op. A periodic caller is production
  wiring; this is the machinery it calls.

The HTTP layer is injected (``http`` with get/head/post returning an
object with .status, .headers, .json(), .text) so the whole flow is
testable against a fake CA — the test suite's fake actually issues a
real certificate from the client's CSR.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.x509.oid import NameOID

from .identity import ServerIdentity, b64url_encode

log = logging.getLogger("soulsync.connect.acme")

LE_PRODUCTION = "https://acme-v02.api.letsencrypt.org/directory"
LE_STAGING = "https://acme-staging-v02.api.letsencrypt.org/directory"
RENEW_BELOW_DAYS = 30


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class _HttpResponse:
    def __init__(self, resp):
        self.status = resp.status_code
        self.headers = resp.headers
        self.text = resp.text
        self._resp = resp

    def json(self):
        return self._resp.json()


class RequestsHttp:
    """The production http adapter (requests) for the injected-http
    protocol the ACME client and broker use: get/head/post/
    request_delete returning objects with .status/.headers/.json()/.text."""

    def __init__(self, timeout: float = 30.0):
        self._timeout = timeout

    def get(self, url):
        import requests

        return _HttpResponse(requests.get(url, timeout=self._timeout))

    def head(self, url):
        import requests

        return _HttpResponse(requests.head(url, timeout=self._timeout))

    def post(self, url, data=None, headers=None):
        import requests

        return _HttpResponse(requests.post(url, data=data, headers=headers, timeout=self._timeout))

    def request_delete(self, url, headers=None):
        import requests

        return _HttpResponse(requests.delete(url, headers=headers, timeout=self._timeout))


# ---------------------------------------------------------------------------
# Certificate store
# ---------------------------------------------------------------------------


class CertificateStore:
    def __init__(self, state_dir: str | Path):
        self._dir = Path(state_dir)
        self.key_path = self._dir / "connect_cert_key.pem"
        self.chain_path = self._dir / "connect_cert_chain.pem"
        self.account_key_path = self._dir / "connect_acme_account_key.pem"

    def load_account_key(self) -> ec.EllipticCurvePrivateKey:
        """The ACME account key (EC P-256), created on first use and
        kept in the state dir like the rest of the connect material."""
        if self.account_key_path.exists():
            return serialization.load_pem_private_key(self.account_key_path.read_bytes(), password=None)
        key = ec.generate_private_key(ec.SECP256R1())
        self.account_key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        self.account_key_path.chmod(0o600)
        return key

    def load_key(self) -> ec.EllipticCurvePrivateKey:
        if self.key_path.exists():
            return serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
        key = ec.generate_private_key(ec.SECP256R1())
        self.key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        self.key_path.chmod(0o600)
        return key

    def save_chain(self, pem_chain: str) -> None:
        self.chain_path.write_text(pem_chain, encoding="utf-8")

    def leaf(self) -> x509.Certificate | None:
        try:
            pem = self.chain_path.read_bytes()
        except OSError:
            return None
        # the chain file's first block is the leaf
        try:
            return x509.load_pem_x509_certificate(pem)
        except ValueError:
            return None

    def fingerprint(self) -> str:
        """What presence answers pin: lowercase hex SHA-256 of the
        leaf's DER, or "" when there is no certificate yet."""
        leaf = self.leaf()
        if leaf is None:
            return ""
        return leaf.fingerprint(hashes.SHA256()).hex()

    def days_left(self) -> float | None:
        leaf = self.leaf()
        if leaf is None:
            return None
        import datetime

        return (leaf.not_valid_after_utc - datetime.datetime.now(datetime.timezone.utc)).total_seconds() / 86400

    def known_serials(self) -> set[str]:
        leaf = self.leaf()
        return {format(leaf.serial_number, "x")} if leaf else set()


# ---------------------------------------------------------------------------
# DNS-01 broker client (signed with the server identity)
# ---------------------------------------------------------------------------


class Dns01Broker:
    def __init__(self, service_url: str, identity: ServerIdentity, http):
        self._base = service_url.rstrip("/")
        self._identity = identity
        self._http = http

    def _signed(self, method: str, path: str, body: bytes):
        headers = self._identity.sign_request(method, path, body)
        headers["X-Server-Pubkey"] = self._identity.public_key
        headers["Content-Type"] = "application/json"
        return headers

    def create(self, txt_value: str) -> str:
        body = json.dumps({"lookup_id": self._identity.lookup_id, "value": txt_value}).encode()
        path = "/v1/dns01/challenges"
        resp = self._http.post(f"{self._base}{path}", data=body, headers=self._signed("POST", path, body))
        if resp.status != 201:
            raise RuntimeError(f"dns01 broker create failed: {resp.status} {resp.text[:200]}")
        return resp.json()["id"]

    def delete(self, challenge_id: str) -> None:
        path = f"/v1/dns01/challenges/{challenge_id}"
        resp = self._http.request_delete(f"{self._base}{path}", headers=self._signed("DELETE", path, b""))
        if resp.status not in (204, 404):
            log.warning("dns01 broker delete returned %s", resp.status)


# ---------------------------------------------------------------------------
# ACME client (RFC 8555)
# ---------------------------------------------------------------------------


class AcmeClient:
    def __init__(self, directory_url: str, account_key: ec.EllipticCurvePrivateKey, http):
        self._directory_url = directory_url
        self._key = account_key
        self._http = http
        self._directory: dict | None = None
        self._nonce: str | None = None
        self._account_url: str | None = None

    # -- JWS plumbing ------------------------------------------------------

    def _jwk(self) -> dict:
        numbers = self._key.public_key().public_numbers()
        return {
            "crv": "P-256",
            "kty": "EC",
            "x": _b64(numbers.x.to_bytes(32, "big")),
            "y": _b64(numbers.y.to_bytes(32, "big")),
        }

    def thumbprint(self) -> str:
        canonical = json.dumps(self._jwk(), sort_keys=True, separators=(",", ":")).encode()
        return _b64(hashlib.sha256(canonical).digest())

    def _jws(self, url: str, payload: dict | None) -> bytes:
        if self._nonce is None:
            self._refresh_nonce()
        protected = {"alg": "ES256", "nonce": self._nonce, "url": url}
        if self._account_url:
            protected["kid"] = self._account_url
        else:
            protected["jwk"] = self._jwk()
        protected_b64 = _b64(json.dumps(protected, separators=(",", ":")).encode())
        payload_b64 = "" if payload is None else _b64(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{protected_b64}.{payload_b64}".encode()
        der = self._key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        signature = _b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return json.dumps(
            {"protected": protected_b64, "payload": payload_b64, "signature": signature}
        ).encode()

    def _post(self, url: str, payload: dict | None):
        body = self._jws(url, payload)
        resp = self._http.post(url, data=body, headers={"Content-Type": "application/jose+json"})
        self._nonce = resp.headers.get("Replay-Nonce") or self._nonce
        if resp.status >= 400:
            raise RuntimeError(f"ACME POST {url} failed: {resp.status} {resp.text[:300]}")
        return resp

    def _refresh_nonce(self) -> None:
        resp = self._http.head(self._directory["newNonce"])
        self._nonce = resp.headers.get("Replay-Nonce")

    # -- flow ----------------------------------------------------------------

    def ensure_account(self) -> None:
        if self._directory is None:
            self._directory = self._http.get(self._directory_url).json()
        if self._account_url:
            return
        resp = self._post(self._directory["newAccount"], {"termsOfServiceAgreed": True})
        self._account_url = resp.headers.get("Location")

    def obtain(self, dns_name: str, broker: Dns01Broker, cert_key: ec.EllipticCurvePrivateKey,
               *, poll_seconds: float = 2.0, max_polls: int = 30) -> str:
        """Run one order for dns_name; returns the PEM chain."""
        self.ensure_account()
        order = self._post(self._directory["newOrder"], {"identifiers": [{"type": "dns", "value": dns_name}]})
        order_url = order.headers.get("Location", "")
        order_body = order.json()
        authz_url = order_body["authorizations"][0]
        authz = self._post(authz_url, None).json()
        challenge = next(c for c in authz["challenges"] if c["type"] == "dns-01")
        key_authorization = f"{challenge['token']}.{self.thumbprint()}"
        txt_value = _b64(hashlib.sha256(key_authorization.encode()).digest())

        challenge_id = broker.create(txt_value)
        try:
            self._post(challenge["url"], {})
            authz = self._poll(authz_url, "valid", poll_seconds, max_polls)
            if authz["status"] != "valid":
                raise RuntimeError(f"authorization did not validate: {authz['status']}")
        finally:
            broker.delete(challenge_id)

        csr = self._build_csr(dns_name, cert_key)
        order = self._post(order_body["finalize"], {"csr": _b64(csr)})
        order_body = self._poll(order_url, "valid", poll_seconds, max_polls, initial=order.json())
        cert_url = order_body.get("certificate")
        if not cert_url:
            raise RuntimeError("order finished without a certificate URL")
        resp = self._post(cert_url, None)
        return resp.text

    def _poll(self, url: str, want: str, poll_seconds: float, max_polls: int, initial: dict | None = None) -> dict:
        body = initial
        for _ in range(max_polls):
            if body is not None and body.get("status") in (want, "invalid"):
                return body
            time.sleep(poll_seconds)
            body = self._post(url, None).json()
        return body or {}

    @staticmethod
    def _build_csr(dns_name: str, cert_key: ec.EllipticCurvePrivateKey) -> bytes:
        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([]))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(dns_name)]), critical=False)
            .sign(cert_key, hashes.SHA256())
        )
        return csr.public_bytes(serialization.Encoding.DER)


def ensure_certificate(
    store: CertificateStore,
    *,
    dns_id_value: str,
    broker: Dns01Broker,
    directory_url: str = LE_PRODUCTION,
    http,
    account_key: ec.EllipticCurvePrivateKey | None = None,
) -> bool:
    """Renew when needed. Returns True when a new certificate was
    obtained. The account key defaults to a fresh EC key kept by the
    caller in production (state dir); tests inject their own."""
    days = store.days_left()
    if days is not None and days > RENEW_BELOW_DAYS:
        return False
    if account_key is None:
        raise ValueError("an ACME account key is required (kept in the state dir in production)")
    client = AcmeClient(directory_url, account_key, http)
    cert_key = store.load_key()
    pem_chain = client.obtain(f"*.{dns_id_value}.connect.ssync.net", broker, cert_key)
    store.save_chain(pem_chain)
    log.info("connect: obtained wildcard certificate for *.%s.connect.ssync.net", dns_id_value)
    return True
