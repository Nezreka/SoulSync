"""Automatic router port forwarding for SoulSync Connect (UPnP IGD + NAT-PMP).

Pure Python stdlib (no external packages):
1. Discovers the local LAN IPv4 address and binds SSDP multicast (`IP_MULTICAST_IF`)
   to that interface so multi-NIC hosts (WSL/Docker/VPN) always probe the real LAN.
2. Tries UPnP IGD (`WANIPConnection:1/2`, `WANPPPConnection:1`) `AddPortMapping`.
3. Falls back to NAT-PMP (RFC 6886, UDP 5351) if UPnP IGD does not respond.
4. When `cert_fingerprint_provider` is provided, verifies that the mapped external
   port is not silently intercepted by the router's own HTTPS management UI (e.g.
   Google/Nest Wifi on port 8443) and automatically steps to a clean external port
   (`8444`, `18443`, `28443`, `38443`) if needed.
5. Renews the lease in a daemon thread and cleans up on shutdown.
"""

from __future__ import annotations

import hashlib
import logging
import socket
import ssl
import struct
import threading
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

_SSDP_ADDR = ("239.255.255.250", 1900)
_IGD_SERVICES = (
    "urn:schemas-upnp-org:service:WANIPConnection:2",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
    "urn:schemas-upnp-org:service:WANPPPConnection:1",
)
_DEFAULT_FALLBACK_PORTS = (8444, 18443, 28443, 38443)


@dataclass(frozen=True)
class PortMapping:
    external_port: int
    internal_port: int
    external_ip: str | None
    method: str  # "upnp" | "natpmp"
    control_url: str | None = None
    service_type: str | None = None
    gateway_ip: str | None = None


def primary_lan_ip() -> str | None:
    """Return the outbound LAN IPv4 address used to reach the internet."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return None


def _ssdp_discover(local_ip: str, timeout: float = 2.0) -> list[tuple[str, str]]:
    """Return list of (gateway_ip, root_desc_url) from UPnP IGD SSDP responses."""
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for st in (
        "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
        "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
    ):
        msg = "\r\n".join(
            [
                "M-SEARCH * HTTP/1.1",
                "HOST: 239.255.255.250:1900",
                'MAN: "ssdp:discover"',
                "MX: 2",
                f"ST: {st}",
                "",
                "",
            ]
        ).encode("ascii")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip))
            sock.bind((local_ip, 0))
            sock.settimeout(timeout)
            sock.sendto(msg, _SSDP_ADDR)
            while True:
                try:
                    data, addr = sock.recvfrom(4096)
                except socket.timeout:
                    break
                text = data.decode("utf-8", errors="replace")
                for line in text.splitlines():
                    if line.lower().startswith("location:"):
                        loc = line.split(":", 1)[1].strip()
                        if loc and loc not in seen:
                            seen.add(loc)
                            results.append((addr[0], loc))
        except OSError:
            pass
        finally:
            sock.close()
        if results:
            break
    return results


def _find_upnp_control(desc_url: str, timeout: float = 3.0) -> tuple[str, str] | None:
    """Fetch IGD rootDesc.xml and return (control_url, service_type)."""
    try:
        req = urllib.request.Request(desc_url, headers={"User-Agent": "SoulSync/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            xml_bytes = resp.read(256 * 1024)
        root = ET.fromstring(xml_bytes)
    except Exception:  # noqa: BLE001
        return None

    found: dict[str, str] = {}
    for elem in root.iter():
        if elem.tag.endswith("}service") or elem.tag == "service":
            stype = None
            ctrl = None
            for child in elem:
                if child.tag.endswith("}serviceType") or child.tag == "serviceType":
                    stype = (child.text or "").strip()
                elif child.tag.endswith("}controlURL") or child.tag == "controlURL":
                    ctrl = (child.text or "").strip()
            if stype and ctrl and stype in _IGD_SERVICES:
                found[stype] = urllib.parse.urljoin(desc_url, ctrl)

    for preferred in _IGD_SERVICES:
        if preferred in found:
            return found[preferred], preferred
    return None


def _soap_call(
    control_url: str,
    service_type: str,
    action: str,
    args: dict[str, str],
    timeout: float = 4.0,
) -> tuple[int, str]:
    inner = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
    body = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:{action} xmlns:u="{service_type}">{inner}</u:{action}></s:Body>'
        "</s:Envelope>"
    ).encode("utf-8")
    req = urllib.request.Request(
        control_url,
        data=body,
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{service_type}#{action}"',
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(65536).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read(65536).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            err_body = ""
        return exc.code, err_body
    except Exception:  # noqa: BLE001
        return 0, ""


def _upnp_get_external_ip(control_url: str, service_type: str) -> str | None:
    status, body = _soap_call(control_url, service_type, "GetExternalIPAddress", {})
    if status != 200:
        return None
    try:
        root = ET.fromstring(body)
        for elem in root.iter():
            if elem.tag.endswith("NewExternalIPAddress") and elem.text:
                return elem.text.strip()
    except Exception as exc:  # noqa: BLE001
        log.debug("connect upnp: failed parsing external IP response: %s", exc)
    return None


def _upnp_add_mapping(
    control_url: str,
    service_type: str,
    *,
    external_port: int,
    internal_port: int,
    internal_ip: str,
    lease_seconds: int,
) -> bool:
    status, _ = _soap_call(
        control_url,
        service_type,
        "AddPortMapping",
        {
            "NewRemoteHost": "",
            "NewExternalPort": str(external_port),
            "NewProtocol": "TCP",
            "NewInternalPort": str(internal_port),
            "NewInternalClient": internal_ip,
            "NewEnabled": "1",
            "NewPortMappingDescription": "SoulSync Connect",
            "NewLeaseDuration": str(lease_seconds),
        },
    )
    if status == 200:
        return True
    # Some routers only accept permanent (0) lease duration
    if lease_seconds != 0:
        status2, _ = _soap_call(
            control_url,
            service_type,
            "AddPortMapping",
            {
                "NewRemoteHost": "",
                "NewExternalPort": str(external_port),
                "NewProtocol": "TCP",
                "NewInternalPort": str(internal_port),
                "NewInternalClient": internal_ip,
                "NewEnabled": "1",
                "NewPortMappingDescription": "SoulSync Connect",
                "NewLeaseDuration": "0",
            },
        )
        return status2 == 200
    return False


def _upnp_delete_mapping(control_url: str, service_type: str, external_port: int) -> None:
    _soap_call(
        control_url,
        service_type,
        "DeletePortMapping",
        {
            "NewRemoteHost": "",
            "NewExternalPort": str(external_port),
            "NewProtocol": "TCP",
        },
    )


def _natpmp_map(
    local_ip: str,
    gateway_ip: str,
    *,
    internal_port: int,
    external_port: int,
    lease_seconds: int,
    timeout: float = 2.0,
) -> tuple[int, str | None] | None:
    """Request a TCP mapping via NAT-PMP (RFC 6886). Returns (mapped_ext_port, ext_ip)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((local_ip, 0))
        sock.settimeout(timeout)
        # First query external IP (ver=0, op=0)
        ext_ip: str | None = None
        try:
            sock.sendto(b"\x00\x00", (gateway_ip, 5351))
            resp, _ = sock.recvfrom(64)
            if len(resp) >= 12 and resp[1] == 128 and struct.unpack("!H", resp[2:4])[0] == 0:
                ext_ip = socket.inet_ntoa(resp[8:12])
        except OSError:
            pass
        # Map TCP (ver=0, op=2)
        req = struct.pack("!BBHHHI", 0, 2, 0, internal_port, external_port, lease_seconds)
        sock.sendto(req, (gateway_ip, 5351))
        resp, _ = sock.recvfrom(64)
        if len(resp) >= 16 and resp[1] == 130 and struct.unpack("!H", resp[2:4])[0] == 0:
            _, _, _, _epoch, _int_p, mapped_ext, _lifetime = struct.unpack("!BBHIHHI", resp[:16])
            if mapped_ext > 0:
                return mapped_ext, ext_ip
    except OSError:
        return None
    finally:
        sock.close()
    return None


def _is_port_intercepted_by_other_tls(
    external_ip: str,
    external_port: int,
    expected_fp: str,
    timeout: float = 2.0,
) -> bool:
    """Detect when the router itself (e.g. Google/Nest Wifi on 8443) answers on
    external_ip:external_port with a DIFFERENT TLS certificate than SoulSync's.
    Returns True ONLY if a different TLS cert answered; connection timeout / refusal
    (no hairpin NAT) returns False so we don't reject valid mappings."""
    if not external_ip or not expected_fp:
        return False
    ctx = ssl._create_unverified_context()  # noqa: SLF001 - intentional fingerprint check
    try:
        with socket.create_connection((external_ip, external_port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=external_ip) as ssock:
                der = ssock.getpeercert(binary_form=True)
                if not der:
                    return False
                got_fp = hashlib.sha256(der).hexdigest().lower()
                return got_fp != expected_fp.lower()
    except OSError:
        # Hairpin NAT disabled or unreachable from LAN -> cannot prove conflict
        return False


def _guess_gateway_ip(local_ip: str) -> str | None:
    parts = local_ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.{parts[2]}.1"
    return None


class UpnpPortMapper:
    """Manages an automatic UPnP IGD / NAT-PMP TCP port mapping for Connect."""

    def __init__(
        self,
        internal_port: int,
        *,
        cert_fingerprint_provider: Callable[[], str | None] | None = None,
        lease_seconds: int = 3600,
    ) -> None:
        self._internal_port = internal_port
        self._cert_fp_provider = cert_fingerprint_provider
        self._lease_seconds = lease_seconds
        self._lock = threading.Lock()
        self._mapping: PortMapping | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def mapping(self) -> PortMapping | None:
        with self._lock:
            return self._mapping

    @property
    def external_port(self) -> int | None:
        m = self.mapping
        return m.external_port if m else None

    def map_once(self) -> PortMapping | None:
        local_ip = primary_lan_ip()
        if not local_ip:
            return None
        expected_fp = (self._cert_fp_provider() or "").strip() if self._cert_fp_provider else ""
        candidate_ports: list[int] = [self._internal_port]
        for p in _DEFAULT_FALLBACK_PORTS:
            if p not in candidate_ports:
                candidate_ports.append(p)

        # 1. Try UPnP IGD first
        igds = _ssdp_discover(local_ip)
        for gw_ip, desc_url in igds:
            ctrl = _find_upnp_control(desc_url)
            if not ctrl:
                continue
            control_url, service_type = ctrl
            ext_ip = _upnp_get_external_ip(control_url, service_type)
            for ext_port in candidate_ports:
                if not _upnp_add_mapping(
                    control_url,
                    service_type,
                    external_port=ext_port,
                    internal_port=self._internal_port,
                    internal_ip=local_ip,
                    lease_seconds=self._lease_seconds,
                ):
                    continue
                if ext_ip and expected_fp and _is_port_intercepted_by_other_tls(ext_ip, ext_port, expected_fp):
                    log.info(
                        "connect upnp: external port %s on %s is intercepted by router TLS; trying next port",
                        ext_port,
                        ext_ip,
                    )
                    _upnp_delete_mapping(control_url, service_type, ext_port)
                    continue
                m = PortMapping(
                    external_port=ext_port,
                    internal_port=self._internal_port,
                    external_ip=ext_ip,
                    method="upnp",
                    control_url=control_url,
                    service_type=service_type,
                    gateway_ip=gw_ip,
                )
                with self._lock:
                    self._mapping = m
                log.info(
                    "connect upnp: mapped external %s:%s -> %s:%s via UPnP IGD",
                    ext_ip or "WAN",
                    ext_port,
                    local_ip,
                    self._internal_port,
                )
                return m

        # 2. Fallback to NAT-PMP on gateway
        gw_ip = igds[0][0] if igds else _guess_gateway_ip(local_ip)
        if gw_ip:
            for ext_port in candidate_ports:
                res = _natpmp_map(
                    local_ip,
                    gw_ip,
                    internal_port=self._internal_port,
                    external_port=ext_port,
                    lease_seconds=self._lease_seconds,
                )
                if not res:
                    continue
                mapped_ext, ext_ip = res
                if ext_ip and expected_fp and _is_port_intercepted_by_other_tls(ext_ip, mapped_ext, expected_fp):
                    log.info(
                        "connect nat-pmp: external port %s on %s is intercepted by router TLS; trying next port",
                        mapped_ext,
                        ext_ip,
                    )
                    _natpmp_map(
                        local_ip,
                        gw_ip,
                        internal_port=self._internal_port,
                        external_port=mapped_ext,
                        lease_seconds=0,
                    )
                    continue
                m = PortMapping(
                    external_port=mapped_ext,
                    internal_port=self._internal_port,
                    external_ip=ext_ip,
                    method="natpmp",
                    gateway_ip=gw_ip,
                )
                with self._lock:
                    self._mapping = m
                log.info(
                    "connect nat-pmp: mapped external %s:%s -> %s:%s via NAT-PMP",
                    ext_ip or "WAN",
                    mapped_ext,
                    local_ip,
                    self._internal_port,
                )
                return m
        return None

    def start(self) -> PortMapping | None:
        """Perform an initial mapping attempt and start background renewal."""
        initial = self.map_once()
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._renew_loop, daemon=True, name="ConnectUpnpRenew")
            self._thread.start()
        return initial

    def _renew_loop(self) -> None:
        interval = max(300, self._lease_seconds // 2)
        while not self._stop.wait(interval):
            try:
                self.map_once()
            except Exception:  # noqa: BLE001
                log.debug("connect upnp: renewal attempt failed", exc_info=True)

    def stop(self) -> None:
        self._stop.set()
        m = self.mapping
        if m and m.method == "upnp" and m.control_url and m.service_type:
            try:
                _upnp_delete_mapping(m.control_url, m.service_type, m.external_port)
            except Exception as exc:  # noqa: BLE001
                log.debug("connect upnp: delete mapping on stop failed: %s", exc)
        with self._lock:
            self._mapping = None
