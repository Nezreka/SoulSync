"""Read playlists out of a NewPipe backup.

NewPipe (the android youtube client) keeps its playlists on the phone, in a
Room sqlite db. Settings > Backup and restore > Export gives a zip with
``newpipe.db`` inside; users hand us the zip or the bare db.

Only LOCAL playlists come out: ``playlists`` -> ``playlist_stream_join`` ->
``streams``. Bookmarked youtube playlists (``remote_playlists``) are just a
url, so they are counted and left to the YouTube tab.

Pure apart from reading the file: the title/artist cleaners are injected so
this module never imports web_server.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from typing import Any, Callable, Dict, List, Optional

from core.youtube_track_meta import derive_artist_and_title

# a zip member bigger than this is not a playlist db, it is a zip bomb
MAX_DB_BYTES = 512 * 1024 * 1024

SQLITE_MAGIC = b'SQLite format 3\x00'

# newpipe's service ids (org.schabi.newpipe.extractor.ServiceList)
YOUTUBE_SERVICE_ID = 0

_YT_ID_RE = re.compile(r'(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})')

_REQUIRED_TABLES = ('playlists', 'playlist_stream_join', 'streams')


class NotANewPipeBackup(ValueError):
    """the file is not a newpipe zip or db."""


def source_playlist_id(uid: Any) -> str:
    """the mirror key. newpipe's uid is an autoincrement that survives a
    backup/restore, so a re-import lands on (and overwrites) the same mirror."""
    return f'newpipe_{uid}'


def _youtube_id(url: str) -> str:
    m = _YT_ID_RE.search(url or '')
    return m.group(1) if m else ''


def _extract_db(path: str, workdir: str) -> str:
    """the path of a readable newpipe.db: the file itself, or pulled from the zip."""
    with open(path, 'rb') as fh:
        head = fh.read(len(SQLITE_MAGIC))
    if head == SQLITE_MAGIC:
        return path
    if not zipfile.is_zipfile(path):
        raise NotANewPipeBackup('not a NewPipe backup (expected the export .zip or newpipe.db)')
    with zipfile.ZipFile(path) as zf:
        member = next((i for i in zf.infolist()
                       if os.path.basename(i.filename) == 'newpipe.db' and not i.is_dir()), None)
        if member is None:
            raise NotANewPipeBackup('this zip has no newpipe.db in it')
        if member.file_size > MAX_DB_BYTES:
            raise NotANewPipeBackup('newpipe.db in this zip is too large to be a playlist db')
        out = os.path.join(workdir, 'newpipe.db')
        # copy by hand rather than zf.extract: the member name never touches the path
        with zf.open(member) as src, open(out, 'wb') as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
    return out


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}


def _track(row: sqlite3.Row, clean_artist: Callable[[str], str],
           clean_title: Callable[[str, Optional[str]], str]) -> Dict[str, Any]:
    url = row['url'] or ''
    raw_title = row['title'] or ''
    # a stream's uploader is ITS channel, not the playlist owner's, so the
    # channel is a fair last-resort artist (unlike a youtube.com playlist, #863)
    artist, title = derive_artist_and_title(
        {'title': raw_title, 'uploader': row['uploader'] or ''}, allow_channel_artist=True)
    if artist:
        artist = clean_artist(artist)
        title = clean_title(title, artist)
    else:
        artist = 'Unknown Artist'
        title = clean_title(title, None)
    yt_id = _youtube_id(url) if row['service_id'] == YOUTUBE_SERVICE_ID else ''
    return {
        'track_name': title or raw_title or 'Unknown Track',
        'artist_name': artist,
        'album_name': '',
        'duration_ms': int(row['duration'] or 0) * 1000,
        'image_url': row['thumbnail_url'] or '',
        # the video id, like a youtube-tab mirror; other services keep the url
        'source_track_id': yt_id or url,
    }


def read_newpipe_backup(
    path: str,
    clean_artist: Callable[[str], str] = lambda a: a,
    clean_title: Callable[[str, Optional[str]], str] = lambda t, a: t,
) -> Dict[str, Any]:
    """``{'playlists': [...], 'bookmarked': n}`` from a newpipe zip or db.

    each playlist is ``{id, uid, name, image_url, track_count, tracks}`` with
    mirror-shaped tracks in playlist order. raises NotANewPipeBackup."""
    workdir = tempfile.mkdtemp(prefix='newpipe_')
    try:
        db_path = _extract_db(path, workdir)
        try:
            # read-only and immutable: never write to (or lock) the user's file
            conn = sqlite3.connect(f'file:{db_path}?mode=ro&immutable=1', uri=True)
        except sqlite3.Error as e:
            raise NotANewPipeBackup(f'could not open the NewPipe db: {e}') from e
        conn.row_factory = sqlite3.Row
        try:
            try:
                tables = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'")}
            except sqlite3.DatabaseError as e:
                raise NotANewPipeBackup(f'not a NewPipe db: {e}') from e
            missing = [t for t in _REQUIRED_TABLES if t not in tables]
            if missing:
                raise NotANewPipeBackup(
                    f"not a NewPipe db (no {', '.join(missing)} table)")

            # display_index arrived in a later newpipe; older backups order by uid
            order = ('display_index, uid' if 'display_index' in _columns(conn, 'playlists')
                     else 'uid')
            playlists = []
            for pl in conn.execute(f'SELECT uid, name FROM playlists ORDER BY {order}').fetchall():
                rows = conn.execute("""
                    SELECT s.service_id, s.url, s.title, s.uploader, s.duration, s.thumbnail_url
                    FROM playlist_stream_join j
                    JOIN streams s ON s.uid = j.stream_id
                    WHERE j.playlist_id = ?
                    ORDER BY j.join_index
                """, (pl['uid'],)).fetchall()
                tracks = [_track(r, clean_artist, clean_title) for r in rows]
                playlists.append({
                    'id': source_playlist_id(pl['uid']),
                    'uid': pl['uid'],
                    'name': (pl['name'] or '').strip() or f"NewPipe playlist {pl['uid']}",
                    'image_url': next((t['image_url'] for t in tracks if t['image_url']), ''),
                    'track_count': len(tracks),
                    'tracks': tracks,
                })

            bookmarked = 0
            if 'remote_playlists' in tables:
                bookmarked = conn.execute('SELECT COUNT(*) FROM remote_playlists').fetchone()[0]
            return {'playlists': playlists, 'bookmarked': bookmarked}
        finally:
            conn.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
