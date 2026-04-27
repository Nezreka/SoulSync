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

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


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
    """Build a set of `name|||artist` keys from the wishlist for fast lookup.

    Try the profile-aware schema first; fall back to the legacy schema if
    profile_id column is missing (older DBs). Errors at any level are
    swallowed — wishlist annotation is best-effort.
    """
    keys: set[str] = set()

    def _absorb(rows):
        for wr in rows:
            try:
                wd = json.loads(wr[0]) if isinstance(wr[0], str) else {}
                wname = (wd.get('name') or '').lower()
                wartists = wd.get('artists', [])
                if wartists:
                    first = wartists[0]
                    wa = first.get('name', '') if isinstance(first, dict) else str(first)
                else:
                    wa = ''
                if wname:
                    keys.add(wname + '|||' + wa.lower().strip())
            except Exception:
                pass

    try:
        cursor.execute("SELECT spotify_data FROM wishlist_tracks WHERE profile_id = ?", (profile_id,))
        _absorb(cursor.fetchall())
        return keys
    except Exception:
        pass

    try:
        cursor.execute("SELECT spotify_data FROM wishlist_tracks")
        _absorb(cursor.fetchall())
    except Exception:
        pass
    return keys


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

        cursor.execute(
            "SELECT LOWER(al.title) || '|||' || LOWER(ar.name) "
            "FROM albums al JOIN artists ar ON ar.id = al.artist_id"
        )
        owned_albums = {r[0] for r in cursor.fetchall()}

        cursor.execute(
            """
            SELECT LOWER(t.title) || '|||' || LOWER(a.name), t.id, t.file_path,
                   t.title, a.name, al.title, al.thumb_url
            FROM tracks t
            JOIN artists a ON a.id = t.artist_id
            JOIN albums al ON al.id = t.album_id
            """
        )
        owned_tracks: dict[str, dict] = {}
        for r in cursor.fetchall():
            if r[0] not in owned_tracks:  # keep first match only
                owned_tracks[r[0]] = {
                    'track_id': r[1],
                    'file_path': r[2],
                    'title': r[3],
                    'artist_name': r[4],
                    'album_title': r[5],
                    'album_thumb_url': r[6],
                }

        wishlist_keys = _load_wishlist_keys(cursor, profile_id)

        album_results: list[bool] = []
        for a in albums:
            key = (a.get('name', '').lower() + '|||' + a.get('artist', '').split(',')[0].strip().lower())
            album_results.append(key in owned_albums)

        plex_base, plex_token = _resolve_plex_credentials(plex_client, config_manager)

        track_results: list[dict] = []
        for t in tracks:
            key = (t.get('name', '').lower() + '|||' + t.get('artist', '').split(',')[0].strip().lower())
            in_wishlist = key in wishlist_keys
            match = owned_tracks.get(key)
            if match:
                thumb = match.get('album_thumb_url') or ''
                match['album_thumb_url'] = _resolve_plex_thumb(thumb, plex_base, plex_token)
                track_results.append({'in_library': True, 'in_wishlist': in_wishlist, **match})
            else:
                track_results.append({'in_library': False, 'in_wishlist': in_wishlist})
    finally:
        conn.close()

    return {'albums': album_results, 'tracks': track_results}
