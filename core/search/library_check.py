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

# Multi-artist delimiters — the same set used by the matching engine's
# featured-artist splitter, plus a comma which MusicBrainz join-phrases
# and CSV-formatted metadata both use.
_ARTIST_SPLIT_RE = re.compile(
    r'\s*(?:[;,&]|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b|\bvs\.?\b)\s*',
    re.IGNORECASE,
)


def _norm_key(text: str) -> str:
    """Normalise text for ownership-key comparison.

    Applies accent folding (Björk → bjork), lowercases, and strips every
    non-alphanumeric character so that punctuation / spacing / "The" prefix
    differences never break a match.
    """
    from core.text.normalize import normalize_for_comparison
    # normalize_for_comparison already lowercases + accent-folds
    normed = normalize_for_comparison(text or '')
    # strip non-alphanumeric (keeps digits + letters only)
    return re.sub(r'[^a-z0-9]', '', normed)


def _first_artist(name: str) -> str:
    """The first artist from a potentially multi-artist string.

    MusicBrainz join-phrases ('A & B', 'A feat. B') and CSV metadata
    ('A, B') produce combined artist names. Library albums are filed under
    the primary artist, so splitting and trying the first one is the right
    heuristic.
    """
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
    """
    conn = database._get_connection()
    try:
        cursor = conn.cursor()

        # --- Album ownership (normalised) ------------------------------------
        cursor.execute(
            "SELECT al.title, ar.name "
            "FROM albums al JOIN artists ar ON ar.id = al.artist_id"
        )
        owned_albums: set[str] = set()
        for row in cursor.fetchall():
            db_title, db_artist = row[0] or '', row[1] or ''
            # Full artist name key
            owned_albums.add(_album_key(db_title, db_artist))
            # Also index the FIRST artist so "Nirvana" matches a query for
            # "Nirvana & Foo Fighters" (and vice-versa).
            first = _first_artist(db_artist)
            if first and first != db_artist:
                owned_albums.add(_album_key(db_title, first))

        # --- Track ownership (normalised) ------------------------------------
        cursor.execute(
            """
            SELECT t.title, a.name, t.id, t.file_path,
                   al.title, al.thumb_url
            FROM tracks t
            JOIN artists a ON a.id = t.artist_id
            JOIN albums al ON al.id = t.album_id
            """
        )
        owned_tracks: dict[str, dict] = {}
        for r in cursor.fetchall():
            track_title, artist_name = r[0] or '', r[1] or ''
            key = _norm_key(track_title) + '|||' + _norm_key(artist_name)
            if key not in owned_tracks:  # keep first match only
                owned_tracks[key] = {
                    'track_id': r[2],
                    'file_path': r[3],
                    'title': r[0],
                    'artist_name': r[1],
                    'album_title': r[4],
                    'album_thumb_url': r[5],
                }
            # Also index by first artist
            first = _first_artist(artist_name)
            if first and first != artist_name:
                first_key = _norm_key(track_title) + '|||' + _norm_key(first)
                if first_key not in owned_tracks:
                    owned_tracks[first_key] = owned_tracks[key]

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

        # --- Match albums ----------------------------------------------------
        album_results: list[bool] = []
        for a in albums:
            q_name = a.get('name', '')
            q_artist = a.get('artist', '')
            # Try: full artist → first-artist-of-query → first-artist-of-csv
            keys_to_try = {_album_key(q_name, q_artist)}
            first_q = _first_artist(q_artist)
            if first_q:
                keys_to_try.add(_album_key(q_name, first_q))
            # CSV split (legacy behaviour: comma-separated)
            csv_first = q_artist.split(',')[0].strip()
            if csv_first:
                keys_to_try.add(_album_key(q_name, csv_first))
            album_results.append(bool(keys_to_try & owned_albums))

        plex_base, plex_token = _resolve_plex_credentials(plex_client, config_manager)

        # --- Match tracks ----------------------------------------------------
        track_results: list[dict] = []
        for t in tracks:
            t_name = t.get('name', '')
            t_artist = t.get('artist', '')
            keys_to_try = [_norm_key(t_name) + '|||' + _norm_key(t_artist)]
            first_t = _first_artist(t_artist)
            if first_t:
                keys_to_try.append(_norm_key(t_name) + '|||' + _norm_key(first_t))
            csv_first = t_artist.split(',')[0].strip()
            if csv_first:
                keys_to_try.append(_norm_key(t_name) + '|||' + _norm_key(csv_first))

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

