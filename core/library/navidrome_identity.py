"""Validate Navidrome playlist IDs and repair confirmed same-path rekeys."""
from collections import defaultdict
from contextvars import ContextVar
from functools import wraps
from inspect import signature
import posixpath
import time


class IdentityError(RuntimeError):
    pass


def _path(value):
    return posixpath.normpath(str(value).replace('\\', '/')) if value else ''


def _request(client, endpoint, params=None):
    return client._make_request(endpoint, params, timeout=(3.05, 10))


def _scan_stamp(client):
    response = _request(client, 'getScanStatus')
    state = (response or {}).get('scanStatus')
    if not isinstance(state, dict) or state.get('scanning') is not False:
        raise IdentityError('Navidrome scan state is unavailable or a scan is running')
    return state.get('count')


def read_inventory(client, page_size=500):
    """A fresh, complete OpenSubsonic search3 inventory; never return partial data."""
    deadline = time.monotonic() + 60
    before = _scan_stamp(client)
    songs = {}
    offset = 0
    while offset < 1000000:
        params = dict(query='', artistCount=0, albumCount=0, songCount=page_size, songOffset=offset)
        if time.monotonic() > deadline:
            raise IdentityError('Navidrome inventory timed out; no changes made')
        # Whole server: a selected folder must not make other live IDs look obsolete.
        response = _request(client, 'search3', params)
        result = (response or {}).get('searchResult3')
        if not isinstance(result, dict):
            raise IdentityError('Incomplete Navidrome song inventory; no changes made')
        page = result.get('song', [])
        if not isinstance(page, list):
            raise IdentityError('Invalid Navidrome song inventory')
        for song in page:
            sid = str(song.get('id') or '') if isinstance(song, dict) else ''
            if not sid or sid in songs:
                raise IdentityError('Missing or repeated song ID in Navidrome inventory')
            songs[sid] = song
        if not page:
            if _scan_stamp(client) != before:
                raise IdentityError('Navidrome library changed while reading its inventory')
            return songs
        # Keep paging even after a short page: servers may cap the requested size.
        offset += len(page)
    raise IdentityError('Navidrome inventory exceeded its safety limit')


def _by_path(songs):
    paths = defaultdict(list)
    for sid, song in songs.items():
        path = _path(song.get('path'))
        if path:
            paths[path].append(sid)
    return paths


def _same_recording(old, song):
    title = ' '.join(str(old.get('title') or '').casefold().split())
    current = ' '.join(str(song.get('title') or '').casefold().split())
    if not title or title != current:
        return False
    old_ms, new_seconds = old.get('duration'), song.get('duration')
    if old_ms and new_seconds and abs(float(old_ms) - float(new_seconds) * 1000) > 5000:
        return False
    return True


# The catalogue is Library v2, so a Navidrome song id is not a row id: it lives
# in lib2_media_server_mappings, with lib2_tracks.server_source/server_id kept as
# a compatibility projection of the last observed one. Both are read, mapping
# first, exactly as get_all_track_ids_for_server resolves a server id.
_TRACK_BY_SERVER_ID = """
    SELECT t.id AS track_id, t.title AS title, t.duration AS duration,
           (SELECT f.path FROM lib2_track_files f
             WHERE f.track_id = t.id
               AND COALESCE(f.file_state, 'active') = 'active'
               AND f.path IS NOT NULL AND TRIM(f.path) <> ''
             ORDER BY f.is_primary DESC, f.id LIMIT 1) AS file_path
      FROM lib2_tracks t
     WHERE t.id = (
             SELECT m.entity_id FROM lib2_media_server_mappings m
              WHERE m.entity_type='track' AND m.server_source='navidrome'
                AND m.server_id = ?
           )
        OR (t.server_source='navidrome' AND t.server_id = ?)
     LIMIT 1
"""


def resolve_tracks(tracks, songs, db):
    from core.navidrome_client import NavidromeTrack
    paths = _by_path(songs)
    resolved = []
    for track in tracks:
        sid = str(getattr(track, 'ratingKey', None) or getattr(track, 'id', None)
                  or (track.get('id', '') if isinstance(track, dict) else ''))
        if sid not in songs:
            with db._get_connection() as conn:
                row = conn.execute(_TRACK_BY_SERVER_ID, (sid, sid)).fetchone()
            old = dict(row) if row else {}
            path = _path(old.get('file_path'))
            candidates = paths.get(path, [])
            if len(candidates) != 1 or not _same_recording(old, songs[candidates[0]]):
                raise IdentityError(f'Cannot safely resolve Navidrome song {sid}; playlist left unchanged. Run a library scan.')
            sid = candidates[0]
        resolved.append(NavidromeTrack(songs[sid], None))
    return resolved


def repair_rekeyed_tracks(db, songs):
    """Re-point Navidrome song ids that the server has reissued.

    Upstream merges two rows of the legacy ``tracks`` table here, because there
    the song id IS the row id: a rekey mints a second row and the repair has to
    move every enrichment column and foreign key onto it before deleting the
    old one. The catalogue does not work that way. A lib2 track keeps its
    identity for life and the server's id is held beside it in
    ``lib2_media_server_mappings``, so a rekey is a stale *mapping* and nothing
    else -- no row is merged, no column is copied, and no catalogue row is ever
    deleted here. Only the mapping table and the two projection columns on
    lib2_tracks are written.

    The safety rules are upstream's, unchanged: never touch an id the server
    still lists, never an unmatched file, never an ambiguous path, and only
    when the file at that path is recognisably the same recording.
    """
    paths = _by_path(songs)
    repaired = 0
    with db._get_connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        rows = conn.execute("""
            SELECT m.id AS mapping_id, m.server_id AS server_id,
                   t.id AS track_id, t.title AS title, t.duration AS duration,
                   (SELECT f.path FROM lib2_track_files f
                     WHERE f.track_id = t.id
                       AND COALESCE(f.file_state, 'active') = 'active'
                       AND f.path IS NOT NULL AND TRIM(f.path) <> ''
                     ORDER BY f.is_primary DESC, f.id LIMIT 1) AS file_path
              FROM lib2_media_server_mappings m
              JOIN lib2_tracks t ON t.id = m.entity_id
             WHERE m.entity_type='track' AND m.server_source='navidrome'
        """).fetchall()
        live_ids = {str(r['server_id']) for r in rows if str(r['server_id']) in songs}
        for row in rows:
            old_id = str(row['server_id'])
            if old_id in songs:
                continue
            candidates = paths.get(_path(row['file_path']), [])
            if len(candidates) != 1:
                continue
            new_id = candidates[0]
            if not _same_recording(dict(row), songs[new_id]):
                continue
            if new_id in live_ids:
                # The sync already filed the live id, on this row or on a twin
                # it created for the same file. UNIQUE(entity_type,
                # server_source, server_id) would reject a second claim, and a
                # duplicate catalogue row is core/library2/dedup_repair.py's
                # business, not ours. Drop the obsolete mapping and stop.
                conn.execute("DELETE FROM lib2_media_server_mappings WHERE id=?",
                             (row['mapping_id'],))
                repaired += 1
                continue
            conn.execute(
                "UPDATE lib2_media_server_mappings "
                "SET server_id=?, last_seen_at=CURRENT_TIMESTAMP WHERE id=?",
                (new_id, row['mapping_id']))
            # the projection follows the mapping, so the compatibility reads
            # (get_all_track_ids_for_server, the server_source CASE in the
            # ownership queries) do not keep answering with the dead id
            conn.execute(
                "UPDATE lib2_tracks SET server_id=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND server_source='navidrome' AND server_id=?",
                (new_id, row['track_id'], old_id))
            for table, column in (('manual_library_track_matches', 'library_track_id'),
                                  ('sync_match_cache', 'server_track_id')):
                try:
                    conn.execute(
                        f'UPDATE {table} SET {column}=? WHERE {column}=? AND server_source=?',
                        (new_id, old_id, 'navidrome'))
                except Exception:
                    # these two are caches; a schema that predates either of
                    # them must not abort the repair
                    pass
            live_ids.add(new_id)
            repaired += 1
        conn.commit()
    return repaired


_active_write = ContextVar('navidrome_validated_write', default=None)


def validated_playlist_write(method):
    """Validate once across nested update/create calls; verify server contents."""
    @wraps(method)
    def guarded(client, *args, **kwargs):
        if _active_write.get() is client:
            return method(client, *args, **kwargs)
        bound = signature(method).bind(client, *args, **kwargs)
        name = bound.arguments.get('name', bound.arguments.get('playlist_name'))
        tracks = list(bound.arguments['tracks'])
        from database.music_database import get_database
        from utils.logging_config import get_logger
        logger = get_logger('navidrome_identity')
        try:
            if not client.ensure_connection():
                return False
            if not tracks:
                raise IdentityError('No validated matches; existing playlist left unchanged')
            playlist_id = bound.arguments.get('playlist_id')
            if not playlist_id:
                playlists = client.get_playlists_by_name(name)
                playlist_id = playlists[0].id if playlists else None
            current = {}
            if playlist_id:
                response = _request(client, 'getPlaylist', {'id': playlist_id})
                playlist_data = (response or {}).get('playlist')
                if not isinstance(playlist_data, dict):
                    raise IdentityError('Cannot read current playlist; no changes made')
                current = {str(t['id']): t for t in playlist_data.get('entry', [])}
            wanted = {str(getattr(t, 'ratingKey', None) or getattr(t, 'id', None)
                          or (t.get('id', '') if isinstance(t, dict) else '')) for t in tracks}
            # An unchanged/subset playlist already supplies live song evidence.
            # Only new or stale IDs require a complete catalogue read.
            songs = current if wanted <= current.keys() else read_inventory(client)
            tracks = resolve_tracks(tracks, songs, get_database())
            expected = {str(t.ratingKey) for t in tracks}
            if method.__name__ == 'append_to_playlist':
                expected.update(current)
            token = _active_write.set(client)
            try:
                bound.arguments['tracks'] = tracks
                success = method(*bound.args, **bound.kwargs)
            finally:
                _active_write.reset(token)
            if not success:
                return False
            playlist_id = bound.arguments.get('playlist_id')
            if not playlist_id:
                playlists = client.get_playlists_by_name(name)
                if not playlists:
                    raise IdentityError('Playlist write could not be verified')
                playlist_id = playlists[0].id
            response = _request(client, 'getPlaylist', {'id': playlist_id})
            actual = (response or {}).get('playlist')
            if not isinstance(actual, dict) or {str(t['id']) for t in actual.get('entry', [])} != expected:
                raise IdentityError('Navidrome did not retain the requested playlist tracks; sync is not complete')
            return True
        except Exception as exc:
            logger.error('Playlist %r not synced: %s', name, exc)
            return False
    return guarded
