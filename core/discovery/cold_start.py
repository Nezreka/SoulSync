"""a filled discover for a profile with little or no taste yet.

a new profile sees thin shelves until a watchlist scan has built similar
artists for it. three things fix that, all here:

  - taste seeds: the artists a profile has told us about, counted from its
    plays (an artist played a couple of times), its watchlist and the artists
    it picked by hand. under SEED_THRESHOLD the profile is "new".
  - starter shelves: while a profile is new and its shelves are empty, they
    fill from charts the app already reads (SoulSync charts from the network,
    deezer's public genre charts, beatport when its homepage is cached). each
    pick says where it came from, explain kind 'trending'.
  - picked artists: "pick artists you love" stores explicit seeds per
    profile. they count as artists the profile chose (the same as library and
    watchlist artists, see MusicDatabase._deliberate_artist_source_ids), so
    their similar artists feed the hero, recommended artists and the
    discovery pool through the normal seed path. the first build runs right
    away in a background thread instead of waiting for the scan.

the network calls are injected so all of this is testable without one.
"""

from __future__ import annotations

import json
import math
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from utils.logging_config import get_logger

logger = get_logger("discovery.cold_start")

# fewer seeds than this and the profile counts as new
SEED_THRESHOLD = 5
# an artist played once is a click, twice is a taste
MIN_PLAYS = 2
# the picker asks for five; more is fine, up to this many are kept
MAX_PICKS = 40
# similar artists fetched per picked artist
SIMILAR_PER_PICK = 15

SEEDS_KEY = 'discover_taste_seeds'
DISMISSED_KEY = 'discover_starter_dismissed'

# deezer genre ids (GET /genre) with the words the page shows
PICKER_GENRES: Tuple[Tuple[int, str], ...] = (
    (0, 'Popular now'),
    (132, 'Pop'),
    (116, 'Hip hop'),
    (152, 'Rock'),
    (85, 'Alternative'),
    (106, 'Electronic'),
    (113, 'Dance'),
    (165, 'R&B'),
    (169, 'Soul & funk'),
    (464, 'Metal'),
    (129, 'Jazz'),
    (84, 'Country'),
    (466, 'Folk'),
    (197, 'Latin'),
    (144, 'Reggae'),
    (98, 'Classical'),
)
_GENRE_NAMES = dict(PICKER_GENRES)
# the genres a starter shelf mixes, in the order they take turns
SHELF_GENRES: Tuple[int, ...] = (0, 132, 116, 152, 106, 165, 85)

CHART_CACHE_SECONDS = 3 * 3600
_chart_cache: Dict[int, Tuple[float, List[dict]]] = {}
_chart_lock = threading.Lock()


def _key(base: str, profile_id: Any) -> str:
    try:
        pid = int(profile_id)
    except (TypeError, ValueError):
        pid = 1
    return f"{base}:{pid}"


def _norm(name: Any) -> str:
    return ' '.join(str(name or '').split()).casefold()


# ── picked artists ───────────────────────────────────────────────────────────

def clean_pick(raw: Any) -> Optional[Dict[str, Any]]:
    """one picked artist as stored: name, deezer id, picture. None when it has
    no name. the id is what its similar artists are keyed by."""
    if not isinstance(raw, dict):
        return None
    name = ' '.join(str(raw.get('name') or raw.get('artist_name') or '').split())[:200]
    if not name:
        return None
    raw_id = raw.get('deezer_id') or raw.get('deezer_artist_id') or raw.get('id')
    deezer_id = str(raw_id).strip() if raw_id not in (None, '') else ''
    if deezer_id and not deezer_id.isdigit():
        deezer_id = ''
    image = raw.get('image_url')
    image = image if isinstance(image, str) and image.startswith(('http://', 'https://', '/')) else None
    return {'name': name, 'deezer_id': deezer_id or None, 'image_url': image}


def parse_picks(raw: Any) -> List[Dict[str, Any]]:
    """the stored list, cleaned. bad json reads as no picks."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    return [p for p in (clean_pick(r) for r in raw) if p]


def load_picks(database, profile_id) -> List[Dict[str, Any]]:
    return parse_picks(database.get_metadata(_key(SEEDS_KEY, profile_id)))


def merge_picks(current: Sequence[dict], incoming: Iterable[Any]) -> List[Dict[str, Any]]:
    """the new picks first, then the old ones, one per artist, capped."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for pick in [clean_pick(r) for r in incoming or ()] + list(current or ()):
        if not pick:
            continue
        key = pick['deezer_id'] or _norm(pick['name'])
        name_key = _norm(pick['name'])
        if key in seen or name_key in seen:
            continue
        seen.update({key, name_key})
        out.append(pick)
        if len(out) >= MAX_PICKS:
            break
    return out


def save_picks(database, profile_id, incoming: Iterable[Any]) -> List[Dict[str, Any]]:
    merged = merge_picks(load_picks(database, profile_id), incoming)
    database.set_metadata(_key(SEEDS_KEY, profile_id), json.dumps(merged))
    return merged


def remove_pick(database, profile_id, name: Any) -> List[Dict[str, Any]]:
    target = _norm(name)
    kept = [p for p in load_picks(database, profile_id) if _norm(p['name']) != target]
    database.set_metadata(_key(SEEDS_KEY, profile_id), json.dumps(kept))
    return kept


def picked_source_ids(raw: Any) -> set:
    """the deezer ids of a stored pick list, as similar_artists source ids."""
    return {p['deezer_id'] for p in parse_picks(raw) if p.get('deezer_id')}


def picked_sources(database, profile_id, names: Sequence[Any]) -> Dict[str, List[str]]:
    """{recommended name: [the picked artists that point at it]}, for the
    "because you picked X" line. empty without picks."""
    picks = load_picks(database, profile_id)
    by_id = {p['deezer_id']: p['name'] for p in picks if p.get('deezer_id')}
    wanted = [n for n in (names or []) if n]
    if not by_id or not wanted:
        return {}
    out: Dict[str, set] = {}
    try:
        with database._get_connection() as conn:
            ids = list(by_id)
            rows = conn.execute(
                "SELECT similar_artist_name, source_artist_id FROM similar_artists "
                f"WHERE profile_id = ? AND source_artist_id IN ({','.join('?' * len(ids))})",
                (int(profile_id), *ids)).fetchall()
    except Exception as e:  # noqa: BLE001 - a reason line is decoration
        logger.debug("picked sources lookup failed: %s", e)
        return {}
    wanted_keys = {_norm(n): n for n in wanted}
    for similar_name, source_id in rows:
        hit = wanted_keys.get(_norm(similar_name))
        seed = by_id.get(str(source_id))
        if hit and seed:
            out.setdefault(hit, set()).add(seed)
    return {k: sorted(v, key=str.casefold) for k, v in out.items()}


# ── how much taste a profile has ─────────────────────────────────────────────

def taste_seed_names(database, profile_id) -> set:
    """every artist this profile has told us about: played a few times, on
    its watchlist, or picked."""
    names = set()
    try:
        for row in database.get_top_artists('all', 50, profile_id=profile_id) or []:
            if int(row.get('play_count') or 0) >= MIN_PLAYS and row.get('name'):
                names.add(_norm(row['name']))
    except Exception as e:  # noqa: BLE001
        logger.debug("played seeds unreadable: %s", e)
    try:
        with database._get_connection() as conn:
            for (name,) in conn.execute(
                    "SELECT artist_name FROM watchlist_artists WHERE profile_id = ? LIMIT 50",
                    (int(profile_id),)).fetchall():
                if name:
                    names.add(_norm(name))
    except Exception as e:  # noqa: BLE001
        logger.debug("watchlist seeds unreadable: %s", e)
    for pick in load_picks(database, profile_id):
        names.add(_norm(pick['name']))
    names.discard('')
    return names


def is_new_profile(database, profile_id) -> bool:
    return len(taste_seed_names(database, profile_id)) < SEED_THRESHOLD


def is_dismissed(database, profile_id) -> bool:
    return bool(database.get_metadata(_key(DISMISSED_KEY, profile_id)))


def dismiss(database, profile_id) -> None:
    database.set_metadata(_key(DISMISSED_KEY, profile_id), str(int(time.time())))


def connections(database, profile_id, config_get: Callable[..., Any]) -> Dict[str, bool]:
    """is listenbrainz / last.fm connected for this profile. the admin's
    accounts are the ones in Settings; everyone else links their own."""
    try:
        pid = int(profile_id)
    except (TypeError, ValueError):
        pid = 1
    if pid == 1:
        return {
            'listenbrainz': bool(config_get('listenbrainz.token', '')),
            'lastfm': bool(config_get('lastfm.session_key', '') or config_get('lastfm.username', '')),
        }
    from core.listening_scope import has_own_account
    return {
        'listenbrainz': has_own_account(database, pid, 'listenbrainz'),
        'lastfm': has_own_account(database, pid, 'lastfm'),
    }


def status(database, profile_id, config_get: Callable[..., Any], *,
           spotify_import: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    seeds = taste_seed_names(database, profile_id)
    picks = load_picks(database, profile_id)
    needs = len(seeds) < SEED_THRESHOLD
    dismissed = is_dismissed(database, profile_id)
    return {
        'seeds': len(seeds),
        'threshold': SEED_THRESHOLD,
        'needs_seeds': needs,
        'dismissed': dismissed,
        'show_banner': needs and not dismissed,
        'picked': picks,
        'connections': connections(database, profile_id, config_get),
        'spotify_import': spotify_import or {'available': False, 'href': None},
        'build': build_status(profile_id),
    }


# ── charts ───────────────────────────────────────────────────────────────────

def _picture(item: dict) -> Optional[str]:
    for key in ('picture_xl', 'picture_big', 'picture_medium', 'picture'):
        value = item.get(key)
        if isinstance(value, str) and value.startswith('http'):
            return value
    return None


def popularity_from_fans(fans: Any) -> int:
    """deezer's fan count on the 0..100 scale the similar_artists rows use."""
    try:
        n = float(fans or 0)
    except (TypeError, ValueError):
        return 0
    if n <= 0:
        return 0
    return max(1, min(100, int(round(math.log10(n + 1) * 14))))


def deezer_artist(item: Any) -> Optional[Dict[str, Any]]:
    """a raw deezer artist as {name, deezer_id, image_url, popularity}."""
    if not isinstance(item, dict) or not item.get('id') or not item.get('name'):
        return None
    return {
        'name': str(item['name']),
        'deezer_id': str(item['id']),
        'image_url': _picture(item),
        'popularity': popularity_from_fans(item.get('nb_fan')),
    }


def _api_rows(client, endpoint: str, limit: int) -> List[dict]:
    payload = client._api_get(endpoint, {'limit': limit}, use_token=False) or {}
    rows = payload.get('data') if isinstance(payload, dict) else None
    return rows if isinstance(rows, list) else []


def chart_artists(client, genre_id: int, *, limit: int = 30,
                  now: Optional[float] = None) -> List[Dict[str, Any]]:
    """deezer's chart artists for one genre, kept for a few hours. an empty
    answer isn't kept, so deezer coming back is picked up next time."""
    now = time.time() if now is None else now
    with _chart_lock:
        hit = _chart_cache.get(int(genre_id))
        if hit and now - hit[0] < CHART_CACHE_SECONDS:
            return hit[1]
    if client is None:
        return []
    try:
        rows = [a for a in map(deezer_artist, _api_rows(client, f"chart/{int(genre_id)}/artists", limit)) if a]
    except Exception as e:  # noqa: BLE001 - a chart is a nice-to-have
        logger.debug("deezer chart %s failed: %s", genre_id, e)
        rows = []
    if rows:
        with _chart_lock:
            _chart_cache[int(genre_id)] = (now, rows)
    return rows


def search_artists(client, query: str, *, limit: int = 12) -> List[Dict[str, Any]]:
    query = ' '.join(str(query or '').split())[:120]
    if not query or client is None:
        return []
    try:
        payload = client._api_get('search/artist', {'q': query, 'limit': limit}, use_token=False) or {}
    except Exception as e:  # noqa: BLE001
        logger.debug("deezer artist search failed: %s", e)
        return []
    rows = payload.get('data') if isinstance(payload, dict) else None
    return [a for a in map(deezer_artist, rows if isinstance(rows, list) else []) if a]


def related_artists(client, deezer_id: Any, *, limit: int = SIMILAR_PER_PICK) -> List[Dict[str, Any]]:
    """deezer's related artists for one artist, as similar_artists rows."""
    if client is None or not str(deezer_id or '').isdigit():
        return []
    try:
        rows = _api_rows(client, f"artist/{deezer_id}/related", limit)
    except Exception as e:  # noqa: BLE001
        logger.debug("deezer related %s failed: %s", deezer_id, e)
        return []
    out = []
    for rank, artist in enumerate(a for a in map(deezer_artist, rows) if a):
        out.append({
            'similar_artist_name': artist['name'],
            'similar_artist_deezer_id': artist['deezer_id'],
            'similarity_rank': rank + 1,
            'image_url': artist['image_url'],
            'popularity': artist['popularity'],
        })
    return out[:limit]


def sin_chart_artists(database, *, per_chart: int = 12) -> List[Tuple[str, str]]:
    """(artist, chart name) from the SoulSync charts the network last sent,
    read from the table. nothing when this install has none."""
    try:
        with database._get_connection() as conn:
            rows = conn.execute(
                "SELECT name, tracks_json FROM metasync_charts ORDER BY position, key").fetchall()
    except Exception:  # noqa: BLE001 - no table: no network charts yet
        return []
    out: List[Tuple[str, str]] = []
    for chart_name, tracks_json in rows:
        try:
            tracks = json.loads(tracks_json or '[]')
        except (ValueError, TypeError):
            continue
        taken = 0
        for track in tracks if isinstance(tracks, list) else []:
            artists = track.get('artists') if isinstance(track, dict) else None
            first = artists[0] if isinstance(artists, list) and artists else None
            name = first.get('name') if isinstance(first, dict) else first
            if isinstance(name, str) and name.strip():
                out.append((name.strip(), str(chart_name or 'SoulSync Charts')))
                taken += 1
                if taken >= per_chart:
                    break
    return out


def beatport_artists(cached: Any, *, limit: int = 20) -> List[Tuple[str, str]]:
    """(artist, 'Beatport') from beatport data the app already cached. never
    scrapes: a shelf must not wait on a scrape."""
    out: List[Tuple[str, str]] = []

    def walk(node: Any) -> None:
        if len(out) >= limit:
            return
        if isinstance(node, dict):
            artist = node.get('artist')
            if isinstance(artist, str) and node.get('title'):
                first = artist.split(',')[0].strip()
                if first and first.casefold() not in ('unknown artist', 'various artists', 'beatport'):
                    out.append((first, 'Beatport'))
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(cached)
    return out[:limit]


def interleave(lists: Sequence[Sequence[dict]], limit: int) -> List[dict]:
    """take turns between the sources so no one chart owns the shelf; the
    first time an artist shows up wins."""
    out: List[dict] = []
    seen = set()
    for i in range(max((len(rows) for rows in lists), default=0)):
        for rows in lists:
            if i < len(rows):
                row = rows[i]
                key = _norm(row.get('artist_name'))
                if key and key not in seen:
                    seen.add(key)
                    out.append(row)
    return out[:limit]


def _card(artist: Dict[str, Any], label: str, genre: Optional[str]) -> Dict[str, Any]:
    from core.discovery.explain import explanation
    card = {
        'artist_id': artist.get('deezer_id'),
        'deezer_artist_id': artist.get('deezer_id'),
        'artist_name': artist['name'],
        'source': 'deezer',
        'popularity': artist.get('popularity') or None,
        'explanation': explanation('trending', [label]),
        'starter': True,
    }
    if artist.get('image_url'):
        card['image_url'] = artist['image_url']
    if genre:
        card['genres'] = [genre]
    return card


def genre_chart_label(genre_id: int) -> str:
    return "Deezer's chart" if int(genre_id) == 0 else f"Deezer's {_GENRE_NAMES.get(int(genre_id), 'genre')} chart"


def owned_names(database, names: Sequence[str]) -> set:
    """which of these artists the library already has music by (exact
    spelling, indexed lookup)."""
    wanted = sorted({n for n in names if n})
    if not wanted:
        return set()
    try:
        from core.library2.sql_util import owned_sql
        with database._get_connection() as conn:
            rows = conn.execute(
                f"SELECT a.name FROM lib2_artists a WHERE a.name IN ({','.join('?' * len(wanted))}) "
                f"AND ({owned_sql('artist', 'a')})", wanted).fetchall()
        return {_norm(r[0]) for r in rows}
    except Exception as e:  # noqa: BLE001 - owning is a filter, not a gate
        logger.debug("owned lookup failed: %s", e)
        return set()


def starter_shelf(database, client, *, limit: int = 24,
                  beatport_cache: Any = None, resolve_limit: int = 8) -> List[Dict[str, Any]]:
    """the starter picks, in the recommended-artist card shape, each with
    its 'trending' reason. owned artists and blocked ones are left out
    (blocked by the route's filter)."""
    lists: List[List[dict]] = []
    for genre_id in SHELF_GENRES:
        artists = chart_artists(client, genre_id)
        genre = None if genre_id == 0 else _GENRE_NAMES.get(genre_id)
        lists.append([_card(a, genre_chart_label(genre_id), genre) for a in artists])

    # charts that only name artists: give them a deezer id by search, a few
    # at most, and only when the name really matches
    named = sin_chart_artists(database) + beatport_artists(beatport_cache)
    if named and client is not None:
        from core.discovery.listening_recommendations import names_match
        resolved: List[dict] = []
        seen = set()
        for name, label in named:
            if len(resolved) >= resolve_limit:
                break
            if _norm(name) in seen:
                continue
            seen.add(_norm(name))
            found = search_artists(client, name, limit=1)
            if found and names_match(name, found[0]['name']):
                resolved.append(_card(found[0], label, None))
        if resolved:
            lists.insert(0, resolved)

    merged = interleave(lists, limit * 2)
    owned = owned_names(database, [c['artist_name'] for c in merged])
    return [c for c in merged if _norm(c['artist_name']) not in owned][:limit]


def picker_artists(client, genre_id: int, *, limit: int = 30) -> List[Dict[str, Any]]:
    genre_id = int(genre_id) if int(genre_id) in _GENRE_NAMES else 0
    return chart_artists(client, genre_id, limit=max(limit, 30))[:limit]


# ── the first build, right away ──────────────────────────────────────────────

_builds: Dict[int, Dict[str, Any]] = {}
_builds_lock = threading.Lock()


def build_status(profile_id) -> Dict[str, Any]:
    try:
        pid = int(profile_id)
    except (TypeError, ValueError):
        pid = 1
    with _builds_lock:
        state = dict(_builds.get(pid) or {})
    return state or {'state': 'idle', 'done': 0, 'total': 0, 'stored': 0}


def build_for_picks(database, profile_id, picks: Sequence[dict],
                    fetch_similar: Callable[[dict], List[dict]],
                    on_progress: Optional[Callable[[], None]] = None) -> Dict[str, Any]:
    """fetch and store similar artists for each picked artist, one at a time,
    telling the caller after each so the shelves fill as it goes. a pick
    without a deezer id has nothing to key its similar artists by."""
    pid = int(profile_id)
    todo = [p for p in picks if p.get('deezer_id')]
    stored_total = 0
    with _builds_lock:
        _builds[pid] = {'state': 'running', 'done': 0, 'total': len(todo), 'stored': 0,
                        'started_at': time.time()}
    for i, pick in enumerate(todo):
        try:
            rows = fetch_similar(pick) or []
            stored = database.replace_similar_artists(
                str(pick['deezer_id']), rows, profile_id=pid, source_provider='deezer') if rows else 0
            stored_total += stored or 0
        except Exception as e:  # noqa: BLE001 - one artist must not stop the rest
            logger.debug("similar artists for picked %s failed: %s", pick.get('name'), e)
        with _builds_lock:
            _builds[pid].update(done=i + 1, stored=stored_total)
        if on_progress:
            try:
                on_progress()
            except Exception as e:  # noqa: BLE001
                logger.debug("starter progress hook failed: %s", e)
    with _builds_lock:
        _builds[pid].update(state='done', finished_at=time.time())
    logger.info("starter build for profile %s: %d similar artists from %d picks",
                pid, stored_total, len(todo))
    return build_status(pid)


def start_build(database, profile_id, picks: Sequence[dict],
                fetch_similar: Callable[[dict], List[dict]],
                on_progress: Optional[Callable[[], None]] = None) -> Dict[str, Any]:
    """build_for_picks in a background thread. a build already running for
    this profile is left alone; the picks it missed are built next scan."""
    pid = int(profile_id)
    with _builds_lock:
        if (_builds.get(pid) or {}).get('state') == 'running':
            return dict(_builds[pid])
        _builds[pid] = {'state': 'running', 'done': 0,
                        'total': len([p for p in picks if p.get('deezer_id')]), 'stored': 0,
                        'started_at': time.time()}

    def run():
        try:
            build_for_picks(database, pid, picks, fetch_similar, on_progress)
        except Exception as e:  # noqa: BLE001
            logger.error("starter build failed for profile %s: %s", pid, e)
            with _builds_lock:
                _builds[pid] = {**(_builds.get(pid) or {}), 'state': 'failed'}

    threading.Thread(target=run, name=f"discover-starter-{pid}", daemon=True).start()
    return build_status(pid)


def _reset_for_tests() -> None:
    with _chart_lock:
        _chart_cache.clear()
    with _builds_lock:
        _builds.clear()
