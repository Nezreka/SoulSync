"""Slice 4: the ACME flow end-to-end against a fake CA that really
issues certificates from the client's CSR, the DNS-01 broker over its
signed HTTP, and the CT watch comparison."""
from __future__ import annotations

import base64
import datetime
import hashlib
import json

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from core.connect import ctwatch
from core.connect.acme import AcmeClient, CertificateStore, Dns01Broker, ensure_certificate
from core.connect.identity import ServerIdentity


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Resp:
    def __init__(self, status=200, body=None, headers=None, text=None):
        self.status = status
        self.headers = headers or {}
        self._body = body
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")

    def json(self):
        return self._body


class FakeCA:
    """A state-machine ACME server + a real issuing CA."""

    def __init__(self, cert_days: int = 90):
        self.ca_key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fake Test CA")])
        self.ca_cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(self.ca_key.public_key())
            .serial_number(0xC0FFEE)
            .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
            .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365))
            .add_extension(x509.BasicConstraints(True, None), critical=True)
            .sign(self.ca_key, hashes.SHA256())
        )
        self.cert_days = cert_days
        self.authz_valid = False
        self.orders = 0
        self.issued: x509.Certificate | None = None
        self._nonce = 0

    def _resp(self, status, body=None, headers=None, text=None):
        self._nonce += 1
        h = {"Replay-Nonce": f"nonce-{self._nonce}"}
        h.update(headers or {})
        return Resp(status, body, h, text)

    def get(self, url):
        assert url.endswith("/directory")
        return self._resp(200, {
            "newNonce": "https://ca.test/nonce",
            "newAccount": "https://ca.test/account",
            "newOrder": "https://ca.test/order",
        })

    def head(self, url):
        return self._resp(200)

    def post(self, url, data=None, headers=None):
        payload = {}
        if data:
            jws = json.loads(data)
            if jws.get("payload"):
                payload = json.loads(b64d(jws["payload"]))
        if url.endswith("/account"):
            return self._resp(201, {"status": "valid"}, {"Location": "https://ca.test/acct/1"})
        if url.endswith("/order"):
            self.orders += 1
            return self._resp(201, {
                "status": "pending",
                "authorizations": ["https://ca.test/authz/1"],
                "finalize": "https://ca.test/order/1/finalize",
            }, {"Location": "https://ca.test/order/1"})
        if url.endswith("/authz/1"):
            return self._resp(200, {
                "status": "valid" if self.authz_valid else "pending",
                "challenges": [{"type": "dns-01", "token": "tok123", "url": "https://ca.test/chal/1"}],
            })
        if url.endswith("/chal/1"):
            self.authz_valid = True
            return self._resp(200, {"status": "valid"})
        if url.endswith("/finalize"):
            csr = x509.load_der_x509_csr(b64d(payload["csr"]))
            san = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            now = datetime.datetime.now(datetime.timezone.utc)
            self.issued = (
                x509.CertificateBuilder()
                .subject_name(csr.subject)
                .issuer_name(self.ca_cert.subject)
                .public_key(csr.public_key())
                .serial_number(0xBEEF)
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=self.cert_days))
                .add_extension(san, critical=False)
                .sign(self.ca_key, hashes.SHA256())
            )
            return self._resp(200, {"status": "valid", "certificate": "https://ca.test/cert/1"})
        if url.endswith("/cert/1"):
            pem = self.issued.public_bytes(serialization.Encoding.PEM).decode()
            pem += self.ca_cert.public_bytes(serialization.Encoding.PEM).decode()
            return self._resp(200, text=pem)
        raise AssertionError(f"unexpected URL {url}")


class FakeBrokerHttp:
    def __init__(self):
        self.created: list[dict] = []
        self.deleted: list[str] = []

    def post(self, url, data=None, headers=None):
        assert headers and headers.get("X-Server-Signature"), "broker calls must be signed"
        self.created.append(json.loads(data))
        return Resp(201, {"id": "b1", "expires_at": 0})

    def request_delete(self, url, headers=None):
        assert headers and headers.get("X-Server-Signature")
        self.deleted.append(url)
        return Resp(204)


def make_broker(identity):
    http = FakeBrokerHttp()
    return Dns01Broker("https://connect.ssync.net", identity, http), http


def test_full_acme_flow_issues_a_real_wildcard(tmp_path):
    identity = ServerIdentity(key=bytes([7] * 32).hex())
    ca = FakeCA()
    broker, broker_http = make_broker(identity)
    store = CertificateStore(tmp_path)
    account_key = ec.generate_private_key(ec.SECP256R1())
    client = AcmeClient("https://ca.test/directory", account_key, ca)

    pem = client.obtain("*.abc123.connect.ssync.net", broker, store.load_key(), poll_seconds=0)
    store.save_chain(pem)

    # the TXT the broker received is exactly the dns-01 digest
    expected_txt = base64.urlsafe_b64encode(
        hashlib.sha256(f"tok123.{client.thumbprint()}".encode()).digest()
    ).rstrip(b"=").decode()
    assert broker_http.created[0]["value"] == expected_txt
    assert broker_http.created[0]["lookup_id"] == identity.lookup_id
    assert broker_http.deleted, "challenge cleaned up after validation"

    leaf = store.leaf()
    assert leaf is not None
    san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == ["*.abc123.connect.ssync.net"]
    assert store.fingerprint() == ca.issued.fingerprint(hashes.SHA256()).hex()
    assert store.days_left() > 80
    assert store.known_serials() == {"beef"}


def test_ensure_certificate_skips_when_fresh_and_renews_when_close(tmp_path):
    identity = ServerIdentity(key=bytes([8] * 32).hex())
    account_key = ec.generate_private_key(ec.SECP256R1())
    store = CertificateStore(tmp_path)
    broker, _http = make_broker(identity)

    ca = FakeCA(cert_days=90)
    assert ensure_certificate(store, dns_id_value="abc123", broker=broker,
                              directory_url="https://ca.test/directory", http=ca,
                              account_key=account_key) is True
    assert ensure_certificate(store, dns_id_value="abc123", broker=broker,
                              directory_url="https://ca.test/directory", http=ca,
                              account_key=account_key) is False  # fresh: no-op
    assert ca.orders == 1

    expiring = CertificateStore(tmp_path / "second")
    (tmp_path / "second").mkdir()
    ca2 = FakeCA(cert_days=10)
    broker2, _ = make_broker(identity)
    assert ensure_certificate(expiring, dns_id_value="abc123", broker=broker2,
                              directory_url="https://ca.test/directory", http=ca2,
                              account_key=account_key) is True
    assert ca2.orders == 1  # renewed because under 30 days (fresh store had no cert)


def test_ct_watch_flags_only_unknown_serials():
    entries = [
        {"serial_number": "BEEF", "issuer_name": "Fake CA", "common_name": "*.abc.connect.ssync.net"},
        {"serial_number": "0BAD", "issuer_name": "Someone Else", "common_name": "*.abc.connect.ssync.net"},
    ]
    unexpected = ctwatch.find_unexpected(entries, {"beef"})
    assert len(unexpected) == 1 and unexpected[0]["serial_number"] == "0bad"

    class StubGet:
        def __call__(self, url):
            assert "abc" in url
            return Resp(200, entries)

    flagged = ctwatch.check_ct_log("abc", {"beef"}, StubGet())
    assert len(flagged) == 1
    assert ctwatch.check_ct_log("abc", {"beef", "0bad"}, StubGet()) == []
