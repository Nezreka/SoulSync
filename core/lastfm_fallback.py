"""last.fm first, free sources after.

from 1 december 2026 a last.fm api key needs a pro subscription. every live
last.fm read goes through here: last.fm answers when the key works (pro users
lose nothing), and listenbrainz / deezer fill in when last.fm is not set up,
refused the key, or had nothing. each answer says which source gave it.

the last.fm client serves its own cached answers when the key is refused, so a
key that lapses keeps what it already knew; the free sources cache theirs too
(core.provider_cache).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core import provider_cache
from utils.logging_config import get_logger

logger = get_logger("lastfm_fallback")

CATALOGUE_TTL = 30 * 86400
_UNSET = object()

Rows = List[Dict[str, Any]]


def configured_client(api_key: Optional[str] = None):
    """a LastFMClient on the configured key, or None when there is no key."""
    try:
        if api_key is None:
            from core.settings import config_manager
            api_key = config_manager.get('lastfm.api_key', '') or ''
        if not api_key:
            return None
        from core.lastfm_client import LastFMClient
        return LastFMClient(api_key=api_key)
    except Exception as exc:  # noqa: BLE001
        logger.debug("last.fm client unavailable: %s", exc)
        return None


def _client(lastfm):
    return configured_client() if lastfm is _UNSET else lastfm


def _fold(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def _from_lastfm(lastfm, call) -> Rows:
    if lastfm is None:
        return []
    try:
        return list(call(lastfm) or [])
    except Exception as exc:  # noqa: BLE001 - the free sources still run
        logger.debug("last.fm read failed: %s", exc)
        return []


# ── deezer (keyless) ──

def _deezer_get(endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    try:
        from core.deezer_throttle import wait_for_slot
        from core.metadata.registry import get_deezer_client
        client = get_deezer_client()
        if client is None or not hasattr(client, '_api_get'):
            return None
        wait_for_slot()
        return client._api_get(endpoint, dict(params or {}), use_token=False)
    except Exception as exc:  # noqa: BLE001
        logger.debug("deezer %s failed: %s", endpoint, exc)
        return None


def _deezer_artist_id(artist_name: str) -> Optional[str]:
    name = _fold(artist_name)
    if not name:
        return None

    def fetch():
        data = _deezer_get('search/artist', {'q': artist_name, 'limit': 5})
        if not isinstance(data, dict):
            return None
        hits = [h for h in (data.get('data') or []) if isinstance(h, dict) and h.get('id')]
        exact = [h for h in hits if _fold(h.get('name')) == name]
        pick = (exact or hits or [None])[0]
        return {'id': str(pick['id'])} if pick else {'id': ''}

    found, _how = provider_cache.cached('deezer', 'artist-id-by-name', {'name': artist_name},
                                        CATALOGUE_TTL, fetch)
    return (found or {}).get('id') or None


def _deezer_top_tracks(artist_name: str, limit: int) -> Rows:
    artist_id = _deezer_artist_id(artist_name)
    if not artist_id:
        return []

    def fetch():
        data = _deezer_get(f'artist/{artist_id}/top', {'limit': 50})
        if not isinstance(data, dict):
            return None
        # deezer has no play counts, so none are shown
        return [{'name': t.get('title') or '', 'playcount': 0, 'listeners': 0,
                 'url': t.get('link') or ''}
                for t in (data.get('data') or []) if isinstance(t, dict) and t.get('title')]

    rows, _how = provider_cache.cached('deezer', 'artist-top', {'id': artist_id}, CATALOGUE_TTL, fetch)
    return list(rows or [])[:limit]


def _deezer_radio(artist_name: str, track_title: str, limit: int) -> Rows:
    artist_id = _deezer_artist_id(artist_name)
    if not artist_id:
        return []

    def fetch():
        data = _deezer_get(f'artist/{artist_id}/radio', {'limit': 100})
        if not isinstance(data, dict):
            return None
        return [{'name': t.get('title') or '', 'artist': (t.get('artist') or {}).get('name') or ''}
                for t in (data.get('data') or []) if isinstance(t, dict) and t.get('title')]

    rows, _how = provider_cache.cached('deezer', 'artist-radio', {'id': artist_id}, CATALOGUE_TTL, fetch)
    seed_artist, seed_track = _fold(artist_name), _fold(track_title)
    others, own = [], []
    for row in rows or []:
        if _fold(row.get('name')) == seed_track and _fold(row.get('artist')) == seed_artist:
            continue
        (own if _fold(row.get('artist')) == seed_artist else others).append(row)
    picked = (others + own)[:limit]
    count = max(len(picked), 1)
    return [{'name': r['name'], 'artist': r['artist'], 'mbid': '',
             'match': round(1.0 - i / count, 4)} for i, r in enumerate(picked)]


def _deezer_related(artist_name: str, limit: int) -> Rows:
    artist_id = _deezer_artist_id(artist_name)
    if not artist_id:
        return []

    def fetch():
        data = _deezer_get(f'artist/{artist_id}/related', {'limit': 50})
        if not isinstance(data, dict):
            return None
        return [{'name': a.get('name') or '', 'mbid': '', 'match': 0.0}
                for a in (data.get('data') or []) if isinstance(a, dict) and a.get('name')]

    rows, _how = provider_cache.cached('deezer', 'artist-related', {'id': artist_id}, CATALOGUE_TTL, fetch)
    rows = list(rows or [])[:limit]
    count = max(len(rows), 1)
    return [dict(r, match=round(1.0 - i / count, 4)) for i, r in enumerate(rows)]


def _deezer_tag_artists(tag: str, limit: int) -> Rows:
    """deezer has no tags; its playlists named for the tag stand in. the
    artists that come up most across the top few are the tag's artists."""

    def fetch():
        found = _deezer_get('search/playlist', {'q': tag, 'limit': 5})
        if not isinstance(found, dict):
            return None
        counts: Dict[str, List[Any]] = {}
        for playlist in (found.get('data') or [])[:3]:
            if not isinstance(playlist, dict) or not playlist.get('id'):
                continue
            tracks = _deezer_get(f"playlist/{playlist['id']}/tracks", {'limit': 100}) or {}
            for track in tracks.get('data') or []:
                name = ((track or {}).get('artist') or {}).get('name') if isinstance(track, dict) else ''
                if name:
                    entry = counts.setdefault(_fold(name), [name, 0])
                    entry[1] += 1
        ranked = sorted(counts.values(), key=lambda e: -e[1])
        return [{'name': name, 'mbid': '', 'url': ''} for name, _n in ranked]

    # an empty answer here is a failed playlist read more often than a real
    # nothing, so it is not remembered
    rows, _how = provider_cache.cached('deezer', 'tag-artists', {'tag': tag}, CATALOGUE_TTL, fetch,
                                       keep=bool)
    return list(rows or [])[:limit]


def _deezer_search_tracks(query: str, limit: int) -> Rows:
    def fetch():
        data = _deezer_get('search/track', {'q': query, 'limit': 15})
        if not isinstance(data, dict):
            return None
        rows = []
        for t in data.get('data') or []:
            if not isinstance(t, dict) or not t.get('title'):
                continue
            album = t.get('album') or {}
            rows.append({'name': t.get('title') or '', 'artist': (t.get('artist') or {}).get('name') or '',
                         'mbid': '', 'listeners': 0,
                         'image_url': album.get('cover_medium') or album.get('cover') or ''})
        return rows

    rows, _how = provider_cache.cached('deezer', 'search-track', {'q': query}, 6 * 3600, fetch)
    return list(rows or [])[:limit]


# ── the reads ──

def artist_top_tracks(artist_name: str, limit: int = 10, *, lastfm: Any = _UNSET,
                      free_sources: Tuple[str, ...] = ('listenbrainz', 'deezer')) -> Tuple[Rows, str]:
    """(tracks, source). rows: {name, playcount, listeners, url}. source is
    'lastfm', 'listenbrainz', 'deezer' or '' (nothing anywhere)."""
    name = str(artist_name or '').strip()
    if not name:
        return [], ''
    rows = _from_lastfm(_client(lastfm), lambda c: c.get_artist_top_tracks(name, limit=limit))
    if rows:
        return rows[:limit], 'lastfm'
    if 'listenbrainz' in free_sources:
        from core import listenbrainz_auth, listenbrainz_public
        # its top recordings need a listenbrainz token: without one, skip
        # the musicbrainz lookup too and go straight to deezer
        mbid = None
        if listenbrainz_auth.available():
            mbid = listenbrainz_public.artist_mbid(name)
        else:
            listenbrainz_auth.note_missing()
        if mbid:
            rows = listenbrainz_public.top_recordings_for_artist(mbid, limit=limit)
            if rows:
                return [{'name': r['name'], 'playcount': r.get('playcount', 0),
                         'listeners': r.get('listeners', 0), 'url': r.get('url', '')}
                        for r in rows], 'listenbrainz'
    if 'deezer' in free_sources:
        rows = _deezer_top_tracks(name, limit)
        if rows:
            return rows, 'deezer'
    return [], ''


def similar_tracks(artist_name: str, track_title: str, limit: int = 25, *,
                   lastfm: Any = _UNSET) -> Tuple[Rows, str]:
    """(tracks, source). rows: {name, artist, match, mbid}. last.fm, then
    deezer's artist radio (the seed artist's neighbours first)."""
    artist, track = str(artist_name or '').strip(), str(track_title or '').strip()
    if not artist or not track:
        return [], ''
    rows = _from_lastfm(_client(lastfm), lambda c: c.get_similar_tracks(artist, track, limit=limit))
    if rows:
        return _in_season(rows, artist, track), 'lastfm'
    rows = _deezer_radio(artist, track, limit)
    if rows:
        return _in_season(rows, artist, track), 'deezer'
    return [], ''


def _in_season(rows: Rows, artist: str, track: str) -> Rows:
    """#2332: a radio off an everyday song leaves holiday songs out of
    season. a radio off a christmas song plays christmas"""
    try:
        from core.holiday_music import policy
        season = policy()
        if season.allows_holiday or season.is_holiday({'title': track, 'artist': artist}):
            return rows
        return [r for r in rows if not season.is_holiday(r)]
    except Exception:  # noqa: BLE001 - the radio matters more
        return rows


def similar_artists(artist_name: str, limit: int = 30, *, lastfm: Any = _UNSET) -> Tuple[Rows, str]:
    """(artists, source). rows: {name, mbid, match}. last.fm, listenbrainz,
    then deezer's related artists."""
    name = str(artist_name or '').strip()
    if not name:
        return [], ''
    rows = _from_lastfm(_client(lastfm), lambda c: c.get_similar_artists(name, limit=limit))
    if rows:
        return rows, 'lastfm'
    from core import listenbrainz_public
    mbid = listenbrainz_public.artist_mbid(name)
    if mbid:
        rows = listenbrainz_public.similar_artists(mbid, limit=limit)
        if rows:
            return rows, 'listenbrainz'
    rows = _deezer_related(name, limit)
    if rows:
        return rows, 'deezer'
    return [], ''


def tag_top_artists(tag: str, limit: int = 30, *, lastfm: Any = _UNSET) -> Tuple[Rows, str]:
    """(artists, source). rows: {name, mbid, url}. last.fm's tag chart, then
    the artists on deezer's playlists for the tag."""
    text = str(tag or '').strip()
    if not text:
        return [], ''
    rows = _from_lastfm(_client(lastfm), lambda c: c.get_tag_top_artists(text, limit=limit))
    if rows:
        return rows, 'lastfm'
    rows = _deezer_tag_artists(text, limit)
    if rows:
        return rows, 'deezer'
    return [], ''


def search_tracks(query: str, limit: int = 8, *, lastfm: Any = _UNSET) -> Tuple[Rows, str]:
    """(tracks, source) for a seed picker. rows: {name, artist, mbid,
    listeners, image_url}. last.fm's track search, then deezer's."""
    text = str(query or '').strip()
    if not text:
        return [], ''
    client = _client(lastfm)
    if client is not None:
        try:
            data = client._make_request('track.search', {'track': text, 'limit': limit})
        except Exception as exc:  # noqa: BLE001
            logger.debug("last.fm track search failed: %s", exc)
            data = None
        raw = ((data or {}).get('results') or {}).get('trackmatches', {}).get('track', []) if data else []
        if not isinstance(raw, list):
            raw = [raw] if raw else []
        rows = []
        for t in raw:
            if not isinstance(t, dict):
                continue
            image_url = client.get_best_image(t.get('image', [])) or ''
            # last.fm's grey-star placeholder loads fine, so the ui never
            # draws its own empty glyph: send '' instead
            if '2a96cbd8b46e442fc41c2b86b821562f' in image_url:
                image_url = ''
            try:
                listeners = int(t.get('listeners') or 0)
            except (TypeError, ValueError):
                listeners = 0
            rows.append({'name': t.get('name', ''), 'artist': t.get('artist', ''),
                         'mbid': t.get('mbid', ''), 'listeners': listeners, 'image_url': image_url})
        if rows:
            return rows, 'lastfm'
    rows = _deezer_search_tracks(text, limit)
    if rows:
        return rows, 'deezer'
    return [], ''
