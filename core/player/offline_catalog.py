"""what a download is made of: a title, a season, an album, a playlist, a
book or a podcast as the items a device keeps, each with its title, art,
length, real size and source, and the qualities it can be had in.

    expand(group_type, group_id)   -> (header, [item])   checks the profile
    describe(items)                -> {"type:id": meta}  titles for a device's list
    qualities(kind, items)         -> [quality]          sizes for the sheet

every lookup goes by the profile asking: its sections (core/player/playable
check), its rating cap for video, and its own library and explicit setting
for music. nothing here writes.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.player.playable import Refused, check
from utils.logging_config import get_logger

logger = get_logger("player.offline")

VIDEO_KINDS = ("movie", "episode", "youtube", "home")
AUDIO_KINDS = ("track", "audiobook", "podcast_episode")
ITEM_KINDS = VIDEO_KINDS + AUDIO_KINDS
GROUP_TYPES = ("movie", "episode", "season", "show", "youtube", "home", "home_album", "track", "album",
               "artist", "playlist", "audiobook", "podcast")
# the kind of item a group holds
GROUP_ITEM = {"movie": "movie", "episode": "episode", "season": "episode", "show": "episode",
              "youtube": "youtube", "home": "home", "home_album": "home", "track": "track", "album": "track", "artist": "track",
              "playlist": "track", "audiobook": "audiobook", "podcast": "podcast_episode"}

PODCAST_DEFAULT = 5
PODCAST_MAX = 50
ARTIST_MAX = 2000
BATCH = 500

# (height, video bitrate bps) per converted video quality; audio rides at 160k
VIDEO_QUALITIES = {"high": (1080, 8_000_000), "phone": (720, 3_500_000), "small": (480, 1_500_000)}
VIDEO_AUDIO_BPS = 160_000
AUDIO_QUALITIES = {"320": 320, "256": 256, "128": 128}
VIDEO_ORDER = ("original", "high", "phone", "small")
AUDIO_ORDER = ("original", "320", "256", "128")


def kind_of(item_type: str) -> str:
    return "video" if item_type in VIDEO_KINDS else "audio"


def key(item_type: str, item_id: Any) -> str:
    return f"{item_type}:{item_id}"


# ── small helpers ────────────────────────────────────────────────────────

def _video_db():
    from api.video import get_video_db
    return get_video_db()


def _music_db():
    from database.music_database import get_database
    return get_database()


def _height(resolution: Any) -> Optional[int]:
    from core.player.sources.video import _height as h
    return h(resolution)


def _cap():
    from api.video.kids import video_cap
    return video_cap()


def _allowed(cap, kind: str, rating: Any) -> bool:
    from api.video.kids import kind_allows
    return kind_allows(cap, kind, rating)


def _ext(path: Any) -> Optional[str]:
    ext = os.path.splitext(str(path or ""))[1].lstrip(".").lower()
    return ext or None


def _kbps(size: Any, seconds: Any) -> Optional[int]:
    try:
        if size and seconds and float(seconds) > 0:
            return int(int(size) * 8 / float(seconds) / 1000)
    except (TypeError, ValueError):
        pass
    return None


def _ep_label(season: Any, episode: Any) -> str:
    return f"S{season} E{episode}"


def _chunks(seq: List[Any]) -> Iterable[List[Any]]:
    for i in range(0, len(seq), BATCH):
        yield seq[i:i + BATCH]


# ── video ────────────────────────────────────────────────────────────────

_FILE_COLS = "id, relative_path, size_bytes, video_codec, audio_codec, runtime_seconds, resolution, dynamic_range"


def _best_files(conn, column: str, ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """each title's biggest file (what the player plays when no version is picked)"""
    out: Dict[int, Dict[str, Any]] = {}
    for chunk in _chunks(ids):
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
                f"SELECT {column}, {_FILE_COLS} FROM media_files WHERE {column} IN ({marks}) "  # noqa: S608
                f"ORDER BY COALESCE(size_bytes, 0) DESC", chunk):
            if r[0] in out:
                continue
            out[r[0]] = {"file_id": r[1], "path": r[2], "size": int(r[3] or 0), "video_codec": r[4],
                         "audio_codec": r[5], "runtime": r[6], "height": _height(r[7]),
                         "hdr": r[8] or None}
    return out


def _video_source(f: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not f:
        return {"container": None, "video_codec": None, "height": None, "hdr": None,
                "audio_codec": None, "bitrate_kbps": None}
    return {"container": _ext(f["path"]), "video_codec": f["video_codec"], "height": f["height"],
            "hdr": f["hdr"], "audio_codec": f["audio_codec"],
            "bitrate_kbps": _kbps(f["size"], f["runtime"])}


def _movies(ids: List[int], cap=None, *, checked: bool = True) -> List[Dict[str, Any]]:
    db = _video_db()
    conn = db._get_connection()
    try:
        rows = {}
        for chunk in _chunks(ids):
            marks = ",".join("?" * len(chunk))
            for r in conn.execute(
                    f"SELECT id, title, year, runtime_minutes, content_rating FROM movies "  # noqa: S608
                    f"WHERE id IN ({marks})", chunk):
                rows[r[0]] = r
        files = _best_files(conn, "movie_id", list(rows))
    finally:
        conn.close()
    out = []
    for mid in ids:
        r = rows.get(mid)
        if r is None or (checked and not _allowed(cap, "movie", r[4])):
            continue
        f = files.get(mid)
        out.append({
            "item_type": "movie", "item_id": str(mid), "title": r[1] or "Movie",
            "subtitle": str(r[2]) if r[2] else "",
            "artwork": f"/api/video/poster/movie/{mid}?w=342",
            "duration_s": int(f["runtime"]) if f and f["runtime"] else (int(r[3]) * 60 if r[3] else None),
            "size_bytes": f["size"] if f else 0, "source": _video_source(f),
            "group": {"type": "movie", "id": str(mid), "title": r[1] or "Movie",
                      "subtitle": str(r[2]) if r[2] else "", "artwork": f"/api/video/poster/movie/{mid}?w=342"},
            "year": r[2], "_has_file": f is not None,
        })
    return out


def _episode_rows(conn, where: str, args: list) -> list:
    return conn.execute(
        "SELECT e.id, e.show_id, e.season_number, e.episode_number, e.title, e.runtime_minutes, "
        "s.title, s.content_rating, se.id, s.year "
        "FROM episodes e JOIN shows s ON s.id = e.show_id "
        "LEFT JOIN seasons se ON se.id = e.season_id "
        f"WHERE {where} "  # noqa: S608 - our own fragment
        "ORDER BY e.season_number = 0, e.season_number, e.episode_number", args).fetchall()


def _episodes_from(rows: list, cap=None, *, checked: bool = True,
                   files: Optional[Dict[int, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        if checked and not _allowed(cap, "show", r[7]):
            continue
        f = (files or {}).get(r[0])
        season_label = "Specials" if r[2] == 0 else f"Season {r[2]}"
        out.append({
            "item_type": "episode", "item_id": str(r[0]),
            "title": f"{_ep_label(r[2], r[3])} · {r[4] or 'Episode ' + str(r[3])}",
            "subtitle": r[6] or "",
            "artwork": f"/api/video/poster/episode/{r[0]}?w=500",
            "duration_s": int(f["runtime"]) if f and f["runtime"] else (int(r[5]) * 60 if r[5] else None),
            "size_bytes": f["size"] if f else 0, "source": _video_source(f),
            "group": {"type": "season", "id": f"{r[1]}:{r[2]}", "title": r[6] or "",
                      "subtitle": season_label,
                      "artwork": f"/api/video/poster/season/{r[8]}?w=342" if r[8]
                      else f"/api/video/poster/show/{r[1]}?w=342",
                      "show_id": str(r[1])},
            "show_id": r[1], "season": r[2], "episode": r[3], "_has_file": f is not None,
        })
    return out


def _episodes(where: str, args: list, cap=None, *, checked: bool = True,
              owned_only: bool = True) -> List[Dict[str, Any]]:
    db = _video_db()
    conn = db._get_connection()
    try:
        rows = _episode_rows(conn, where, args)
        files = _best_files(conn, "episode_id", [r[0] for r in rows])
    finally:
        conn.close()
    items = _episodes_from(rows, cap, checked=checked, files=files)
    return [i for i in items if i["_has_file"]] if owned_only else items


def _show_header(show_id: int, season: Optional[int] = None) -> Dict[str, Any]:
    db = _video_db()
    conn = db._get_connection()
    try:
        r = conn.execute("SELECT title, year, content_rating FROM shows WHERE id = ?", (show_id,)).fetchone()
        sr = None
        if season is not None:
            sr = conn.execute("SELECT id FROM seasons WHERE show_id = ? AND season_number = ?",
                              (show_id, season)).fetchone()
    finally:
        conn.close()
    if not r:
        raise Refused(404, "not_found", "That show isn't in your library.")
    if not _allowed(_cap(), "show", r[2]):
        raise Refused(403, "restricted", "That isn't available on this profile.")
    art = (f"/api/video/poster/season/{sr[0]}?w=342" if sr
           else f"/api/video/poster/show/{show_id}?w=342")
    sub = ("Specials" if season == 0 else f"Season {season}") if season is not None else (str(r[1]) if r[1] else "")
    return {"title": r[0] or "Show", "subtitle": sub, "artwork": art}


def _youtube(ids: List[str]) -> List[Dict[str, Any]]:
    from core.player import youtube
    db = _video_db()
    conn = db._get_connection()
    try:
        ledger = youtube.downloaded_ids(conn)
        found = {}
        for chunk in _chunks(ids):
            marks = ",".join("?" * len(chunk))
            for r in conn.execute(
                    f"SELECT youtube_id, duration, thumbnail_url FROM youtube_channel_videos "  # noqa: S608
                    f"WHERE youtube_id IN ({marks})", chunk):
                found[r[0]] = r
        titles = {r[0]: r[1] for r in conn.execute("SELECT channel_id, title FROM youtube_channel_meta")}
    finally:
        conn.close()
    out = []
    for vid in ids:
        entry = ledger.get(vid)
        if not entry:
            continue
        meta = found.get(vid)
        art = youtube.thumbnail(vid, (meta[2] if meta else None) or entry.get("poster_url"))
        channel = titles.get(entry.get("channel_id") or "") or ""
        out.append({
            "item_type": "youtube", "item_id": vid, "title": entry.get("title") or "Video",
            "subtitle": channel, "artwork": art,
            "duration_s": youtube.parse_duration(meta[1]) if meta else None,
            "size_bytes": int(entry.get("size_bytes") or 0),
            "source": {"container": None, "video_codec": None, "height": _height(entry.get("quality_label")),
                       "hdr": None, "audio_codec": None, "bitrate_kbps": None},
            "group": {"type": "youtube", "id": vid, "title": entry.get("title") or "Video",
                      "subtitle": channel, "artwork": art},
            "_has_file": True,
        })
    return out


def _home(ids: List[Any], *, checked: bool = True, album: Optional[str] = None) -> List[Dict[str, Any]]:
    """home videos (core/video/home_videos.py) by id, or a whole album
    (``album``: its key) oldest first. checked: only the ones in a home
    library this profile can see, with a file."""
    from api.player import player_db
    from core.player import home_access
    from core.video import home_videos as H
    db = _video_db()
    labels = {r["id"]: r["label"] for r in H.roots(db)}
    seen = None
    if checked:
        seen = set(home_access.visible_roots(player_db(), db, home_access.hidden_now()))
    conn = db._get_connection()
    try:
        if album is not None:
            rows = [H._row(r) for r in conn.execute(
                f"SELECT {H._COLS} FROM home_videos WHERE album_key = ? AND missing_since IS NULL "  # noqa: S608
                "ORDER BY sort_at, id", (album,))]
        else:
            got = H.titles_of(conn, ids)
            rows = [got[str(i)] for i in ids if str(i) in got]
    finally:
        conn.close()
    out = []
    for r in rows:
        if seen is not None and (r["root"] not in seen or r.get("missing_since")):
            continue
        v = H.video_json(r, labels)
        when = H.format_when(v["filmed_at"], with_time=False)
        art = v["thumb_url"]
        out.append({
            "item_type": "home", "item_id": str(r["id"]), "title": v["title"],
            "subtitle": " · ".join(x for x in (v["album"], when) if x),
            "artwork": art, "duration_s": int(r["duration_s"]) if r.get("duration_s") else None,
            "size_bytes": int(r.get("size_bytes") or 0),
            "source": {"container": _ext(r.get("path")), "video_codec": r.get("video_codec"),
                       "height": r.get("height"), "hdr": None, "audio_codec": r.get("audio_codec"),
                       "bitrate_kbps": _kbps(r.get("size_bytes"), r.get("duration_s"))},
            "group": {"type": "home_album", "id": r.get("album_key") or "", "title": v["album"] or "Home videos",
                      "subtitle": "Home videos", "artwork": art},
            "_has_file": True,
        })
    return out


# ── music ────────────────────────────────────────────────────────────────

def _music_scope():
    from core.player.sources.music import _scope, hides_explicit
    return _scope(), hides_explicit()


def _tracks(ids: List[int], *, checked: bool = True,
            group: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """tracks in this order, each with its best file; ones this profile
    can't play are left out"""
    from core.player.playlists import playable_ids
    db = _music_db()
    wanted = [int(i) for i in ids if i is not None]
    if not wanted:
        return []
    with db._get_connection() as conn:
        ok = set(wanted)
        if checked:
            scope, hide = _music_scope()
            ok = playable_ids(conn, wanted, scope=scope, hide_explicit=hide)
        rows: Dict[int, Any] = {}
        files: Dict[int, Any] = {}
        good = [i for i in wanted if i in ok]
        for chunk in _chunks(sorted(set(good))):
            marks = ",".join("?" * len(chunk))
            for r in conn.execute(
                    f"SELECT t.id, t.title, t.duration, t.track_number, al.id, al.title, "  # noqa: S608
                    f"COALESCE(t.track_artist, ar.name), ar.name "
                    f"FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id "
                    f"LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id "
                    f"WHERE t.id IN ({marks})", chunk):
                rows[r[0]] = r
            for r in conn.execute(
                    f"SELECT track_id, path, size, bitrate, format FROM lib2_track_files "  # noqa: S608
                    f"WHERE track_id IN ({marks}) AND COALESCE(import_status, 'imported') = 'imported' "
                    f"ORDER BY CASE WHEN quality_tier = 'lossless' THEN 0 ELSE 1 END, "
                    f"COALESCE(bitrate, 0) DESC", chunk):
                files.setdefault(r[0], r)
    out = []
    for tid in good:
        r = rows.get(tid)
        if r is None:
            continue
        f = files.get(tid)
        art = f"/api/library/v2/artwork/album/{r[4]}?size=thumb"
        secs = (r[2] or 0) / 1000 or None
        bitrate = int(f[3]) if f and f[3] else None
        if bitrate and bitrate > 10_000:
            bitrate //= 1000  # some scans store bps
        out.append({
            "item_type": "track", "item_id": str(tid), "title": r[1] or "Track",
            "subtitle": " · ".join(x for x in (r[6], r[5]) if x),
            "artwork": art, "duration_s": int(secs) if secs else None,
            "size_bytes": int(f[2] or 0) if f else 0,
            "source": {"container": (f[4] or _ext(f[1])) if f else None, "video_codec": None, "height": None,
                       "hdr": None, "audio_codec": (f[4] or _ext(f[1])) if f else None,
                       "bitrate_kbps": bitrate or _kbps(f[2] if f else None, secs)},
            "group": dict(group) if group else {"type": "album", "id": str(r[4]), "title": r[5] or "Album",
                                                "subtitle": r[7] or "", "artwork": art},
            "_has_file": f is not None,
        })
    return out


def _album(album_id: int) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    db = _music_db()
    with db._get_connection() as conn:
        a = conn.execute(
            "SELECT al.title, ar.name, al.year FROM lib2_albums al "
            "LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id WHERE al.id = ?", (album_id,)).fetchone()
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM lib2_tracks WHERE album_id = ? "
            "ORDER BY COALESCE(disc_number, 1), COALESCE(track_number, 0), id", (album_id,))]
    if not a:
        raise Refused(404, "not_found", "That album isn't in your library.")
    header = {"title": a[0] or "Album", "subtitle": " · ".join(str(x) for x in (a[1], a[2]) if x),
              "artwork": f"/api/library/v2/artwork/album/{album_id}?size=thumb"}
    return header, _tracks(ids)


def _artist(artist_id: int) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    db = _music_db()
    with db._get_connection() as conn:
        a = conn.execute("SELECT name FROM lib2_artists WHERE id = ?", (artist_id,)).fetchone()
        ids = [r[0] for r in conn.execute(
            "SELECT t.id FROM lib2_albums al JOIN lib2_tracks t ON t.album_id = al.id "
            "WHERE al.primary_artist_id = ? "
            "ORDER BY COALESCE(al.year, 0) DESC, al.id, COALESCE(t.disc_number, 1), "
            "COALESCE(t.track_number, 0) LIMIT ?", (artist_id, ARTIST_MAX))]
    if not a:
        raise Refused(404, "not_found", "That artist isn't in your library.")
    header = {"title": a[0] or "Artist", "subtitle": "Every album",
              "artwork": f"/api/library/v2/artwork/artist/{artist_id}?size=thumb"}
    return header, _tracks(ids)


def playlist_tracks(playlist_key: str) -> Tuple[Dict[str, Any], List[int]]:
    """a player playlist key (user:<id>, mirror:<id>, discover:<key>) as its
    header and the library tracks this profile can play, in order"""
    from core.player.playable import current_profile_id
    pid = current_profile_id()
    kind, _, ref = str(playlist_key or "").partition(":")
    scope, hide = _music_scope()
    db = _music_db()
    if kind in ("user", "mirror") and ref.isdigit():
        from core.player import playlists
        out = playlists.detail(db, pid, int(ref), scope=scope, hide_explicit=hide)
    elif kind == "discover" and ref:
        from core.permissions import can
        if not can(None, "music.discover"):
            raise Refused(403, "not_allowed", "That isn't part of your profile.")
        from core.player import discovery
        out = discovery.detail(db, pid, ref, scope=scope, hide_explicit=hide)
    else:
        raise Refused(400, "bad_id", "That isn't a playlist.")
    if not out:
        raise Refused(404, "not_found", "No such playlist.")
    tiles = out.get("cover_tiles") or []
    art = out.get("image_url") or (tiles[0] if tiles else None)
    header = {"title": out.get("name") or out.get("title") or "Playlist",
              "subtitle": "Playlist", "artwork": art}
    ids = [s["track_id"] for s in out.get("songs") or [] if s.get("track_id") is not None]
    return header, ids


def _playlist(playlist_key: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    header, ids = playlist_tracks(playlist_key)
    group = {"type": "playlist", "id": playlist_key, **header}
    # a song twice in a playlist is one download
    seen, order = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            order.append(i)
    return header, _tracks(order, checked=False, group=group)


# ── audiobooks ───────────────────────────────────────────────────────────

def _book(key_: str) -> Optional[Dict[str, Any]]:
    from core.audiobook_database import get_audiobook_db
    row = get_audiobook_db().get_library_entry(key_)
    if not row:
        return None
    files = [p for p in (row.get("file_paths") or []) if isinstance(p, str)]
    art = f"/api/audiobooks/library/{key_}/cover"
    title = row.get("title") or "Book"
    secs = int(row.get("runtime_minutes") or 0) * 60 or None
    fmt = (row.get("audio_format") or _ext(files[0] if files else "") or None)
    return {
        "item_type": "audiobook", "item_id": key_, "title": title,
        "subtitle": row.get("author") or "", "artwork": art, "duration_s": secs,
        "size_bytes": int(row.get("size_bytes") or 0),
        "source": {"container": fmt, "video_codec": None, "height": None, "hdr": None,
                   "audio_codec": fmt, "bitrate_kbps": _kbps(row.get("size_bytes"), secs)},
        "group": {"type": "audiobook", "id": key_, "title": title, "subtitle": row.get("author") or "",
                  "artwork": art},
        "parts": len(files), "_has_file": bool(files),
    }


# ── podcasts ─────────────────────────────────────────────────────────────

def episode_key(ep: Dict[str, Any]) -> str:
    """the player's key for an episode (webui -lib/rails episodeKey)"""
    return str(ep.get("guid") or ep.get("enclosure_url") or ep.get("title") or "")[:200]


def _podcast_sub(podcast_id: int) -> Dict[str, Any]:
    from core.permissions import can
    from core.player.playable import current_profile_id
    if not can(None, "podcasts.access"):
        raise Refused(403, "not_allowed", "That isn't part of your profile.")
    pid = current_profile_id()
    subs = _music_db().get_watchlist_podcasts(profile_id=pid) or []
    sub = next((p for p in subs if int(p.get("id") or 0) == int(podcast_id)), None)
    if not sub:
        raise Refused(404, "not_found", "You don't follow that podcast.")
    return sub


def feed(feed_url: str) -> Optional[Dict[str, Any]]:
    """a podcast's feed, from the podcast page's own cache when fresh"""
    try:
        from api import podcasts as P
    except Exception:  # noqa: BLE001
        return None
    hit = P._show_cache.get(feed_url)
    if hit and time.time() - hit["timestamp"] < P._SHOW_CACHE_TTL:
        return hit["show"]
    show = P.get_podcast_client().fetch_feed(feed_url)
    if show is None:
        return None
    data = P.show_to_dict(show, include_episodes=True)
    P._show_cache[feed_url] = {"timestamp": time.time(), "show": data}
    return data


def _episode_item(podcast_id: int, sub: Dict[str, Any], show: Dict[str, Any], ep: Dict[str, Any]) -> Dict[str, Any]:
    art = ep.get("artwork_url") or show.get("artwork_url") or sub.get("artwork_url")
    title = show.get("title") or sub.get("title") or "Podcast"
    secs = ep.get("duration_seconds")
    size = int(ep.get("enclosure_length") or 0)
    fmt = _ext(str(ep.get("enclosure_url") or "").split("?")[0]) or "mp3"
    return {
        "item_type": "podcast_episode", "item_id": f"{podcast_id}:{episode_key(ep)}",
        "title": ep.get("title") or "Episode", "subtitle": title, "artwork": art,
        "duration_s": int(secs) if secs else None, "size_bytes": size,
        "source": {"container": fmt, "video_codec": None, "height": None, "hdr": None, "audio_codec": fmt,
                   "bitrate_kbps": _kbps(size, secs)},
        "group": {"type": "podcast", "id": str(podcast_id), "title": title,
                  "subtitle": sub.get("author") or "", "artwork": show.get("artwork_url") or sub.get("artwork_url")},
        "published": ep.get("pub_date"), "_has_file": bool(ep.get("enclosure_url")),
        "_enclosure": ep.get("enclosure_url"),
    }


def _podcast(podcast_id: int, count: int = PODCAST_DEFAULT) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    sub = _podcast_sub(podcast_id)
    show = feed(sub.get("feed_url") or "") or {}
    eps = sorted(show.get("episodes") or [], key=lambda e: str(e.get("pub_date") or ""), reverse=True)
    items = [_episode_item(podcast_id, sub, show, e) for e in eps if e.get("enclosure_url")]
    header = {"title": show.get("title") or sub.get("title") or "Podcast",
              "subtitle": f"Newest {count}", "artwork": show.get("artwork_url") or sub.get("artwork_url")}
    return header, items[:max(1, min(PODCAST_MAX, int(count)))]


def podcast_episode(item_id: str) -> Dict[str, Any]:
    """one episode by its offline id, with its enclosure url (server only)"""
    pod, _, ep_key = str(item_id or "").partition(":")
    if not pod.isdigit() or not ep_key:
        raise Refused(400, "bad_id", "That isn't a podcast episode.")
    sub = _podcast_sub(int(pod))
    show = feed(sub.get("feed_url") or "") or {}
    for e in show.get("episodes") or []:
        if episode_key(e) == ep_key and e.get("enclosure_url"):
            return _episode_item(int(pod), sub, show, e)
    raise Refused(404, "not_found", "That episode isn't in the feed any more.")


# ── groups ───────────────────────────────────────────────────────────────

def expand(group_type: str, group_id: Any, *, count: int = PODCAST_DEFAULT) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """(header {title, subtitle, artwork}, items) for a group, as this
    profile sees it. raises Refused for a section it can't use or a thing
    that isn't there."""
    gtype = str(group_type or "").strip().lower()
    if gtype not in GROUP_TYPES:
        raise Refused(400, "bad_type", "That can't be downloaded.")
    gid = str(group_id or "").strip()
    if not gid or len(gid) > 400:
        raise Refused(400, "bad_id", "That isn't an id.")
    item_kind = GROUP_ITEM[gtype]
    if item_kind != "podcast_episode":
        check(item_kind)
    if gtype in ("movie",):
        items = [m for m in _movies([_int(gid)], _cap()) if m["_has_file"]]
        if not items:
            raise Refused(404, "not_found", "That movie has no file to download.")
        m = items[0]
        return {"title": m["title"], "subtitle": m["subtitle"], "artwork": m["artwork"]}, items
    if gtype == "episode":
        items = _episodes("e.id = ?", [_int(gid)], _cap())
        if not items:
            raise Refused(404, "not_found", "That episode has no file to download.")
        e = items[0]
        return {"title": e["title"], "subtitle": e["subtitle"], "artwork": e["artwork"]}, items
    if gtype == "season":
        show, _, season = gid.partition(":")
        sid, n = _int(show), _int(season)
        header = _show_header(sid, n)
        return header, _episodes("e.show_id = ? AND e.season_number = ?", [sid, n], _cap())
    if gtype == "show":
        sid = _int(gid)
        header = _show_header(sid)
        return header, _episodes("e.show_id = ?", [sid], _cap())
    if gtype == "youtube":
        items = _youtube([gid])
        if not items:
            raise Refused(404, "not_found", "That video isn't downloaded on the server.")
        y = items[0]
        return {"title": y["title"], "subtitle": y["subtitle"], "artwork": y["artwork"]}, items
    if gtype == "home":
        items = _home([_int(gid)])
        if not items:
            raise Refused(404, "not_found", "That home video isn't in your library.")
        h = items[0]
        return {"title": h["title"], "subtitle": h["subtitle"], "artwork": h["artwork"]}, items
    if gtype == "home_album":
        items = _home([], album=gid)
        if not items:
            raise Refused(404, "not_found", "That album has nothing to download.")
        g = items[0]["group"]
        return {"title": g["title"], "subtitle": f"{len(items)} home videos", "artwork": items[-1]["artwork"]}, items
    if gtype == "track":
        items = _tracks([_int(gid)])
        if not items:
            raise Refused(404, "not_found", "That track isn't in your library.")
        t = items[0]
        return {"title": t["title"], "subtitle": t["subtitle"], "artwork": t["artwork"]}, items
    if gtype == "album":
        return _album(_int(gid))
    if gtype == "artist":
        return _artist(_int(gid))
    if gtype == "playlist":
        return _playlist(gid)
    if gtype == "audiobook":
        book = _book(gid)
        if not book or not book["_has_file"]:
            raise Refused(404, "not_found", "That book isn't in your library.")
        return {"title": book["title"], "subtitle": book["subtitle"], "artwork": book["artwork"]}, [book]
    return _podcast(_int(gid), count)


def _int(text: Any) -> int:
    try:
        return int(str(text))
    except (TypeError, ValueError):
        raise Refused(400, "bad_id", "That isn't an id.") from None


def describe(items: Iterable[Tuple[str, str]]) -> Dict[str, Dict[str, Any]]:
    """titles, art and groups for items a device holds: {"type:id": item}.
    unchecked (they're already on the device); one that can't be found is
    left out, and the caller keeps what it had."""
    by: Dict[str, List[str]] = {}
    for t, i in items:
        by.setdefault(t, []).append(str(i))
    out: Dict[str, Dict[str, Any]] = {}

    def add(rows):
        for r in rows:
            out[key(r["item_type"], r["item_id"])] = r

    try:
        if by.get("movie"):
            add(_movies([int(i) for i in by["movie"] if i.isdigit()], checked=False))
        if by.get("episode"):
            ids = [int(i) for i in by["episode"] if i.isdigit()]
            for chunk in _chunks(ids):
                add(_episodes(f"e.id IN ({','.join('?' * len(chunk))})", chunk, checked=False,
                              owned_only=False))
        if by.get("youtube"):
            add(_youtube(by["youtube"]))
        if by.get("home"):
            add(_home([i for i in by["home"] if i.isdigit()], checked=False))
        if by.get("track"):
            add(_tracks([int(i) for i in by["track"] if i.isdigit()], checked=False))
        for b in by.get("audiobook") or []:
            book = _book(b)
            if book:
                add([book])
        for e in by.get("podcast_episode") or []:
            try:
                add([podcast_episode(e)])
            except Refused:
                continue
    except Exception:  # noqa: BLE001 - names are nice to have, never a failed report
        logger.warning("offline: describing device items failed", exc_info=True)
    return out


# ── qualities and sizes ──────────────────────────────────────────────────

def _video_label(items: List[Dict[str, Any]]) -> str:
    heights = [i["source"]["height"] for i in items if i["source"].get("height")]
    top = max(heights) if heights else None
    hdr = any(i["source"].get("hdr") for i in items)
    if top and top >= 2000:
        res = "4K"
    elif top:
        res = f"{top}p"
    else:
        res = "Full quality"
    return f"{res}{' HDR' if hdr else ''}, as it is on the server"


def _audio_label(items: List[Dict[str, Any]]) -> str:
    codecs = {str(i["source"].get("audio_codec") or "").upper() for i in items if i["source"].get("audio_codec")}
    rates = [i["source"]["bitrate_kbps"] for i in items if i["source"].get("bitrate_kbps")]
    if len(codecs) == 1:
        codec = codecs.pop()
        if codec in ("FLAC", "ALAC", "WAV", "AIFF"):
            return f"{codec}, lossless, as it is"
        if rates:
            return f"{codec} {max(rates)}k, as it is"
        return f"{codec}, as it is"
    return "As it is on the server"


def estimate(item: Dict[str, Any], quality: str) -> int:
    """bytes for one item in a quality: the real size for original, else
    length times the target bitrate, never more than the original"""
    size = int(item.get("size_bytes") or 0)
    if quality == "original":
        return size
    secs = float(item.get("duration_s") or 0)
    if item["item_type"] in VIDEO_KINDS:
        bps = VIDEO_QUALITIES[quality][1] + VIDEO_AUDIO_BPS
    else:
        bps = AUDIO_QUALITIES[quality] * 1000
    guess = int(secs * bps / 8) if secs else 0
    return min(guess, size) if size and guess else (guess or size)


def offered(kind: str, items: List[Dict[str, Any]]) -> List[str]:
    """the qualities worth offering: never above the source"""
    if kind == "video":
        heights = [i["source"].get("height") for i in items]
        unknown = any(h is None for h in heights)
        top = max([h for h in heights if h] or [0])
        return ["original"] + [q for q in ("high", "phone", "small")
                               if unknown or top >= VIDEO_QUALITIES[q][0]]
    rates = [i["source"].get("bitrate_kbps") for i in items]
    unknown = any(r is None for r in rates)
    top = max([r for r in rates if r] or [0])
    return ["original"] + [q for q in ("320", "256", "128") if unknown or top > AUDIO_QUALITIES[q]]


SUBS = {
    "high": ("High", "1080p, converted by the server"),
    "phone": ("Phone", "720p, good on a phone screen"),
    "small": ("Small", "480p, for long trips on little space"),
    "320": ("320 kbps", "Best, converted by the server"),
    "256": ("256 kbps", "Very good, a little smaller"),
    "128": ("128 kbps", "Small, for long trips"),
}


def qualities(kind: str, items: List[Dict[str, Any]], ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    out = []
    for q in (ids if ids is not None else offered(kind, items)):
        if q == "original":
            label, sub = "Original", _video_label(items) if kind == "video" else _audio_label(items)
        else:
            label, sub = SUBS[q]
        per = {key(i["item_type"], i["item_id"]): estimate(i, q) for i in items}
        out.append({"id": q, "label": label, "sub": sub, "total_bytes": sum(per.values()), "per_item_bytes": per})
    return out


def valid_quality(item_type: str, quality: str) -> bool:
    return quality in (VIDEO_ORDER if item_type in VIDEO_KINDS else AUDIO_ORDER)


def public(item: Dict[str, Any]) -> Dict[str, Any]:
    """an item as a client sees it (no private fields)"""
    return {k: v for k, v in item.items() if not k.startswith("_")}
