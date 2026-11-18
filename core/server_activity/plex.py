"""plex: /status/sessions xml -> the shared session shape.

works on the raw xml (``srv.query``), never on plexapi objects. a plexapi object
reloads itself over the network when a missing attribute is read, and ``.user``
fetches a plex.tv account: both are a round trip per session per poll.

while plex transcodes, the session's Media/Part/Stream describe what is SENT.
the file itself comes from ``/library/metadata/<ratingKey>``, fetched once per
item and cached (what tautulli does too), so the poll stays one call.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

import core.server_activity as sa
from core.server_activity import quality as q
from core.server_activity import reasons as rs

logger = sa.logger

_SOURCE_TTL = 6 * 3600
_BOOK_TTL = 30 * 60
_CACHE_MAX = 400
# new items a single poll may look up; the rest wait for the next poll so a
# server with 20 fresh streams never stalls one tick on 20 calls
SOURCE_FETCH_BUDGET = 6

_cache_lock = threading.Lock()
_source_cache: "OrderedDict[str, tuple]" = OrderedDict()
_book_cache: "OrderedDict[str, tuple]" = OrderedDict()


def reset_caches() -> None:
    with _cache_lock:
        _source_cache.clear()
        _book_cache.clear()
        _cpu_cache.update(at=0.0, value=None, off_until=0.0)


def _a(el: Optional[ET.Element], key: str, default: str = "") -> str:
    if el is None:
        return default
    v = el.get(key)
    return default if v is None else v


def _n(v: Any) -> Optional[int]:
    try:
        n = int(float(v))
        return n
    except (TypeError, ValueError):
        return None


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _flag(v: Any) -> Optional[bool]:
    if v is None or v == "":
        return None
    return str(v).lower() in ("1", "true")


def _selected(els: List[ET.Element]) -> Optional[ET.Element]:
    for e in els:
        if e.get("selected") == "1":
            return e
    return els[0] if els else None


def _media_part(el: Optional[ET.Element], media_id: str = "") -> tuple:
    if el is None:
        return None, None
    medias = el.findall("Media")
    media = None
    if media_id:
        media = next((m for m in medias if m.get("id") == media_id), None)
    if media is None:       # an Element with no children is falsy: never `or` one
        media = _selected(medias)
    part = _selected(media.findall("Part")) if media is not None else None
    return media, part


def _streams(part: Optional[ET.Element]) -> Dict[str, Optional[ET.Element]]:
    out: Dict[str, Optional[ET.Element]] = {"video": None, "audio": None, "subtitle": None}
    if part is None:
        return out
    by: Dict[str, List[ET.Element]] = {"1": [], "2": [], "3": []}
    for s in part.findall("Stream"):
        by.setdefault(s.get("streamType", ""), []).append(s)
    out["video"] = _selected(by["1"])
    audio = by["2"]
    picked = next((s for s in audio if s.get("selected") == "1"), None)
    if picked is None:
        picked = next((s for s in audio if s.get("default") == "1"), None)
    if picked is None and audio:
        picked = audio[0]
    out["audio"] = picked
    # a subtitle only counts when one is switched on
    out["subtitle"] = next((s for s in by["3"] if s.get("selected") == "1"), None)
    return out


def _match(src_part: Optional[ET.Element], stream: Optional[ET.Element], kind: str) -> Optional[ET.Element]:
    """the file's own stream for a session stream: same id, else the file's pick."""
    if src_part is None:
        return None
    if stream is not None and stream.get("id"):
        for s in src_part.findall("Stream"):
            if s.get("id") == stream.get("id"):
                return s
    return _streams(src_part)[kind]


def _atmos(s: Optional[ET.Element]) -> bool:
    if s is None:
        return False
    text = " ".join(_a(s, k) for k in ("displayTitle", "extendedDisplayTitle", "title", "profile")).lower()
    return "atmos" in text


def _video_side(s: Optional[ET.Element], media: Optional[ET.Element]) -> Optional[Dict[str, Any]]:
    if s is None and (media is None or not media.get("videoCodec")):
        return None
    hdr = q.hdr_label(_a(s, "colorTrc"), _a(s, "DOVIPresent"))
    return q.video_side(codec=_a(s, "codec") or _a(media, "videoCodec"), profile=_a(s, "profile"),
                        width=_a(s, "width") or _a(media, "width"), height=_a(s, "height") or _a(media, "height"),
                        resolution=_a(media, "videoResolution") if s is None or not s.get("height") else None,
                        bit_depth=_a(s, "bitDepth"), hdr=hdr, bitrate_kbps=_a(s, "bitrate"),
                        frame_rate=_a(s, "frameRate") or _a(media, "videoFrameRate"))


def _audio_side(s: Optional[ET.Element], media: Optional[ET.Element]) -> Optional[Dict[str, Any]]:
    if s is None and (media is None or not media.get("audioCodec")):
        return None
    return q.audio_side(codec=_a(s, "codec") or _a(media, "audioCodec"), profile=_a(s, "profile"),
                        channels=_a(s, "channels") or _a(media, "audioChannels"),
                        layout=_a(s, "audioChannelLayout").split("(")[0],
                        language=_a(s, "language"), bit_depth=_a(s, "bitDepth"),
                        sample_rate=_a(s, "samplingRate"), bitrate_kbps=_a(s, "bitrate"), atmos=_atmos(s))


def _subtitle_side(s: Optional[ET.Element]) -> Optional[Dict[str, Any]]:
    if s is None:
        return None
    return q.subtitle_side(codec=_a(s, "codec"), language=_a(s, "language"), title=_a(s, "title"),
                           forced=_a(s, "forced"))


def _tmdb(*els: Optional[ET.Element]) -> str:
    for el in els:
        if el is None:
            continue
        for g in el.findall("Guid"):
            gid = g.get("id", "")
            if gid.startswith("tmdb://"):
                num = gid[7:].split("?")[0].strip()
                if num.isdigit():
                    return num
    return ""


# ── whole-book progress ─────────────────────────────────────────────────────

def book_progress(*, offset_ms: int, duration_ms: int, tracks: Optional[List[Dict[str, Any]]] = None,
                  current_key: str = "", chapters: Optional[List[Dict[str, Any]]] = None
                  ) -> Optional[Dict[str, Any]]:
    """chapter N of M plus where the whole book is.

    a book is either one file per chapter (``tracks``: the book's tracks in
    order with their durations) or one file with chapter marks (``chapters``:
    start/end in ms). pure."""
    if tracks:
        idx = next((i for i, t in enumerate(tracks) if str(t.get("key")) == str(current_key)), None)
        if idx is None:
            return None
        total = sum(int(t.get("duration_ms") or 0) for t in tracks)
        before = sum(int(t.get("duration_ms") or 0) for t in tracks[:idx])
        pos = before + max(0, offset_ms)
        cur = tracks[idx]
        ch = {"index": idx + 1, "count": len(tracks), "title": cur.get("title") or None,
              "position_ms": max(0, offset_ms), "duration_ms": int(cur.get("duration_ms") or duration_ms or 0)}
    elif chapters:
        chs = sorted(chapters, key=lambda c: int(c.get("start_ms") or 0))
        idx = 0
        for i, c in enumerate(chs):
            if offset_ms >= int(c.get("start_ms") or 0):
                idx = i
        c = chs[idx]
        start = int(c.get("start_ms") or 0)
        end = int(c.get("end_ms") or 0) or (int(chs[idx + 1]["start_ms"]) if idx + 1 < len(chs) else duration_ms)
        total, pos = duration_ms, max(0, offset_ms)
        ch = {"index": idx + 1, "count": len(chs), "title": c.get("title") or None,
              "position_ms": max(0, offset_ms - start), "duration_ms": max(0, end - start)}
    else:
        return None
    if not total:
        return None
    return {"chapter": ch, "position_ms": pos, "duration_ms": total,
            "percent": max(0, min(100, round(100 * pos / total))), "left_ms": max(0, total - pos)}


# ── cached lookups (one call per item, not per poll) ────────────────────────

def _cache_get(cache: "OrderedDict[str, tuple]", key: str, ttl: float):
    with _cache_lock:
        hit = cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            cache.move_to_end(key)
            return True, hit[1]
    return False, None


def _cache_put(cache: "OrderedDict[str, tuple]", key: str, value: Any) -> None:
    with _cache_lock:
        cache[key] = (time.time(), value)
        cache.move_to_end(key)
        while len(cache) > _CACHE_MAX:
            cache.popitem(last=False)


def source_item(srv, rating_key: str, budget: List[int]) -> Optional[ET.Element]:
    """the library item behind a session (the file's own streams, chapters,
    guids). cached; spends one of this poll's ``budget`` on a miss."""
    if not rating_key or not rating_key.isdigit():
        return None
    hit, val = _cache_get(_source_cache, rating_key, _SOURCE_TTL)
    if hit:
        return val
    if budget[0] <= 0:
        return None
    budget[0] -= 1
    el = None
    try:
        root = srv.query(f"/library/metadata/{rating_key}", params={"includeChapters": 1})
        el = root[0] if root is not None and len(root) else None
    except Exception:   # noqa: BLE001 - a missing item just means no source detail
        logger.debug("plex metadata %s failed", rating_key, exc_info=True)
    _cache_put(_source_cache, rating_key, el)
    return el


def prefetch_sources(srv, rating_keys: List[str], budget: List[int], workers: int = 6) -> None:
    """source_item for every new stream at once (each still spends budget), so
    a poll with several new streams waits for the slowest, not the sum."""
    misses = []
    for k in dict.fromkeys(str(k or "") for k in rating_keys):
        if k.isdigit() and not _cache_get(_source_cache, k, _SOURCE_TTL)[0]:
            misses.append(k)
    misses = misses[:max(0, budget[0])]
    if len(misses) < 2:
        return
    budget[0] -= len(misses)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(workers, len(misses)), thread_name_prefix="plex-source") as ex:
        list(ex.map(lambda k: source_item(srv, k, [1]), misses))


def book_tracks(srv, parent_key: str, budget: List[int]) -> Optional[List[Dict[str, Any]]]:
    """the tracks of a book (plex files a book as an album), in order."""
    if not parent_key or not parent_key.isdigit():
        return None
    hit, val = _cache_get(_book_cache, parent_key, _BOOK_TTL)
    if hit:
        return val
    if budget[0] <= 0:
        return None
    budget[0] -= 1
    out: Optional[List[Dict[str, Any]]] = None
    try:
        root = srv.query(f"/library/metadata/{parent_key}/children")
        if root is not None:
            rows = []
            for t in root:
                rows.append({"key": t.get("ratingKey", ""), "title": t.get("title", ""),
                             "duration_ms": _n(t.get("duration")) or 0,
                             "disc": _n(t.get("parentIndex")) or 1, "index": _n(t.get("index")) or 0})
            rows.sort(key=lambda r: (r["disc"], r["index"]))
            out = rows
    except Exception:   # noqa: BLE001
        logger.debug("plex book children %s failed", parent_key, exc_info=True)
    _cache_put(_book_cache, parent_key, out)
    return out


def chapters_of(el: Optional[ET.Element]) -> List[Dict[str, Any]]:
    if el is None:
        return []
    out = []
    for c in el.findall("Chapter"):
        out.append({"start_ms": _n(c.get("startTimeOffset")) or 0, "end_ms": _n(c.get("endTimeOffset")) or 0,
                    "title": c.get("tag") or c.get("title") or ""})
    return out


# ── normalize ───────────────────────────────────────────────────────────────

_METHOD_LABEL = {"direct_play": "Direct Play", "direct_stream": "Direct Stream", "transcode": "Transcode"}
WATCH_KINDS = {"movie", "episode", "clip", "live"}


def facts(el: ET.Element) -> Dict[str, str]:
    """the bits live.py needs before normalizing: keys, section, file."""
    media, part = _media_part(el)
    return {"rating_key": _a(el, "ratingKey"), "parent_key": _a(el, "parentRatingKey"),
            "grandparent_key": _a(el, "grandparentRatingKey"), "type": _a(el, "type").lower(),
            "section": _a(el, "librarySectionTitle"), "file": _a(part, "file")}


def normalize(el: ET.Element, *, server_name: str = "Plex", source: Optional[ET.Element] = None,
              audio_kind: Optional[str] = None, book: Optional[Dict[str, Any]] = None,
              book_meta: Optional[Dict[str, Any]] = None, plex_pass: Optional[bool] = None
              ) -> Dict[str, Any]:
    """one session element -> the shared session dict. pure.

    ``source`` is the library item (the file as stored); ``audio_kind`` marks a
    track as 'audiobook' or 'podcast'; ``book`` is :func:`book_progress`."""
    ptype = _a(el, "type").lower()
    kind = ptype if ptype in ("movie", "episode", "track", "clip") else ("live" if ptype == "channel" else "other")
    if kind == "track" and audio_kind in ("audiobook", "podcast"):
        kind = audio_kind
    player = el.find("Player")
    sess = el.find("Session")
    user = el.find("User")
    tc = el.find("TranscodeSession")

    media, part = _media_part(el)
    st = _streams(part)
    src_media, src_part = _media_part(source, _a(media, "id"))
    sv = _match(src_part, st["video"], "video") if src_part is not None else None
    sa_ = _match(src_part, st["audio"], "audio") if src_part is not None else None
    ssub = _match(src_part, st["subtitle"], "subtitle") if src_part is not None else None

    vd = (_a(tc, "videoDecision") or _a(st["video"], "decision")).lower()
    ad = (_a(tc, "audioDecision") or _a(st["audio"], "decision")).lower()
    sd = (_a(tc, "subtitleDecision") or _a(st["subtitle"], "decision")).lower()
    if st["subtitle"] is not None and _a(st["subtitle"], "burn") == "1":
        sd = "burn"
    if tc is None and vd not in ("transcode", "copy") and ad not in ("transcode", "copy"):
        method = "direct_play"
    elif vd == "transcode" or ad == "transcode":
        method = "transcode"
    else:
        method = "direct_stream"

    # the file as stored. without a looked-up source, a direct play's session
    # IS the file; a transcode's session is the copy, so keep only what plex
    # still says about the original
    direct = method == "direct_play"
    v_src_el = sv if sv is not None else (st["video"] if direct or vd != "transcode" else None)
    a_src_el = sa_ if sa_ is not None else (st["audio"] if direct or ad != "transcode" else None)
    has_video = kind in WATCH_KINDS and (st["video"] is not None or bool(_a(media, "videoCodec")))
    has_audio = st["audio"] is not None or bool(_a(media, "audioCodec"))
    v_media = src_media if src_media is not None else (media if vd != "transcode" else None)
    a_media = src_media if src_media is not None else (media if ad != "transcode" else None)
    v_src = _video_side(v_src_el, v_media) if has_video else None
    if v_src is None and has_video and tc is not None and _a(tc, "sourceVideoCodec"):
        v_src = q.video_side(codec=_a(tc, "sourceVideoCodec"))
    a_src = _audio_side(a_src_el, a_media) if has_audio else None
    if a_src is None and has_audio and tc is not None and _a(tc, "sourceAudioCodec"):
        a_src = q.audio_side(codec=_a(tc, "sourceAudioCodec"))

    if has_video and vd == "transcode":
        v_out = q.video_side(codec=_a(tc, "videoCodec") or _a(st["video"], "codec"),
                             width=_a(tc, "width") or _a(st["video"], "width"),
                             height=_a(tc, "height") or _a(st["video"], "height"),
                             resolution=_a(media, "videoResolution"),
                             # plex maps hdr to sdr whenever it converts the video
                             hdr="",
                             bitrate_kbps=_a(st["video"], "bitrate"))
    else:
        v_out = v_src
    if has_audio and ad == "transcode":
        a_out = q.audio_side(codec=_a(tc, "audioCodec") or _a(st["audio"], "codec"),
                             channels=_a(tc, "audioChannels") or _a(st["audio"], "channels"),
                             bitrate_kbps=_a(st["audio"], "bitrate"),
                             bit_depth=_a(st["audio"], "bitDepth"), sample_rate=_a(st["audio"], "samplingRate"))
    else:
        a_out = a_src

    sub_src = _subtitle_side(ssub if ssub is not None else st["subtitle"])
    if sub_src is None:
        subs = None
    else:
        how = {"burn": "Burned in", "transcode": "Converted to text", "copy": "Sent as is",
               "": "Sent as is"}.get(sd, sd.capitalize())
        if sd in ("copy", "") and sub_src["kind"] == "text":
            how = "Sent as text"
        subs = {"source": sub_src, "stream": {"label": how}, "decision": sd or "direct", "changed": sd == "burn"}

    src_container = q.container_label(_a(src_media, "container") or (_a(media, "container") if direct else ""))
    if not src_container and source is None:
        src_container = q.container_label(_a(part, "container")) if direct else ""
    protocol = _a(tc, "protocol").lower()
    out_container = q.container_label(protocol if protocol in ("hls", "dash") else (_a(tc, "container") or "")) \
        if tc is not None else src_container
    src_kbps = _n(_a(src_media, "bitrate")) or (_n(_a(media, "bitrate")) if direct else None)
    out_kbps = (_n(_a(media, "bitrate")) if not direct else src_kbps) or None
    bandwidth = _n(_a(sess, "bandwidth")) or out_kbps or 0

    location = _a(sess, "location").lower()
    local_flag = _flag(_a(player, "local", None) if player is not None else None)
    remote = location == "wan" if location in ("lan", "wan") else (local_flag is False)
    ip = _a(player, "address")
    public_ip = _a(player, "remotePublicAddress")

    codes = rs.infer_plex(
        method=method, video_decision=vd, audio_decision=ad, subtitle_decision=sd,
        subtitle_codec=_a(ssub if ssub is not None else st["subtitle"], "codec"),
        src_height=(v_src or {}).get("height"), out_height=(v_out or {}).get("height") if vd == "transcode" else None,
        src_video_kbps=(v_src or {}).get("bitrate_kbps") or src_kbps,
        out_video_kbps=((v_out or {}).get("bitrate_kbps") or out_kbps) if vd == "transcode" else None,
        src_hdr=(v_src or {}).get("hdr") or "", out_hdr=(v_out or {}).get("hdr") or "",
        src_vcodec=(v_src or {}).get("codec") or "", out_vcodec=(v_out or {}).get("codec") or "",
        src_acodec=(a_src or {}).get("codec") or "", out_acodec=(a_out or {}).get("codec") or "",
        src_channels=(a_src or {}).get("channels"), out_channels=(a_out or {}).get("channels"),
        src_container=src_container, out_container=out_container, remote=remote)
    reason = rs.summary(codes, method=method, inferred=True, src_kbps=src_kbps, out_kbps=out_kbps)

    transcoder = None
    if tc is not None:
        dec = _a(tc, "transcodeHwDecodingTitle") or _a(tc, "transcodeHwDecoding")
        enc = _a(tc, "transcodeHwEncodingTitle") or _a(tc, "transcodeHwEncoding")
        max_off = _f(_a(tc, "maxOffsetAvailable"))
        view_s = (_n(_a(el, "viewOffset")) or 0) / 1000.0
        buffered = round(max(0.0, max_off - view_s), 1) if max_off is not None else None
        transcoder = {
            "hw": bool(dec or enc), "hw_decode": dec or None, "hw_encode": enc or None,
            "hw_full_pipeline": _a(tc, "transcodeHwFullPipeline") == "1",
            "speed": _f(_a(tc, "speed")),
            "buffered_s": buffered,
            "throttled": _a(tc, "throttled") == "1",
            "progress_pct": round(_f(_a(tc, "progress")) or 0),
            "video": vd or None, "audio": ad or None,
        }

    duration = _n(_a(el, "duration")) or _n(_a(source, "duration")) or 0
    offset = _n(_a(el, "viewOffset")) or 0
    state = _a(player, "state", "playing").lower()

    # titles per kind
    title = _a(el, "title")
    gp, parent = _a(el, "grandparentTitle"), _a(el, "parentTitle")
    year = _n(_a(el, "year")) or _n(_a(source, "year"))
    show = music = bookd = None
    if kind == "episode":
        season, ep = _n(_a(el, "parentIndex")), _n(_a(el, "index"))
        code = f"S{season} E{ep}" if season is not None and ep is not None else ""
        subtitle = " · ".join(x for x in (gp, code) if x)
        show = {"title": gp, "season": season, "episode": ep, "season_title": parent or None}
    elif kind == "track":
        artist = _a(el, "originalTitle") or gp
        subtitle = " · ".join(x for x in (artist, parent) if x)
        music = {"artist": artist, "album_artist": gp, "album": parent, "track": _n(_a(el, "index")),
                 "disc": _n(_a(el, "parentIndex"))}
    elif kind in ("audiobook", "podcast"):
        author = (book_meta or {}).get("author") or gp
        narrator = (book_meta or {}).get("narrator") or None
        book_title = (book_meta or {}).get("title") or parent or title
        bits = [author]
        if narrator:
            bits.append(f"read by {narrator}")
        if book and book.get("chapter") and book["chapter"].get("count", 0) > 1:
            bits.append(f"Chapter {book['chapter']['index']} of {book['chapter']['count']}")
        subtitle = " · ".join(x for x in bits if x)
        bookd = {"author": author or None, "narrator": narrator, "title": book_title,
                 "progress": book}
        if kind == "audiobook" and book_title and book_title != title and (book or {}).get("chapter"):
            title = book_title
    else:
        subtitle = str(year) if year else ""

    if kind in ("track", "audiobook", "podcast"):
        cover = _a(el, "parentThumb") or _a(el, "thumb")
        thumb = cover or _a(el, "grandparentThumb")
        art = cover or _a(el, "art") or _a(el, "grandparentArt")
    else:
        thumb = _a(el, "grandparentThumb") or _a(el, "parentThumb") or _a(el, "thumb")
        art = _a(el, "art") or _a(el, "grandparentArt")

    session_key = _a(el, "sessionKey") or _a(sess, "id")
    user_id = _a(user, "id")
    stop_ok = plex_pass is not False and bool(_a(sess, "id") or session_key)
    out = {
        "id": f"plex:{session_key}",
        "server": "plex",
        "server_name": server_name,
        "session_key": session_key,
        "kind": kind,
        "activity": "watching" if kind in WATCH_KINDS else "listening",
        "state": state if state in ("playing", "paused", "buffering") else "playing",
        "title": title,
        "subtitle": subtitle,
        "year": year,
        "show": show, "music": music, "book": bookd,
        "thumb": thumb, "art": art,
        "duration_ms": duration, "offset_ms": offset,
        "progress_pct": max(0, min(100, round(100 * offset / duration))) if duration else 0,
        "user": _a(user, "title") or "Someone",
        "user_id": user_id,
        "user_avatar": f"/api/server-activity/avatar?user=plex:{user_id}" if user_id and _a(user, "thumb") else None,
        "player": {"product": _a(player, "product"), "version": _a(player, "version") or None,
                   "platform": _a(player, "platform") or None,
                   "platform_version": _a(player, "platformVersion") or None,
                   "device": _a(player, "device") or None, "model": _a(player, "model") or None,
                   "name": _a(player, "title") or None},
        "connection": {"ip": ip or None, "public_ip": public_ip or None, "remote": remote,
                       "secure": _flag(_a(player, "secure", None) if player is not None else None),
                       "relayed": _flag(_a(player, "relayed", None) if player is not None else None),
                       "location": None},
        "bandwidth_kbps": bandwidth,
        "playback": {
            "method": method, "method_label": _METHOD_LABEL[method],
            "video": {"source": v_src, "stream": v_out, "decision": vd or "direct",
                      "changed": vd == "transcode"} if (v_src or v_out) else None,
            "audio": {"source": a_src, "stream": a_out, "decision": ad or "direct",
                      "changed": ad == "transcode"} if (a_src or a_out) else None,
            "subtitles": subs,
            "container": {"source": src_container or None, "stream": out_container or None,
                          "changed": bool(src_container and out_container and src_container != out_container)},
            "bitrate_kbps": {"source": src_kbps, "stream": out_kbps},
        },
        "reason": reason,
        "transcoder": transcoder,
        "controls": {"stop": stop_ok, "message": False,
                     "stop_note": None if plex_pass is not False else "Stopping a stream needs Plex Pass on the server."},
        "_link": {"server": "plex", "kind": kind, "rating_key": _a(el, "ratingKey"),
                  "parent_key": _a(el, "parentRatingKey"), "grandparent_key": _a(el, "grandparentRatingKey"),
                  "title": title if kind == "movie" else gp, "year": year if kind == "movie" else None,
                  "tmdb": _tmdb(el, source) if kind == "movie" else ""},
        "_user_thumb": _a(user, "thumb"),
        "_session_id": _a(sess, "id"),
    }
    return out


# ── server calls ────────────────────────────────────────────────────────────

def session_elements(srv) -> List[ET.Element]:
    root = srv.query("/status/sessions")
    return [c for c in (root if root is not None else []) if c.tag in ("Video", "Track", "Photo", "Episode")]


_cpu_cache: Dict[str, Any] = {"at": 0.0, "value": None, "off_until": 0.0}


def cpu(srv) -> Optional[Dict[str, Any]]:
    """the newest dashboard resources sample. only when it is fresh: an old
    sample is not the cpu now, so no number is better than a wrong one."""
    now = time.time()
    with _cache_lock:
        if now < _cpu_cache["off_until"]:
            return None
        if now - _cpu_cache["at"] < 15:
            return _cpu_cache["value"]
    value = None
    try:
        root = srv.query("/statistics/resources", params={"timespan": 6})
        rows = [r for r in (root if root is not None else []) if r.tag == "StatisticsResources"]
        if rows:
            last = max(rows, key=lambda r: _n(r.get("at")) or 0)
            at = _n(last.get("at")) or 0
            host = _f(last.get("hostCpuUtilization"))
            if host is not None and abs(now - at) <= 120:
                value = {"host_pct": round(host, 1),
                         "process_pct": round(_f(last.get("processCpuUtilization")) or 0, 1),
                         "at": at}
    except Exception:   # noqa: BLE001 - the dashboard api needs plex pass on some servers
        logger.debug("plex resources unavailable", exc_info=True)
        with _cache_lock:
            _cpu_cache.update(off_until=now + 600)
        return None
    with _cache_lock:
        _cpu_cache.update(at=now, value=value)
    return value


def stop(srv, session_id: str, message: str) -> Dict[str, Any]:
    """end a plex stream with a message. needs plex pass on the server."""
    if not session_id:
        return {"ok": False, "error": "that stream is no longer active"}
    if _flag(getattr(srv, "myPlexSubscription", None)) is False:
        return {"ok": False, "error": "Stopping a stream needs Plex Pass on the server.", "code": "plex_pass"}
    try:
        srv.query("/status/sessions/terminate", params={"sessionId": session_id, "reason": message})
        return {"ok": True}
    except Exception as e:   # noqa: BLE001
        text = str(e)
        logger.debug("plex terminate failed", exc_info=True)
        if "401" in text or "403" in text or "unauthorized" in text.lower():
            return {"ok": False, "error": "Plex refused: stopping a stream needs Plex Pass on the server.",
                    "code": "plex_pass"}
        return {"ok": False, "error": "Plex couldn't stop the stream."}
