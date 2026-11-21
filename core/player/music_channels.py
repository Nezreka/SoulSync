"""the guide's music channels: one per genre you have plenty of, and one per
vibe, from the moods the library already knows (AudioDB's on the artist,
album or track, the most specific one winning).

owned tracks only, in the caller's library scope: a channel never airs a
song that isn't on disk. each channel carries a sample of its tracks (a
stable one, so the same channel airs the same songs today on every screen);
the guide shuffles them into a day.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

PER_CHANNEL = 240
TOP_GENRES = 16
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


def _spread(track_id: int) -> int:
    """a stable scatter of track ids (knuth's multiplicative hash), so a
    channel's sample is spread across the library, not its oldest corner."""
    return (int(track_id) * 2654435761) & 0xFFFFFFFF


def vibe_for(mood: Any) -> str | None:
    if not mood:
        return None
    for part in str(mood).replace("/", ",").split(","):
        vibe = _MOOD_TO_VIBE.get(part.strip().lower())
        if vibe:
            return vibe
    return None


def build(rows, per_channel: int = PER_CHANNEL, top_genres: int = TOP_GENRES,
          min_tracks: int = MIN_TRACKS) -> List[Dict[str, Any]]:
    """rows: (track_id, title, artist, duration_ms, album_id, album_genres,
    artist_genres, mood). pure, so the grouping is tested apart from sql."""
    by_genre: Dict[str, List[list]] = {}
    display: Dict[str, Dict[str, int]] = {}
    by_vibe: Dict[str, List[list]] = {}
    for track_id, title, artist, duration, album_id, album_genres, artist_genres, mood in rows:
        seconds = int(duration / 1000) if duration else 0
        if seconds < 60:
            continue
        track = [track_id, title or "", artist or "", seconds, album_id]
        genres = _genres(album_genres) or _genres(artist_genres)
        for g in genres[:2]:
            key = genre_key(g)
            if not key:
                continue
            by_genre.setdefault(key, []).append(track)
            names = display.setdefault(key, {})
            names[g] = names.get(g, 0) + 1
        vibe = vibe_for(mood)
        if vibe:
            by_vibe.setdefault(vibe, []).append(track)

    def sample(tracks: List[list]) -> List[list]:
        return sorted(tracks, key=lambda t: _spread(t[0]))[:per_channel]

    out: List[Dict[str, Any]] = []
    ranked = sorted(by_genre.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for key, tracks in ranked[:top_genres]:
        if len(tracks) < min_tracks:
            continue
        name = max(display[key].items(), key=lambda kv: kv[1])[0]
        out.append({"id": "music-genre-" + key.replace(" ", "-"), "name": name, "kind": "genre",
                    "count": len(tracks), "tracks": sample(tracks)})
    for vibe in VIBES:
        tracks = by_vibe.get(vibe, [])
        if len(tracks) >= min_tracks:
            out.append({"id": f"music-vibe-{vibe.lower().replace(' & ', '-').replace(' ', '-')}",
                        "name": vibe, "kind": "vibe", "count": len(tracks),
                        "tracks": sample(tracks)})
    return out


def read(conn) -> List[Dict[str, Any]]:
    """owned tracks in the caller's library scope, grouped into channels."""
    from core.library2.sql_util import owned_sql, scope_visibility_sql

    where = [owned_sql("track", "t")]
    visible = scope_visibility_sql("track", "t")
    if visible:
        where.append(visible)
    rows = conn.execute(f"""
        SELECT t.id, t.title, COALESCE(ar.name, ''), t.duration, al.id, al.genres, ar.genres,
               COALESCE(NULLIF(t.mood, ''), NULLIF(al.mood, ''), NULLIF(ar.mood, ''))
        FROM lib2_tracks t
        JOIN lib2_albums al ON al.id = t.album_id
        LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
        WHERE {' AND '.join(where)}
    """).fetchall()  # noqa: S608 - the fragments are the library's own sql builders
    return build([tuple(r) for r in rows])
