"""jellyfin: /Sessions json -> the shared session shape, plus stop and message.

endpoints checked against jellyfin's openapi (12.4):
  GET  /Sessions
  POST /Sessions/{sessionId}/Message           body {Header, Text, TimeoutMs}
  POST /Sessions/{sessionId}/Playing/{command} command Stop
  GET  /UserImage?userId=                       (/Users/{id}/Images/Primary before 10.9)
ticks are 100 ns. jellyfin states its own transcode reasons.
"""

from __future__ import annotations

import ipaddress
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

import core.server_activity as sa
from core.server_activity import quality as q
from core.server_activity import reasons as rs

logger = sa.logger

SESSION_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_METHOD_LABEL = {"direct_play": "Direct Play", "direct_stream": "Direct Stream", "transcode": "Transcode"}
_KINDS = {"movie": "movie", "episode": "episode", "audio": "track", "audiobook": "audiobook",
          "musicvideo": "clip", "video": "clip", "trailer": "clip", "tvchannel": "live",
          "livetvchannel": "live", "livetvprogram": "live", "program": "live", "recording": "live"}
_HW = {"qsv": "Intel Quick Sync", "nvenc": "NVIDIA NVENC", "vaapi": "VA-API", "amf": "AMD AMF",
       "videotoolbox": "Apple VideoToolbox", "v4l2m2m": "V4L2", "rkmpp": "Rockchip MPP"}
WATCH_KINDS = {"movie", "episode", "clip", "live"}


def _ms(ticks: Any) -> int:
    try:
        return int(ticks or 0) // 10000
    except (TypeError, ValueError):
        return 0


def _kb(bps: Any) -> Optional[int]:
    try:
        v = int(bps or 0) // 1000
        return v or None
    except (TypeError, ValueError):
        return None


def is_private_ip(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(str(ip).strip().split("%")[0])
    except ValueError:
        return False
    return a.is_private or a.is_loopback or a.is_link_local


def _clean_ip(raw: Any) -> str:
    s = str(raw or "").strip()
    if s.startswith("[") and "]" in s:
        return s[1:s.index("]")]
    if s.count(":") == 1:          # ipv4:port
        return s.split(":")[0]
    return s


def _pick_streams(npi: Dict[str, Any], ps: Dict[str, Any]) -> Dict[str, Optional[Dict[str, Any]]]:
    streams = npi.get("MediaStreams") or []
    video = next((s for s in streams if s.get("Type") == "Video"), None)
    audios = [s for s in streams if s.get("Type") == "Audio"]
    ai = ps.get("AudioStreamIndex")
    audio = next((s for s in audios if ai is not None and s.get("Index") == ai), None) or \
        next((s for s in audios if s.get("IsDefault")), None) or (audios[0] if audios else None)
    si = ps.get("SubtitleStreamIndex")
    sub = None
    if si is not None and int(si) >= 0:
        sub = next((s for s in streams if s.get("Type") == "Subtitle" and s.get("Index") == si), None)
    return {"video": video, "audio": audio, "subtitle": sub}


def _vside(s: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not s:
        return None
    return q.video_side(codec=s.get("Codec"), profile=s.get("Profile"), width=s.get("Width"),
                        height=s.get("Height"), bit_depth=s.get("BitDepth"),
                        hdr=q.hdr_label(s.get("ColorTransfer"), range_type=s.get("VideoRangeType")),
                        bitrate_kbps=_kb(s.get("BitRate")), frame_rate=s.get("RealFrameRate"))


def _aside(s: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not s:
        return None
    atmos = "atmos" in " ".join(str(s.get(k) or "") for k in
                                ("AudioSpatialFormat", "Profile", "DisplayTitle", "Title")).lower()
    return q.audio_side(codec=s.get("Codec"), profile=s.get("Profile"), channels=s.get("Channels"),
                        layout=s.get("ChannelLayout"), language=s.get("Language"),
                        bit_depth=s.get("BitDepth"), sample_rate=s.get("SampleRate"),
                        bitrate_kbps=_kb(s.get("BitRate")), atmos=atmos)


def chapters_of(npi: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for c in npi.get("Chapters") or []:
        out.append({"start_ms": _ms(c.get("StartPositionTicks")), "end_ms": 0, "title": c.get("Name") or ""})
    return out


def facts(s: Dict[str, Any]) -> Dict[str, str]:
    npi = s.get("NowPlayingItem") or {}
    return {"rating_key": str(npi.get("Id") or ""), "parent_key": str(npi.get("ParentId") or npi.get("AlbumId") or ""),
            "grandparent_key": str(npi.get("SeriesId") or ""), "type": str(npi.get("Type") or "").lower(),
            "section": "", "file": str(npi.get("Path") or "")}


def normalize(s: Dict[str, Any], *, server_name: str = "Jellyfin", audio_kind: Optional[str] = None,
              book: Optional[Dict[str, Any]] = None, book_meta: Optional[Dict[str, Any]] = None
              ) -> Dict[str, Any]:
    """one /Sessions entry with a NowPlayingItem -> the shared session dict. pure."""
    npi = s.get("NowPlayingItem") or {}
    ps = s.get("PlayState") or {}
    ti = s.get("TranscodingInfo") or None
    ptype = str(npi.get("Type") or "").lower()
    kind = _KINDS.get(ptype, "other")
    if kind == "track" and audio_kind in ("audiobook", "podcast"):
        kind = audio_kind
    st = _pick_streams(npi, ps)
    ip = _clean_ip(s.get("RemoteEndPoint"))
    remote = bool(ip) and not is_private_ip(ip)

    pm = str(ps.get("PlayMethod") or "").lower().replace(" ", "")
    has_video = kind in WATCH_KINDS and st["video"] is not None
    has_audio = st["audio"] is not None
    if pm == "transcode" and ti:
        v_direct = bool(ti.get("IsVideoDirect")) or not has_video
        a_direct = bool(ti.get("IsAudioDirect")) or not has_audio
        # a transcode that copies both streams is a remux: what plex calls direct stream
        method = "direct_stream" if (v_direct and a_direct) else "transcode"
    elif pm == "transcode":
        v_direct = a_direct = False
        method = "transcode"
    elif pm == "directstream":
        v_direct = a_direct = True
        method = "direct_stream"
    else:
        v_direct = a_direct = True
        method = "direct_play"
    vd = "direct" if method == "direct_play" else ("copy" if v_direct else "transcode")
    ad = "direct" if method == "direct_play" else ("copy" if a_direct else "transcode")
    if not has_video:
        vd = ""
    codes = rs.from_jellyfin((ti or {}).get("TranscodeReasons"), remote=remote) if method != "direct_play" else []

    v_src = _vside(st["video"]) if has_video else None
    a_src = _aside(st["audio"])
    if has_video and vd == "transcode":
        v_out = q.video_side(codec=ti.get("VideoCodec"), width=ti.get("Width"), height=ti.get("Height"),
                             hdr="" if ("hdr" in codes or not v_src) else (v_src.get("hdr") or ""),
                             bitrate_kbps=_kb(ti.get("Bitrate")))
    else:
        v_out = v_src
    if has_audio and ad == "transcode":
        a_out = q.audio_side(codec=ti.get("AudioCodec"), channels=ti.get("AudioChannels"))
    else:
        a_out = a_src

    subs = None
    if st["subtitle"]:
        sub_src = q.subtitle_side(codec=st["subtitle"].get("Codec"), language=st["subtitle"].get("Language")
                                  or st["subtitle"].get("DisplayTitle"), forced=st["subtitle"].get("IsForced"))
        text_sub = bool(st["subtitle"].get("IsTextSubtitleStream")) or sub_src["kind"] == "text"
        if vd == "transcode" and not text_sub:
            sd = "burn"
        elif "subtitle_codec" in codes:
            sd = "transcode" if text_sub else "burn"
        else:
            sd = "copy"
        if sd == "burn" and "subtitle_codec" not in codes:
            codes.append("subtitle_burn_image")
        how = {"burn": "Burned in", "transcode": "Converted to text"}.get(sd, "Sent as text" if text_sub else "Sent as is")
        subs = {"source": sub_src, "stream": {"label": how}, "decision": sd, "changed": sd == "burn"}

    sources = npi.get("MediaSources") or []
    src_container = q.container_label(npi.get("Container") or (sources[0].get("Container") if sources else ""))
    out_container = q.container_label(ti.get("Container")) if (ti and method != "direct_play") else src_container
    src_kbps = _kb(sources[0].get("Bitrate")) if sources else None
    if not src_kbps:
        src_kbps = (sum(x.get("bitrate_kbps") or 0 for x in (v_src, a_src) if x) or None)
    out_kbps = (_kb(ti.get("Bitrate")) if (ti and method != "direct_play") else None) or src_kbps
    reason = rs.summary(codes, method=method, inferred=False, src_kbps=src_kbps, out_kbps=out_kbps)

    transcoder = None
    if ti and method != "direct_play":
        hw = str(ti.get("HardwareAccelerationType") or "").lower()
        engine = _HW.get(hw) if hw and hw != "none" else None
        speed = None
        try:
            fps = float(ti.get("Framerate") or 0)
            src_fps = float((st["video"] or {}).get("RealFrameRate") or 0)
            if fps > 0 and src_fps > 0 and vd == "transcode":
                speed = round(fps / src_fps, 1)
        except (TypeError, ValueError):
            speed = None
        transcoder = {"hw": bool(engine), "hw_decode": engine, "hw_encode": engine,
                      "hw_full_pipeline": False, "speed": speed, "buffered_s": None, "throttled": None,
                      "progress_pct": round(float(ti.get("CompletionPercentage") or 0)),
                      "video": vd or None, "audio": ad or None}

    dur = _ms(npi.get("RunTimeTicks"))
    off = _ms(ps.get("PositionTicks"))
    item_id = str(npi.get("Id") or "")
    title = str(npi.get("Name") or "")
    year = npi.get("ProductionYear")
    show = music = bookd = None
    if kind == "episode":
        series = str(npi.get("SeriesName") or "")
        sn, en = npi.get("ParentIndexNumber"), npi.get("IndexNumber")
        code = f"S{sn} E{en}" if sn is not None and en is not None else ""
        subtitle = " · ".join(x for x in (series, code) if x)
        show = {"title": series, "season": sn, "episode": en, "season_title": npi.get("SeasonName") or None}
    elif kind == "track":
        artist = str((npi.get("Artists") or [""])[0] or npi.get("AlbumArtist") or "")
        album = str(npi.get("Album") or "")
        subtitle = " · ".join(x for x in (artist, album) if x)
        music = {"artist": artist, "album_artist": str(npi.get("AlbumArtist") or artist), "album": album,
                 "track": npi.get("IndexNumber"), "disc": npi.get("ParentIndexNumber")}
    elif kind in ("audiobook", "podcast"):
        author = (book_meta or {}).get("author") or str(npi.get("AlbumArtist") or (npi.get("Artists") or [""])[0] or "")
        narrator = (book_meta or {}).get("narrator") or None
        book_title = (book_meta or {}).get("title") or str(npi.get("Album") or "") or title
        bits = [author]
        if narrator:
            bits.append(f"read by {narrator}")
        if book and book.get("chapter") and book["chapter"].get("count", 0) > 1:
            bits.append(f"Chapter {book['chapter']['index']} of {book['chapter']['count']}")
        subtitle = " · ".join(x for x in bits if x)
        bookd = {"author": author or None, "narrator": narrator, "title": book_title, "progress": book}
        if kind == "audiobook" and book_title and book_title != title and (book or {}).get("chapter"):
            title = book_title
    else:
        subtitle = str(year) if year else ""

    art_id = item_id
    if kind in ("track", "audiobook", "podcast") and not (npi.get("ImageTags") or {}).get("Primary") \
            and npi.get("AlbumId") and npi.get("AlbumPrimaryImageTag"):
        art_id = str(npi["AlbumId"])
    elif kind == "episode" and npi.get("SeriesId") and npi.get("SeriesPrimaryImageTag"):
        art_id = str(npi["SeriesId"])
    thumb = ("jf:" + art_id) if art_id else ""

    sid = str(s.get("Id") or "")
    cmds = s.get("SupportedCommands") or (s.get("Capabilities") or {}).get("SupportedCommands") or []
    can_message = "DisplayMessage" in cmds
    can_stop = bool(s.get("SupportsMediaControl") or s.get("SupportsRemoteControl"))
    prov = npi.get("ProviderIds") or {}
    tmdb = str(prov.get("Tmdb") or prov.get("tmdb") or "")
    user_id = str(s.get("UserId") or "")
    state = "paused" if ps.get("IsPaused") else "playing"
    return {
        "id": f"jellyfin:{sid}",
        "server": "jellyfin",
        "server_name": server_name,
        "session_key": f"jellyfin:{sid}",
        "kind": kind,
        "activity": "watching" if kind in WATCH_KINDS else "listening",
        "state": state,
        "title": title, "subtitle": subtitle, "year": year,
        "show": show, "music": music, "book": bookd,
        "thumb": thumb, "art": thumb,
        "duration_ms": dur, "offset_ms": off,
        "progress_pct": max(0, min(100, round(100 * off / dur))) if dur else 0,
        "user": str(s.get("UserName") or "Someone"),
        "user_id": user_id,
        "user_avatar": f"/api/server-activity/avatar?user=jellyfin:{user_id}"
        if user_id and s.get("UserPrimaryImageTag") else None,
        "player": {"product": str(s.get("Client") or ""), "version": s.get("ApplicationVersion") or None,
                   "platform": None, "platform_version": None, "device": s.get("DeviceType") or None,
                   "model": None, "name": s.get("DeviceName") or None},
        "connection": {"ip": ip or None, "public_ip": None, "remote": remote, "secure": None,
                       "relayed": None, "location": None},
        "bandwidth_kbps": out_kbps or 0,
        "playback": {
            "method": method, "method_label": _METHOD_LABEL[method],
            "video": {"source": v_src, "stream": v_out, "decision": vd, "changed": vd == "transcode"}
            if (v_src or v_out) else None,
            "audio": {"source": a_src, "stream": a_out, "decision": ad, "changed": ad == "transcode"}
            if (a_src or a_out) else None,
            "subtitles": subs,
            "container": {"source": src_container or None, "stream": out_container or None,
                          "changed": bool(src_container and out_container and src_container != out_container)},
            "bitrate_kbps": {"source": src_kbps, "stream": out_kbps},
        },
        "reason": reason,
        "transcoder": transcoder,
        "controls": {"stop": can_stop and bool(SESSION_ID.match(sid)),
                     "message": can_message and bool(SESSION_ID.match(sid)), "stop_note": None},
        "_link": {"server": "jellyfin", "kind": kind, "rating_key": item_id,
                  "parent_key": str(npi.get("AlbumId") or npi.get("ParentId") or ""),
                  "grandparent_key": str(npi.get("SeriesId") or ""),
                  "title": title if kind == "movie" else str(npi.get("SeriesName") or ""),
                  "year": year if kind == "movie" else None,
                  "tmdb": tmdb if (kind == "movie" and tmdb.isdigit()) else ""},
        "_user_thumb": "",
        "_session_id": sid,
    }


# ── server calls ────────────────────────────────────────────────────────────

def _url(cfg: Dict[str, str], path: str) -> str:
    return cfg["base_url"].rstrip("/") + path


def sessions(cfg: Dict[str, str]) -> List[Dict[str, Any]]:
    """raw /Sessions entries that are playing something. raises on failure so
    the caller can tell 'nothing playing' from 'couldn't ask'."""
    import requests
    from core.jellyfin_client import jellyfin_auth_headers
    r = requests.get(_url(cfg, "/Sessions"), headers=jellyfin_auth_headers(cfg["api_key"]), timeout=sa._PLEX_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"jellyfin /Sessions answered {r.status_code}")
    return [s for s in (r.json() or []) if isinstance(s, dict) and s.get("NowPlayingItem")]


_book_lock = threading.Lock()
_book_cache: "OrderedDict[str, tuple]" = OrderedDict()


def book_tracks(cfg: Dict[str, str], parent_id: str, budget: List[int]) -> Optional[List[Dict[str, Any]]]:
    """the files of a book in order, cached 30 min. one call per book."""
    if not parent_id or not SESSION_ID.match(parent_id):
        return None
    with _book_lock:
        hit = _book_cache.get(parent_id)
        if hit and time.time() - hit[0] < 1800:
            return hit[1]
    if budget[0] <= 0:
        return None
    budget[0] -= 1
    out = None
    try:
        import requests
        from core.jellyfin_client import jellyfin_auth_headers
        r = requests.get(_url(cfg, "/Items"), headers=jellyfin_auth_headers(cfg["api_key"]),
                         params={"ParentId": parent_id, "SortBy": "ParentIndexNumber,IndexNumber,SortName"},
                         timeout=sa._PLEX_TIMEOUT)
        if r.status_code == 200:
            items = (r.json() or {}).get("Items") or []
            out = [{"key": str(i.get("Id") or ""), "title": i.get("Name") or "",
                    "duration_ms": _ms(i.get("RunTimeTicks"))} for i in items]
    except Exception:   # noqa: BLE001
        logger.debug("jellyfin book items failed", exc_info=True)
    with _book_lock:
        _book_cache[parent_id] = (time.time(), out)
        while len(_book_cache) > 200:
            _book_cache.popitem(last=False)
    return out


def _post(cfg: Dict[str, str], path: str, body: Optional[Dict[str, Any]] = None):
    import requests
    from core.jellyfin_client import jellyfin_auth_headers
    return requests.post(_url(cfg, path), headers=jellyfin_auth_headers(cfg["api_key"]),
                         json=body, timeout=sa._PLEX_TIMEOUT)


def message(cfg: Dict[str, str], session_id: str, text: str, header: str = "",
            timeout_ms: int = 15000) -> Dict[str, Any]:
    if not SESSION_ID.match(session_id or ""):
        return {"ok": False, "error": "that stream is no longer active"}
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "write a message first"}
    try:
        body = {"Text": text[:500], "TimeoutMs": int(timeout_ms)}
        if header:
            body["Header"] = str(header)[:120]
        r = _post(cfg, f"/Sessions/{session_id}/Message", body)
        if r.status_code in (200, 204):
            return {"ok": True}
        return {"ok": False, "error": f"Jellyfin couldn't show the message ({r.status_code})."}
    except Exception:   # noqa: BLE001
        logger.debug("jellyfin message failed", exc_info=True)
        return {"ok": False, "error": "Couldn't reach Jellyfin."}


def stop(cfg: Dict[str, str], session_id: str, text: str) -> Dict[str, Any]:
    """show the message, then stop. a player that can't show messages still stops."""
    if not SESSION_ID.match(session_id or ""):
        return {"ok": False, "error": "that stream is no longer active"}
    shown = False
    if str(text or "").strip():
        shown = bool(message(cfg, session_id, text, header="Stream stopped").get("ok"))
    try:
        r = _post(cfg, f"/Sessions/{session_id}/Playing/Stop")
        if r.status_code in (200, 204):
            return {"ok": True, "message_shown": shown}
        return {"ok": False, "error": f"Jellyfin couldn't stop the stream ({r.status_code})."}
    except Exception:   # noqa: BLE001
        logger.debug("jellyfin stop failed", exc_info=True)
        return {"ok": False, "error": "Couldn't reach Jellyfin."}
