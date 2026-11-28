"""Standard M3U / M3U8 playlist parser for user-supplied live channel lists.

Parses #EXTINF attributes (tvg-id, tvg-name, tvg-logo, tvg-chno, group-title)
and optional #EXTVLCOPT:http-user-agent / http-referrer headers from the user's
own playlist. Supports http(s) URLs and local files.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List

import requests

_ATTR_RE = re.compile(r'([a-zA-Z0-9_-]+)="([^"]*)"')


def parse_m3u(text: str) -> List[Dict[str, Any]]:
    """Parse an #EXTM3U string into channel dicts for store.upsert_channels."""
    channels: List[Dict[str, Any]] = []
    meta: Dict[str, str] = {}
    title = ""
    headers: Dict[str, str] = {}

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            meta = {m.group(1).lower(): m.group(2).strip() for m in _ATTR_RE.finditer(line)}
            comma = line.rfind(",")
            title = line[comma + 1:].strip() if comma != -1 else ""
            headers = {}
            continue
        if line.startswith("#EXTVLCOPT:"):
            opt = line[len("#EXTVLCOPT:"):].strip()
            low = opt.lower()
            if low.startswith("http-user-agent="):
                headers["User-Agent"] = opt.split("=", 1)[1].strip()
            elif low.startswith("http-referrer="):
                headers["Referer"] = opt.split("=", 1)[1].strip()
            continue
        if line.startswith("#"):
            continue

        # Stream URL line
        if not line.startswith(("http://", "https://", "rtsp://", "rtmp://", "udp://")):
            meta = {}
            title = ""
            headers = {}
            continue
        name = title or meta.get("tvg-name") or f"Channel {len(channels) + 1}"
        num = meta.get("tvg-chno") or meta.get("channel-number") or ""
        channels.append({
            "guide_number": num,
            "name": name,
            "stream_url": line,
            "logo_url": meta.get("tvg-logo") or None,
            "group_title": meta.get("group-title") or None,
            "tvg_id": meta.get("tvg-id") or None,
            "headers": dict(headers),
            "is_drm": False,
        })
        meta = {}
        title = ""
        headers = {}
    return channels


def load_m3u(url_or_path: str, *, timeout: float = 15.0, http_get=None) -> List[Dict[str, Any]]:
    """Load and parse an M3U playlist from an HTTP(S) URL or local file path."""
    src = str(url_or_path or "").strip()
    if not src:
        raise ValueError("M3U URL or file path is required")
    if src.startswith(("http://", "https://")):
        getter = http_get or requests.get
        r = getter(src, timeout=timeout)
        r.raise_for_status()
        return parse_m3u(r.text)
    if not os.path.isfile(src):
        raise FileNotFoundError(f"M3U file not found: {src}")
    with open(src, "r", encoding="utf-8", errors="replace") as fh:
        return parse_m3u(fh.read())
