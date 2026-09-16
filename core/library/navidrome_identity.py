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


def resolve_tracks(tracks, songs, db):
    from core.navidrome_client import NavidromeTrack
    paths = _by_path(songs)
    resolved = []
    for track in tracks:
        sid = str(getattr(track, 'ratingKey', None) or getattr(track, 'id', None)
                  or (track.get('id', '') if isinstance(track, dict) else ''))
        if sid not in songs:
            with db._get_connection() as conn:
                row = conn.execute("SELECT file_path,title,duration FROM tracks WHERE id=? AND server_source='navidrome'", (sid,)).fetchone()
            old = dict(row) if row else {}
            path = _path(old.get('file_path'))
            candidates = paths.get(path, [])
            if len(candidates) != 1 or not _same_recording(old, songs[candidates[0]]):
                raise IdentityError(f'Cannot safely resolve Navidrome song {sid}; playlist left unchanged. Run a library scan.')
            sid = candidates[0]
        resolved.append(NavidromeTrack(songs[sid], None))
    return resolved


def repair_rekeyed_tracks(db, songs):
    """Merge only obsolete IDs with one live same-path row already imported.

    Never delete an unmatched file, an ID still on the server, or an ambiguous
    path. Keep live server fields and fill missing enrichment from the old row.
    """
    paths = _by_path(songs)
    repaired = 0
    with db._get_connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        rows = conn.execute("SELECT * FROM tracks WHERE server_source='navidrome'").fetchall()
        by_id = {str(r['id']): r for r in rows}
        owned = {'id', 'album_id', 'artist_id', 'title', 'track_number', 'disc_number',
                 'duration', 'file_path', 'bitrate', 'file_size', 'server_source', 'track_artist',
                 'created_at', 'updated_at'}
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        references = []
        for table in tables:
            for fk in conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall():
                if fk[2] == 'tracks' and fk[4] == 'id':
                    references.append((table, fk[3]))
        for old_id, old in by_id.items():
            if old_id in songs:
                continue
            candidates = paths.get(_path(old['file_path']), [])
            if len(candidates) != 1 or candidates[0] not in by_id:
                continue
            new_id = candidates[0]
            if not _same_recording(dict(old), songs[new_id]):
                continue
            columns = [column for column in old.keys() if column not in owned and old[column] not in (None, '')]
            if columns:
                assignments = ', '.join(f'"{c}"=CASE WHEN "{c}" IS NULL OR "{c}"=? THEN ? ELSE "{c}" END' for c in columns)
                values = [value for c in columns for value in ('', old[c])]
                conn.execute(f'UPDATE tracks SET {assignments} WHERE id=?', values + [new_id])
            for table, column in [('manual_library_track_matches', 'library_track_id'), ('sync_match_cache', 'server_track_id')]:
                if table in tables:
                    conn.execute(f'UPDATE {table} SET {column}=? WHERE {column}=? AND server_source=?', (new_id, old_id, 'navidrome'))
            # Preserve declared foreign-key references before removing the old row.
            for table, column in references:
                conn.execute(f'UPDATE "{table}" SET "{column}"=? WHERE "{column}"=?', (new_id, old_id))
            conn.execute("DELETE FROM tracks WHERE id=? AND server_source='navidrome'", (old_id,))
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
