"""city and region for remote viewers.

plex.tv's geoip lookup (what tautulli uses), asked with the server's own token.
only public addresses are ever sent; home addresses never leave. each address
is asked once a month (a miss once a day), cached in memory and in the db, and
looked up off the poll thread so a slow plex.tv never slows the live view.
jellyfin viewers use the same lookup when a plex token exists, else they show
their address only.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any, Dict, Optional
from xml.etree import ElementTree as ET

import core.server_activity as sa
from core.server_activity import store
from core.server_activity.jellyfin import is_private_ip

logger = sa.logger

GEO_URL = "https://plex.tv/api/v2/geoip"
OK_TTL = 30 * 86400
MISS_TTL = 86400

_US = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}

_mem: Dict[str, tuple] = {}
_mem_lock = threading.Lock()
_queue: "queue.Queue[str]" = queue.Queue()
_pending: set = set()
_worker: Optional[threading.Thread] = None


def reset() -> None:
    with _mem_lock:
        _mem.clear()
        _pending.clear()


def _shape(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    city = str(raw.get("city") or "").strip()
    region = str(raw.get("subdivisions") or raw.get("region") or "").strip()
    country = str(raw.get("country") or "").strip()
    code = str(raw.get("code") or raw.get("country_code") or "").strip().upper()
    if not (city or region or country):
        return None
    lat = lon = None
    coords = str(raw.get("coordinates") or "")
    if "," in coords:
        try:
            lat, lon = (float(x) for x in coords.split(",")[:2])
        except ValueError:
            lat = lon = None
    short_code = "UK" if code == "GB" else code
    if code == "US" and region.lower() in _US:
        short = ", ".join(x for x in (city, _US[region.lower()]) if x)
    else:
        short = ", ".join(x for x in (city or region, short_code or country) if x)
    return {"city": city or None, "region": region or None, "country": country or None,
            "code": code or None, "timezone": raw.get("time_zone") or raw.get("timezone") or None,
            "lat": lat, "lon": lon,
            "label": ", ".join(x for x in (city, region, short_code or country) if x),
            "short": short}


def parse(body: str) -> Optional[Dict[str, Any]]:
    """plex.tv answers xml (<location city=.. subdivisions=.. country=.. code=..>)
    or json when asked for it. pure."""
    body = (body or "").strip()
    if not body:
        return None
    if body.startswith("{"):
        try:
            data = json.loads(body)
        except ValueError:
            return None
        return _shape(data.get("location") if isinstance(data.get("location"), dict) else data)
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    el = root if root.tag == "location" else root.find("location")
    return _shape(dict(el.attrib)) if el is not None else None


def _client_id() -> str:
    try:
        from core.settings import config_manager
        return str(config_manager.get("plex_signin.client_id", "") or "") or "soulsync-server-activity"
    except Exception:   # noqa: BLE001
        return "soulsync-server-activity"


def _http_get(ip: str, token: str) -> Optional[str]:
    import requests
    r = requests.get(GEO_URL, params={"ip_address": ip}, timeout=8, headers={
        "X-Plex-Token": token, "X-Plex-Product": "SoulSync", "X-Plex-Client-Identifier": _client_id(),
        "Accept": "application/json"})
    if r.status_code != 200:
        return None
    return r.text


def fetch_now(ip: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """ask plex.tv once and cache the answer (or the miss). the worker calls this."""
    now = time.time() if now is None else now
    token = sa._plex_config().get("token") or ""
    if not token or not ip or is_private_ip(ip):
        return None
    data = None
    try:
        data = parse(_http_get(ip, token) or "")
    except Exception:   # noqa: BLE001 - a failed lookup is cached as a miss for a day
        logger.debug("geoip lookup failed", exc_info=True)
    try:
        store.geo_put(ip, data, bool(data), now)
    except Exception:   # noqa: BLE001
        logger.debug("geo cache write failed", exc_info=True)
    with _mem_lock:
        _mem[ip] = (now + (OK_TTL if data else MISS_TTL), data)
    return data


def _run_worker() -> None:
    while True:
        ip = _queue.get()
        try:
            fetch_now(ip)
        except Exception:   # noqa: BLE001
            logger.debug("geo worker failed", exc_info=True)
        finally:
            with _mem_lock:
                _pending.discard(ip)
        time.sleep(1.0)     # gentle on plex.tv


def _schedule(ip: str) -> None:
    global _worker
    with _mem_lock:
        if ip in _pending:
            return
        _pending.add(ip)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run_worker, name="server-activity-geo", daemon=True)
            _worker.start()
    _queue.put(ip)


def enabled() -> bool:
    from core.server_activity.settings import get_settings
    return bool(get_settings().get("geo_lookup", True))


def lookup(ip: Optional[str], now: Optional[float] = None, schedule: bool = True) -> Optional[Dict[str, Any]]:
    """the cached place for ``ip``, or None. a miss queues a lookup for later.
    with the setting off nothing is looked up or shown."""
    if not ip or is_private_ip(ip) or not enabled():
        return None
    now = time.time() if now is None else now
    with _mem_lock:
        hit = _mem.get(ip)
    if hit and hit[0] > now:
        return hit[1]
    stale = hit[1] if hit else None
    if not hit:
        try:
            row = store.geo_get(ip)
        except Exception:   # noqa: BLE001
            row = None
            logger.debug("geo cache read failed", exc_info=True)
        if row:
            ttl = OK_TTL if row["ok"] else MISS_TTL
            with _mem_lock:
                _mem[ip] = (row["fetched_at"] + ttl, row["data"])
            if row["fetched_at"] + ttl > now:
                return row["data"]
            stale = row["data"]
    if schedule and sa._plex_config().get("token"):
        _schedule(ip)
    return stale
