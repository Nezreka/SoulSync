"""HDHomeRun HTTP API + LAN discovery (SiliconDust public spec).

- UDP broadcast discovery on port 65001 + optional manual IP probe (`discover.json`)
- Lineup fetch (`lineup.json`) — flags DRM-protected channels so they stay hidden
- Free 2-3 day OTA XMLTV guide via `api.hdhomerun.com/api/xmltv?DeviceAuth=...`
- Friendly error translation for 503 + X-HDHomeRun-Error (805 all tuners busy,
  807 no video data) so raw error codes never leak to the UI.
"""

from __future__ import annotations

import socket
import struct
from typing import Any, Dict, List, Optional

import requests

from utils.logging_config import get_logger

logger = get_logger("player.live.hdhomerun")

_UDP_PORT = 65001
# Standard HDHomeRun discover request packet (type=0x0002, wildcard device type & id)
_DISCOVER_PAYLOAD = struct.pack(">HHBBI", 0x0002, 12, 0x01, 4, 0xFFFFFFFF) + struct.pack(">BBI", 0x02, 4, 0xFFFFFFFF)


def _crc32_hdhr(data: bytes) -> int:
    """IEEE 802.3 CRC32 (little-endian) used by HDHomeRun UDP discovery."""
    import zlib
    return zlib.crc32(data) & 0xFFFFFFFF


def _build_discover_packet() -> bytes:
    return _DISCOVER_PAYLOAD + struct.pack("<I", _crc32_hdhr(_DISCOVER_PAYLOAD))


def normalize_base_url(host_or_url: str) -> str:
    raw = str(host_or_url or "").strip().rstrip("/")
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "http://" + raw
    return raw


def probe_device(host_or_url: str, *, timeout: float = 5.0, http_get=None) -> Dict[str, Any]:
    """Read /discover.json from a single HDHomeRun tuner."""
    base = normalize_base_url(host_or_url)
    if not base:
        raise ValueError("host or URL is required")
    getter = http_get or requests.get
    r = getter(f"{base}/discover.json", timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return {
        "friendly_name": data.get("FriendlyName") or "HDHomeRun",
        "model_number": data.get("ModelNumber") or "",
        "firmware_version": data.get("FirmwareVersion") or "",
        "device_id": str(data.get("DeviceID") or ""),
        "device_auth": str(data.get("DeviceAuth") or ""),
        "base_url": str(data.get("BaseURL") or base).rstrip("/"),
        "lineup_url": str(data.get("LineupURL") or f"{base}/lineup.json"),
        "tuner_count": int(data.get("TunerCount") or 2),
    }


def discover_devices(*, timeout: float = 1.5, http_get=None) -> List[Dict[str, Any]]:
    """Broadcast on LAN UDP 65001 to find HDHomeRun tuners, then probe each one."""
    ips: List[str] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(timeout)
        pkt = _build_discover_packet()
        try:
            sock.sendto(pkt, ("255.255.255.255", _UDP_PORT))
        except OSError:
            return []
        while True:
            try:
                _data, addr = sock.recvfrom(2048)
                if addr and addr[0] and addr[0] not in ips:
                    ips.append(addr[0])
            except socket.timeout:
                break
            except OSError:
                break
    finally:
        sock.close()

    devices: List[Dict[str, Any]] = []
    for ip in ips:
        try:
            devices.append(probe_device(ip, timeout=3.0, http_get=http_get))
        except Exception as exc:  # noqa: BLE001
            logger.debug("hdhomerun probe failed for %s: %s", ip, exc)
    return devices


def fetch_lineup(base_or_lineup_url: str, *, timeout: float = 8.0, http_get=None) -> List[Dict[str, Any]]:
    """Fetch lineup.json from an HDHomeRun and map to SoulSync channel dicts.
    DRM-protected channels are marked is_drm=True so they stay hidden."""
    url = normalize_base_url(base_or_lineup_url)
    if not url.endswith(".json"):
        url = f"{url}/lineup.json"
    getter = http_get or requests.get
    r = getter(url, timeout=timeout)
    r.raise_for_status()
    items = r.json() or []
    out: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        num = str(it.get("GuideNumber") or "").strip()
        name = str(it.get("GuideName") or "").strip()
        stream_url = str(it.get("URL") or "").strip()
        if not stream_url:
            continue
        tags = str(it.get("Tags") or "").lower()
        is_drm = bool(it.get("DRM")) or ("drm" in tags.split(","))
        out.append({
            "guide_number": num,
            "name": name or num,
            "stream_url": stream_url,
            "tvg_id": num,
            "group_title": "Over-the-Air",
            "is_drm": is_drm,
        })
    return out


def fetch_xmltv(device_auth: str, *, timeout: float = 20.0, http_get=None) -> Optional[bytes]:
    """Fetch SiliconDust's free 2-3 day OTA XMLTV guide for the owner's tuner."""
    auth = str(device_auth or "").strip()
    if not auth:
        return None
    getter = http_get or requests.get
    r = getter(
        "https://api.hdhomerun.com/api/xmltv",
        params={"DeviceAuth": auth},
        headers={"Accept-Encoding": "gzip"},
        timeout=timeout,
    )
    if r.status_code != 200:
        logger.debug("hdhomerun xmltv returned %s", r.status_code)
        return None
    return r.content


def friendly_tuner_error(status_code: int, headers: Optional[Dict[str, str]] = None) -> str:
    """Human-readable message for HDHomeRun stream errors (never leak raw 805/807)."""
    hdr = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    code = hdr.get("x-hdhomerun-error", "")
    if code.startswith("805") or status_code == 503:
        return "All tuners on this HDHomeRun are currently in use."
    if code.startswith("807"):
        return "No broadcast signal on this channel right now."
    if status_code == 404:
        return "That channel is no longer in the tuner's lineup."
    return f"The tuner couldn't start this channel (HTTP {status_code})."
