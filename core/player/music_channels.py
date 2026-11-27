"""the guide's music channels: one per genre you have plenty of, and one per
vibe, from the moods the library already knows (AudioDB's, on the album or
its artist).

owned tracks only, in the caller's library scope: a channel never airs a
song that isn't on disk.

it has to stay quick on a big library (300k tracks), so it works by album:
one grouped pass gives every owned album with its genres, mood and how
many owned tracks it has; the channels are made from those; then only a
spread sample of each channel's albums has its tracks read. the sample is
stable (by id), so the same channel airs the same songs today on every
screen; the guide shuffles them into a day.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List

PER_CHANNEL = 240
ALBUMS_PER_CHANNEL = 60
TOP_GENRES = 30
MIN_TRACKS = 20

# moods (lowercase) folded into the vibes a guide shows. anything not listed
# simply isn't on a vibe channel.
VIBES: Dict[str, tuple] = {
    "Feel good": ("happy", "cheerful", "good natured", "fun", "upbeat", "joyous", "playful",
                  "uplifting", "carefree", "sunny"),
    "High energy": ("energetic", "excitable", "intense", "powerful", "rousing", "driving",
                    "exuberant", "boisterous", "fiery"),
    "Chill": ("relaxed", "dreamy", "calm", "mellow", "laid-back", "laid back", "soothing", "peaceful",
              "gentle", "reflective", "meditative", "smooth", "ethereal"),
    "Dark & gritty": ("gritty", "confrontational", "angry", "aggressive", "dark", "menacing",
                      "brooding", "hostile", "rebellious", "volatile"),
    "Romantic": ("romantic", "sensual", "passionate", "sexy", "tender", "warm", "intimate",
                 "sweet", "affectionate"),
    "Melancholy": ("sad", "melancholy", "bittersweet", "somber", "sombre", "wistful", "lonely",
                   "yearning", "gloomy", "rainy"),
}
_MOOD_TO_VIBE = {mood: vibe for vibe, moods in VIBES.items() for mood in moods}


def _genres(raw: Any) -> List[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    return [str(g).strip() for g in value if str(g).strip()] if isinstance(value, list) else []


def genre_key(name: str) -> str:
    """one channel for one genre however it's spelled: 'Rap/Hip Hop',
    'Hip-Hop/Rap' and 'hip hop & rap' are the same words in another order."""
    words = re.split(r"[^a-z0-9]+", name.lower())
    return " ".join(sorted(w for w in words if w))


def _spread(entity_id: int) -> int:
    """a stable scatter of ids (knuth's multiplicative hash), so a sample is
    spread across the library, not its oldest corner."""
    return (int(entity_id) * 2654435761) & 0xFFFFFFFF


def vibe_for(mood: Any) -> str | None:
    if not mood:
        return None
    for part in str(mood).replace("/", ",").split(","):
        vibe = _MOOD_TO_VIBE.get(part.strip().lower())
        if vibe:
            return vibe
    return None


def plan(albums: Iterable[tuple], track_moods: Iterable[tuple] = (),
         top_genres: int = TOP_GENRES, min_tracks: int = MIN_TRACKS) -> List[Dict[str, Any]]:
    """albums: (album_id, album_genres, artist_genres, mood, owned_tracks),
    the mood being the album's or else its artist's. track_moods:
    (album_id, track mood, owned tracks with it): a track's own mood comes
    first, so an album with them takes its vibes from its tracks.
    the channels and which albums feed each, without a single track read.
    pure, so the grouping is tested apart from sql."""
    vibes_by_album: Dict[Any, Dict[str, int]] = {}
    for album_id, mood, count in track_moods:
        vibe = vibe_for(mood)
        if vibe:
            votes = vibes_by_album.setdefault(album_id, {})
            votes[vibe] = votes.get(vibe, 0) + int(count or 0)
    by_genre: Dict[str, Dict[str, Any]] = {}
    by_vibe: Dict[str, Dict[str, Any]] = {}
    for album_id, album_genres, artist_genres, mood, tracks in albums:
        tracks = int(tracks or 0)
        if tracks <= 0:
            continue
        for g in (_genres(album_genres) or _genres(artist_genres))[:2]:
            key = genre_key(g)
            if not key:
                continue
            entry = by_genre.setdefault(key, {"albums": [], "count": 0, "names": {}})
            entry["albums"].append(album_id)
            entry["count"] += tracks
            entry["names"][g] = entry["names"].get(g, 0) + tracks
        votes = vibes_by_album.get(album_id)
        if votes is None:
            fallback = vibe_for(mood)
            votes = {fallback: tracks} if fallback else {}
        for vibe, count in votes.items():
            entry = by_vibe.setdefault(vibe, {"albums": [], "count": 0})
            entry["albums"].append(album_id)
            entry["count"] += count

    def sample(ids: List[int]) -> List[int]:
        return sorted(ids, key=_spread)[:ALBUMS_PER_CHANNEL]

    out: List[Dict[str, Any]] = []
    ranked = sorted(by_genre.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    for key, entry in ranked[:top_genres]:
        if entry["count"] < min_tracks:
            continue
        name = max(entry["names"].items(), key=lambda kv: kv[1])[0]
        out.append({"id": "music-genre-" + key.replace(" ", "-"), "name": name, "kind": "genre",
                    "count": entry["count"], "albums": sample(entry["albums"])})
    for vibe in VIBES:
        entry = by_vibe.get(vibe)
        if entry and entry["count"] >= min_tracks:
            out.append({"id": f"music-vibe-{vibe.lower().replace(' & ', '-').replace(' ', '-')}",
                        "name": vibe, "kind": "vibe", "count": entry["count"],
                        "albums": sample(entry["albums"])})
    return out


def fill(channels: List[Dict[str, Any]], tracks_by_album: Dict[int, List[list]],
         per_channel: int = PER_CHANNEL,
         track_vibe: Dict[Any, str] | None = None) -> List[Dict[str, Any]]:
    """each planned channel with a stable sample of its albums' tracks. on a
    vibe channel, a track with a mood of its own airs only on its vibe."""
    track_vibe = track_vibe or {}
    out = []
    for c in channels:
        pool = [t for album_id in c["albums"] for t in tracks_by_album.get(album_id, [])
                if c["kind"] != "vibe" or track_vibe.get(t[0], c["name"]) == c["name"]]
        picked = sorted(pool, key=lambda t: _spread(t[0]))[:per_channel]
        if picked:
            out.append({"id": c["id"], "name": c["name"], "kind": c["kind"],
                        "count": c["count"], "tracks": picked})
    return out


def read(conn, *, hide_explicit: bool = False) -> List[Dict[str, Any]]:
    """owned tracks in the caller's library scope, grouped into channels.

    ``hide_explicit``: the profile hides explicit music. its channels were
    built from every track all the same, so a kids profile's guide listed
    songs that were then refused when their turn came."""
    from core.library2.sql_util import owned_sql, scope_visibility_sql

    where = [owned_sql("track", "t")]
    visible = scope_visibility_sql("track", "t")
    if visible:
        where.append(visible)
    owned = " AND ".join(where)
    clean = ""
    if hide_explicit:
        from core.subsonic.library import _has_overrides, track_explicit_sql
        clean = f" AND NOT {track_explicit_sql('t', 'al', _has_overrides(conn))}"
    albums = conn.execute(f"""
        SELECT al.id, al.genres, ar.genres,
               COALESCE(NULLIF(al.mood, ''), NULLIF(ar.mood, '')), COUNT(t.id)
        FROM lib2_tracks t
        JOIN lib2_albums al ON al.id = t.album_id
        LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
        WHERE {owned}{clean}
        GROUP BY al.id
    """).fetchall()  # noqa: S608 - the fragments are the library's own sql builders
    track_moods = conn.execute(f"""
        SELECT t.album_id, t.mood, COUNT(*)
        FROM lib2_tracks t
        WHERE t.mood IS NOT NULL AND t.mood != '' AND {owned}
        GROUP BY t.album_id, t.mood
    """).fetchall()  # noqa: S608
    channels = plan([tuple(r) for r in albums], [tuple(r) for r in track_moods])
    # #2332: out of season, no christmas channel and no carols on the others
    from core.holiday_music import genres_say_holiday, policy
    holiday = policy()
    season = None if holiday.allows_holiday else holiday
    if season is not None:
        channels = [c for c in channels if not genres_say_holiday([c["name"]])]
    wanted = sorted({a for c in channels for a in c["albums"]})
    tracks_by_album: Dict[int, List[list]] = {}
    track_vibe: Dict[Any, str] = {}
    for start in range(0, len(wanted), 500):
        chunk = wanted[start:start + 500]
        rows = conn.execute(f"""
            SELECT t.id, t.title, COALESCE(ar.name, ''), t.duration, t.album_id, t.mood,
                   al.title, al.genres, ar.genres
            FROM lib2_tracks t
            JOIN lib2_albums al ON al.id = t.album_id
            LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
            WHERE t.album_id IN ({",".join("?" for _ in chunk)}) AND {owned}{clean}
        """, chunk).fetchall()  # noqa: S608
        for r in rows:
            seconds = int(r[3] / 1000) if r[3] else 0
            if season is not None and season.is_holiday(
                    {"title": r[1], "album": r[6], "artist": r[2], "genres": [r[7], r[8], r[5]]}):
                continue
            if seconds >= 60:
                tracks_by_album.setdefault(r[4], []).append([r[0], r[1] or "", r[2], seconds, r[4]])
                vibe = vibe_for(r[5])
                if vibe:
                    track_vibe[r[0]] = vibe
    return fill(channels, tracks_by_album, track_vibe=track_vibe)
