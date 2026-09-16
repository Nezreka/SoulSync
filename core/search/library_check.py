"""Batch library presence check for search results.

Given a list of `albums` and `tracks` from a metadata search, return per-row
booleans (and matched-row metadata for tracks) indicating whether each
result is already in the user's library or wishlist. Plex relative-path
thumb URLs are rewritten to absolute URLs with token.

Called async from the frontend after the main search renders, so the user
sees results immediately and "in library" badges fade in once the check
completes.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from core.wishlist.presence import load_wishlist_keys as _load_wishlist_keys_shared

logger = logging.getLogger(__name__)

# Only explicit featured-artist credits are safe to split without artist IDs.
# Commas and ampersands also occur inside indivisible band names.
_ARTIST_SPLIT_RE = re.compile(r'\s+\b(?:feat|ft|featuring)\b\.?\s*', re.IGNORECASE)


def _norm_key(text: str) -> str:
    """Normalise text for ownership-key comparison.

    Applies accent folding (Björk → bjork), lowercases, and strips every
    non-alphanumeric character so that punctuation / spacing
    differences never break a match. the same function fills
    artists.name_key, which is how a result row finds its artist by index.
    """
    from core.text.normalize import normalize_key
    return normalize_key(text or '')


def _first_artist(name: str) -> str:
    """Return the primary credit when an explicit featured artist is present."""
    parts = _ARTIST_SPLIT_RE.split(name or '')
    return parts[0].strip() if parts else (name or '').strip()


def _album_key(album_title: str, artist_name: str) -> str:
    """Build a normalised album ownership key."""
    return _norm_key(album_title) + '|||' + _norm_key(artist_name)


def _resolve_plex_thumb(thumb: str, plex_base: str, plex_token: str) -> str:
    """Rewrite a Plex relative thumb path to an absolute URL with token."""
    if not thumb or thumb.startswith('http') or not plex_base or not thumb.startswith('/'):
        return thumb
    if plex_token:
        return f"{plex_base}{thumb}?X-Plex-Token={plex_token}"
    return f"{plex_base}{thumb}"


def _resolve_plex_credentials(plex_client, config_manager) -> tuple[str, str]:
    """Pull (base_url, token) for the active Plex server.

    Prefers the live `plex_client.server` attrs; falls back to config_manager
    if the live client isn't connected yet. Mirrors original web_server.py
    inline logic byte-for-byte.
    """
    base, token = '', ''
    if plex_client and plex_client.server:
        base = getattr(plex_client.server, '_baseurl', '') or ''
        token = getattr(plex_client.server, '_token', '') or ''
    if not base:
        cfg = config_manager.get_plex_config()
        base = (cfg.get('base_url', '') or '').rstrip('/')
        token = token or cfg.get('token', '')
    return base, token


def _load_wishlist_keys(cursor, profile_id: int) -> set[str]:
    return _load_wishlist_keys_shared(cursor, profile_id)


def _artist_ids_for(cursor, database, names: list[str]) -> list:
    """library artist ids whose name_key equals any of these names' keys.
    indexed on artists.name_key; a library that hasn't finished its norm
    backfill gets the same answer from a scan of the (small) artists table."""
    keys = list(dict.fromkeys(_norm_key(n) for n in names if n))
    keys = [k for k in keys if k]
    if not keys:
        return []
    ph = ','.join('?' for _ in keys)
    if database._norm_ready(cursor):
        cursor.execute(f"SELECT id FROM artists WHERE name_key IN ({ph})", keys)
        return [r[0] for r in cursor.fetchall()]
    cursor.execute("SELECT id, name FROM artists")
    return [r[0] for r in cursor.fetchall() if _norm_key(r[1] or '') in keys]


def _owned_album_keys(cursor, artist_ids: list) -> set[str]:
    if not artist_ids:
        return set()
    ph = ','.join('?' for _ in artist_ids)
    cursor.execute(
        f"SELECT al.title, ar.name FROM albums al JOIN artists ar ON ar.id = al.artist_id "
        f"WHERE al.artist_id IN ({ph})", artist_ids)
    keys: set[str] = set()
    for row in cursor.fetchall():
        db_title, db_artist = row[0] or '', row[1] or ''
        keys.add(_album_key(db_title, db_artist))
        first = _first_artist(db_artist)
        if first and first != db_artist:
            keys.add(_album_key(db_title, first))
    return keys


def _owned_tracks_for(cursor, artist_ids: list) -> dict[str, dict]:
    if not artist_ids:
        return {}
    ph = ','.join('?' for _ in artist_ids)
    cursor.execute(
        f"""
        SELECT t.title, a.name, t.id, t.file_path, al.title, al.thumb_url
        FROM tracks t
        JOIN artists a ON a.id = t.artist_id
        JOIN albums al ON al.id = t.album_id
        WHERE t.artist_id IN ({ph})
        """, artist_ids)
    owned: dict[str, dict] = {}
    for r in cursor.fetchall():
        track_title, artist_name = r[0] or '', r[1] or ''
        key = _norm_key(track_title) + '|||' + _norm_key(artist_name)
        if key not in owned:  # keep first match only
            owned[key] = {
                'track_id': r[2],
                'file_path': r[3],
                'title': r[0],
                'artist_name': r[1],
                'album_title': r[4],
                'album_thumb_url': r[5],
            }
        first = _first_artist(artist_name)
        if first and first != artist_name:
            first_key = _norm_key(track_title) + '|||' + _norm_key(first)
            if first_key not in owned:
                owned[first_key] = owned[key]
    return owned


def check_library_presence(
    database,
    plex_client,
    config_manager,
    profile_id: int,
    albums: list[dict],
    tracks: list[dict],
) -> dict:
    """Return `{albums: [bool], tracks: [{...}]}` for the given search results.

    - `albums` returns one bool per input row.
    - `tracks` returns one dict per input row. Matched rows get the full
      track metadata + resolved thumb URL; unmatched rows get
      `{in_library: False, in_wishlist: bool}`.

    this used to read EVERY album and EVERY track in the library into python
    on every call, normalizing each, to build two lookup dicts (a million
    rows per search on a big library, on the request thread, after every
    search and every chat wanted card). now each result row looks up its
    artist by indexed key and compares against that artist's rows only, with
    the same key function, so the answers are identical and the cost is
    proportional to the results rather than the library.
    """
    conn = database._get_connection()
    try:
        cursor = conn.cursor()
        artist_cache: dict[tuple, list] = {}
        album_keys_cache: dict[tuple, set] = {}
        tracks_cache: dict[tuple, dict] = {}

        def _ids(q_artist: str) -> tuple:
            names = [q_artist]
            first = _first_artist(q_artist)
            if first:
                names.append(first)
            key = tuple(dict.fromkeys(_norm_key(n) for n in names if n))
            if key not in artist_cache:
                artist_cache[key] = _artist_ids_for(cursor, database, names)
            return tuple(artist_cache[key])

        # --- Match albums ----------------------------------------------------
        album_results: list[bool] = []
        for a in albums:
            q_name = a.get('name', '')
            q_artist = a.get('artist', '')
            ids = _ids(q_artist)
            if ids not in album_keys_cache:
                album_keys_cache[ids] = _owned_album_keys(cursor, list(ids))
            owned_albums = album_keys_cache[ids]
            # Try the full credit before an explicit featured-artist fallback.
            keys_to_try = {_album_key(q_name, q_artist)}
            first_q = _first_artist(q_artist)
            if first_q:
                keys_to_try.add(_album_key(q_name, first_q))
            album_results.append(bool(keys_to_try & owned_albums))

        raw_wishlist_keys = _load_wishlist_keys(cursor, profile_id)
        # Normalise wishlist keys the same way we normalise owned keys,
        # so the lookup uses the same alphabet.
        wishlist_keys: set[str] = set()
        for wk in raw_wishlist_keys:
            parts = wk.split('|||', 1)
            if len(parts) == 2:
                wishlist_keys.add(_norm_key(parts[0]) + '|||' + _norm_key(parts[1]))
            else:
                wishlist_keys.add(_norm_key(wk))

        plex_base, plex_token = _resolve_plex_credentials(plex_client, config_manager)

        # --- Match tracks ----------------------------------------------------
        track_results: list[dict] = []
        for t in tracks:
            t_name = t.get('name', '')
            t_artist = t.get('artist', '')
            ids = _ids(t_artist)
            if ids not in tracks_cache:
                tracks_cache[ids] = _owned_tracks_for(cursor, list(ids))
            owned_tracks = tracks_cache[ids]
            keys_to_try = [_norm_key(t_name) + '|||' + _norm_key(t_artist)]
            first_t = _first_artist(t_artist)
            if first_t:
                keys_to_try.append(_norm_key(t_name) + '|||' + _norm_key(first_t))

            in_wishlist = any(k in wishlist_keys for k in keys_to_try)
            match = None
            for k in keys_to_try:
                match = owned_tracks.get(k)
                if match:
                    break
            if match:
                thumb = match.get('album_thumb_url') or ''
                match['album_thumb_url'] = _resolve_plex_thumb(thumb, plex_base, plex_token)
                track_results.append({'in_library': True, 'in_wishlist': in_wishlist, **match})
            else:
                track_results.append({'in_library': False, 'in_wishlist': in_wishlist})
    finally:
        conn.close()

    return {'albums': album_results, 'tracks': track_results}
