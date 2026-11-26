"""where a paired device can reach this server (PAIRING_SPEC.md 6).

the address list is ``[{kind, url}]``, local first:

* ``local``: found on their own. the address the admin's browser is using
  right now when it is a home-network one, then this machine's own
  home-network addresses on the web port;
* ``remote``: the one address for outside the home (own domain, tunnel,
  port forward), typed in Settings > Devices (``connect.remote_url``);
* ``custom``: any others the admin adds (``connect.custom_urls``), say a
  Tailscale name.

the pairing payload is the lookup ID, the fingerprint, this list and the
code, as JSON; the QR carries it as a ``soulsync://pair?d=`` link, so a
phone's own camera opens the player app on its pairing screen.

nothing here is sent anywhere. the connect service (later) gets the list
inside its sealed answers, never as a stored record.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import socket
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

PAYLOAD_VERSION = 1
LINK_PREFIX = "soulsync://pair?d="
MAX_CUSTOM = 5
REMOTE_KEY = "connect.remote_url"
CUSTOM_KEY = "connect.custom_urls"

# private, link-local and the carrier-grade range tailscale lives in
_HOME_NETS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16",
    "fc00::/7", "fe80::/10"))


def is_home_address(host: str) -> bool:
    """an address only reachable inside a home network (or a tailnet)."""
    host = (host or "").strip("[]")
    if host.endswith(".local") or host.endswith(".lan") or host.endswith(".home.arpa"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in _HOME_NETS)


def _docker_bridge(ip: str) -> bool:
    """docker's default bridge (172.17.0.0/16): this container's own
    address, which nothing outside the host can reach."""
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network("172.17.0.0/16")
    except ValueError:
        return False


def clean_url(raw: Any) -> Tuple[Optional[str], Optional[str]]:
    """(url, None) for a usable server address, (None, why) when it isn't.
    http(s), a host, an optional port and path; no user, query or fragment."""
    text = str(raw or "").strip()
    if not text:
        return None, None
    if "://" not in text:
        return None, "Start it with https:// (or http://)."
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None, "That doesn't look like an address."
    if parts.scheme not in ("http", "https"):
        return None, "Only http:// and https:// addresses work."
    if not parts.hostname:
        return None, "That address has no host name."
    if parts.username or parts.password:
        return None, "Leave the user name and password out of the address."
    if parts.query or parts.fragment:
        return None, "Leave out anything after ? or #."
    host = parts.hostname
    if ":" in host:
        host = f"[{host}]"
    netloc = host + (f":{port}" if port else "")
    path = parts.path.rstrip("/")
    return f"{parts.scheme}://{netloc}{path}", None


def is_plain_http(url: str) -> bool:
    return (url or "").lower().startswith("http://")


def _own_ips() -> List[str]:
    """this machine's IPv4 addresses, best effort, never raising. no packet
    leaves: a UDP connect only asks the routing table."""
    found: List[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1, never answered, never sent to
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    out = []
    for ip in found:
        if ip not in out and not ip.startswith("127."):
            out.append(ip)
    return out


def web_port() -> int:
    try:
        return int(os.environ.get("SOULSYNC_WEB_BIND_PORT", "8008"))
    except ValueError:
        return 8008


def local_urls(request_host_url: Optional[str] = None, own_ips: Optional[Iterable[str]] = None,
               port: Optional[int] = None) -> List[str]:
    """home-network addresses: the browser's own, when it is one, then this
    machine's (docker's bridge left out)."""
    out: List[str] = []
    if request_host_url:
        url, _ = clean_url(request_host_url)
        if url and is_home_address(urlsplit(url).hostname or ""):
            out.append(url)
    port = web_port() if port is None else port
    for ip in (_own_ips() if own_ips is None else own_ips):
        if not is_home_address(ip) or _docker_bridge(ip):
            continue
        url = f"http://{ip}:{port}"
        if url not in out:
            out.append(url)
    return out


def configured(get) -> Dict[str, Any]:
    """the remote address and the extra ones, as saved."""
    remote, _ = clean_url(get(REMOTE_KEY, "") or "")
    custom = []
    raw = get(CUSTOM_KEY, []) or []
    if isinstance(raw, str):
        raw = [raw]
    for item in raw if isinstance(raw, list) else []:
        url, _ = clean_url(item)
        if url and url not in custom:
            custom.append(url)
    return {"remote_url": remote, "custom_urls": custom[:MAX_CUSTOM]}


def address_list(get, *, request_host_url: Optional[str] = None,
                 own_ips: Optional[Iterable[str]] = None) -> List[Dict[str, str]]:
    """[{kind, url}], local first, no repeats."""
    conf = configured(get)
    out: List[Dict[str, str]] = []
    seen = set()

    def add(kind, url):
        if url and url not in seen:
            seen.add(url)
            out.append({"kind": kind, "url": url})

    for url in local_urls(request_host_url, own_ips):
        add("local", url)
    add("remote", conf["remote_url"])
    for url in conf["custom_urls"]:
        add("custom", url)
    return out


def warnings(addresses: List[Dict[str, str]]) -> List[str]:
    out = []
    for a in addresses:
        if a["kind"] != "local" and is_plain_http(a["url"]):
            out.append(f"{a['url']} is plain http: passwords, tokens and everything you play cross "
                       "the internet readable by anyone on the way. Put it behind https.")
    return out


def pairing_payload(*, lookup_id: str, fingerprint: str, addresses: List[Dict[str, str]],
                    code: Optional[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"v": PAYLOAD_VERSION, "lookup_id": lookup_id, "fingerprint": fingerprint,
                           "addresses": addresses}
    if code:
        out["code"] = code
    return out


def payload_text(payload: Dict[str, Any]) -> str:
    """the compact JSON the QR carries."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def pairing_link(payload: Dict[str, Any]) -> str:
    """the link the QR carries: the compact JSON as unpadded base64url.
    the player app opens it; it only fills the pairing screen in."""
    raw = base64.urlsafe_b64encode(payload_text(payload).encode("utf-8")).decode("ascii")
    return LINK_PREFIX + raw.rstrip("=")


__all__ = ["PAYLOAD_VERSION", "LINK_PREFIX", "pairing_link", "REMOTE_KEY", "CUSTOM_KEY", "MAX_CUSTOM", "is_home_address", "clean_url",
           "is_plain_http", "local_urls", "configured", "address_list", "warnings", "pairing_payload",
           "payload_text", "web_port"]
