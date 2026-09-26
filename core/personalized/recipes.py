"""Renewable mixes (plan phase 7): daily mixes as recipes you keep.

A recipe says what a mix is made of: seed artists and/or genres, a year
range, how much comes from the library, the discovery pool and what's
trending, how long it is, and how often it renews. Each generation follows
the same rules:

- one song per artist.
- a source that comes up short hands its share to the others, so the mix
  reaches its length whenever the pools can fill it.
- a reserve is kept: the next best tracks, still one per artist and none by
  an artist already in the mix.
- a track that keeps failing to download (the wishlist retried it and gave
  up for now) is swapped for the first reserve track.

"Keep this one" freezes the current generation into a normal (mirrored)
playlist, which then syncs and downloads like any other.

Storage follows the discover daily mixes: the recipe lives in
``mix_recipes``, each generation's full track dicts in
discovery_curated_playlists under ``mix_recipe_<id>`` (render-ready, so the
Discover mix card and modal show it as they show a daily mix).

"Trending" means the discovery pool's most popular tracks matching the
recipe's genres and years, whoever brought them in (with no genres, the
most popular of the seeds' circle): there is no per-genre chart source to
ask.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from utils.logging_config import get_logger

logger = get_logger("personalized.recipes")

SOURCES = ('library', 'discovery', 'trending')
DEFAULT_MIX = {'library': 0.6, 'discovery': 0.3, 'trending': 0.1}
SCHEDULES = {'daily': timedelta(hours=20), 'weekly': timedelta(days=6, hours=20), 'manual': None}
MIN_LENGTH, MAX_LENGTH = 10, 100
RESERVE_SHARE = 0.25          # reserve size, as a share of the length
FAILING_RETRIES = 2           # the wishlist gave up on it this many times
PAYLOAD_VERSION = 1
MAX_SEEDS = 10
MAX_GENRES = 10


def _norm(text: Any) -> str:
    return str(text or '').strip().casefold()


def _clean_names(values: Any, cap: int) -> List[str]:
    out, seen = [], set()
    for v in values or []:
        name = str(v or '').strip()
        if name and _norm(name) not in seen:
            seen.add(_norm(name))
            out.append(name)
    return out[:cap]


def _year(value: Any) -> Optional[int]:
    try:
        y = int(value)
    except (TypeError, ValueError):
        return None
    return y if 1900 <= y <= 2100 else None


@dataclass
class Recipe:
    name: str
    seeds: List[str] = field(default_factory=list)
    genres: List[str] = field(default_factory=list)
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    mix: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_MIX))
    length: int = 40
    schedule: str = 'weekly'

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'Recipe':
        """Clamp and clean whatever a form sent. Raises ValueError when it
        can't be a recipe: no name, or nothing to build from."""
        name = str((d or {}).get('name') or '').strip()[:80]
        if not name:
            raise ValueError('a recipe needs a name')
        seeds = _clean_names(d.get('seeds'), MAX_SEEDS)
        genres = _clean_names(d.get('genres'), MAX_GENRES)
        if not seeds and not genres:
            raise ValueError('a recipe needs seed artists or genres')
        year_from, year_to = _year(d.get('year_from')), _year(d.get('year_to'))
        if year_from and year_to and year_from > year_to:
            year_from, year_to = year_to, year_from
        raw_mix = d.get('mix') if isinstance(d.get('mix'), dict) else {}
        mix = {}
        for src in SOURCES:
            try:
                mix[src] = max(0.0, float(raw_mix.get(src, DEFAULT_MIX[src] if not raw_mix else 0)))
            except (TypeError, ValueError):
                mix[src] = 0.0
        total = sum(mix.values())
        mix = {k: round(v / total, 4) for k, v in mix.items()} if total else dict(DEFAULT_MIX)
        try:
            length = int(d.get('length') or 40)
        except (TypeError, ValueError):
            length = 40
        schedule = d.get('schedule') if d.get('schedule') in SCHEDULES else 'weekly'
        return cls(name, seeds, genres, year_from, year_to, mix,
                   max(MIN_LENGTH, min(MAX_LENGTH, length)), schedule)

    def to_dict(self) -> Dict[str, Any]:
        return {'name': self.name, 'seeds': list(self.seeds), 'genres': list(self.genres),
                'year_from': self.year_from, 'year_to': self.year_to, 'mix': dict(self.mix),
                'length': self.length, 'schedule': self.schedule}

    @property
    def reserve_size(self) -> int:
        return max(3, round(self.length * RESERVE_SHARE))


# ---- the pure part: pools in, a mix out --------------------------------------------

def _artist_of(track: Dict[str, Any]) -> str:
    artists = track.get('artists')
    if isinstance(artists, list) and artists:
        first = artists[0]
        return first.get('name', '') if isinstance(first, dict) else str(first)
    return str(track.get('artist_name') or '')


def quotas(recipe: Recipe) -> Dict[str, int]:
    """Each source's share of the length, summing exactly to it."""
    raw = {s: recipe.mix.get(s, 0.0) * recipe.length for s in SOURCES}
    out = {s: int(v) for s, v in raw.items()}
    # largest remainders get the leftover slots
    for s in sorted(SOURCES, key=lambda s: raw[s] - out[s], reverse=True):
        if sum(out.values()) >= recipe.length:
            break
        if recipe.mix.get(s, 0) > 0:
            out[s] += 1
    return out


def build_mix(pools: Dict[str, Sequence[Dict[str, Any]]], recipe: Recipe,
              skip_artist=lambda name: False) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(mix, reserve) from ranked pools. One song per artist across the whole
    mix; a short source hands its share to the others, biggest share first;
    the reserve is what's left, still one per artist. ``skip_artist`` drops
    artists that must not appear (blocked, not now, less like this)."""
    used: set = set()
    cursor = {s: 0 for s in SOURCES}
    picked: Dict[str, List[Dict[str, Any]]] = {s: [] for s in SOURCES}

    def take(source: str) -> Optional[Dict[str, Any]]:
        pool = pools.get(source) or []
        while cursor[source] < len(pool):
            track = pool[cursor[source]]
            cursor[source] += 1
            artist = _norm(_artist_of(track))
            if not artist or artist in used or skip_artist(artist):
                continue
            used.add(artist)
            return dict(track, mix_source=source)
        return None

    want = quotas(recipe)
    for source in SOURCES:
        while len(picked[source]) < want[source]:
            t = take(source)
            if t is None:
                break
            picked[source].append(t)
    # shortfall: the others fill it, the biggest share first. a source set to
    # nothing gives nothing: "no trending" means none, not "none unless short"
    order = [s for s in sorted(SOURCES, key=lambda s: recipe.mix.get(s, 0), reverse=True)
             if recipe.mix.get(s, 0) > 0]
    missing = recipe.length - sum(len(v) for v in picked.values())
    for source in order:
        while missing > 0:
            t = take(source)
            if t is None:
                break
            picked[source].append(t)
            missing -= 1

    mix = _weave(picked, order)
    reserve: List[Dict[str, Any]] = []
    for source in order:
        while len(reserve) < recipe.reserve_size:
            t = take(source)
            if t is None:
                break
            reserve.append(t)
    return mix, reserve


def _weave(picked: Dict[str, List[Dict[str, Any]]], order: Sequence[str]) -> List[Dict[str, Any]]:
    """Spread the sources through the mix instead of playing them in blocks:
    each next slot goes to the source furthest behind its share."""
    total = sum(len(v) for v in picked.values())
    if not total:
        return []
    idx = {s: 0 for s in order}
    out = []
    for n in range(1, total + 1):
        best = None
        for s in order:
            if idx[s] >= len(picked[s]):
                continue
            behind = len(picked[s]) * n / total - idx[s]
            if best is None or behind > best[0]:
                best = (behind, s)
        s = best[1]
        out.append(picked[s][idx[s]])
        idx[s] += 1
    return out


def replace_failing(tracks: List[Dict[str, Any]], reserve: List[Dict[str, Any]],
                    failing) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
    """Swap every track ``failing(track)`` says won't download for the first
    reserve track by an artist not already in the mix. Same slot, so the
    order holds. Returns (tracks, reserve, replaced)."""
    tracks, reserve = list(tracks), list(reserve)
    replaced = 0
    for i, track in enumerate(tracks):
        if not failing(track):
            continue
        present = {_norm(_artist_of(t)) for j, t in enumerate(tracks) if j != i}
        for k, cand in enumerate(reserve):
            if _norm(_artist_of(cand)) not in present:
                tracks[i] = reserve.pop(k)
                replaced += 1
                break
    return tracks, reserve, replaced


# ---- storage -------------------------------------------------------------------

def _row(r) -> Dict[str, Any]:
    row = dict(r)
    try:
        row['recipe'] = json.loads(row.pop('recipe_json') or '{}')
    except (TypeError, ValueError):
        row['recipe'] = {}
    return row


def list_recipes(database, profile_id: int) -> List[Dict[str, Any]]:
    with database._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, recipe_json, created_at, updated_at FROM mix_recipes "
                    "WHERE profile_id = ? ORDER BY id", (profile_id,))
        return [_row(r) for r in cur.fetchall()]


def get_recipe(database, profile_id: int, recipe_id: int) -> Optional[Dict[str, Any]]:
    with database._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, recipe_json, created_at, updated_at FROM mix_recipes "
                    "WHERE id = ? AND profile_id = ?", (int(recipe_id), profile_id))
        r = cur.fetchone()
        return _row(r) if r else None


def save_recipe(database, profile_id: int, recipe: Recipe,
                recipe_id: Optional[int] = None) -> Optional[int]:
    body = json.dumps(recipe.to_dict())
    with database._get_connection() as conn:
        cur = conn.cursor()
        if recipe_id is None:
            cur.execute("INSERT INTO mix_recipes (profile_id, recipe_json) VALUES (?, ?)",
                        (profile_id, body))
            new_id = cur.lastrowid
        else:
            cur.execute("UPDATE mix_recipes SET recipe_json = ?, updated_at = CURRENT_TIMESTAMP "
                        "WHERE id = ? AND profile_id = ?", (body, int(recipe_id), profile_id))
            new_id = int(recipe_id) if cur.rowcount else None
        conn.commit()
    return new_id


def delete_recipe(database, profile_id: int, recipe_id: int) -> bool:
    with database._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM mix_recipes WHERE id = ? AND profile_id = ?",
                    (int(recipe_id), profile_id))
        conn.commit()
        gone = cur.rowcount > 0
    if gone:
        try:
            database.save_curated_playlist(_key(recipe_id), None, profile_id)
        except Exception as exc:  # noqa: BLE001 - a stale payload is harmless
            logger.debug("recipe payload clear failed: %s", exc)
    return gone


def _key(recipe_id: int) -> str:
    return f'mix_recipe_{int(recipe_id)}'


# ---- the pools -----------------------------------------------------------------

def _genre_match(raw: Any, wanted: Sequence[str]) -> bool:
    if not wanted:
        return True
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        parsed = str(raw or '').split(',')
    names = {_norm(g) for g in (parsed or []) if g} if isinstance(parsed, list) else {_norm(parsed)}
    return any(_norm(w) in names or any(_norm(w) in n for n in names) for w in wanted)


def _in_years(year: Any, recipe: Recipe) -> bool:
    y = _year(str(year or '')[:4])
    if recipe.year_from is None and recipe.year_to is None:
        return True
    if y is None:
        return False
    return (recipe.year_from is None or y >= recipe.year_from) and (
        recipe.year_to is None or y <= recipe.year_to)


def _circle(database, recipe: Recipe, profile_id: int) -> Tuple[List[str], set]:
    """The seeds and the artists similar to them, and the library's names."""
    from core.personalized.daily_mixes import _seed_edges
    seeds = [_norm(s) for s in recipe.seeds]
    if not seeds:
        similars, owned = _seed_edges(database, [], profile_id)
        return [], owned
    similars, owned = _seed_edges(database, seeds, profile_id)
    circle = list(seeds)
    for s in seeds:
        for sim in similars.get(s) or []:
            nm = _norm(sim.get('name'))
            if nm and nm not in circle:
                circle.append(nm)
    return circle, owned


def library_pool(database, recipe: Recipe, profile_id: int, circle: Sequence[str],
                 rng: random.Random) -> List[Dict[str, Any]]:
    """Owned tracks by the seeds' circle and/or in the genres, in the years;
    most-played first per artist, artists shuffled for the day."""
    from core.metadata import normalize_image_url
    with database._get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT t.title, t.duration, ar.name AS artist, ar.genres AS artist_genres, "
            "al.title AS album, al.year, al.genres AS album_genres, "
            "COALESCE(al.thumb_url, ar.thumb_url) AS cover, "
            "(SELECT COUNT(*) FROM listening_history lh WHERE LOWER(lh.artist) = LOWER(ar.name) "
            " AND LOWER(lh.title) = LOWER(t.title)) AS plays "
            "FROM tracks t JOIN artists ar ON ar.id = t.artist_id "
            "LEFT JOIN albums al ON al.id = t.album_id "
            "WHERE t.file_path IS NOT NULL AND t.file_path != ''")
        rows = [dict(r) for r in cur.fetchall()]
    in_circle = set(circle)
    by_artist: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        artist = _norm(r['artist'])
        if recipe.seeds and artist not in in_circle and not (
                recipe.genres and _genre_match(r['artist_genres'] or r['album_genres'], recipe.genres)):
            continue
        if not recipe.seeds and not _genre_match(r['artist_genres'] or r['album_genres'], recipe.genres):
            continue
        if not _in_years(r['year'], recipe):
            continue
        cover = normalize_image_url(r['cover']) if r['cover'] else None
        by_artist.setdefault(artist, []).append({
            'name': r['title'], 'artists': [{'name': r['artist']}],
            'album': {'name': r['album'] or '', 'images': [{'url': cover}] if cover else []},
            'duration_ms': int(r['duration'] or 0), 'owned': True, '_plays': r['plays'] or 0})
    artists = list(by_artist)
    rng.shuffle(artists)
    # seeds first, then their circle, then genre matches
    rank = {a: i for i, a in enumerate(circle)}
    artists.sort(key=lambda a: rank.get(a, len(rank)))
    out = []
    for a in artists:
        best = sorted(by_artist[a], key=lambda t: -t['_plays'])[0]
        best.pop('_plays', None)
        out.append(best)
    return out


def _pool_rows(database, profile_id: int) -> List[Dict[str, Any]]:
    with database._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT track_name, artist_name, album_name, album_cover_url, duration_ms, "
                    "popularity, release_date, artist_genres, track_data_json, "
                    "spotify_track_id, deezer_track_id, itunes_track_id "
                    "FROM discovery_pool WHERE profile_id = ?", (profile_id,))
        return [dict(r) for r in cur.fetchall()]


def _pool_track(r: Dict[str, Any]) -> Dict[str, Any]:
    track = None
    if r.get('track_data_json'):
        try:
            track = json.loads(r['track_data_json'])
        except (TypeError, ValueError):
            track = None
    # an empty or partial blob would make a nameless track: rebuild from columns
    if not isinstance(track, dict) or not track.get('name') or not track.get('artists'):
        track = {'name': r['track_name'], 'artists': [{'name': r['artist_name']}],
                 'album': {'name': r['album_name'] or '',
                           'images': [{'url': r['album_cover_url']}] if r['album_cover_url'] else []},
                 'duration_ms': r['duration_ms'] or 0}
    track = dict(track)
    track.setdefault('id', r.get('spotify_track_id') or r.get('deezer_track_id') or r.get('itunes_track_id'))
    track['owned'] = False
    return track


def discovery_pool(database, recipe: Recipe, profile_id: int, circle: Sequence[str],
                   owned: set, rows: List[Dict[str, Any]], rng: random.Random) -> List[Dict[str, Any]]:
    """Unowned tracks by the seeds' similar artists and/or in the genres."""
    in_circle = set(circle)
    fit = [r for r in rows if _norm(r['artist_name']) not in owned
           and ((recipe.seeds and _norm(r['artist_name']) in in_circle)
                or (recipe.genres and _genre_match(r['artist_genres'], recipe.genres)))
           and _in_years(r['release_date'], recipe)]
    rng.shuffle(fit)
    rank = {a: i for i, a in enumerate(circle)}
    fit.sort(key=lambda r: rank.get(_norm(r['artist_name']), len(rank)))
    return [_pool_track(r) for r in fit]


def trending_pool(recipe: Recipe, owned: set, rows: List[Dict[str, Any]],
                  circle: Sequence[str] = ()) -> List[Dict[str, Any]]:
    """The pool's most popular tracks in the genres and years, whoever
    brought them in. A recipe with no genres has nothing to be popular IN
    but its seeds, so there it's the most popular of the seeds' circle."""
    in_circle = set(circle)

    def fits(r) -> bool:
        if recipe.genres:
            return _genre_match(r['artist_genres'], recipe.genres)
        return _norm(r['artist_name']) in in_circle

    fit = [r for r in rows if _norm(r['artist_name']) not in owned and fits(r)
           and _in_years(r['release_date'], recipe)]
    fit.sort(key=lambda r: -(r.get('popularity') or 0))
    return [_pool_track(r) for r in fit]


# ---- a generation ----------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fingerprints(database, profile_id: int) -> Tuple[str, str]:
    from core.discovery.blocked import BlockedArtists
    from core.discovery.feedback import Taste
    return (BlockedArtists.load(database, profile_id).fingerprint(),
            Taste.load(database, profile_id).fingerprint())


def generate(database, profile_id: int, recipe_id: int, recipe: Recipe,
             seed: Optional[str] = None) -> Dict[str, Any]:
    from core.discovery.blocked import BlockedArtists
    from core.discovery.explain import explanation
    from core.discovery.feedback import Taste

    rng = random.Random(seed or f"{_now().date().isoformat()}:{profile_id}:{recipe_id}")
    blocked = BlockedArtists.load(database, profile_id)
    taste = Taste.load(database, profile_id)
    circle, owned = _circle(database, recipe, profile_id)
    circle = [a for a in circle if not blocked.blocks_name(a)]
    rows = _pool_rows(database, profile_id)
    pools = {
        'library': library_pool(database, recipe, profile_id, circle, rng),
        'discovery': discovery_pool(database, recipe, profile_id, circle, owned, rows, rng),
        'trending': trending_pool(recipe, owned, rows, circle),
    }
    tracks, reserve = build_mix(
        pools, recipe,
        skip_artist=lambda a: blocked.blocks_name(a) or taste.artist_factor(a) < 1.0)
    kind = 'listened' if recipe.seeds else 'genre'
    why = explanation(kind, recipe.seeds or recipe.genres, None)
    blocked_fp, taste_fp = blocked.fingerprint(), taste.fingerprint()
    return {
        'recipe_id': recipe_id, 'name': recipe.name, 'recipe': recipe.to_dict(),
        'tracks': tracks, 'reserve': reserve, 'explanation': why,
        'counts': {s: sum(1 for t in tracks if t.get('mix_source') == s) for s in SOURCES},
        'generated_at': _now().isoformat(timespec='seconds'),
        'blocked': blocked_fp, 'taste': taste_fp, 'v': PAYLOAD_VERSION,
    }


def _failing_ids(database, profile_id: int) -> set:
    """Track ids the wishlist has retried and given up on for now."""
    try:
        with database._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT spotify_track_id FROM wishlist_tracks WHERE retry_count >= ?",
                        (FAILING_RETRIES,))
            return {str(r[0]) for r in cur.fetchall() if r[0]}
    except Exception as exc:  # noqa: BLE001 - unknown means not failing
        logger.debug("wishlist failure read failed: %s", exc)
        return set()


def _track_ids(track: Dict[str, Any]) -> set:
    ids = {track.get('id'), track.get('spotify_track_id'), track.get('deezer_track_id'),
           track.get('itunes_track_id')}
    return {str(i) for i in ids if i}


def is_stale(payload: Any, recipe_row: Dict[str, Any], fingerprints: Tuple[str, str],
             now: Optional[datetime] = None) -> bool:
    if not isinstance(payload, dict) or payload.get('v') != PAYLOAD_VERSION:
        return True
    if (payload.get('blocked'), payload.get('taste')) != fingerprints:
        return True
    if payload.get('recipe') != recipe_row.get('recipe'):
        return True            # the recipe was edited
    every = SCHEDULES.get((recipe_row.get('recipe') or {}).get('schedule'))
    if every is None:
        return False           # manual: only a refresh renews it
    try:
        age = (now or _now()) - datetime.fromisoformat(payload.get('generated_at', ''))
    except ValueError:
        return True
    return age >= every


def get_or_build(database, profile_id: int, recipe_id: int, force: bool = False) -> Optional[Dict[str, Any]]:
    """The mix as it stands: renewed when its schedule says so (or the recipe,
    the blocks or the feedback changed), failing tracks swapped for reserve."""
    row = get_recipe(database, profile_id, recipe_id)
    if not row:
        return None
    recipe = Recipe.from_dict(row['recipe'])
    payload = None
    try:
        payload = database.get_curated_playlist(_key(recipe_id), profile_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("recipe payload unreadable: %s", exc)
    if force or is_stale(payload, row, _fingerprints(database, profile_id)):
        payload = generate(database, profile_id, recipe_id, recipe,
                           seed=_now().isoformat() if force else None)
        _store(database, profile_id, recipe_id, payload)
        return payload
    failing = _failing_ids(database, profile_id)
    if failing:
        tracks, reserve, n = replace_failing(
            payload.get('tracks') or [], payload.get('reserve') or [],
            lambda t: bool(_track_ids(t) & failing))
        if n:
            payload = dict(payload, tracks=tracks, reserve=reserve,
                           replaced=int(payload.get('replaced') or 0) + n)
            _store(database, profile_id, recipe_id, payload)
    return payload


def _store(database, profile_id: int, recipe_id: int, payload: Dict[str, Any]) -> None:
    try:
        database.save_curated_playlist(_key(recipe_id), payload, profile_id)
    except Exception as exc:  # noqa: BLE001 - served fresh, stored next time
        logger.warning("recipe mix save failed: %s", exc)


def keep(database, profile_id: int, recipe_id: int, name: Optional[str] = None) -> Optional[int]:
    """Freeze the current generation into a normal playlist."""
    payload = get_or_build(database, profile_id, recipe_id)
    if not payload or not payload.get('tracks'):
        return None
    day = str(payload.get('generated_at') or '')[:10]
    title = (name or '').strip() or f"{payload.get('name')} ({day})"
    return database.mirror_playlist(
        source='soulsync_mix', source_playlist_id=f"recipe-{recipe_id}-{payload.get('generated_at')}",
        name=title, tracks=payload['tracks'], profile_id=profile_id,
        description=f"Kept from the {payload.get('name')} mix")
