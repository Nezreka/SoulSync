"""the reachability check (PAIRING_SPEC.md 6): can the outside world reach
this server at its remote address?

no public probe service is used. the ones that exist are third parties
with their own terms and rate limits, and asking one would hand it this
server's address plus a live path on it. instead the check has two halves,
both against a one-time URL that only this process knows
(``/api/devices/reachability/<nonce>``, answered by api/devices.py):

1. the server fetches its own remote address. an answer carrying the nonce
   proves the address leads back to this very server (not another one,
   not a parked domain). it can't prove the wider internet gets in: a
   router that loops a server's own public address back to itself, or a
   home DNS that points the name inside, passes here and fails outside,
   and a router that can't loop back fails here although outside works.
   the words say so.
2. the same URL, as a link and a QR, for a phone on mobile data. when the
   phone opens it the hit is recorded with the address it came from, so
   the page can say "reached from 203.0.113.7, outside your network".

the connect service may offer a real outside probe later; this is the seam.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Any, Callable, Dict, List, Optional

PROBE_PATH = "/api/devices/reachability/"
PROBE_LIFETIME = 10 * 60
MAX_PROBES = 10
MAX_HITS = 20
SELF_HEADER = "X-SoulSync-Probe"

_lock = threading.Lock()
_probes: Dict[str, Dict[str, Any]] = {}
# marks the server's own fetch, so an outside caller can't pass as it
_self_marker = secrets.token_urlsafe(24)

GUIDANCE = [
    {"id": "port-forward", "title": "Forward a port on your router",
     "body": "Send a port on your router to this server's port, and use your public address "
             "(or a dynamic DNS name) with https in front of it."},
    {"id": "cloudflare-tunnel", "title": "Cloudflare Tunnel",
     "body": "Runs next to SoulSync and gives it an https address with no ports opened. "
             "Use the tunnel's address as the remote address."},
    {"id": "tailscale", "title": "Tailscale",
     "body": "Puts your phone and this server on one private network. Add the server's "
             "Tailscale name as an extra address; every device that listens needs Tailscale too."},
    {"id": "own-domain", "title": "Your own domain",
     "body": "A reverse proxy (Caddy, nginx, Traefik) with a certificate in front of SoulSync. "
             "Use that https address."},
]


def _prune(now: float) -> None:
    for nonce in [n for n, p in _probes.items() if p["expires"] <= now]:
        _probes.pop(nonce, None)


def start(now: Optional[float] = None) -> Dict[str, Any]:
    """a new one-time probe."""
    now = time.time() if now is None else now
    nonce = secrets.token_urlsafe(18)
    with _lock:
        _prune(now)
        while len(_probes) >= MAX_PROBES:
            _probes.pop(min(_probes, key=lambda n: _probes[n]["created"]))
        _probes[nonce] = {"nonce": nonce, "created": now, "expires": now + PROBE_LIFETIME, "hits": []}
    return view(nonce, now) or {}


def latest(now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    now = time.time() if now is None else now
    with _lock:
        _prune(now)
        if not _probes:
            return None
        nonce = max(_probes, key=lambda n: _probes[n]["created"])
    return view(nonce, now)


def view(nonce: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    now = time.time() if now is None else now
    with _lock:
        p = _probes.get(nonce)
        if not p or p["expires"] <= now:
            return None
        return {"nonce": nonce, "path": PROBE_PATH + nonce, "seconds_left": int(p["expires"] - now),
                "hits": [dict(h) for h in p["hits"]]}


def record_hit(nonce: str, ip: str, *, self_marker: str = "", now: Optional[float] = None) -> bool:
    """a fetch of the one-time URL. False for an unknown or spent nonce."""
    now = time.time() if now is None else now
    from core.connect.addresses import is_home_address
    via = "self" if self_marker and secrets.compare_digest(self_marker, _self_marker) else "outside"
    with _lock:
        p = _probes.get(nonce)
        if not p or p["expires"] <= now:
            return False
        if len(p["hits"]) < MAX_HITS:
            p["hits"].append({"ip": ip or "", "at": now, "via": via,
                              "from_home": via == "self" or is_home_address(ip or "")})
        return True


def self_check(remote_url: str, nonce: str, *, fetch: Optional[Callable[..., Any]] = None,
               timeout: float = 8.0) -> Dict[str, Any]:
    """fetch our own remote address's one-time URL. {ok, code, words}."""
    if fetch is None:
        import requests
        fetch = requests.get
    url = remote_url.rstrip("/") + PROBE_PATH + nonce
    started = time.monotonic()
    try:
        resp = fetch(url, timeout=timeout, headers={SELF_HEADER: _self_marker,
                                                     "Accept": "application/json"},
                     allow_redirects=True)
    except Exception as exc:  # noqa: BLE001 - every failure gets words
        return _failed(exc, remote_url)
    ms = int((time.monotonic() - started) * 1000)
    status = getattr(resp, "status_code", 0)
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        body = None
    if isinstance(body, dict) and body.get("soulsync_probe") == nonce:
        return {"ok": True, "code": "ok", "elapsed_ms": ms,
                "words": f"{remote_url} answered as this server in {ms} ms."}
    if status in (401, 403):
        return {"ok": False, "code": "blocked", "words":
                f"{remote_url} answered with {status}: something in front of SoulSync (a proxy's "
                "sign-in, an access rule) turned the check away. Let /api/devices/ through."}
    return {"ok": False, "code": "not_this_server", "words":
            f"Something answered at {remote_url} (status {status}), but it isn't this SoulSync. "
            "Check the address, and that your proxy or tunnel points here."}


def _failed(exc: Exception, remote_url: str) -> Dict[str, Any]:
    name = type(exc).__name__
    text = str(exc)
    if "SSL" in name or "SSL" in text or "certificate" in text.lower():
        return {"ok": False, "code": "certificate", "words":
                f"{remote_url} answered, but its certificate isn't valid for that name. "
                "Apps refuse it too. Fix the certificate on your proxy or tunnel."}
    if "Timeout" in name or "timed out" in text:
        return {"ok": False, "code": "timeout", "words":
                f"Nothing answered at {remote_url} in time. The port may not be forwarded, or a "
                "firewall drops it."}
    if "resolve" in text.lower() or "Name or service not known" in text or "getaddrinfo" in text:
        return {"ok": False, "code": "dns", "words":
                f"The name in {remote_url} doesn't lead anywhere yet. Check its DNS record."}
    return {"ok": False, "code": "unreachable", "words":
            f"Couldn't connect to {remote_url}. The port may be closed, or your router may not "
            "loop its own public address back inside: try the phone link below from mobile data."}


def _reset_for_tests() -> None:
    with _lock:
        _probes.clear()


__all__ = ["PROBE_PATH", "PROBE_LIFETIME", "SELF_HEADER", "GUIDANCE", "start", "latest", "view",
           "record_hit", "self_check"]
