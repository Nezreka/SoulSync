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
UPNP_CONFIG_KEY = "connect.upnp_enabled"
# the home address the admin's browser used to make a code (#2328): inside
# docker the container only sees its own bridge address, which no phone can
# reach, so the host's address comes from the browser instead
LAN_HINT_KEY = "connect.lan_hint"
_RENEW_INTERVAL_SECONDS = 12 * 3600

_LIVE_SQL = "used_at IS NULL AND cancelled_at IS NULL AND locked_at IS NULL AND expires_at > ?"

_client: PresenceClient | None = None
_https_server = None
_upnp_mapper = None
_runtime_thread: threading.Thread | None = None
_runtime_lock = threading.Lock()
# why setup or the https listener last failed, for Settings > Devices
_runtime_error: str | None = None
_listener_error: str | None = None
_lan_hint: str | None = None


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
        except Exception as exc:  # noqa: BLE001
            log.debug("connect_tls_port config lookup failed: %s", exc)
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


def _is_public_ip(ip_text: str | None) -> bool:
    """True only for globally routable public IPs (rejects RFC1918/Docker proxy IPs like 172.16.x.x)."""
    if not ip_text:
        return False
    try:
        return ipaddress.ip_address(ip_text.strip()).is_global
    except ValueError:
        return False


def _best_public_ip(observed: str | None = None) -> str | None:
    """Pick the real globally routable WAN IP (UPnP IGD external IP or broker observed_ip)."""
    upnp_ip = (
        _upnp_mapper.mapping.external_ip
        if (_upnp_mapper is not None and _upnp_mapper.mapping is not None)
        else None
    )
    if _is_public_ip(upnp_ip):
        return upnp_ip
    if _is_public_ip(observed):
        return observed
    if _client is not None and _is_public_ip(_client.observed_public_ip):
        return _client.observed_public_ip
    return None


def _docker_bridge(ip_text: str) -> bool:
    """docker's default bridge (172.17.0.0/16): the container's own
    address, which nothing outside the host can reach."""
    try:
        return ipaddress.ip_address(ip_text) in ipaddress.ip_network("172.17.0.0/16")
    except ValueError:
        return False


def usable_lan_hint(host: str | None) -> str | None:
    """a browser's host when it is a home ip a phone could reach: private
    or tailnet, not loopback, not docker's bridge. names are left out."""
    text = (host or "").strip().strip("[]")
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return None
    if addr.is_loopback or addr.is_link_local or addr.is_unspecified or _docker_bridge(text):
        return None
    from .addresses import is_home_address

    return str(addr) if is_home_address(str(addr)) else None


def remember_lan_hint(host: str | None, config_manager=None) -> None:
    """keep the browser's home address for the sealed answers (#2328)."""
    global _lan_hint
    ip = usable_lan_hint(host)
    if not ip or ip == _lan_hint:
        return
    _lan_hint = ip
    if config_manager is not None:
        try:
            if config_manager.get(LAN_HINT_KEY, "") != ip:
                config_manager.set(LAN_HINT_KEY, ip)
        except Exception:  # noqa: BLE001 - the hint is a nicety
            log.debug("could not save the lan hint", exc_info=True)


def _lan_ips_with_hint(config_manager=None) -> list[str]:
    ips = _local_lan_ips()
    hint = _lan_hint
    if hint is None and config_manager is not None:
        try:
            hint = usable_lan_hint(config_manager.get(LAN_HINT_KEY, "") or "")
        except Exception:  # noqa: BLE001
            hint = None
    if hint and hint not in ips:
        ips.append(hint)
    return ips


def _local_lan_ips() -> list[str]:
    ips: set[str] = set()
    try:  # the primary outbound address, the LAN IP other devices use
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))  # TEST-NET-1: no traffic is sent (UDP)
            ips.add(sock.getsockname()[0])
    except OSError:
        pass
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0].split("%", 1)[0]
            try:
                addr = ipaddress.ip_address(ip)
            except ValueError:
                continue
            # Skip loopback and IPv6 link-local (fe80::) which aren't usable cross-host
            if addr.is_loopback or addr.is_link_local:
                continue
            ips.add(str(addr))
    except OSError:
        pass
    # docker's bridge address can't be reached from a phone (#2328)
    return sorted(ip for ip in ips if not _docker_bridge(ip))


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
            observed_public_ip=_best_public_ip(observed),
            lan_ips=_lan_ips_with_hint(config_manager),
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
    global _https_server, _listener_error
    if _https_server is not None:
        try:
            _https_server.shutdown()
        except Exception as exc:  # noqa: BLE001
            log.debug("connect_tls_port config lookup failed: %s", exc)
        _https_server = None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile=str(store.chain_path), keyfile=str(store.key_path))
    from werkzeug.serving import make_server

    try:
        srv = make_server(host, port, app, threaded=True, ssl_context=ctx)
    except OSError as exc:
        _listener_error = f"port {port} could not be opened: {exc}"[:200]
        raise
    _listener_error = None
    _https_server = srv
    threading.Thread(target=srv.serve_forever, daemon=True, name="ConnectHttpsListener").start()
    log.info("connect https: listening on https://%s:%s (cert %s)", host, port, store.fingerprint()[:12])


def upnp_enabled(config_manager=None) -> bool:
    """Whether automatic UPnP IGD / NAT-PMP port forwarding is opted in (default: False)."""
    if config_manager is None:
        return False
    try:
        return bool(config_manager.get(UPNP_CONFIG_KEY, False))
    except Exception:  # noqa: BLE001
        return False


def upnp_status(config_manager=None) -> dict:
    """Live status of automatic UPnP / NAT-PMP port forwarding for Settings > Devices."""
    enabled = upnp_enabled(config_manager)
    tls_port = connect_tls_port(config_manager)
    m = _upnp_mapper.mapping if _upnp_mapper is not None else None
    return {
        "enabled": enabled,
        "active": bool(enabled and m is not None),
        "method": m.method if (enabled and m is not None) else None,
        "external_ip": m.external_ip if (enabled and m is not None) else None,
        "external_port": m.external_port if (enabled and m is not None) else None,
        "internal_port": tls_port,
    }


def set_upnp_enabled(enabled: bool, get_database, config_manager) -> dict:
    """Toggle automatic UPnP / NAT-PMP port forwarding immediately and persist."""
    global _upnp_mapper
    want = bool(enabled)
    config_manager.set(UPNP_CONFIG_KEY, want)
    tls_port = connect_tls_port(config_manager)
    if want:
        if _upnp_mapper is None:
            from .upnp import UpnpPortMapper

            _, store = _identity_and_store(get_database, config_manager)
            _upnp_mapper = UpnpPortMapper(
                tls_port,
                cert_fingerprint_provider=store.fingerprint,
            )
            _upnp_mapper.start()
        elif _upnp_mapper.mapping is None:
            _upnp_mapper.map_once()
    else:
        if _upnp_mapper is not None:
            _upnp_mapper.stop()
            _upnp_mapper = None
    return upnp_status(config_manager)


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
            global _upnp_mapper, _runtime_error
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
                        if upnp_enabled(config_manager) and _upnp_mapper is None:
                            from .upnp import UpnpPortMapper

                            _upnp_mapper = UpnpPortMapper(
                                tls_port,
                                cert_fingerprint_provider=store.fingerprint,
                            )
                            _upnp_mapper.start()
                        start(get_database, config_manager, port=tls_port)
                        _runtime_error = None
                        backoff = 15
                        time.sleep(_RENEW_INTERVAL_SECONDS)
                        continue
                except Exception as exc:  # noqa: BLE001
                    _runtime_error = (str(exc).strip() or type(exc).__name__)[:200]
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
        observed = _best_public_ip()
        ext_port = _upnp_mapper.external_port if _upnp_mapper else None
        return build_address_list(
            identity,
            observed_public_ip=observed,
            lan_ips=_lan_ips_with_hint(config_manager),
            port=tls_port,
            external_port=ext_port,
        )
    except Exception:  # noqa: BLE001
        return []


def pairing_status(get_database, config_manager) -> dict:
    """where pairing stands, in words, for Settings > Devices (#2328).

    state is one of: connected, connecting (no link to the pairing
    service right now), setting_up (no certificate yet), displaced
    (another copy of this server took over), off (turned off, or the
    pairing runtime never started). ``ready`` is true only when a phone can
    find this server with a code right now. ``listening`` says whether the
    https port the app connects to is open on this machine."""
    tls_port = connect_tls_port(config_manager)
    base = {"tls_port": tls_port, "listening": _https_server is not None,
            "listener_error": _listener_error, "error": None}
    if os.environ.get("SOULSYNC_CONNECT_DISABLE") == "1":
        return {**base, "state": "off", "ready": False,
                "words": "Pairing is turned off on this server (SOULSYNC_CONNECT_DISABLE=1). "
                         "Remove that setting and restart SoulSync."}
    if _client is not None:
        st = _client.state()
        if st["state"] == "connected":
            return {**base, "state": "connected", "ready": True,
                    "words": "Connected to the pairing service."}
        if st["state"] == "displaced":
            return {**base, "state": "displaced", "ready": False,
                    "words": "Another copy of this server took over pairing (a restored backup "
                             "running alongside this one?). Stop the other copy, then restart SoulSync."}
        return {**base, "state": "connecting", "ready": False, "error": st.get("error"),
                "words": "Not connected to the pairing service right now, so the app can't find "
                         "this server. SoulSync keeps trying; codes made now start working once it "
                         "connects. Check this server can reach connect.ssync.net."}
    try:
        _, store = _identity_and_store(get_database, config_manager)
        has_cert = bool(store.fingerprint())
    except Exception:  # noqa: BLE001
        has_cert = False
    if _runtime_thread is None and not has_cert:
        return {**base, "state": "off", "ready": False,
                "words": "Pairing hasn't started on this server. Restart SoulSync."}
    return {**base, "state": "setting_up", "ready": False, "error": _runtime_error,
            "words": "Still setting up pairing: getting this server's certificate. This takes a "
                     "minute after the first start; it needs to reach connect.ssync.net."}


def app_targets(config_manager) -> list[dict]:
    """the addresses a paired app will try, as plain ip and port, home ones
    first: what the sealed answer carries, for the admin to read (#2328)."""
    tls_port = connect_tls_port(config_manager)
    out: list[dict] = []
    for ip in _lan_ips_with_hint(config_manager):
        try:
            if isinstance(ipaddress.ip_address(ip), ipaddress.IPv6Address):
                continue  # the v4 ones are the readable, likely ones
        except ValueError:
            continue
        out.append({"ip": ip, "port": tls_port, "home": True})
    public = _best_public_ip()
    if public:
        ext = _upnp_mapper.external_port if _upnp_mapper else None
        out.append({"ip": public, "port": ext or tls_port, "home": False})
    return out


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
    global _client, _lan_hint, _runtime_error, _listener_error
    _client = None
    _lan_hint = None
    _runtime_error = None
    _listener_error = None
