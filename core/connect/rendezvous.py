"""The rendezvous glue — DESIGN-CONNECT v0, step 7.

Wires the presence client, the certificate machinery and the CT watch
to the rest of SoulSync: the server identity from
:mod:`core.connect.server_key`, the live invite codes from the device
invite table (code_hash is stored there for exactly this), the web
port and LAN addresses from here, and the certificate store in the
connect state dir.

Presence starts only once the server has a certificate: a sealed
answer the app cannot pin is worse than no answer, so until
:func:`ensure_certificate` has run successfully, start() is a no-op
that logs why. Starting is idempotent — the devices blueprint's
configure() calls it and later calls return the running client.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
import ssl
import threading
import time

from . import dns_labels
from .acme import CertificateStore, Dns01Broker, RequestsHttp, ensure_certificate as _acme_ensure
from .ctwatch import check_ct_log
from .identity import ServerIdentity
from .presence import PresenceClient

log = logging.getLogger("soulsync.connect.rendezvous")

DEFAULT_SERVICE_URL = "https://connect.ssync.net"
ZONE = "connect.ssync.net"
DEFAULT_TLS_PORT = 8443
_RENEW_INTERVAL_SECONDS = 12 * 3600

_LIVE_SQL = "used_at IS NULL AND cancelled_at IS NULL AND locked_at IS NULL AND expires_at > ?"

_client: PresenceClient | None = None
_https_server = None
_upnp_mapper = None
_runtime_thread: threading.Thread | None = None
_runtime_lock = threading.Lock()


def connect_tls_port(config_manager=None) -> int:
    """Port for the dedicated Connect HTTPS listener (default 8443)."""
    env_val = os.environ.get("SOULSYNC_CONNECT_TLS_PORT")
    if env_val:
        try:
            return int(env_val)
        except ValueError:
            pass
    if config_manager is not None:
        try:
            cfg = config_manager.get("connect.tls_port", None)
            if cfg:
                return int(cfg)
        except Exception:  # noqa: BLE001
            pass
    return DEFAULT_TLS_PORT


def dns_id(identity: ServerIdentity) -> str:
    """The DNS/cert form of the server's identity: lookup_id[:32] (a
    DNS label holds at most 63 chars; the full lookup_id is 64)."""
    return identity.lookup_id[:32]


def build_address_list(
    identity: ServerIdentity,
    *,
    observed_public_ip: str | None,
    lan_ips: list[str],
    port: int,
    external_port: int | None = None,
) -> list[str]:
    """Ordered candidate URLs for one sealed answer: every candidate
    an HTTPS URL under the server's own certificate name
    (``<ip-label>.<dns_id>.connect.ssync.net``) so the app can pin the
    certificate fingerprint whichever one it reaches. LAN IPv4 first,
    then the observed public IPv4 (using ``external_port`` if UPnP/NAT-PMP
    mapped a different WAN port), then IPv6 forms. No raw-IP or
    plain-HTTP candidates: an address the app cannot verify against
    the pinned fingerprint has no place in the list."""
    server_dns_id = dns_id(identity)
    urls: list[str] = []
    seen: set[str] = set()
    wan_port = external_port if external_port else port

    def add(ip_text: str, use_port: int) -> None:
        try:
            addr = ipaddress.ip_address(ip_text)
        except ValueError:
            return
        if isinstance(addr, ipaddress.IPv4Address):
            label = dns_labels.encode_ipv4_label(str(addr))
        else:
            label = dns_labels.encode_ipv6_label(str(addr))
        host = f"{label}.{server_dns_id}.{ZONE}"
        url = f"https://{host}:{use_port}" if use_port != 443 else f"https://{host}"
        if url not in seen:
            seen.add(url)
            urls.append(url)

    def is_v4(ip_text: str) -> bool:
        try:
            return isinstance(ipaddress.ip_address(ip_text), ipaddress.IPv4Address)
        except ValueError:
            return True  # unparseable entries sort as v4; add() drops them anyway

    for ip in [i for i in lan_ips if is_v4(i)]:
        add(ip, port)
    if observed_public_ip:
        add(observed_public_ip, wan_port)
    for ip in [i for i in lan_ips if not is_v4(i)]:
        add(ip, port)
    return urls


def _local_lan_ips() -> list[str]:
    ips: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # the primary outbound address, the LAN IP other devices use
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))  # TEST-NET-1: no traffic is sent (UDP)
            ips.add(sock.getsockname()[0])
    except OSError:
        pass
    return sorted(ips)


# -- invite store providers ---------------------------------------------------


def _registrations(db) -> list[tuple[str, int]]:
    now = time.time()
    try:
        with db._get_connection() as conn:  # noqa: SLF001 - same pattern as invite_codes
            rows = conn.execute(
                "SELECT code_hash, expires_at FROM device_invite_codes "
                f"WHERE {_LIVE_SQL}",  # noqa: S608 - a constant
                (now,),
            ).fetchall()
    except Exception:  # noqa: BLE001 - table may not exist on a fresh install
        return []
    return [(r["code_hash"], max(1, int(float(r["expires_at"]) - now))) for r in rows]


def _is_live(db, code_hash_hex: str) -> bool:
    try:
        with db._get_connection() as conn:  # noqa: SLF001
            row = conn.execute(
                "SELECT 1 FROM device_invite_codes "
                f"WHERE code_hash = ? AND {_LIVE_SQL}",  # noqa: S608 - a constant
                (code_hash_hex, time.time()),
            ).fetchone()
    except Exception:  # noqa: BLE001
        return False
    return row is not None


# -- lifecycle ------------------------------------------------------------------


def _identity_and_store(get_database, config_manager):
    from .server_key import server_identity, state_dir

    identity = server_identity(get_database(), config_manager)
    return identity, CertificateStore(state_dir())


def start(get_database, config_manager, *, port: int | None = None) -> PresenceClient | None:
    """Start presence if the server has a certificate. Idempotent."""
    global _client
    if _client is not None:
        return _client
    identity, store = _identity_and_store(get_database, config_manager)
    if not store.fingerprint():
        log.info("connect rendezvous: no TLS certificate yet; presence not started")
        return None
    tls_port = port if port is not None else connect_tls_port(config_manager)

    service_url = ""
    try:
        service_url = config_manager.get("connect.service_url", "") or ""
    except Exception:  # noqa: BLE001 - config shape varies in tests
        service_url = ""
    db = get_database()
    _client = PresenceClient(
        service_url or DEFAULT_SERVICE_URL,
        identity,
        registrations_provider=lambda: _registrations(db),
        is_live=lambda code_hash: _is_live(db, code_hash),
        address_provider=lambda observed: build_address_list(
            identity,
            observed_public_ip=observed or (_upnp_mapper.mapping.external_ip if _upnp_mapper and _upnp_mapper.mapping else None),
            lan_ips=_local_lan_ips(),
            port=tls_port,
            external_port=_upnp_mapper.external_port if _upnp_mapper else None,
        ),
        cert_fingerprint_provider=store.fingerprint,
    )
    _client.start()
    log.info("connect rendezvous: presence started for %s (https port %s)", identity.fingerprint, tls_port)
    return _client


def _start_https_listener(app, store: CertificateStore, host: str, port: int) -> None:
    """Start (or restart on cert renewal) the dedicated Connect HTTPS listener."""
    global _https_server
    if _https_server is not None:
        try:
            _https_server.shutdown()
        except Exception:  # noqa: BLE001
            pass
        _https_server = None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile=str(store.chain_path), keyfile=str(store.key_path))
    from werkzeug.serving import make_server

    srv = make_server(host, port, app, threaded=True, ssl_context=ctx)
    _https_server = srv
    threading.Thread(target=srv.serve_forever, daemon=True, name="ConnectHttpsListener").start()
    log.info("connect https: listening on https://%s:%s (cert %s)", host, port, store.fingerprint()[:12])


def start_runtime(app, get_database, config_manager) -> None:
    """Background Connect bootstrap: obtain/renew the wildcard TLS cert, start
    the dedicated HTTPS listener, and connect the rendezvous WebSocket.

    Safe to call once from start_runtime_services(); never blocks startup and
    skips automatically inside pytest or when SOULSYNC_CONNECT_DISABLE=1.
    """
    global _runtime_thread
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("SOULSYNC_CONNECT_DISABLE") == "1":
        return
    with _runtime_lock:
        if _runtime_thread is not None:
            return

        def _worker() -> None:
            global _upnp_mapper
            host = os.environ.get("SOULSYNC_WEB_BIND_HOST", "0.0.0.0")
            tls_port = connect_tls_port(config_manager)
            backoff = 15
            while True:
                try:
                    renewed = ensure_certificate(get_database, config_manager)
                    _, store = _identity_and_store(get_database, config_manager)
                    if store.fingerprint():
                        if _https_server is None or renewed:
                            _start_https_listener(app, store, host, tls_port)
                        if _upnp_mapper is None:
                            from .upnp import UpnpPortMapper

                            _upnp_mapper = UpnpPortMapper(
                                tls_port,
                                cert_fingerprint_provider=store.fingerprint,
                            )
                            _upnp_mapper.start()
                        start(get_database, config_manager, port=tls_port)
                        backoff = 15
                        time.sleep(_RENEW_INTERVAL_SECONDS)
                        continue
                except Exception as exc:  # noqa: BLE001
                    log.warning("connect runtime: setup attempt failed (%s); retrying in %ss", exc, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 600)

        _runtime_thread = threading.Thread(target=_worker, daemon=True, name="ConnectRuntime")
        _runtime_thread.start()


def current_addresses(get_database, config_manager) -> list[str]:
    """Return the server's live LAN + WAN Connect candidate URLs (for /api/devices/me)."""
    try:
        identity, store = _identity_and_store(get_database, config_manager)
        if not store.fingerprint():
            return []
        tls_port = connect_tls_port(config_manager)
        observed = None
        if _client is not None and _client.observed_public_ip:
            observed = _client.observed_public_ip
        elif _upnp_mapper is not None and _upnp_mapper.mapping and _upnp_mapper.mapping.external_ip:
            observed = _upnp_mapper.mapping.external_ip
        ext_port = _upnp_mapper.external_port if _upnp_mapper else None
        return build_address_list(
            identity,
            observed_public_ip=observed,
            lan_ips=_local_lan_ips(),
            port=tls_port,
            external_port=ext_port,
        )
    except Exception:  # noqa: BLE001
        return []


def register_created(code_hash_hex: str, ttl_seconds: int) -> None:
    if _client is not None:
        _client.register_code(code_hash_hex, ttl_seconds)


def report_redeemed(code_hash_hex: str) -> None:
    if _client is not None:
        _client.report_redeemed(code_hash_hex)


def ensure_certificate(get_database, config_manager) -> bool:
    """Obtain/renew the wildcard certificate (under 30 days left)."""
    identity, store = _identity_and_store(get_database, config_manager)
    http = RequestsHttp()
    service_url = ""
    try:
        service_url = config_manager.get("connect.service_url", "") or ""
    except Exception:  # noqa: BLE001
        service_url = ""
    broker = Dns01Broker(service_url or DEFAULT_SERVICE_URL, identity, http)
    return _acme_ensure(
        store,
        dns_id_value=dns_id(identity),
        broker=broker,
        http=http,
        account_key=store.load_account_key(),
    )


def check_ct(get_database, config_manager) -> list[dict]:
    """CT-watch this server's own name; returns unexpected certs."""
    identity, store = _identity_and_store(get_database, config_manager)
    http = RequestsHttp()
    return check_ct_log(dns_id(identity), store.known_serials(), http.get)


def _reset_for_tests() -> None:
    global _client
    _client = None
