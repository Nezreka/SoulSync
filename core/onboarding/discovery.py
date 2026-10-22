"""find the services a new install will want, before anyone types a url.

the settings page's auto-detect (core.connection_detect) walks the whole
subnet for one service and takes up to a minute. setup asks about seven at
once while the welcome screen is up, so this only tries the places they
actually live: this machine, the docker host, the gateway, and the compose
service name (a stack on one network can reach "prowlarr" by name). every
fingerprint works without credentials.
"""

from __future__ import annotations

import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, Optional

import requests

PROBE_TIMEOUT = 1.5

# service -> (ports, compose names)
SERVICES = {
    "plex": ((32400,), ("plex",)),
    "jellyfin": ((8096,), ("jellyfin",)),
    "navidrome": ((4533,), ("navidrome",)),
    "slskd": ((5030,), ("slskd",)),
    "prowlarr": ((9696,), ("prowlarr",)),
    "qbittorrent": ((8080, 8090), ("qbittorrent", "qbit")),
    "sabnzbd": ((8080, 8085), ("sabnzbd",)),
}


def _text(r) -> str:
    try:
        return (r.text or "")[:20000].lower()
    except Exception:
        return ""


def _fingerprint(service: str, base: str, get) -> bool:
    """true when whatever answers at base is that service"""
    try:
        if service == "plex":
            r = get(f"{base}/identity")
            return r.status_code == 200 and "mediacontainer" in _text(r)
        if service == "jellyfin":
            r = get(f"{base}/System/Info/Public")
            return r.status_code == 200 and "jellyfin" in _text(r)
        if service == "navidrome":
            r = get(f"{base}/rest/ping", params={"f": "json"})
            return "subsonic-response" in _text(r)
        if service == "slskd":
            r = get(f"{base}/api/v0/session")
            if r.status_code not in (200, 401):
                return False
            return "slskd" in _text(get(f"{base}/"))
        if service == "prowlarr":
            return "prowlarr" in _text(get(f"{base}/"))
        if service == "qbittorrent":
            return "qbittorrent" in _text(get(f"{base}/"))
        if service == "sabnzbd":
            r = get(f"{base}/api", params={"mode": "version", "output": "json"})
            return r.status_code == 200 and '"version"' in _text(r)
    except Exception:
        return False
    return False


def _local_ip() -> Optional[str]:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def _resolves(name: str) -> bool:
    try:
        socket.gethostbyname(name)
        return True
    except OSError:
        return False


def candidate_hosts(*, docker: bool, local_ip: Optional[str]) -> list:
    """in the order a match should win. inside docker "localhost" is the
    container itself, so it's left out there"""
    hosts = []
    if docker:
        hosts += ["host.docker.internal", "172.17.0.1"]
    else:
        hosts.append("localhost")
    if local_ip:
        hosts.append(local_ip)
        hosts.append(local_ip.rsplit(".", 1)[0] + ".1")
    out = []
    for h in hosts:
        if h not in out:
            out.append(h)
    return out


def discover(services: Iterable[str], *, docker: bool,
             local_ip: Optional[str] = None,
             resolves: Callable[[str], bool] = _resolves,
             get: Optional[Callable] = None,
             workers: int = 24) -> dict:
    """{service: url} for every service found. a service found on several
    hosts keeps the first in candidate order (compose name, then host)"""
    wanted = [s for s in services if s in SERVICES]
    if get is None:
        session = requests.Session()

        def get(url, **kw):
            return session.get(url, timeout=PROBE_TIMEOUT, allow_redirects=True, **kw)

    hosts = candidate_hosts(docker=docker, local_ip=local_ip if local_ip is not None else _local_ip())
    jobs = []  # (service, rank, base)
    for service in wanted:
        ports, names = SERVICES[service]
        rank = 0
        for name in names:
            if resolves(name):
                for port in ports:
                    jobs.append((service, rank, f"http://{name}:{port}"))
                    rank += 1
        for host in hosts:
            for port in ports:
                jobs.append((service, rank, f"http://{host}:{port}"))
                rank += 1

    found: dict = {}
    if not jobs:
        return found
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(jobs)))) as pool:
        futures = {pool.submit(_fingerprint, svc, base, get): (svc, rank, base) for svc, rank, base in jobs}
        for fut in as_completed(futures):
            svc, rank, base = futures[fut]
            try:
                hit = fut.result()
            except Exception:
                hit = False
            if hit and (svc not in found or rank < found[svc][0]):
                found[svc] = (rank, base)
    return {svc: base for svc, (rank, base) in found.items()}
