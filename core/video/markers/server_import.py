"""markers a media server already found, so a library plex or jellyfin
analysed has skip buttons the same day.

* plex: ``GET /library/metadata/<ratingKey>?includeMarkers=1`` carries
  ``Marker`` entries (``type`` intro / credits / commercial, times in ms)
* jellyfin 10.10+: ``GET /MediaSegments/<itemId>`` (Intro, Outro, Recap,
  Preview, Commercial; ticks of 100 ns). an older Intro Skipper plugin
  answers ``GET /Episode/<itemId>/IntroSkipperSegments`` instead

the parsers are pure; ``fetch`` makes one short call per server and treats
any failure as "nothing there".
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video.markers.server")

TIMEOUT = (4, 10)
_JF_TYPES = {"intro": "intro", "outro": "credits", "credits": "credits", "recap": "recap",
             "preview": "preview", "commercial": "commercial"}


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_plex(data: Any) -> List[Dict[str, Any]]:
    """plex's markers from a metadata answer (json)"""
    try:
        items = (data or {}).get("MediaContainer", {}).get("Metadata") or []
    except AttributeError:
        return []
    out = []
    for item in items[:1]:
        for mk in item.get("Marker") or []:
            kind = str(mk.get("type") or "").lower()
            if kind not in ("intro", "credits", "commercial"):
                continue
            s, e = _f(mk.get("startTimeOffset")), _f(mk.get("endTimeOffset"))
            if s is None or e is None or e <= s:
                continue
            out.append({"kind": kind, "start": round(s / 1000.0, 3), "end": round(e / 1000.0, 3),
                        "source": "plex", "extra": {"final": bool(mk.get("final"))}})
    # plex can mark credits twice (the roll, then a card): the first is the skip
    credits = sorted((m for m in out if m["kind"] == "credits"), key=lambda m: m["start"])
    if len(credits) > 1:
        keep = credits[0]
        out = [m for m in out if m["kind"] != "credits" or m is keep]
    return out


def parse_jellyfin_segments(data: Any) -> List[Dict[str, Any]]:
    """jellyfin's MediaSegments answer"""
    items = (data or {}).get("Items") if isinstance(data, dict) else data
    out = []
    for seg in items or []:
        if not isinstance(seg, dict):
            continue
        kind = _JF_TYPES.get(str(seg.get("Type") or "").lower())
        s, e = _f(seg.get("StartTicks")), _f(seg.get("EndTicks"))
        if not kind or s is None or e is None or e <= s:
            continue
        out.append({"kind": kind, "start": round(s / 1e7, 3), "end": round(e / 1e7, 3),
                    "source": "jellyfin"})
    return out


def parse_intro_skipper(data: Any) -> List[Dict[str, Any]]:
    """the older Intro Skipper plugin's own answer"""
    out = []
    if not isinstance(data, dict):
        return out
    for key, kind in (("Introduction", "intro"), ("Credits", "credits"), ("Recap", "recap"),
                      ("Preview", "preview")):
        seg = data.get(key)
        if not isinstance(seg, dict) or seg.get("Valid") is False:
            continue
        s, e = _f(seg.get("Start", seg.get("IntroStart"))), _f(seg.get("End", seg.get("IntroEnd")))
        if s is None or e is None or e <= s:
            continue
        out.append({"kind": kind, "start": round(s, 3), "end": round(e, 3), "source": "jellyfin"})
    return out


def _get(url: str, headers: Dict[str, str]) -> Optional[Any]:
    import requests
    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT)
    except Exception:  # noqa: BLE001 - a server that doesn't answer has no markers
        logger.debug("marker fetch failed", exc_info=True)
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def fetch_plex(base_url: str, token: str, rating_key: Any) -> Optional[List[Dict[str, Any]]]:
    """plex's markers, or None when plex didn't answer (keep what we had)"""
    base = str(base_url or "").rstrip("/")
    if not base or not token or not str(rating_key or "").strip():
        return None
    data = _get(f"{base}/library/metadata/{rating_key}?includeMarkers=1",
                {"X-Plex-Token": token, "Accept": "application/json"})
    return None if data is None else parse_plex(data)


def fetch_jellyfin(base_url: str, api_key: str, item_id: Any) -> Optional[List[Dict[str, Any]]]:
    """jellyfin's segments, or None when neither endpoint answered"""
    base = str(base_url or "").rstrip("/")
    if not base or not api_key or not str(item_id or "").strip():
        return None
    headers = {"X-Emby-Token": api_key, "Accept": "application/json"}
    data = _get(f"{base}/MediaSegments/{item_id}", headers)
    found = parse_jellyfin_segments(data) if data is not None else []
    if found:
        return found
    old = _get(f"{base}/Episode/{item_id}/IntroSkipperSegments", headers)
    if old is not None:
        return parse_intro_skipper(old)
    return None if data is None else []


def server_handles(db, kind: str, item_id: int) -> List[Dict[str, str]]:
    """every media server item this title is: its own server, and the other
    server when the two are linked (video_server_links)"""
    table = "movies" if kind == "movie" else "episodes"
    out: List[Dict[str, str]] = []
    conn = db._get_connection()
    try:
        r = conn.execute(f"SELECT server_source, server_id FROM {table} WHERE id = ?",  # noqa: S608
                         (int(item_id),)).fetchone()
        if r and r[0] and r[1]:
            out.append({"server": str(r[0]).lower(), "id": str(r[1])})
        try:
            for src, sid in conn.execute(
                    "SELECT server_source, server_id FROM video_server_links WHERE kind = ? AND item_id = ?",
                    (kind, int(item_id))):
                if src and sid and not any(h["server"] == str(src).lower() for h in out):
                    out.append({"server": str(src).lower(), "id": str(sid)})
        except Exception:  # noqa: BLE001 - no links table yet
            pass
    finally:
        conn.close()
    return out


def fetch_for(db, kind: str, item_id: int) -> Dict[str, List[Dict[str, Any]]]:
    """{server: markers} for one movie or episode; a server it isn't on, that
    isn't set up or didn't answer is left out"""
    from core.video.sources import video_jellyfin_config, video_plex_config
    out: Dict[str, List[Dict[str, Any]]] = {}
    for h in server_handles(db, kind, item_id):
        if h["server"] == "plex":
            cfg = video_plex_config(db)
            got = fetch_plex(cfg.get("base_url"), cfg.get("token"), h["id"])
            if got is not None:
                out["plex"] = got
        elif h["server"] in ("jellyfin", "emby"):
            cfg = video_jellyfin_config(db)
            got = fetch_jellyfin(cfg.get("base_url"), cfg.get("api_key"), h["id"])
            if got is not None:
                out["jellyfin"] = got
    return out
