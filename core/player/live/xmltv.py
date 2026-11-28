"""Streaming XMLTV EPG parser (plain .xml and gzip .xml.gz).

Parses <channel> and <programme> elements into UTC-timestamped program dicts
for store.replace_programs: start/stop, title, sub-title, desc, category,
icon, episode-num (xmltv_ns / onscreen), <new/>, and <live/>.
"""

from __future__ import annotations

import gzip
import io
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

_TS_RE = re.compile(
    r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})?\s*([+-]\d{4}|Z)?$"
)


def parse_xmltv_time(raw: str) -> Optional[float]:
    """Parse an XMLTV timestamp like '20261128190000 -0500' to UTC epoch seconds."""
    m = _TS_RE.match(str(raw or "").strip())
    if not m:
        return None
    y, mo, d, h, mi = (int(m.group(i)) for i in range(1, 6))
    s = int(m.group(6) or 0)
    tz_raw = m.group(7) or "+0000"
    if tz_raw == "Z":
        tz = timezone.utc
    else:
        sign = 1 if tz_raw[0] == "+" else -1
        tz = timezone(timedelta(hours=sign * int(tz_raw[1:3]), minutes=sign * int(tz_raw[3:5])))
    try:
        return datetime(y, mo, d, h, mi, s, tzinfo=tz).timestamp()
    except ValueError:
        return None


def _maybe_gunzip(data: bytes) -> bytes:
    if len(data) >= 2 and data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    return data


def _format_episode_num(elem) -> Optional[str]:
    text = (elem.text or "").strip()
    if not text:
        return None
    system = (elem.attrib.get("system") or "").strip().lower()
    if system == "xmltv_ns":
        parts = text.split(".")
        try:
            s = int(parts[0].split("/")[0]) + 1 if parts and parts[0].strip() else None
            e = int(parts[1].split("/")[0]) + 1 if len(parts) > 1 and parts[1].strip() else None
            if s is not None and e is not None:
                return f"S{s:02d}E{e:02d}"
            if e is not None:
                return f"E{e:02d}"
        except ValueError:
            return text
    return text


def parse_xmltv(data: bytes) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse XMLTV bytes (plain or gzipped) -> (channels, programs)."""
    raw = _maybe_gunzip(data or b"")
    channels: List[Dict[str, Any]] = []
    programs: List[Dict[str, Any]] = []
    stream = io.BytesIO(raw)
    for _event, elem in ET.iterparse(stream, events=("end",)):
        tag = elem.tag.split("}")[-1].lower()
        if tag == "channel":
            cid = str(elem.attrib.get("id") or "").strip()
            names = [(n.text or "").strip() for n in elem.findall("display-name") if (n.text or "").strip()]
            icon = elem.find("icon")
            if cid:
                channels.append({
                    "id": cid,
                    "names": names,
                    "icon_url": icon.attrib.get("src") if icon is not None else None,
                })
            elem.clear()
        elif tag == "programme":
            cid = str(elem.attrib.get("channel") or "").strip()
            start_ts = parse_xmltv_time(elem.attrib.get("start") or "")
            end_ts = parse_xmltv_time(elem.attrib.get("stop") or "")
            title_el = elem.find("title")
            title = (title_el.text or "").strip() if title_el is not None else ""
            if cid and start_ts is not None and end_ts is not None and title and end_ts > start_ts:
                sub_el = elem.find("sub-title")
                desc_el = elem.find("desc")
                cat_el = elem.find("category")
                icon_el = elem.find("icon")
                ep_el = elem.find("episode-num")
                programs.append({
                    "epg_channel_id": cid,
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                    "title": title,
                    "subtitle": (sub_el.text or "").strip() if sub_el is not None and sub_el.text else None,
                    "description": (desc_el.text or "").strip() if desc_el is not None and desc_el.text else None,
                    "category": (cat_el.text or "").strip() if cat_el is not None and cat_el.text else None,
                    "icon_url": icon_el.attrib.get("src") if icon_el is not None else None,
                    "episode_num": _format_episode_num(ep_el) if ep_el is not None else None,
                    "is_new": elem.find("new") is not None,
                    "is_live": elem.find("live") is not None,
                })
            elem.clear()
    return channels, programs


def load_xmltv(url_or_path: str, *, timeout: float = 25.0, http_get=None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Load and parse an XMLTV guide from an HTTP(S) URL or local file."""
    src = str(url_or_path or "").strip()
    if not src:
        raise ValueError("XMLTV URL or file path is required")
    if src.startswith(("http://", "https://")):
        getter = http_get or requests.get
        r = getter(src, headers={"Accept-Encoding": "gzip"}, timeout=timeout)
        r.raise_for_status()
        return parse_xmltv(r.content)
    if not os.path.isfile(src):
        raise FileNotFoundError(f"XMLTV file not found: {src}")
    with open(src, "rb") as fh:
        return parse_xmltv(fh.read())
