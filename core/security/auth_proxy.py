"""Trust an authenticated-user header from a forward-auth proxy.

When SoulSync sits behind an auth proxy (Authelia / Authentik / oauth2-proxy), the
proxy authenticates the user and passes their identity in a header (commonly
``Remote-User``). With ``security.auth_proxy_header`` set to that header name,
SoulSync treats a request carrying it as already authenticated and lets it past
the launch lock: the proxy is the gatekeeper.

The header only counts when the request's real peer (the address that opened the
connection, never X-Forwarded-For) is in ``security.auth_proxy_trusted``, a list
of IPs / CIDRs. An empty list means the header is ignored, so a direct client
sending ``Remote-User: admin`` gets nothing.

OFF by default (empty header name): a strict no-op, the launch PIN behaves exactly
as before.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Callable, Iterable, List, Optional, Tuple, Union

HEADER_KEY = "security.auth_proxy_header"
TRUSTED_KEY = "security.auth_proxy_trusted"

_Network = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]


def trusted_proxy_user(get_header: Callable[[str], Optional[str]],
                       header_name: str) -> Optional[str]:
    """Return the username from the configured proxy header, or None.

    Low level: reads the header only, no address check. Request code goes
    through :func:`request_proxy_user`, which also checks the peer.
    """
    if not header_name:
        return None
    try:
        value = (get_header(header_name) or "").strip()
    except Exception:
        return None
    return value or None


def parse_trusted_proxies(raw: Any) -> Tuple[List[_Network], List[str]]:
    """Parse the trusted proxy setting into networks.

    Takes a string (comma, space or newline separated) or a list. A bare IP
    becomes a single-host network. Returns (networks, unreadable entries)."""
    if raw is None:
        return [], []
    if isinstance(raw, str):
        items: Iterable[Any] = re.split(r"[\s,;]+", raw)
    elif isinstance(raw, (list, tuple, set)):
        items = raw
    else:
        return [], [str(raw)]
    nets: List[_Network] = []
    bad: List[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        try:
            nets.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            bad.append(text)
    return nets, bad


def peer_address(environ: dict) -> str:
    """The address that really connected to us.

    With reverse-proxy mode on, werkzeug's ProxyFix rewrites REMOTE_ADDR from
    X-Forwarded-For and keeps the original in ``werkzeug.proxy_fix.orig``. A
    forwarded value is client controlled, so the check uses the original."""
    try:
        orig = environ.get("werkzeug.proxy_fix.orig") or {}
        addr = orig.get("REMOTE_ADDR") if isinstance(orig, dict) else None
        if not addr:
            addr = environ.get("REMOTE_ADDR")
        return str(addr or "").strip()
    except Exception:
        return ""


def address_is_trusted(addr: str, networks: Iterable[_Network]) -> bool:
    if not addr:
        return False
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    # ::ffff:10.0.0.5 is the same peer as 10.0.0.5
    mapped = getattr(ip, "ipv4_mapped", None)
    candidates = [ip] + ([mapped] if mapped is not None else [])
    for net in networks:
        for cand in candidates:
            if cand.version == net.version and cand in net:
                return True
    return False


def request_proxy_user(request, config_get: Optional[Callable[..., Any]]) -> Optional[str]:
    """The proxy-authenticated username for this request, or None.

    None unless the header setting is on, the header is present, and the
    request's real peer address is in the trusted proxy list."""
    if config_get is None:
        return None
    try:
        header = config_get(HEADER_KEY, "") or ""
    except Exception:
        return None
    if not header:
        return None
    user = trusted_proxy_user(request.headers.get, header)
    if not user:
        return None
    try:
        nets, _bad = parse_trusted_proxies(config_get(TRUSTED_KEY, ""))
    except Exception:
        return None
    if not nets:
        return None
    if not address_is_trusted(peer_address(getattr(request, "environ", None) or {}), nets):
        return None
    return user


def setup_problem(config_get: Callable[..., Any]) -> Optional[str]:
    """A plain-words problem with the auth proxy setup, or None when fine or off."""
    try:
        header = config_get(HEADER_KEY, "") or ""
        raw = config_get(TRUSTED_KEY, "")
    except Exception:
        return None
    if not header:
        return None
    nets, bad = parse_trusted_proxies(raw)
    if bad:
        return (f"Some trusted proxy addresses can't be read: {', '.join(bad[:5])}. "
                "Use an IP like 172.18.0.2 or a range like 172.18.0.0/16.")
    if not nets:
        return (f"The auth proxy header ({header}) is set but no trusted proxy address is, "
                "so SoulSync ignores the header and people get the PIN screen instead. "
                "Add your proxy's IP or range in Settings, Security, Reverse proxy.")
    return None


__all__ = [
    "trusted_proxy_user", "parse_trusted_proxies", "peer_address",
    "address_is_trusted", "request_proxy_user", "setup_problem",
    "HEADER_KEY", "TRUSTED_KEY",
]
