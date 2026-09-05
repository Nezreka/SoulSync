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
from typing import Optional

from core.wishlist.presence import load_wishlist_keys as _load_wishlist_keys_shared
from core.wishlist.presence import presence_key as _presence_key

logger = logging.getLogger(__name__)


# Ownership is asked of Library v2 (docs §50.4.4.14). Three things the port had
# to settle:
#
# **"Owned" requires a physical active file.** ``origin`` records provenance,
# not whether a usable file is still present.
#
# **The comparison key is built in Python on both sides.** It always was on the
# search-result side; the catalogue side used SQL ``LOWER()``, which is
# ASCII-only, so a stored ``Björk`` and a searched ``BJÖRK`` folded to different
# strings and an owned track was reported missing. Same normalizer both sides
# now — the whole table is read into a dict here anyway, so nothing is paid for
# it.
#
# **A path is a file row.** ``file_path`` comes from the primary active file
# (ADR-03); a known, unfetched catalogue track is not reported as owned.
_OWNED_ALBUMS_SQL = """
    SELECT al.title, ar.name
      FROM lib2_albums al
      JOIN lib2_artists ar ON ar.id = al.primary_artist_id
     WHERE EXISTS (SELECT 1 FROM lib2_tracks t JOIN lib2_track_files f
                   ON f.track_id=t.id WHERE t.album_id=al.id
                   AND f.file_state='active' AND TRIM(f.path)<>'')
"""

# INT-03: ownership is keyed on the TRACK's artist. Joining only
# ``lib2_albums.primary_artist_id`` meant a Muse track sitting on a Various
# Artists compilation was keyed (title, "Various Artists") and nothing else —
# so a search for the Muse track reported the file we already own as missing,
# and the user was one click from downloading it a second time. Every credit the
# catalogue holds for the track is emitted as its own key: the per-track credit
# text, each relational track artist, and the album artist as the fallback it
# always was. ``name_rank`` keeps the album artist last so an existing key still
# resolves to the row it used to.
_TRACK_CREDIT_NAMES_SQL = """
    SELECT ta.track_id AS track_id, ar.name AS name, 0 AS name_rank
      FROM lib2_track_artists ta
      JOIN lib2_artists ar ON ar.id = ta.artist_id
     WHERE TRIM(COALESCE(ar.name, '')) <> ''
    UNION ALL
    SELECT t.id, t.track_artist, 1
      FROM lib2_tracks t
     WHERE TRIM(COALESCE(t.track_artist, '')) <> ''
    UNION ALL
    SELECT t.id, ar.name, 2
      FROM lib2_tracks t
      JOIN lib2_albums al ON al.id = t.album_id
      JOIN lib2_artists ar ON ar.id = al.primary_artist_id
     WHERE TRIM(COALESCE(ar.name, '')) <> ''
"""

_OWNED_TRACKS_SQL = f"""
    WITH track_credits AS ({_TRACK_CREDIT_NAMES_SQL})
    SELECT t.title, credit.name,
           COALESCE((SELECT m.server_id FROM lib2_media_server_mappings m
                      WHERE m.entity_type='track' AND m.entity_id=t.id
                        AND m.server_source=? LIMIT 1),
                    CASE WHEN t.server_source=? THEN t.server_id END,
                    t.legacy_track_id,
                    -- Always populated, so the projection cannot come back
                    -- identity-less. After the legacy cutover a natively
                    -- imported track has no mapping, no matching server_source
                    -- and no legacy id, and the player was handed ''.
                    t.id),
           t.id,
           al.title, al.image_url,
           (SELECT f.path FROM lib2_track_files f
             WHERE f.track_id = t.id
               AND COALESCE(f.file_state, 'active') = 'active'
               AND f.path IS NOT NULL AND f.path != ''
             ORDER BY f.is_primary DESC, f.id LIMIT 1)
      FROM lib2_tracks t
      JOIN lib2_albums al ON al.id = t.album_id
      JOIN track_credits credit ON credit.track_id = t.id
     WHERE EXISTS (SELECT 1 FROM lib2_track_files owned_f WHERE owned_f.track_id=t.id
                   AND owned_f.file_state='active' AND TRIM(owned_f.path)<>'')
     ORDER BY (EXISTS (SELECT 1 FROM lib2_media_server_mappings active_m
                        WHERE active_m.entity_type='track'
                          AND active_m.entity_id=t.id
                          AND active_m.server_source=?)
               OR t.server_source=?) DESC,
              credit.name_rank,
              t.id
"""


def _primary_artist(raw: str) -> str:
    """A search result credits every artist; ownership is keyed on the first."""
    return str(raw or '').split(',')[0]


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

        cursor.execute(_OWNED_ALBUMS_SQL)
        owned_albums = {_presence_key(r[0], r[1]) for r in cursor.fetchall()}

        active_server = getattr(
            config_manager, 'get_active_media_server',
            lambda: config_manager.get('media_server.type', 'plex'))()
        cursor.execute(
            _OWNED_TRACKS_SQL,
            (active_server, active_server, active_server, active_server),
        )
        owned_tracks: dict[str, dict] = {}
        for r in cursor.fetchall():
            key = _presence_key(r[0], r[1])
            if key not in owned_tracks:  # keep first match only
                owned_tracks[key] = {
                    'track_id': r[2],
                    'lib2_track_id': r[3],
                    'file_path': r[6],
                    'title': r[0],
                    'artist_name': r[1],
                    'album_title': r[4],
                    'album_thumb_url': r[5],
                }

        wishlist_keys = _load_wishlist_keys(cursor, profile_id)

        album_results: list[bool] = []
        for a in albums:
            key = _presence_key(a.get('name', ''), _primary_artist(a.get('artist', '')))
            album_results.append(key in owned_albums)

        plex_base, plex_token = _resolve_plex_credentials(plex_client, config_manager)

        track_results: list[dict] = []
        for t in tracks:
            key = _presence_key(t.get('name', ''), _primary_artist(t.get('artist', '')))
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
