"""Idempotent Library-v2 materialization for confirmed wishlist/acquisition
intents (docs/library-v2.md §52.8).

Every entry path that writes a CONFIRMED track/release intent into the
legacy Wishlist — Search-page "Add to Wishlist", Playlist-Sync's unmatched-
track auto-add, and the Watchlist-Scanner's new-release detection — must
resolve/create the same Library-v2 Artist/Release/Track rows and record
explicit track-level monitoring, so the lib2 entity already exists and is
readable before Search/Download starts. That way even a hard-fail or
quarantine (which never reaches the post-download autolink step) still has
an entity to attach its history to. An unconfirmed click on a search RESULT
that never becomes a Wishlist/Acquisition write must NOT call this — nothing
here fires without an actual confirmed write already having happened.

Reuse-first: the resolve-or-create semantics are exactly the ones
``core/library2/autolink.py`` already uses for the POST-download link step;
this module runs the same resolver PRE-download/PRE-search. Wishlist database
writes materialize on the same connection and roll back if monitoring fails.
The standalone compatibility adapter remains best-effort.

Only the named TRACK becomes explicitly monitored/wanted here — this must
never silently expand into the whole artist's watchlist (that stays gated on
the artist bookmark / Artist Settings per §52.3).
"""

from __future__ import annotations

import json
from contextlib import closing
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

from .autolink import find_or_create_album, find_or_create_artist, find_or_create_track
from .monitor_rules import PROVENANCE_WISHLIST, record_rule
from .profile_lookup import assign_quality_profile, effective_quality_profile
from .wanted import recompute_wanted_for_entity

logger = get_logger("library2.materialize")


def materialize_track_intent(
    conn,
    *,
    artist_name: str,
    track_title: str,
    artist_spotify_id: Optional[str] = None,
    artist_provider_id: Optional[str] = None,
    album_title: Optional[str] = None,
    album_spotify_id: Optional[str] = None,
    album_provider_id: Optional[str] = None,
    album_type: str = "album",
    track_spotify_id: Optional[str] = None,
    track_provider_id: Optional[str] = None,
    track_number: Optional[int] = None,
    disc_number: Optional[int] = None,
    explicit_profile_id: Optional[int] = None,
    provenance: str = PROVENANCE_WISHLIST,
    profile_id: int = 1,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve-or-create Artist/Release/Track, optionally pin an explicit
    track profile, mark the concrete track monitored/wanted, and return the
    resolved ids + effective profile.

    Does not commit and does not mirror into the wishlist itself — callers
    keep their own mirror/dispatch call (``mirror_tracks_wishlist`` or the
    legacy ``add_to_wishlist``); this only guarantees the lib2 entity, the
    explicit profile (when one was actually chosen) and the wanted rule
    exist first. Idempotent: safe to call repeatedly for the same track.
    """
    if not artist_name or not track_title:
        raise ValueError("materialize_track_intent requires artist_name and track_title")

    artist_identity = artist_provider_id or artist_spotify_id
    album_identity = album_provider_id or album_spotify_id
    track_identity = track_provider_id or track_spotify_id
    artist_id = find_or_create_artist(conn, artist_name, spotify_id=artist_identity,
                                      source=source)
    if artist_id is None:
        raise ValueError(f"could not resolve or create artist {artist_name!r}")

    album_id = find_or_create_album(
        conn, artist_id, album_title or track_title,
        album_type=album_type, spotify_album_id=album_identity,
        source=source, monitored=0)

    track_id = find_or_create_track(
        conn, album_id, artist_id, track_title,
        track_number=track_number, spotify_track_id=track_identity,
        disc_number=disc_number, source=source, monitored=1 if int(profile_id) == 1 else 0)
    if int(profile_id) == 1:
        conn.execute('UPDATE lib2_tracks SET monitored=1 WHERE id=? AND monitored=0', (track_id,))

    if explicit_profile_id is not None and int(profile_id) == 1:
        assign_quality_profile(conn, "tracks", track_id, int(explicit_profile_id))

    record_rule(conn, "track", track_id, True, provenance, profile_id=profile_id)
    recompute_wanted_for_entity(conn, "track", track_id, profile_id=profile_id)

    return {
        "artist_id": artist_id,
        "album_id": album_id,
        "track_id": track_id,
        "quality_profile": effective_quality_profile(conn, "tracks", track_id),
    }


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def materialize_wishlist_row(conn, row_id: int, *, profile_id: int = 1) -> Optional[int]:
    """Persist queue membership and track intent together; never mirror back into the queue."""
    row = conn.execute('SELECT * FROM wishlist_tracks WHERE id=? AND profile_id=?', (row_id, profile_id)).fetchone()
    if row is None:
        return None
    info = json.loads(row['source_info'] or '{}')
    if not isinstance(info, dict):
        info = {}
    if info.get('source') == 'library_v2':
        return None  # A projection mirror must retain inherited intent, without pinning a track rule.
    data = json.loads(row['spotify_data'] or '{}')
    from core.library2.importer import _wishlist_provider
    from core.library2.provider_ids import provider_id_sql
    data.setdefault('id', str(row['spotify_track_id']).split('::', 1)[0])
    data['source'] = _wishlist_provider(data, info)
    native_id = _int_or_none(info.get('lib2_track_id'))
    track = conn.execute('SELECT * FROM lib2_tracks WHERE id=?', (native_id,)).fetchone() if native_id else None
    if track is None and str(data.get('id') or '').startswith('lib2-track:'):
        track = conn.execute('SELECT * FROM lib2_tracks WHERE stable_id=?', (data['id'].removeprefix('lib2-track:'),)).fetchone()
    if track is None:
        album = data.get('album') or {}
        album_id = album.get('id') if isinstance(album, dict) else None
        matches = conn.execute(f"SELECT t.* FROM lib2_tracks t JOIN lib2_albums al ON al.id=t.album_id "
                               f"WHERE {provider_id_sql(data['source'], alias='t')}=?" +
                               (f" AND {provider_id_sql(data['source'], alias='al')}=?" if album_id else ''),
                               (str(data['id']), str(album_id)) if album_id else (str(data['id']),)).fetchall()
        if len(matches) > 1:
            raise ValueError('Wishlist identity matches multiple Library tracks; select the release edition')
        track = matches[0] if matches else None
    if track is None:
        data.setdefault('name', 'Unknown Track')
        data['artists'] = data.get('artists') or [{'name': 'Unknown Artist'}]
        result = materialize_from_spotify_track(conn, data, profile_id=profile_id, explicit_profile_id=row['quality_profile_id'])
        if result is None:
            raise ValueError('Wishlist track cannot be linked to Library: missing title or artist')
        track_id, album_id = result['track_id'], result['album_id']
    else:
        track_id, album_id = track['id'], track['album_id']
        if int(profile_id) == 1:
            conn.execute('UPDATE lib2_tracks SET monitored=1 WHERE id=? AND monitored=0', (track_id,))
            if row['quality_profile_id'] is not None and (track['quality_profile_id'] != row['quality_profile_id'] or not track['quality_profile_explicit']):
                assign_quality_profile(conn, 'tracks', track_id, int(row['quality_profile_id']))
        rule = conn.execute("SELECT monitored FROM lib2_monitor_rules WHERE entity_type='track' AND entity_id=? AND profile_id=?", (track_id, profile_id)).fetchone()
        if rule is None or not rule[0]:
            record_rule(conn, 'track', track_id, True, PROVENANCE_WISHLIST, profile_id=profile_id)
        recompute_wanted_for_entity(conn, 'track', track_id, profile_id=profile_id)
    if info.get('lib2_track_id') != track_id or info.get('lib2_album_id') != album_id:
        info.update(lib2_track_id=track_id, lib2_album_id=album_id)
        conn.execute('UPDATE wishlist_tracks SET source_info=? WHERE id=?', (json.dumps(info), row_id))
    return int(track_id)


def materialize_from_spotify_track(
    conn,
    spotify_track_data: Dict[str, Any],
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """Adapt the common ``spotify_track_data`` dict shape (search results,
    playlist sync, watchlist scan — an ``{"id","name","artists":[...],
    "album":{...}}`` object) into ``materialize_track_intent``.

    Returns ``None`` without side effects when the minimum fields (a track
    title and at least one artist name) are missing — e.g. a wing-it
    synthetic entry.
    """
    if not isinstance(spotify_track_data, dict):
        return None
    track_title = spotify_track_data.get("name")
    artists = spotify_track_data.get("artists") or []
    artist = artists[0] if artists else {}
    if isinstance(artist, str):
        artist = {"name": artist}
    if not isinstance(artist, dict):
        artist = {}
    artist_name = artist.get("name")
    if not track_title or not artist_name:
        return None

    album = spotify_track_data.get("album")
    if not isinstance(album, dict):
        album = {}
    album_title = album.get("name") or track_title
    total_tracks = album.get("total_tracks")
    album_type = str(album.get("album_type") or "").lower() or (
        "single" if total_tracks in (1, "1") else "album")

    # §62.4: the payload's actual provider, when the caller recorded one —
    # the id fields of this "spotify-shaped" dict hold THAT provider's ids.
    source = str(
        spotify_track_data.get("source") or spotify_track_data.get("provider") or ""
    ).strip().lower() or "spotify"
    track_id_raw = (str(spotify_track_data["id"])
                    if spotify_track_data.get("id") else None)
    return materialize_track_intent(
        conn,
        artist_name=str(artist_name),
        artist_provider_id=(str(artist["id"]) if artist.get("id") else None),
        album_title=str(album_title),
        album_provider_id=(str(album["id"]) if album.get("id") else None),
        album_type=album_type,
        track_title=str(track_title),
        track_provider_id=track_id_raw,
        track_number=_int_or_none(spotify_track_data.get("track_number")),
        disc_number=_int_or_none(spotify_track_data.get("disc_number")),
        source=source,
        **kwargs,
    )


def materialize_wishlist_intent(
    spotify_track_data: Dict[str, Any],
    *,
    explicit_profile_id: Optional[int] = None,
    provenance: str = PROVENANCE_WISHLIST,
    profile_id: int = 1,
    actor_profile_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Best-effort, fail-open entry point for callers OUTSIDE ``core.library2``
    (Search routes, Playlist-Sync, Watchlist-Scanner): opens its own
    connection, commits on success, and never raises — mirroring the safety
    contract of ``autolink.link_download_into_library_v2`` so a
    materialization failure can never break the wishlist add it accompanies.
    """
    try:
        from core.library2 import ADMIN_PROFILE_ID
        # Library-v2 intent is global. Fail closed unless the caller proves
        # that the action belongs to the admin profile; a non-admin wishlist,
        # watchlist or playlist may only mutate its own legacy list.
        # Arithmetic on purpose: this runs inside a caller's open write
        # transaction, and a profile lookup here would build and
        # schema-initialise a second MusicDatabase, which then waits on
        # the write lock the caller is holding. A second admin loses
        # nothing by it — the `profile_id` check below already requires
        # the admin profile, so a non-admin's own wishlist add is
        # refused on the row key whatever the actor turns out to be.
        if int(actor_profile_id or 0) != ADMIN_PROFILE_ID:
            return None
        if int(profile_id) != ADMIN_PROFILE_ID:
            return None
        # The `library_v2_enabled(config_manager)` call that stood here was a
        # side-effect-only no-op: the function returns True unconditionally
        # (the cutover is not reversible through config) and its one-time
        # deprecation warning only ever fires when the key is set FALSY --
        # which on a default install it never is, so the flag stays unset
        # and this paid for a config read forever, on a path that runs per
        # completed download / per wishlist item. One call at boot
        # (core/library2/bootstrap.py) delivers 100% of the warning.
        from database.music_database import get_database
        db = get_database()
        with closing(db._get_connection()) as conn:
            result = materialize_from_spotify_track(
                conn, spotify_track_data,
                explicit_profile_id=explicit_profile_id,
                provenance=provenance,
                profile_id=profile_id,
            )
            if result is not None:
                conn.commit()
            return result
    except Exception as e:  # noqa: BLE001
        logger.debug("wishlist materialization failed: %s", e)
        return None


__all__ = [
    "materialize_from_spotify_track",
    "materialize_track_intent",
    "materialize_wishlist_intent",
    "materialize_wishlist_row",
]
