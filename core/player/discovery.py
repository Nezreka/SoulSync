"""discovery playlists in the player: what the discover page makes for a
profile (flow, daily mixes, on repeat, moods, fresh tape, the archives, the
listening mix, the season's mix, hidden gems and the other stored picks,
listenbrainz), as playlists the player can open.

listing reads stored rows only. the generators that make these run on the
discover side (its warmer, its scans); nothing here rebuilds them, and
nothing here calls out to the network. flow and the on repeat mixes are
read straight off listening history, so they're made when one is opened.

a discovery song is a song by name (or a library track for the owned
mixes). each one goes through the player's playlist matching
(core.player.playlists._matched), so it plays the same library track a
mirrored playlist would, or says why it can't. a song you don't have can go
on the wishlist through the manager's own add (core.wishlist.routes).
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from utils.logging_config import get_logger

logger = get_logger("player.discovery")

SECTIONS: Tuple[Tuple[str, str], ...] = (
    ("made", "Made for you"),
    ("fresh", "New for you"),
    ("picks", "Picks"),
    ("listenbrainz", "ListenBrainz"),
)

KEY_RE = re.compile(r"^[a-z0-9_-]{1,80}$")

_PROVIDERS = ("spotify", "deezer", "itunes", "musicbrainz", "discogs", "hydrabase", "jiosaavn", "bandcamp")

# personalized_playlists kinds the other sections already show from their own store
_PICKS_ELSEWHERE = {"fresh_tape", "archives", "listening_mix", "seasonal_mix", "daily_mix", "holiday_mix"}
_PICKS_ORDER = ("hidden_gems", "popular_picks", "discovery_shuffle", "time_machine", "genre_playlist")
_PICKS_BLURB = {
    "hidden_gems": "Deeper cuts you might have missed",
    "popular_picks": "Popular tracks from artists you love",
    "discovery_shuffle": "A shuffle through your discovery pool",
    "time_machine": "Classics from one decade",
    "genre_playlist": "One genre, from your discovery pool",
}
_LB_BLURB = {
    "created_for": "Made for you by ListenBrainz",
    "user": "Your ListenBrainz playlist",
    "collaborative": "A collaborative ListenBrainz playlist",
    "lastfm_radio": "Last.fm radio",
}


# ── track shapes ─────────────────────────────────────────────────────────

def _json(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _first_url(images: Any) -> Optional[str]:
    if isinstance(images, list):
        for im in images:
            url = im.get("url") if isinstance(im, dict) else im if isinstance(im, str) else None
            if url:
                return str(url)
    return None


def norm_track(t: Dict[str, Any]) -> Dict[str, Any]:
    """title, artist, album, cover and length off any discovery row. the
    same fallbacks the discover page's normalizeTrack uses"""
    td = _json(t.get("track_data_json"))
    td = td if isinstance(td, dict) else t
    artists = td.get("artists")
    a0 = artists[0] if isinstance(artists, list) and artists else None
    a0_name = (a0.get("name") if isinstance(a0, dict) else a0 if isinstance(a0, str) else None)
    album = td.get("album")
    album_name = album.get("name") if isinstance(album, dict) else album if isinstance(album, str) else None
    cover = (_first_url(album.get("images")) if isinstance(album, dict) else None)
    if not cover and isinstance(t.get("album"), dict):
        cover = _first_url(t["album"].get("images"))
    if not cover:
        cover = (
            t.get("album_cover_url")
            or t.get("image_url")
            or t.get("cover_url")
            or td.get("album_cover_url")
            or td.get("image_url")
            or td.get("cover_url")
        )
    if not cover:
        aid = t.get("album_id") or td.get("album_id")
        try:
            if aid and int(aid) > 0:
                cover = f"/api/library/v2/artwork/album/{int(aid)}?size=thumb"
        except (TypeError, ValueError):
            pass
    return {
        "title": td.get("name") or td.get("track_name") or td.get("title") or t.get("track_name") or t.get("name") or "",
        "artist": a0_name or td.get("artist_name") or t.get("artist_name")
                  or (td.get("artist") if isinstance(td.get("artist"), str) else "") or "",
        "album": album_name or td.get("album_name") or t.get("album_name") or "",
        "image_url": cover or None,
        "duration_ms": int(td.get("duration_ms") or t.get("duration_ms") or 0),
    }


def provider_id(t: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """(provider, id) the wishlist keys this song by, or (None, None)"""
    td = _json(t.get("track_data_json"))
    td = td if isinstance(td, dict) else {}
    src = str(t.get("source") or t.get("provider") or "").strip().lower()
    if src in _PROVIDERS:
        tid = t.get(f"{src}_track_id") or t.get("track_id") or td.get("id")
        if tid:
            return src, str(tid)
    for src in ("spotify", "deezer", "itunes"):
        if t.get(f"{src}_track_id"):
            return src, str(t[f"{src}_track_id"])
    if t.get("mbid"):
        return "musicbrainz", str(t["mbid"])
    return None, None


def _match_row(t: Dict[str, Any]) -> Dict[str, Any]:
    """a discovery song as a playlist row the player's matching takes. a
    library track the source carries goes in as a manual match"""
    n = norm_track(t)
    extra = None
    try:
        lid = int(t.get("lib2_track_id") or 0) or None
    except (TypeError, ValueError):
        lid = None
    if lid:
        extra = {"library_track_id": lid, "library_track_id_kind": "lib2"}
    return {"track_name": n["title"], "artist_name": n["artist"], "album_name": n["album"], "extra_data": extra}


def _tiles(tracks: Sequence[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for t in tracks:
        url = norm_track(t)["image_url"]
        if url and url not in out:
            out.append(url)
            if len(out) == 4:
                break
    return out


def _card(key: str, name: str, blurb: str, section: str, tracks: Optional[List[Dict[str, Any]]], *,
          owned: Optional[bool] = None, updated_at: Any = None) -> Dict[str, Any]:
    """a playlist on a shelf. ``tracks`` None: made when it's opened (flow)"""
    rows = tracks or []
    return {
        "key": key, "name": name, "blurb": blurb or "", "section": section,
        "owned": bool(owned) if owned is not None else bool(rows) and all(bool(t.get("owned")) for t in rows),
        "track_count": None if tracks is None else len(rows),
        "cover_tiles": _tiles(rows),
        "updated_at": str(updated_at) if updated_at else None,
        "_tracks": tracks,
    }


# ── stored reads ─────────────────────────────────────────────────────────

def _curated_rows(db: Any, pid: int, like: str, *, exact: bool = False) -> List[Tuple[str, Any, Any]]:
    """(playlist_type, parsed json, curated_date) for this profile, newest first"""
    try:
        with db._get_connection() as conn:
            rows = conn.execute(
                "SELECT playlist_type, track_ids_json, curated_date FROM discovery_curated_playlists "
                f"WHERE profile_id = ? AND playlist_type {'=' if exact else 'LIKE'} ? "
                "ORDER BY curated_date DESC, id DESC",
                (int(pid), like)).fetchall()
    except Exception:  # noqa: BLE001 - a missing table is no playlists
        logger.debug("curated read failed for %s", like, exc_info=True)
        return []
    return [(r[0], _json(r[1]), r[2]) for r in rows]


def _from_pool(db: Any, pid: int, ids: Sequence[Any], source: Optional[str]) -> List[Dict[str, Any]]:
    """saved pool ids back to rows, in order (the pre-snapshot curated path)"""
    from core.discovery.curated_full import full_row_from_pool_track
    ids = [str(i) for i in ids if i]
    found: Dict[str, Any] = {}
    for src in ([source] if source else ["spotify", "deezer", "itunes"]):
        for k, v in (db.get_discovery_pool_tracks_by_ids(ids, src, profile_id=int(pid)) or {}).items():
            found.setdefault(str(k), v)
    return [full_row_from_pool_track(found[i]) for i in ids if i in found]


_CURATED = (
    ("release_radar", "Fresh Tape", "New releases from artists you follow"),
    ("discovery_weekly", "The Archives", "A weekly dig through artists across your library"),
)


def _curated(db: Any, pid: int) -> List[Dict[str, Any]]:
    """fresh tape and the archives: the full-row snapshot when there is one,
    else the saved ids looked up in the discovery pool"""
    out = []
    for base, name, blurb in _CURATED:
        rows = [r for r in _curated_rows(db, pid, base + "%")
                if r[0] == base or r[0].startswith(base + "_")]
        full = [r for r in rows if r[0].endswith("_full") and isinstance(r[1], list) and r[1]
                and isinstance(r[1][0], dict)]
        tracks, when = None, None
        if full:
            tracks, when = full[0][1], full[0][2]
        else:
            ids = [r for r in rows if not r[0].endswith("_full") and isinstance(r[1], list) and r[1]]
            if ids:
                kind, saved, when = ids[0]
                source = kind[len(base) + 1:] or None
                tracks = _from_pool(db, pid, saved, source)
        if tracks:
            out.append(_card(f"curated-{base}", name, blurb, "fresh", tracks, owned=False, updated_at=when))
    return out


def _listening_mix(db: Any, pid: int) -> List[Dict[str, Any]]:
    from core.discovery.listening_recommendations import RECS_TRACKS_KEY, read_recs_raw
    try:
        tracks = _json(read_recs_raw(db.get_metadata, RECS_TRACKS_KEY, pid))
    except Exception:  # noqa: BLE001
        logger.debug("listening mix read failed", exc_info=True)
        tracks = None
    if not isinstance(tracks, list) or not tracks:
        return []
    tracks = [t for t in tracks if isinstance(t, dict)]
    return [_card("listening-mix", "Your Listening Mix", "Artists you'd love, from what you play",
                  "fresh", tracks, owned=False)] if tracks else []


def _season_now(db: Any) -> Optional[str]:
    """the season the discover page shows now. its own rule, without the
    service's start-up (that makes tables and holds a metadata client)"""
    from core.seasonal_discovery import SeasonalDiscoveryService
    svc = object.__new__(SeasonalDiscoveryService)
    svc.database = db
    return svc.get_current_season()


def _seasonal(db: Any, pid: int) -> List[Dict[str, Any]]:
    from core.seasonal_discovery import SEASONAL_CONFIG
    try:
        season = _season_now(db)
    except Exception:  # noqa: BLE001
        logger.debug("season lookup failed", exc_info=True)
        return []
    if not season or season not in SEASONAL_CONFIG:
        return []
    try:
        with db._get_connection() as conn:
            # one row per source ('<season>_<source>'), the newest is the one in use
            row = conn.execute(
                "SELECT season_key, track_ids, curated_at FROM curated_seasonal_playlists "
                "WHERE substr(season_key, 1, ?) = ? ORDER BY curated_at DESC LIMIT 1",
                (len(season) + 1, season + "_")).fetchone()
            if not row:
                return []
            source = row[0][len(season) + 1:]
            ids = [str(i) for i in (_json(row[1]) or []) if i]
            found: Dict[str, Dict[str, Any]] = {}
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                for r in conn.execute(
                        f"SELECT spotify_track_id, track_name, artist_name, album_name, album_cover_url, "
                        f"duration_ms, track_data_json FROM seasonal_tracks WHERE source = ? "
                        f"AND spotify_track_id IN ({','.join('?' * len(chunk))})", [source, *chunk]).fetchall():
                    found.setdefault(str(r[0]), {
                        "track_id": r[0], "source": source, "track_name": r[1], "artist_name": r[2],
                        "album_name": r[3], "album_cover_url": r[4], "duration_ms": r[5],
                        "track_data_json": _json(r[6])})
    except Exception:  # noqa: BLE001 - no seasonal tables yet
        logger.debug("seasonal read failed", exc_info=True)
        return []
    rest = [i for i in ids if i not in found]
    if rest:
        for t in _from_pool(db, pid, rest, source):
            tid = t.get(f"{source}_track_id") or t.get("track_id")
            if tid:
                found.setdefault(str(tid), dict(t, source=source))
    tracks = [found[i] for i in ids if i in found]
    if not tracks:
        return []
    cfg = SEASONAL_CONFIG[season]
    return [_card(f"seasonal-{season}", f"{cfg.get('icon', '')} {cfg['name']} Mix".strip(),
                  cfg.get("description") or "", "fresh", tracks, owned=False, updated_at=row[2])]


def _stored_payload(db: Any, pid: int, key: str) -> Optional[Dict[str, Any]]:
    """a whole stored payload (daily mixes, moods), as last built. never
    rebuilt here: the discover side does that"""
    rows = _curated_rows(db, pid, key, exact=True)
    payload = rows[0][1] if rows else None
    return payload if isinstance(payload, dict) else None


def _daily(db: Any, pid: int) -> List[Dict[str, Any]]:
    from core.personalized.daily_mixes import CURATED_KEY
    payload = _stored_payload(db, pid, CURATED_KEY) or {}
    out = []
    for mix in payload.get("mixes") or []:
        m = re.match(r"^daily_mix_(\d+)$", str(mix.get("key") or ""))
        tracks = [t for t in mix.get("tracks") or [] if isinstance(t, dict)]
        if m and tracks:
            out.append(_card(f"daily-{m.group(1)}", mix.get("name") or f"Daily Mix {m.group(1)}",
                             mix.get("subtitle") or "", "made", tracks, updated_at=payload.get("generated_at")))
    return out


def _moods(db: Any, pid: int) -> List[Dict[str, Any]]:
    from core.discovery.moods import CURATED_KEY
    payload = _stored_payload(db, pid, CURATED_KEY) or {}
    out = []
    for mix in payload.get("mixes") or []:
        m = re.match(r"^mood_([a-z0-9_]+)$", str(mix.get("key") or ""))
        tracks = [t for t in mix.get("tracks") or [] if isinstance(t, dict)]
        if m and tracks:
            out.append(_card("mood-" + m.group(1).replace("_", "-"), mix.get("name") or m.group(1).title(),
                             mix.get("subtitle") or "", "made", tracks, owned=True,
                             updated_at=payload.get("generated_at")))
    return out


def _picks(db: Any, pid: int) -> List[Dict[str, Any]]:
    """hidden gems, popular picks, discovery shuffle and the other stored
    picks: the snapshots the sync side keeps. read only (the manager's GET
    route would make a row for a kind nobody asked for)"""
    from core.personalized.manager import PersonalizedPlaylistManager
    try:
        manager = PersonalizedPlaylistManager(db, None)
        records = [r for r in manager.list_playlists(int(pid)) if r.kind not in _PICKS_ELSEWHERE]
    except Exception:  # noqa: BLE001 - no personalized tables yet
        logger.debug("personalized playlists read failed", exc_info=True)
        return []
    order = {k: n for n, k in enumerate(_PICKS_ORDER)}
    records.sort(key=lambda r: (order.get(r.kind, len(order)), r.kind, r.variant))
    out = []
    for r in records:
        if not r.track_count:
            continue
        tracks = []
        for t in manager.get_playlist_tracks(r.id):
            tracks.append({
                "track_name": t.track_name, "artist_name": t.artist_name, "album_name": t.album_name,
                "album_cover_url": t.album_cover_url, "duration_ms": t.duration_ms,
                "spotify_track_id": t.spotify_track_id, "deezer_track_id": t.deezer_track_id,
                "itunes_track_id": t.itunes_track_id,
                "track_data_json": t.track_data_json if isinstance(t.track_data_json, dict) else None,
            })
        if tracks:
            out.append(_card(f"picks-{r.id}", r.name, _PICKS_BLURB.get(r.kind, ""), "picks", tracks,
                             owned=False, updated_at=r.last_generated_at))
    return out


def _listenbrainz(db: Any, pid: int, only: Optional[int] = None) -> List[Dict[str, Any]]:
    """the playlists listenbrainz (and last.fm radio) made for this profile,
    as cached. one never cached yet stays out: fetching it is the network"""
    try:
        with db._get_connection() as conn:
            sql = ("SELECT id, title, creator, playlist_type, last_updated FROM listenbrainz_playlists "
                   "WHERE profile_id = ?")
            args: List[Any] = [int(pid)]
            if only is not None:
                sql += " AND id = ?"
                args.append(int(only))
            lists = conn.execute(sql + " ORDER BY last_updated DESC, id DESC LIMIT 50", args).fetchall()
            ids = [int(r[0]) for r in lists]
            tracks: Dict[int, List[Dict[str, Any]]] = {}
            if ids:
                for r in conn.execute(
                        f"SELECT playlist_id, track_name, artist_name, album_name, duration_ms, recording_mbid, "
                        f"release_mbid, album_cover_url FROM listenbrainz_tracks "
                        f"WHERE playlist_id IN ({','.join('?' * len(ids))}) ORDER BY playlist_id, position",
                        ids).fetchall():
                    tracks.setdefault(int(r[0]), []).append({
                        "track_name": r[1], "artist_name": r[2], "album_name": r[3], "duration_ms": r[4],
                        "mbid": r[5], "release_mbid": r[6], "album_cover_url": r[7]})
    except Exception:  # noqa: BLE001 - no listenbrainz tables
        logger.debug("listenbrainz read failed", exc_info=True)
        return []
    out = []
    for lid, title, creator, kind, updated in lists:
        rows = tracks.get(int(lid)) or []
        if rows:
            blurb = _LB_BLURB.get(kind) or (f"By {creator}" if creator else "")
            out.append(_card(f"lb-{lid}", title, blurb, "listenbrainz", rows, owned=False, updated_at=updated))
    return out


# ── made from listening history (on open) ────────────────────────────────

def _has_history(db: Any, pid: int) -> bool:
    from core.listening_scope import listening_owner
    try:
        owner = listening_owner(db, pid)
        with db._get_connection() as conn:
            return conn.execute("SELECT 1 FROM listening_history WHERE profile_id = ? LIMIT 1",
                                (owner,)).fetchone() is not None
    except Exception:  # noqa: BLE001
        return False


def _owned_row(r: Dict[str, Any]) -> Dict[str, Any]:
    from core.personalized.for_you import mix_track
    out = mix_track(r)
    try:
        out["lib2_track_id"] = int(r["id"])
    except (KeyError, TypeError, ValueError):
        pass
    return out


def _flow_tracks(db: Any, pid: int) -> List[Dict[str, Any]]:
    from core.listening_scope import listening_owner
    from core.personalized.for_you import flow
    owner = listening_owner(db, pid)
    conn = db._get_connection()
    try:
        rows = flow(conn, owner, int(pid))
    finally:
        conn.close()
    return [_owned_row(r) for r in rows]


_FOR_YOU = {
    "on-repeat": ("On Repeat", "What you can't stop playing right now"),
    "repeat-rewind": ("Repeat Rewind", "Your old favourites, gone quiet lately"),
}
# the for-you mixes as they were last opened here, so the shelf can show them
_FOR_YOU_TTL = 1800.0
_for_you_seen: Dict[Any, Tuple[float, Dict[str, Any]]] = {}


def _forget_for_you() -> None:
    _for_you_seen.clear()


def _for_you_card(key: str, name: str, blurb: str, tracks: List[Dict[str, Any]], when: Any = None):
    return _card(key, name, blurb, "made", tracks, owned=True, updated_at=when)


def _for_you_shelf(db: Any, pid: int) -> List[Dict[str, Any]]:
    """on repeat, repeat rewind and blends, as the discover page last served
    them (its warmer keeps that answer per profile) or as last opened here.
    neither: they stay off the shelf rather than being built for it"""
    import sys
    cards: Dict[str, Dict[str, Any]] = {}
    dr = sys.modules.get("api.discover_routes")
    cache = getattr(dr, "_DISCOVER_SHELF_CACHE", None) if dr else None
    if isinstance(cache, dict):
        now = time.time()
        for k, v in list(cache.items()):
            try:
                if k[0] != "get_for_you_mixes" or k[2] != pid or v[0] <= now or v[1][1] != 200:
                    continue
                body = json.loads(v[1][0])
            except Exception:  # noqa: BLE001 - not a shape we know
                continue
            for mix in body.get("mixes") or []:
                key = str(mix.get("key") or "").replace("_", "-")
                tracks = [t for t in mix.get("tracks") or [] if isinstance(t, dict)]
                if key and tracks:
                    cards[key] = _for_you_card(key, mix.get("name") or key, mix.get("subtitle") or "", tracks)
            break
    dbk = _db_key(db)
    for (d, p, key), (at, card) in list(_for_you_seen.items()):
        if d == dbk and p == pid and time.monotonic() - at < _FOR_YOU_TTL:
            cards.setdefault(key, card)
    order = {"on-repeat": 0, "repeat-rewind": 1}
    return sorted(cards.values(), key=lambda c: (order.get(c["key"], 2), c["key"]))


def _for_you_open(db: Any, pid: int, key: str) -> Optional[Dict[str, Any]]:
    """one for-you mix, made now off listening history"""
    from core.listening_scope import listening_owner
    from core.personalized import for_you as F
    owner = listening_owner(db, pid)
    m = re.match(r"^blend-(\d+)$", key)
    if key not in _FOR_YOU and not m:
        return None
    if m:
        other = int(m.group(1))
        try:
            if other not in F.blend_partners(db, owner):
                return None
        except Exception:  # noqa: BLE001 - can't tell who shares: no blend
            return None
    conn = db._get_connection()
    try:
        if key == "on-repeat":
            rows = F.on_repeat(conn, owner)
        elif key == "repeat-rewind":
            rows = F.repeat_rewind(conn, owner)
        else:
            rows = F.blend(conn, owner, other)
        names = F._profile_names(conn) if m else {}
    finally:
        conn.close()
    if len(rows) < F.MIN_TRACKS:
        return None
    if m:
        them = names.get(other, "Someone")
        name, blurb = f"{names.get(int(pid), 'You')} + {them}", f"A blend of what you and {them} play"
    else:
        name, blurb = _FOR_YOU[key]
    card = _for_you_card(key, name, blurb, [_owned_row(r) for r in rows])
    if len(_for_you_seen) >= 64:
        _for_you_seen.pop(next(iter(_for_you_seen)))
    _for_you_seen[(_db_key(db), int(pid), key)] = (time.monotonic(), card)
    return card


def _db_key(db: Any) -> str:
    return str(getattr(db, "database_path", id(db)))


# ── the shelf ────────────────────────────────────────────────────────────

def _blocked(db: Any, pid: int):
    from core.discovery.blocked import BlockedArtists
    return BlockedArtists.load(db, pid)


# holiday music on purpose (#2332): these keep their christmas songs
_HOLIDAY_CARDS = ("holiday", "seasonal-christmas")


def _holiday(db: Any):
    """the holiday policy when it takes songs out today, else None"""
    try:
        from core.holiday_music import policy
        found = policy(db)
    except Exception:  # noqa: BLE001 - no policy, nothing taken out
        logger.debug("holiday policy unreadable", exc_info=True)
        return None
    return None if found.allows_holiday else found


def _without_blocked(cards: List[Dict[str, Any]], blocked, holiday: Any = None) -> List[Dict[str, Any]]:
    """the discover page's rule: a blocked artist's songs go, holiday songs
    out of season go (#2332), and a playlist whose songs all went goes with
    them"""
    if blocked.is_empty and holiday is None:
        return cards
    out = []
    for c in cards:
        tracks = c["_tracks"]
        if tracks is None:
            out.append(c)
            continue
        season = None if c["key"] in _HOLIDAY_CARDS else holiday
        kept = [t for t in tracks if not blocked.blocks_work(t)
                and not (season is not None and season.is_holiday(t))]
        if not kept:
            continue
        if len(kept) != len(tracks):
            c = _card(c["key"], c["name"], c["blurb"], c["section"], kept,
                      owned=c["owned"], updated_at=c["updated_at"])
        out.append(c)
    return out


def _holiday_card(db: Any, pid: int) -> List[Dict[str, Any]]:
    """#2332: in season, a mix of the holiday music you own"""
    from core.holiday_music import holiday_mix, policy
    if not policy(db).shows_holiday_mix:
        return []
    from core.personalized.for_you import _owned
    ids = [str(r["id"]) for r in holiday_mix(db)]
    if not ids:
        return []
    with db._get_connection() as conn:
        owned = _owned(conn, ids)
    tracks = [_owned_row(owned[i]) for i in ids if i in owned]
    if not tracks:
        return []
    return [_card("holiday", "Holiday", "The holiday music in your library", "made", tracks, owned=True)]


def _flow_card(db: Any, pid: int) -> List[Dict[str, Any]]:
    if not _has_history(db, pid):
        return []
    return [_card("flow", "Flow", "Your songs, nonstop", "made", None, owned=True)]


# every family in shelf order, and the key prefix each one answers for
_FAMILIES: Tuple[Tuple[str, Callable[[Any, int], List[Dict[str, Any]]]], ...] = (
    ("flow", _flow_card),
    ("holiday", _holiday_card),
    ("daily-", _daily),
    ("for-you", _for_you_shelf),
    ("mood-", _moods),
    ("curated-", _curated),
    ("listening-mix", _listening_mix),
    ("seasonal-", _seasonal),
    ("picks-", _picks),
    ("lb-", _listenbrainz),
)


def _public(card: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in card.items() if not k.startswith("_")}


def list_for(db: Any, profile_id: int) -> List[Dict[str, Any]]:
    """the profile's discovery playlists, in sections, empty ones left out.
    stored rows only: nothing is built or fetched for the shelf"""
    pid = int(profile_id)
    cards: List[Dict[str, Any]] = []
    for name, family in _FAMILIES:
        try:
            cards.extend(family(db, pid))
        except Exception:  # noqa: BLE001 - one broken source never empties the shelf
            logger.warning("discovery playlists: %s failed", name, exc_info=True)
    cards = _without_blocked(cards, _blocked(db, pid), _holiday(db))
    out = []
    for sid, label in SECTIONS:
        shelf = [_public(c) for c in cards if c["section"] == sid]
        if shelf:
            out.append({"id": sid, "label": label, "playlists": shelf})
    return out


def _find(db: Any, pid: int, key: str) -> Optional[Dict[str, Any]]:
    """one card with its songs, from its own family only"""
    if not KEY_RE.match(key or ""):
        return None
    if key == "flow":
        if not _has_history(db, pid):
            return None
        card = _card("flow", "Flow", "Your songs, nonstop", "made", _flow_tracks(db, pid), owned=True)
    elif key in _FOR_YOU or key.startswith("blend-"):
        card = _for_you_open(db, pid, key)
    elif key.startswith("lb-"):
        try:
            only = int(key[3:])
        except ValueError:
            return None
        card = next(iter(_listenbrainz(db, pid, only=only)), None)
    else:
        card = None
        for prefix, family in _FAMILIES:
            if prefix in ("flow", "for-you", "lb-") or not key.startswith(prefix):
                continue
            card = next((c for c in family(db, pid) if c["key"] == key), None)
            break
    if card is None:
        return None
    blocked = _blocked(db, pid)
    holiday = _holiday(db)
    if blocked.is_empty and holiday is None:
        return card
    kept = _without_blocked([card], blocked, holiday)
    if kept:
        return kept[0]
    # every song blocked: an empty playlist, not a missing one
    return _card(card["key"], card["name"], card["blurb"], card["section"], [],
                 owned=card["owned"], updated_at=card["updated_at"])


def _wanted_ids(db: Any, pid: int) -> set:
    """the provider ids on this profile's wishlist (a composite
    ``<track>::<album>`` key counts as its track)"""
    try:
        with db._get_connection() as conn:
            return {str(r[0]).split("::", 1)[0] for r in conn.execute(
                "SELECT spotify_track_id FROM wishlist_tracks WHERE profile_id = ?", (int(pid),)).fetchall()}
    except Exception:  # noqa: BLE001
        return set()


def _wish_profile(pid: int) -> int:
    try:
        from core.library_scope import acting_profile_id
        return int(acting_profile_id(pid) or pid)
    except Exception:  # noqa: BLE001 - outside a request
        return int(pid)


def _songs(db: Any, pid: int, card: Dict[str, Any], *, scope: Any, hide_explicit: bool):
    from core.player.playlists import _matched
    tracks = card["_tracks"] or []
    rows = [_match_row(t) for t in tracks]
    ids, ok, explicit, facts = _matched(db, rows, scope=scope, hide_explicit=hide_explicit,
                                        playlist_id=card["key"])
    wanted = _wanted_ids(db, _wish_profile(pid))
    songs = []
    for n, (t, tid) in enumerate(zip(tracks, ids, strict=True), start=1):
        playable = tid is not None and tid in ok
        f = facts.get(tid) if playable else None
        base = norm_track(t)
        _src, wid = provider_id(t)
        songs.append({
            "position": n,
            "title": (f or {}).get("title") or base["title"],
            "artist": (f or {}).get("artist") or base["artist"],
            "album": (f or {}).get("album") or base["album"],
            "duration_ms": (f or {}).get("duration_ms") or base["duration_ms"],
            "image_url": base["image_url"],
            "track_id": tid if playable else None,
            "album_id": (f or {}).get("album_id"),
            "artist_id": (f or {}).get("artist_id"),
            "unplayable": None if playable else ("explicit" if (n - 1) in explicit else "missing"),
            "wanted": bool(wid) and wid in wanted,
        })
    return songs


def detail(db: Any, profile_id: int, key: str, *, scope: Any, hide_explicit: bool) -> Optional[Dict[str, Any]]:
    """one discovery playlist with its songs, each with the library track it
    plays or why it can't. None: no such playlist for this profile"""
    pid = int(profile_id)
    card = _find(db, pid, key)
    if card is None:
        return None
    songs = _songs(db, pid, card, scope=scope, hide_explicit=hide_explicit)
    out = _public(card)
    out.update({"track_count": len(songs), "songs": songs, "hides_explicit": bool(hide_explicit),
                "playable_count": sum(1 for s in songs if s["track_id"] is not None)})
    return out


# ── getting the missing ones ─────────────────────────────────────────────

def _wish_payload(t: Dict[str, Any], provider: str, wid: str):
    """the manager's add takes (track, artist, album) the way its download
    dialog sends them (discoverTrackToSpotifyShape)"""
    td = _json(t.get("track_data_json"))
    n = norm_track(t)
    track = dict(td) if isinstance(td, dict) else {
        "name": n["title"], "artists": [{"name": n["artist"]}],
        "album": {"name": n["album"], "images": [{"url": n["image_url"]}] if n["image_url"] else []},
        "duration_ms": n["duration_ms"],
    }
    track["id"] = wid
    artists = [a if isinstance(a, dict) else {"name": str(a)} for a in (track.get("artists") or []) if a]
    track["artists"] = artists or [{"name": n["artist"]}]
    album = track.get("album")
    if not isinstance(album, dict):
        album = {"name": n["album"], "images": [{"url": n["image_url"]}] if n["image_url"] else []}
    album = dict(album)
    album.setdefault("name", n["album"])
    if provider == "musicbrainz" and t.get("release_mbid") and not album.get("id"):
        album["id"] = t["release_mbid"]
    track["album"] = album
    return track, track["artists"][0], album


def get_missing(db: Any, profile_id: int, key: str, *, scope: Any, hide_explicit: bool, runtime: Any,
                positions: Optional[Sequence[int]] = None) -> Optional[Dict[str, int]]:
    """the playlist's songs you don't have (all, or those positions) onto
    this profile's wishlist through the manager's own add. None: no such
    playlist. a song hidden as explicit is not missing, so it stays put"""
    from core.wishlist import routes as wishlist_routes
    pid = int(profile_id)
    card = _find(db, pid, key)
    if card is None:
        return None
    songs = _songs(db, pid, card, scope=scope, hide_explicit=hide_explicit)
    want = {int(p) for p in positions} if positions else None
    added = already = skipped = 0
    for t, s in zip(card["_tracks"] or [], songs, strict=True):
        if s["unplayable"] != "missing" or (want is not None and s["position"] not in want):
            continue
        if s["wanted"]:
            already += 1
            continue
        provider, wid = provider_id(t)
        if not wid:
            # nothing to key a wishlist row by (an owned mix's song that's gone)
            skipped += 1
            continue
        track, artist, album = _wish_payload(t, provider, wid)
        try:
            payload, status = wishlist_routes.add_album_track_to_wishlist(
                runtime, track=track, artist=artist, album=album, source_type="playlist",
                source_context={"playlist_name": card["name"], "metadata_source": provider,
                                "source_page": "player"})
        except Exception:  # noqa: BLE001 - one bad song never stops the rest
            logger.warning("wishlist add failed for %s", wid, exc_info=True)
            skipped += 1
            continue
        if status == 200 and payload.get("success") and not payload.get("skipped"):
            added += 1
        else:
            skipped += 1
    return {"added": added, "already": already, "skipped": skipped}
