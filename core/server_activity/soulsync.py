"""soulsync's own player as a server: its live screens (core/player/presence)
-> the shared session shape, plus stop and message.

every screen that plays says so every few seconds (a heartbeat). what's
known is what the screen says (what, where it is, how it plays: the file it
picked and what the converter does to it), the request (ip, browser or
paired device) and the libraries (titles, episode numbers, file codecs).
anything nobody said stays None: a beat that doesn't say how it plays has
no method.

a stop or a message waits in presence and rides back on that screen's next
heartbeat (the web player beats every 8 s while it plays).
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import core.server_activity as sa
from core.server_activity import quality as q
from core.server_activity import reasons as rs
from core.server_activity.jellyfin import is_private_ip

logger = sa.logger

SERVER = "soulsync"
PREFIX = "soulsync:"
_METHOD_LABEL = {"direct_play": "Direct Play", "direct_stream": "Direct Stream", "transcode": "Transcode"}
# the player's item types -> the activity kinds
KINDS = {"movie": "movie", "episode": "episode", "track": "track", "audiobook": "audiobook",
         "podcast_episode": "podcast", "youtube": "clip"}
WATCH_KINDS = {"movie", "episode", "clip", "live"}
# the converter's tallest picture for each quality (core/player/transcode.QUALITIES)
_MAX_HEIGHT = {"original": 1080, "1080p": 1080, "720p": 720, "480p": 480}
_FACTS_TTL = 600.0

_facts_lock = threading.Lock()
_facts_cache: Dict[tuple, tuple] = {}
_who: Dict[str, Any] = {"at": 0.0, "map": {}}


def reset() -> None:
    with _facts_lock:
        _facts_cache.clear()
    _who.update(at=0.0, map={})


# ── what the libraries know ─────────────────────────────────────────────────

def _server_name() -> str:
    try:
        from core.player import setup
        from database.player_database import get_player_database
        return setup.get(get_player_database()).get("server_name") or "SoulSync"
    except Exception:   # noqa: BLE001 - a name is a nicety
        logger.debug("player server name unavailable", exc_info=True)
        return "SoulSync"


def _profiles() -> Dict[int, Dict[str, Any]]:
    """names and pictures, kept a minute"""
    if time.time() - _who["at"] > 60:
        try:
            from database.music_database import get_database
            rows = get_database().get_all_profiles() or []
        except Exception:   # noqa: BLE001
            rows = []
        _who["map"] = {int(r["id"]): {"name": r.get("name") or "Someone", "avatar_url": r.get("avatar_url")}
                       for r in rows if r.get("id")}
        _who["at"] = time.time()
    return _who["map"]


def _kbps(value: Any) -> Optional[int]:
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return int(round(n / 1000 if n > 10000 else n))


def _video_facts(kind: str, item_id: str, file_id: Optional[int]) -> Dict[str, Any]:
    from api.video import get_video_db
    try:
        ident = int(item_id)
    except ValueError:
        return {}
    out: Dict[str, Any] = {}
    db = get_video_db()
    conn = db._get_connection()
    try:
        if kind == "movie":
            r = conn.execute("SELECT title, year, tmdb_id FROM movies WHERE id = ?", (ident,)).fetchone()
            if r:
                out.update(title=r[0], year=r[1])
            column = "movie_id"
        else:
            r = conn.execute(
                "SELECT e.title, e.season_number, e.episode_number, e.show_id, s.title "
                "FROM episodes e JOIN shows s ON s.id = e.show_id WHERE e.id = ?", (ident,)).fetchone()
            if r:
                out.update(title=r[0], season=r[1], episode=r[2], show_id=r[3], show=r[4])
            column = "episode_id"
        # the file playing: the one the screen named, or the only one there is
        sql = (f"SELECT id, relative_path, size_bytes, video_codec, audio_codec, runtime_seconds, "  # noqa: S608
               f"resolution, dynamic_range FROM media_files WHERE {column} = ?")
        rows = conn.execute(sql + (" AND id = ?" if file_id else " LIMIT 2"),
                            (ident, file_id) if file_id else (ident,)).fetchall()
    finally:
        conn.close()
    if len(rows) == 1:
        f = rows[0]
        from core.player.sources.video import _height
        size, runtime = f[2], f[5]
        out["file"] = {"container": os.path.splitext(str(f[1] or ""))[1].lstrip("."),
                       "video_codec": f[3], "audio_codec": f[4], "height": _height(f[6]),
                       "dynamic_range": f[7],
                       "bitrate_kbps": int(size * 8 / runtime / 1000) if size and runtime else None}
    return out


def _track_facts(item_id: str) -> Dict[str, Any]:
    from core.server_activity import store
    try:
        ident = int(item_id)
    except ValueError:
        return {}
    rows = store.query(
        "SELECT t.title AS title, t.track_number AS track, t.disc_number AS disc, t.album_id AS album_id, "
        "al.title AS album, al.primary_artist_id AS artist_id, ar.name AS artist "
        "FROM lib2_tracks t LEFT JOIN lib2_albums al ON al.id = t.album_id "
        "LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id WHERE t.id = ?", [ident])
    if not rows:
        return {}
    out = dict(rows[0])
    # the file the player serves first: imported, lossless first, then bitrate
    files = store.query(
        "SELECT format, bitrate, sample_rate, bit_depth FROM lib2_track_files "
        "WHERE track_id = ? AND COALESCE(import_status, 'imported') = 'imported' "
        "ORDER BY CASE WHEN quality_tier = 'lossless' THEN 0 ELSE 1 END, COALESCE(bitrate, 0) DESC LIMIT 1",
        [ident])
    if files:
        out["file"] = dict(files[0])
    return out


def _facts(item_type: str, item_id: str, file_id: Optional[int], now: float) -> Dict[str, Any]:
    """what the libraries say about the item, cached 10 minutes. {} when
    they can't say."""
    if item_type not in ("movie", "episode", "track"):
        return {}
    key = (item_type, item_id, file_id)
    with _facts_lock:
        hit = _facts_cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    try:
        out = _track_facts(item_id) if item_type == "track" else _video_facts(item_type, item_id, file_id)
    except Exception:   # noqa: BLE001 - no library, no facts
        logger.debug("soulsync item facts failed", exc_info=True)
        out = {}
    with _facts_lock:
        _facts_cache[key] = (now + _FACTS_TTL, out)
        while len(_facts_cache) > 1000:
            _facts_cache.pop(next(iter(_facts_cache)), None)
    return out


# ── how it plays ────────────────────────────────────────────────────────────

def _playback(kind: str, stream: Dict[str, Any], facts: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]],
                                                                                 int]:
    """(playback, reason, bandwidth). the method is what the screen said:
    the converter copying both streams is a remux (direct stream), changing
    either is a transcode, no converter is direct play. music, books,
    podcasts and youtube are never converted. a video whose screen didn't
    say has no method."""
    f = facts.get("file") or {}
    t = stream.get("transcode") if stream else None
    is_video = kind in ("movie", "episode")
    if not is_video:
        method: Optional[str] = "direct_play"
    elif "transcode" not in (stream or {}):
        method = None
    elif t is None:
        method = "direct_play"
    else:
        method = "direct_stream" if (t["video"] == "copy" and t["audio"] == "copy") else "transcode"

    v_src = v_out = a_src = a_out = None
    src_container = out_container = None
    src_kbps = out_kbps = None
    codes: List[str] = []
    if is_video and f:
        v_src = q.video_side(codec=f.get("video_codec"), height=f.get("height"),
                             hdr=str(f.get("dynamic_range") or "") if str(f.get("dynamic_range") or "").upper()
                             not in ("", "SDR") else "",
                             bitrate_kbps=f.get("bitrate_kbps"))
        a_src = q.audio_side(codec=f.get("audio_codec")) if f.get("audio_codec") else None
        src_container = q.container_label(f.get("container")) or None
        src_kbps = f.get("bitrate_kbps")
    elif kind == "track" and f:
        a_src = q.audio_side(codec=f.get("format"), bit_depth=f.get("bit_depth"),
                             sample_rate=f.get("sample_rate"), bitrate_kbps=_kbps(f.get("bitrate")))
        src_container = q.container_label(f.get("format")) or None
        src_kbps = _kbps(f.get("bitrate"))
    v_out, a_out, out_container = v_src, a_src, src_container
    vd = ad = "direct"
    if t is not None and method in ("direct_stream", "transcode"):
        out_container = "MP4"
        vd = "copy" if t["video"] == "copy" else "transcode"
        ad = "copy" if t["audio"] == "copy" else "transcode"
        if vd == "transcode":
            cap = _MAX_HEIGHT.get(t.get("quality") or "original", 1080)
            src_h = (v_src or {}).get("height")
            v_out = q.video_side(codec="h264", height=min(src_h, cap) if src_h else None)
            burn = any("subtitles drawn in" in r for r in t.get("reasons") or [])
            if burn:
                codes.append("subtitle_burn")
            if (t.get("quality") or "original") != "original":
                codes.append("over_quality_limit")
            if not burn and (t.get("quality") or "original") == "original":
                codes.append("video_codec")
        if ad == "transcode":
            a_out = q.audio_side(codec="aac", channels=2)
            codes.append("audio_codec")
    else:
        out_kbps = src_kbps
    if method == "direct_play":
        vd = ad = "direct"
    reason = rs.summary(codes, method=method, inferred=False, src_kbps=src_kbps, out_kbps=out_kbps) \
        if method in ("direct_stream", "transcode") else None
    if method in ("direct_stream", "transcode") and reason is None and t and t.get("reasons"):
        verb = "Converting" if method == "transcode" else "Repackaging"
        reason = {"codes": [], "items": [], "inferred": False,
                  "text": f"{verb}: " + ", ".join(t["reasons"]) + "."}
    playback = {
        "method": method, "method_label": _METHOD_LABEL.get(method or ""),
        "video": {"source": v_src, "stream": v_out, "decision": vd, "changed": vd == "transcode"}
        if (v_src or v_out) else None,
        "audio": {"source": a_src, "stream": a_out, "decision": ad, "changed": ad == "transcode"}
        if (a_src or a_out) else None,
        "subtitles": None,
        "container": {"source": src_container, "stream": out_container,
                      "changed": bool(src_container and out_container and src_container != out_container)},
        "bitrate_kbps": {"source": src_kbps, "stream": out_kbps},
    }
    return playback, reason, int(out_kbps or 0)


def _transcoder(stream: Dict[str, Any], method: Optional[str]) -> Optional[Dict[str, Any]]:
    t = (stream or {}).get("transcode")
    if not t or method not in ("direct_stream", "transcode"):
        return None
    enc = t.get("encoder")
    hw = bool(enc and enc != "CPU")
    vd = "copy" if t["video"] == "copy" else "transcode"
    return {"hw": hw, "hw_decode": None, "hw_encode": enc if hw else None, "hw_full_pipeline": False,
            "speed": None, "buffered_s": None, "throttled": None, "progress_pct": None,
            "video": vd, "audio": "copy" if t["audio"] == "copy" else "transcode"}


# ── one screen ──────────────────────────────────────────────────────────────

def _link(item_type: str, item_id: str, facts: Dict[str, Any], meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if item_type == "movie" and item_id.isdigit():
        return {"kind": "movie", "id": int(item_id), "source": "library"}
    if item_type == "episode":
        show = facts.get("show_id") or meta.get("parent_id")
        if show and str(show).isdigit():
            return {"kind": "show", "id": int(show), "source": "library",
                    "season": facts.get("season"), "episode": facts.get("episode")}
    if item_type == "track" and facts.get("artist_id") and facts.get("album_id"):
        return {"kind": "music", "id": facts["artist_id"], "artist_id": facts["artist_id"],
                "album_id": facts["album_id"], "track_id": int(item_id), "source": "library"}
    if item_type == "audiobook" and item_id:
        return {"kind": "audiobook", "id": item_id, "source": "library"}
    return None


def normalize(p: Any, *, server_name: str = "SoulSync", profile: Optional[Dict[str, Any]] = None,
              facts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """one presence session -> the shared session dict. pure."""
    facts = facts or {}
    meta = dict(p.meta or {})
    kind = KINDS.get(p.item_type, "other")
    stream = dict(p.stream or {})
    client = dict(p.client or {})
    title = str(facts.get("title") or meta.get("title") or "")
    year = facts.get("year")
    show = music = book = None
    if kind == "episode":
        series = str(facts.get("show") or meta.get("parent_title") or "")
        sn, en = facts.get("season"), facts.get("episode")
        code = f"S{sn} E{en}" if sn is not None and en is not None else ""
        subtitle = " · ".join(x for x in (series, code) if x) or str(meta.get("subtitle") or "")
        show = {"title": series, "season": sn, "episode": en, "season_title": None}
    elif kind == "track":
        artist = str(facts.get("artist") or meta.get("subtitle") or "")
        album = str(facts.get("album") or meta.get("parent_title") or "")
        subtitle = " · ".join(x for x in (artist, album) if x)
        music = {"artist": artist, "album_artist": artist, "album": album,
                 "track": facts.get("track"), "disc": facts.get("disc")}
    elif kind in ("audiobook", "podcast"):
        author = str(meta.get("subtitle") or "") if kind == "audiobook" else str(meta.get("parent_title") or "")
        subtitle = str(meta.get("subtitle") or "")
        book = {"author": author or None, "narrator": None,
                "title": title if kind == "audiobook" else str(meta.get("parent_title") or ""), "progress": None}
    else:
        subtitle = str(meta.get("subtitle") or (year or ""))
    channel = p.channel if (p.live and p.channel) else None
    if channel:
        where = " ".join(x for x in (str(channel.get("number") or ""), channel.get("name") or "") if x)
        subtitle = " · ".join(x for x in (subtitle, f"Live on channel {where}".strip()) if x)

    duration_ms = int((p.duration or 0) * 1000)
    offset_ms = int((p.position or 0) * 1000)
    playback, reason, bw = _playback(kind, stream, facts)
    ip = p.ip or None
    pid = int(p.profile_id)
    who = profile or {}
    avatar = who.get("avatar_url")
    key = f"{PREFIX}{pid}:{p.session}"
    thumb = str(meta.get("artwork") or "")
    rating_key = str(p.item_id)
    parent_key = str(facts.get("album_id") or meta.get("parent_id") or "") or None
    grandparent_key = str(facts.get("show_id") or facts.get("artist_id") or "") or None
    if kind == "episode":
        parent_key, grandparent_key = None, str(facts.get("show_id") or meta.get("parent_id") or "") or None
    return {
        "id": key,
        "server": SERVER,
        "server_name": server_name,
        "session_key": key,
        "kind": kind,
        "activity": "watching" if kind in WATCH_KINDS else "listening",
        "state": "playing" if p.playing else "paused",
        "title": title, "subtitle": subtitle, "year": year,
        "show": show, "music": music, "book": book,
        "channel": channel,
        "thumb": thumb, "art": str(meta.get("backdrop") or thumb),
        "duration_ms": duration_ms, "offset_ms": offset_ms,
        "progress_pct": max(0, min(100, round(100 * offset_ms / duration_ms))) if duration_ms else 0,
        "user": str(who.get("name") or "Someone"),
        "user_id": str(pid),
        # an uploaded picture is ours (/api/profiles/<id>/avatar); anything else isn't fetched
        "user_avatar": avatar if isinstance(avatar, str) and avatar.startswith("/api/") else None,
        "player": {"product": client.get("product") or "SoulSync player", "version": client.get("version"),
                   "platform": client.get("platform"), "platform_version": None,
                   "device": client.get("device"), "model": None, "name": client.get("name")},
        "connection": {"ip": ip, "public_ip": None, "remote": bool(ip) and not is_private_ip(ip),
                       "secure": None, "relayed": None, "location": None},
        "bandwidth_kbps": bw,
        "playback": playback,
        "reason": reason,
        "transcoder": _transcoder(stream, playback["method"]),
        "controls": {"stop": True, "message": True, "stop_note": None},
        "keys": {"rating_key": rating_key, "parent_key": parent_key, "grandparent_key": grandparent_key},
        "link": _link(p.item_type, str(p.item_id), facts, meta),
    }


def sessions(now: Optional[float] = None) -> tuple:
    """(sessions, server info). the player is always there: info is never None."""
    now = time.time() if now is None else now
    name = _server_name()
    info = {"server": SERVER, "name": name, "version": None, "reachable": True, "plex_pass": None}
    try:
        from core.player import presence
        rows = presence.everyone(now=now)
    except Exception:   # noqa: BLE001
        logger.debug("player presence unavailable", exc_info=True)
        info["reachable"] = False
        return [], info
    if not rows:
        return [], info
    who = _profiles()
    out: List[Dict[str, Any]] = []
    for p in rows:
        try:
            facts = _facts(p.item_type, str(p.item_id), (p.stream or {}).get("file_id"), now)
            out.append(normalize(p, server_name=name, profile=who.get(int(p.profile_id)), facts=facts))
        except Exception:   # noqa: BLE001 - one odd screen never blanks the view
            logger.debug("soulsync session normalize failed", exc_info=True)
    return out, info


def is_active(now: Optional[float] = None) -> bool:
    """anyone playing on the player right now (cheap, memory only)"""
    try:
        from core.player import presence
        return bool(presence.everyone(now=now))
    except Exception:   # noqa: BLE001
        return False


# ── controls ────────────────────────────────────────────────────────────────

def _split(key: str) -> Optional[Tuple[int, str]]:
    rest = key[len(PREFIX):] if key.startswith(PREFIX) else ""
    pid, _, session = rest.partition(":")
    if not pid.isdigit() or not session:
        return None
    return int(pid), session


def stop(key: str, text: str) -> Dict[str, Any]:
    """the screen is told on its next beat (it shows the message and stops),
    its conversion ends now, and its item isn't served to that profile for
    a little while."""
    from core.player import presence
    got = _split(key)
    if not got:
        return {"ok": False, "error": "that stream is no longer active"}
    s = presence.order_stop(got[0], got[1], text)
    if s is None:
        return {"ok": False, "error": "that stream is no longer active"}
    try:
        from core.player import transcode
        # the web player's converter owner is its profile and its screen id
        transcode.stop_owner(f"{got[0]}:{got[1]}")
    except Exception:   # noqa: BLE001 - the beat still stops it
        logger.debug("couldn't end the stopped screen's conversion", exc_info=True)
    return {"ok": True, "message_shown": None}


def message(key: str, text: str, header: str = "") -> Dict[str, Any]:
    from core.player import presence
    got = _split(key)
    if not got:
        return {"ok": False, "error": "that stream is no longer active"}
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "write a message first"}
    if not presence.order_message(got[0], got[1], text, header=str(header or "").strip()):
        return {"ok": False, "error": "that stream is no longer active"}
    return {"ok": True}
