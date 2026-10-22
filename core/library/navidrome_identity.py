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


# How long a playlist write waits out a running library scan. Downloads
# trigger scans, so a pipeline that just fetched tracks routinely reaches its
# write mid-scan; refusing outright threw away a whole sync's matching.
PLAYLIST_WRITE_SCAN_WAIT = 180
_SCAN_POLL_SECONDS = 5


def _scan_stamp(client, wait=0):
    """The scan counter once Navidrome is idle. ``wait`` seconds of polling
    ride out a running (or briefly unreachable) scan before giving up."""
    deadline = time.monotonic() + wait
    while True:
        response = _request(client, 'getScanStatus')
        state = (response or {}).get('scanStatus')
        if isinstance(state, dict) and state.get('scanning') is False:
            return state.get('count')
        if time.monotonic() >= deadline:
            raise IdentityError('Navidrome scan state is unavailable or a scan is running')
        time.sleep(_SCAN_POLL_SECONDS)


def read_inventory(client, page_size=500, scan_wait=0):
    """A fresh, complete OpenSubsonic search3 inventory; never return partial data."""
    before = _scan_stamp(client, wait=scan_wait)
    deadline = time.monotonic() + 60
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


def _tail(path):
    """artist/album/file, the part of a path every mount agrees on. soulsync
    writes rows in its own view (/Media/Music/...) while navidrome reports
    its own (/music/...), so a raw compare never matched those rows (#1571)."""
    parts = [p for p in path.split('/') if p]
    return '/'.join(parts[-3:]).casefold() if len(parts) >= 3 else ''


def _by_path(songs):
    paths = defaultdict(list)
    for sid, song in songs.items():
        path = _path(song.get('path'))
        if path:
            paths[path].append(sid)
            tail = _tail(path)
            if tail:
                paths[('tail', tail)].append(sid)
    return paths


def _live_candidates(paths, stored_path):
    """Live song ids at a stored path: the exact path first, then the same
    artist/album/file under another mount. Callers still require exactly one
    and the same recording, so a tail shared by two songs resolves nothing."""
    path = _path(stored_path)
    if not path:
        return []
    exact = paths.get(path)
    if exact:
        return exact
    tail = _tail(path)
    return paths.get(('tail', tail), []) if tail else []


def _rekey_candidates(same_file_ids, old, paths, songs):
    """Live ids that may be this stale row's song, best evidence first.

    1. the path Navidrome reported for it (server_path, #1573), or file_path
       on rows from before that column, exactly
    2. another row for the same local file whose id is live: navidrome
       re-keyed the song and the scan already brought the new row in
    3. the artist/album/file tail of either path (#1571)
    """
    server_path, file_path = old.get('server_path'), old.get('file_path')
    for stored in (server_path, file_path):
        exact = paths.get(_path(stored)) if stored else None
        if exact:
            return exact
    live = [sid for sid in same_file_ids if sid in songs]
    if live:
        return live
    for stored in (server_path, file_path):
        candidates = _live_candidates(paths, stored)
        if candidates:
            return candidates
    return []


def _same_recording(old, song):
    title = ' '.join(str(old.get('title') or '').casefold().split())
    current = ' '.join(str(song.get('title') or '').casefold().split())
    if not title or title != current:
        return False
    old_ms, new_seconds = old.get('duration'), song.get('duration')
    if old_ms and new_seconds and abs(float(old_ms) - float(new_seconds) * 1000) > 5000:
        return False
    return True


def resolve_tracks(tracks, songs, db, skipped=None, ambiguous=None):
    """live navidrome tracks for these ids. an id we can't safely resolve is
    left out instead of sinking the whole write (#1715): one deleted song
    used to block every other track on the playlist. nothing resolving at
    all still raises.

    the left-out ids are split by what the evidence proves (#1754):
    ``skipped`` gets the ones with NO live candidate at all — the verified
    complete inventory holds nothing at their stored path or under their
    file, so the song is gone from the server. ``ambiguous`` gets the ones
    a rekey might explain but cannot prove (several live candidates, or
    the one candidate is a different recording): uncertainty, not
    deletion, and callers must not treat them as missing."""
    from core.navidrome_client import NavidromeTrack
    paths = _by_path(songs)
    resolved = []
    unresolved = []
    uncertain = []
    for track in tracks:
        sid = str(getattr(track, 'ratingKey', None) or getattr(track, 'id', None)
                  or (track.get('id', '') if isinstance(track, dict) else ''))
        if sid not in songs:
            with db._get_connection() as conn:
                row = conn.execute("SELECT * FROM tracks WHERE id=? AND server_source='navidrome'", (sid,)).fetchone()
                old = dict(row) if row else {}
                same_file = [str(r[0]) for r in conn.execute(
                    "SELECT id FROM tracks WHERE file_path=? AND server_source='navidrome' AND id!=?",
                    (old['file_path'], sid))] if old.get('file_path') else []
            candidates = _rekey_candidates(same_file, old, paths, songs) if old else []
            if len(candidates) == 1 and _same_recording(old, songs[candidates[0]]):
                sid = candidates[0]
            elif not candidates:
                unresolved.append(sid)
                continue
            else:
                uncertain.append(sid)
                continue
        resolved.append(NavidromeTrack(songs[sid], None))
    # extend BEFORE the raise: an all-dead write still has to hand its
    # drops to the caller (#1754), or those tracks stay "matched" forever
    if skipped is not None:
        skipped.extend(unresolved)
    if ambiguous is not None:
        ambiguous.extend(uncertain)
    if (unresolved or uncertain) and not resolved:
        raise IdentityError(f'Cannot safely resolve Navidrome song {(unresolved + uncertain)[0]}; playlist left unchanged. Run a library scan.')
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
        # one pass, not a query per stale row: file_path has no index and this
        # runs under a write lock with thousands of stale rows on some libraries
        by_file = defaultdict(list)
        for r in rows:
            if r['file_path']:
                by_file[r['file_path']].append(str(r['id']))
        owned = {'id', 'album_id', 'artist_id', 'title', 'track_number', 'disc_number',
                 'duration', 'file_path', 'server_path', 'bitrate', 'file_size', 'server_source', 'track_artist',
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
            same_file = [i for i in by_file.get(old['file_path'], []) if i != old_id]
            candidates = _rekey_candidates(same_file, dict(old), paths, songs)
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


def has_same_path_duplicates(db):
    """Whether any two Navidrome rows share a file path, the only case the
    rekey repair has work for. The one place that knows which table holds
    the server's rows: a catalogue with a different layout changes this
    query and nothing else."""
    with db._get_connection() as conn:
        return conn.execute(
            "SELECT 1 FROM tracks WHERE server_source='navidrome' AND file_path IS NOT NULL "
            "AND file_path!='' GROUP BY file_path HAVING COUNT(*) > 1 LIMIT 1").fetchone() is not None


def repair_if_needed(db, client):
    """The post-scan repair as one call: no inventory fetch unless same-path
    duplicates need investigation, then merge the rekeyed rows. Returns the
    number of rows merged (0 when nothing was needed)."""
    if not has_same_path_duplicates(db):
        return 0
    return repair_rekeyed_tracks(db, read_inventory(client))


def validated_playlist_write(method):
    """Validate once across nested update/create calls; verify server contents."""
    @wraps(method)
    def guarded(client, *args, **kwargs):
        if _active_write.get() is client:
            return method(client, *args, **kwargs)
        bound = signature(method).bind(client, *args, **kwargs)
        name = bound.arguments.get('name', bound.arguments.get('playlist_name'))
        tracks = list(bound.arguments['tracks'])
        # The caller can read which of its tracks this write had to drop
        # (server no longer has them) off the client afterwards; reset up
        # front so a write that never reaches resolution cannot leak a
        # previous write's drops into a later sync (#1754). ``skipped``
        # collects the proven-dead ids as resolution finds them — declared
        # here so the except path can still hand them over when
        # resolution itself raises (an all-dead write).
        client._last_validated_write_skipped = []
        skipped = []
        ambiguous = []
        from database.music_database import get_database
        from utils.logging_config import get_logger
        logger = get_logger('navidrome_identity')
        try:
            if not client.ensure_connection():
                return False
            if not tracks and not (
                # #1543: creating an EMPTY playlist is legal (new mirror
                # with no library matches yet). Emptying an existing one
                # via update is still refused — the method itself enforces
                # that, and the sync service never sends empty updates.
                method.__name__ == 'create_playlist'
                and not bound.arguments.get('playlist_id')
            ):
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
            songs = (current if wanted <= current.keys()
                     else read_inventory(client, scan_wait=PLAYLIST_WRITE_SCAN_WAIT))
            tracks = resolve_tracks(tracks, songs, get_database(), skipped, ambiguous)
            # Hand the proven-dead drops to the caller (#1754): a track
            # navidrome no longer has is not on the playlist and not in
            # the library, and the sync layer treats it as missing
            # (wishlist) instead of counting it as synced. Ambiguous ids
            # stay log-only: a rekey might explain them, so they are not
            # evidence of deletion. The log lines stay for diagnosis.
            client._last_validated_write_skipped = list(skipped)
            expected = {str(t.ratingKey) for t in tracks}
            if skipped or ambiguous:
                # an unresolved id might be a song navidrome re-keyed, and
                # the new key could be sitting in this playlist. a write
                # that would drop anything already there waits until we
                # can tell.
                if method.__name__ != 'append_to_playlist' and set(current) - expected:
                    raise IdentityError(f'Cannot safely resolve Navidrome song {(skipped + ambiguous)[0]}; playlist left unchanged. Run a library scan.')
                if skipped:
                    logger.warning('Playlist %r: skipped %d track(s) navidrome no longer has (%s)',
                                   name, len(skipped), ', '.join(skipped[:5]))
                if ambiguous:
                    logger.warning('Playlist %r: left out %d track(s) that could not be safely resolved (%s)',
                                   name, len(ambiguous), ', '.join(ambiguous[:5]))
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
            if method.__name__ == 'append_to_playlist':
                # old wrong matches the append took off on purpose (#1801)
                expected -= {str(r) for r in
                             getattr(client, '_last_append_removed', None) or ()}
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
            # a write that died during resolution (e.g. every offered
            # track is gone) still hands over the drops it proved, so
            # the caller can treat those tracks as missing (#1754)
            client._last_validated_write_skipped = list(skipped)
            logger.error('Playlist %r not synced: %s', name, exc)
            return False
    return guarded
