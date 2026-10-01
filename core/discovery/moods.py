"""mood mixes: chill, focus, energy, feel good, late night. from your own library.

the discover page used to have a 'flow moods' bar whose pills only scrolled
the page. this is the real thing. last.fm tags sit on ~40% of boulder's
albums (chillout on 1,706 of them, ambient 2,259, downtempo 1,191) and
audiodb adds a mood on a few thousand more. an album whose top tags say
'chill' is a chill album; its owned tracks are chill candidates.

every track is one you own, so a mood mix plays straight away. picks lean on
what you actually play, are spread across artists and albums, and change
once a day (seeded by the date) rather than on every page load.
"""

import hashlib
import json
import math
import random
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from utils.logging_config import get_logger

logger = get_logger("discovery.moods")

CURATED_KEY = "mood_mixes_v1"
PAYLOAD_VERSION = 1
TTL_HOURS = 20
MIX_SIZE = 50
PER_ARTIST = 3
PER_ALBUM = 2
# a mood needs at least this many owned tracks to be worth a card
MIN_TRACKS = 15
# only an album's leading tags describe it; tag #9 of 10 is noise
TAG_DEPTH = 5

# each mood: the words that mean it, as last.fm tags or audiodb moods
MOODS: List[Dict[str, Any]] = [
    {
        "key": "chill",
        "name": "Chill",
        "blurb": "Downtempo, lo-fi and chillout",
        "tags": {"chill", "chillout", "chill out", "downtempo", "lo-fi", "lofi", "lo-fi beat",
                 "chillwave", "chillhop", "lounge", "psychill", "trip-hop", "trip hop",
                 "dream pop", "relaxing", "mellow", "smooth"},
        "moods": {"relaxed", "dreamy", "calm", "mellow", "laid back"},
    },
    {
        "key": "focus",
        "name": "Focus",
        "blurb": "Instrumental, ambient and piano",
        "tags": {"ambient", "instrumental", "piano", "classical", "modern classical",
                 "neoclassical", "neo-classical", "post-rock", "idm", "minimal", "study",
                 "focus", "soundtrack", "score", "contemporary classical"},
        "moods": {"reflective", "peaceful", "meditative", "calm"},
    },
    {
        "key": "energy",
        "name": "Energy",
        "blurb": "High tempo, loud and fast",
        "tags": {"dance", "edm", "electro house", "big room", "drum and bass", "dnb",
                 "dubstep", "hardstyle", "trance", "workout", "party", "bass",
                 "future bass", "electro", "phonk", "brostep"},
        # not angry / aggressive: those are metal and punk, and a metal track
        # in the middle of a dance mix is a different mood entirely
        "moods": {"energetic", "excitable", "rousing"},
    },
    {
        "key": "feel_good",
        "name": "Feel Good",
        "blurb": "Funk, disco and good moods",
        "tags": {"funk", "disco", "happy", "feel good", "feelgood", "fun", "summer",
                 "upbeat", "good vibes", "nu disco", "nu-disco", "boogie", "motown"},
        "moods": {"happy", "cheerful", "carefree", "good natured", "uplifting", "playful",
                  "quirky", "in love"},
    },
    {
        "key": "late_night",
        "name": "Late Night",
        "blurb": "Dark, moody and after hours",
        "tags": {"darksynth", "darkwave", "witch house", "dark ambient", "night",
                 "nocturnal", "melancholic", "melancholy", "sad", "moody", "noir",
                 "after hours", "late night", "night drive", "sadcore"},
        # not 'dark' or 'gritty': on real data those meant scorpions and
        # steppenwolf, which is not what 2am sounds like
        "moods": {"moody", "sad", "melancholy", "sombre", "brooding"},
    },
]

# tags that mean "not music you'd put on": a mood mix must never queue these
EXCLUDE_TAGS = {"audiobook", "audiobooks", "spoken word", "spoken", "podcast", "comedy",
                "stand-up", "asmr", "sound effects", "white noise", "meditation guide"}


# an interlude, an intro or a skit is part of an album, not a song for a mix
_NOT_A_SONG = re.compile(r"\b(interlude|intro|outro|skit|demo)\b", re.IGNORECASE)


def is_song(title: str) -> bool:
    return bool(title) and not _NOT_A_SONG.search(title)


def _tags(raw: Any) -> List[str]:
    if isinstance(raw, list):
        items = raw
    else:
        try:
            items = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            items = []
    return [str(t).strip().lower() for t in items if isinstance(t, str) and t.strip()]


def mood_score(tags: Sequence[str], audiodb_mood: Optional[str], mood: Dict[str, Any]) -> float:
    """how strongly an album is this mood. its leading tags count most (last.fm
    orders tags by weight), an audiodb mood counts like a top tag. 0 = not it."""
    lowered = [t.lower() for t in tags]
    if EXCLUDE_TAGS.intersection(lowered):
        return 0.0
    score = 0.0
    for pos, tag in enumerate(lowered[:TAG_DEPTH]):
        if tag in mood["tags"]:
            score += 1.0 / (1 + pos)
    if audiodb_mood and audiodb_mood.strip().lower() in mood["moods"]:
        score += 1.0
    return score


def pick_tracks(candidates: Sequence[Dict[str, Any]], size: int, seed: str) -> List[Dict[str, Any]]:
    """a weighted, artist-spread pick. each candidate carries ``score`` (the
    album's mood score), ``plays``, ``artist`` and ``album``. played tracks
    and strongly-tagged albums lead; the daily seed shuffles within that."""
    rng = random.Random(seed)
    keyed = []
    for c in candidates:
        weight = max(c.get("score", 0.0), 0.0) * (1.0 + math.log1p(max(c.get("plays") or 0, 0)))
        if weight <= 0:
            continue
        # weighted sampling without replacement (efraimidis-spirakis): u^(1/w)
        # lands nearer 1 the heavier the weight, and the highest keys go first
        keyed.append((rng.random() ** (1.0 / weight), c))
    keyed.sort(key=lambda kv: kv[0], reverse=True)
    per_artist: Dict[str, int] = {}
    per_album: Dict[str, int] = {}
    picked: List[Dict[str, Any]] = []
    seen = set()
    for _, c in keyed:
        artist = (c.get("artist") or "").lower()
        album = f"{artist}|{(c.get('album') or '').lower()}"
        title = f"{artist}|{(c.get('title') or '').lower()}"
        if title in seen or per_artist.get(artist, 0) >= PER_ARTIST or per_album.get(album, 0) >= PER_ALBUM:
            continue
        seen.add(title)
        per_artist[artist] = per_artist.get(artist, 0) + 1
        per_album[album] = per_album.get(album, 0) + 1
        picked.append(c)
        if len(picked) >= size:
            break
    return picked


def _chunks(items: Sequence[Any], n: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _scored_albums(conn) -> Dict[str, Dict[int, float]]:
    """album id -> score, per mood, for every album that is any mood at all."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, lastfm_tags, mood FROM albums
        WHERE (lastfm_tags IS NOT NULL AND lastfm_tags NOT IN ('', '[]'))
           OR (mood IS NOT NULL AND mood != '')
        """
    )
    out: Dict[str, Dict[int, float]] = {m["key"]: {} for m in MOODS}
    for album_id, raw_tags, audiodb_mood in cur.fetchall():
        tags = _tags(raw_tags)
        for mood in MOODS:
            s = mood_score(tags, audiodb_mood, mood)
            if s > 0:
                out[mood["key"]][album_id] = s
    return out


def _owned_tracks(conn, album_ids: Sequence[int]) -> List[Dict[str, Any]]:
    from core.metadata import normalize_image_url
    rows: List[Dict[str, Any]] = []
    cur = conn.cursor()
    for chunk in _chunks(list(album_ids), 900):
        cur.execute(
            f"""
            SELECT t.album_id, t.title, t.duration, t.play_count,
                   ar.name, al.title, COALESCE(al.thumb_url, ar.thumb_url)
            FROM tracks t
            JOIN artists ar ON ar.id = t.artist_id
            JOIN albums al ON al.id = t.album_id
            WHERE t.file_path IS NOT NULL AND t.file_path != ''
              AND t.album_id IN ({','.join('?' * len(chunk))})
            """,
            list(chunk),
        )
        for album_id, title, duration, plays, artist, album, cover in cur.fetchall():
            if not artist or not is_song(title or ""):
                continue
            rows.append({
                "album_id": album_id, "title": title, "duration": duration or 0,
                "plays": plays or 0, "artist": artist, "album": album or "",
                "cover": normalize_image_url(cover) if cover else None,
            })
    return rows


def _mix_track(c: Dict[str, Any]) -> Dict[str, Any]:
    """the same shape a daily mix track has, so the page treats both alike."""
    return {
        "name": c["title"],
        "artists": [{"name": c["artist"]}],
        "album": {"name": c["album"], "images": [{"url": c["cover"]}] if c.get("cover") else []},
        "duration_ms": int(c.get("duration") or 0),
        "play_count": c.get("plays") or 0,
        "owned": True,
    }


def generate_mood_mixes(database, profile_id: int = 1, *, today: Optional[date] = None,
                        size: int = MIX_SIZE) -> Dict[str, Any]:
    today = today or date.today()
    from core.discovery.blocked import BlockedArtists
    blocked = BlockedArtists.load(database, profile_id)
    with database._get_connection() as conn:
        scored = _scored_albums(conn)
        wanted = sorted({aid for per in scored.values() for aid in per})
        tracks = _owned_tracks(conn, wanted) if wanted else []
    by_album: Dict[int, List[Dict[str, Any]]] = {}
    for t in tracks:
        if not blocked.is_empty and blocked.blocks_artist({"artist_name": t["artist"]}):
            continue
        by_album.setdefault(t["album_id"], []).append(t)
    mixes = []
    for mood in MOODS:
        candidates = []
        for album_id, score in scored[mood["key"]].items():
            for t in by_album.get(album_id, []):
                candidates.append({**t, "score": score})
        if len(candidates) < MIN_TRACKS:
            continue
        seed = hashlib.sha1(f"{today.isoformat()}:{profile_id}:{mood['key']}".encode()).hexdigest()
        picked = pick_tracks(candidates, size, seed)
        if len(picked) < MIN_TRACKS:
            continue
        mixes.append({
            "key": f"mood_{mood['key']}",
            "name": mood["name"],
            "subtitle": mood["blurb"],
            "tracks": [_mix_track(c) for c in picked],
            "pool": len(candidates),
        })
    return {
        "mixes": mixes,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "blocked": blocked.fingerprint(),
        "v": PAYLOAD_VERSION,
    }


def get_or_build_mood_mixes(database, profile_id: int = 1, *, force: bool = False,
                            ttl_hours: float = TTL_HOURS) -> Dict[str, Any]:
    """stored per profile, rebuilt once a day or when the blocklist changes."""
    from core.discovery.blocked import BlockedArtists
    if not force:
        try:
            stored = database.get_curated_playlist(CURATED_KEY, profile_id)
        except Exception as e:  # noqa: BLE001 - a bad stored copy just rebuilds
            logger.debug("stored mood mixes unreadable: %s", e)
            stored = None
        if (isinstance(stored, dict) and stored.get("v") == PAYLOAD_VERSION
                and stored.get("blocked") == BlockedArtists.load(database, profile_id).fingerprint()):
            try:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(stored.get("generated_at", ""))
                if age.total_seconds() < ttl_hours * 3600:
                    return stored
            except ValueError:
                pass
    payload = generate_mood_mixes(database, profile_id)
    try:
        database.save_curated_playlist(CURATED_KEY, payload, profile_id)
    except Exception as e:  # noqa: BLE001 - serving it matters more than storing it
        logger.warning("mood mixes save failed: %s", e)
    return payload
