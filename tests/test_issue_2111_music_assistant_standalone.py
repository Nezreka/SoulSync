"""#2111: music assistant as the active server, and standalone m3u/scan gaps.

sync and scans looked the client up from lists of plex, jellyfin and
navidrome, so music assistant fell through to plex or to "not available".
standalone m3u export filtered on server_source='soulsync', which a full
refresh never stamps. the batch m3u was written from the batch alone.
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_library_track


# ── 2: playlist sync reaches music assistant, not plex ─────────────────────

def _sync_service(monkeypatch, clients, active):
    from services import sync_service as ss
    svc = ss.PlaylistSyncService.__new__(ss.PlaylistSyncService)
    svc._media_client = lambda name: clients.get(name)
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: active)
    return svc


def test_sync_uses_the_music_assistant_client(monkeypatch):
    """#2111: it logged "Syncing playlist to PLEX server" and failed."""
    ma, plex = object(), object()
    svc = _sync_service(monkeypatch, {'music_assistant': ma, 'plex': plex}, 'music_assistant')
    assert svc._get_active_media_client() == (ma, 'music_assistant')
    svc = _sync_service(monkeypatch, {'plex': plex}, 'music_assistant')
    assert svc._get_active_media_client() == (None, 'music_assistant')


def test_only_plex_needs_a_fetched_server_item():
    """#2111: a db track becomes the playlist item for every server but plex."""
    from services.sync_service import _db_backed
    assert all(_db_backed(s) for s in ('jellyfin', 'navidrome', 'soulsync', 'music_assistant'))
    assert not _db_backed('plex') and not _db_backed('')


# ── 4: scans find the music assistant client ───────────────────────────────

def test_scans_look_the_client_up_by_its_registered_name(monkeypatch):
    """#2111: update and deep scan said "Media client for 'music_assistant'
    not available"."""
    from api import database_admin as db_admin

    class _Engine:
        def client(self, name):
            return {'music_assistant': 'ma', 'plex': 'px'}.get(name)

    monkeypatch.setattr(db_admin, 'media_server_engine', _Engine())
    assert db_admin._scan_media_client('music_assistant') == 'ma'
    assert db_admin._scan_media_client('plex') == 'px'
    assert db_admin._scan_media_client('nope') is None
    monkeypatch.setattr(db_admin, 'media_server_engine', None)
    assert db_admin._scan_media_client('plex') is None


def test_quick_scan_reads_recent_albums_from_any_server():
    """#2111: an incremental scan of music assistant hit "Unknown server
    type" and found nothing."""
    from core.database_update_worker import DatabaseUpdateWorker
    worker = DatabaseUpdateWorker.__new__(DatabaseUpdateWorker)
    worker.server_type = 'music_assistant'

    class _Client:
        def get_recently_added_albums(self, limit):
            return [f'album-{limit}']

    worker.media_client = _Client()
    assert worker._get_recent_albums_for_server() == ['album-400']


# ── 6: standalone m3u sees what a full refresh indexed ─────────────────────

def _db(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as c:
        # full refresh writes no server identity
        seed_library_track(c, artist='Hillsong Young & Free', album='A', title='Highs & Lows',
                           track_server_id='t1', file_path='/music/h.flac', server_source=None)
        seed_library_track(c, artist='Plexed', album='B', title='Elsewhere', artist_server_id='ar2',
                           album_server_id='al2', track_server_id='t2', file_path='/music/p.flac',
                           server_source='plex')
        c.commit()
    return db


def test_standalone_m3u_resolves_full_refresh_tracks(tmp_path):
    """#2111: every track came out "NOT AVAILABLE" because the m3u asked for
    server_source='soulsync' and a full refresh leaves it empty."""
    db = _db(tmp_path)
    titles = {r['title'] for r in db.get_tracks_for_m3u_resolution(server_source='soulsync')}
    assert titles == {'Highs & Lows'}  # another server's track is still not standalone's
    # a real server keeps its strict filter
    assert {r['title'] for r in db.get_tracks_for_m3u_resolution(server_source='plex')} == {'Elsewhere'}


def test_standalone_search_finds_full_refresh_tracks(tmp_path):
    """#2111: the batch m3u resolves through search_tracks, same filter."""
    db = _db(tmp_path)
    found = db.search_tracks(artist='Hillsong Young & Free', server_source='soulsync')
    assert [t.title for t in found] == ['Highs & Lows']
    assert db.search_tracks(artist='Hillsong Young & Free', server_source='plex') == []


def test_on_server_sql_only_widens_for_standalone():
    from core.library2.sql_util import on_server_sql
    sql, params = on_server_sql('track', 't', 'soulsync')
    assert 't.server_source IS NULL' in sql and params == ['soulsync', 'soulsync']
    sql, _ = on_server_sql('track', 't', 'jellyfin')
    assert 'IS NULL' not in sql
    with pytest.raises(ValueError):
        on_server_sql('track', 't; drop', 'plex')


# ── 9: the batch m3u is the whole playlist ─────────────────────────────────

class _MirrorDb:
    def __init__(self, rows, resolved=None):
        self.rows = rows
        self.resolved = resolved
        self.asked = []

    def get_mirrored_playlist_tracks(self, pk, profile_id=None):
        self.asked.append((pk, profile_id))
        return self.rows

    def resolve_mirrored_playlist_assignment(self, playlist_ref, playlist_name=None, profile_id=1):
        return self.resolved


_ROWS = [{'track_name': f'Song {i}', 'artist_name': 'A', 'duration_ms': 1000 * i,
          'source_track_id': f's{i}'} for i in range(1, 7)]


def test_batch_m3u_writes_the_whole_mirrored_playlist():
    """#2111: one new download rewrote a six track m3u with one entry."""
    from core.playlists.batch_m3u import playlist_m3u_tracks
    db = _MirrorDb(_ROWS)
    batch = {'mirrored_playlist_id': 5, 'profile_id': 2, 'playlist_name': 'My Playlist'}
    out = playlist_m3u_tracks(batch, [{'name': 'Song 6', 'artist': 'A', 'duration_ms': 6000}], db)
    assert [t['name'] for t in out] == [f'Song {i}' for i in range(1, 7)]
    assert out[0] == {'name': 'Song 1', 'artist': 'A', 'duration_ms': 1000}
    assert db.asked == [(5, 2)]


def test_batch_m3u_finds_the_mirror_by_its_sync_id():
    from core.playlists.batch_m3u import batch_mirror_id
    assert batch_mirror_id({'playlist_id': 'auto_mirror_7'}, _MirrorDb([])) == 7


def test_batch_m3u_keeps_the_batch_list_without_a_mirror():
    from core.playlists.batch_m3u import playlist_m3u_tracks
    own = [{'name': 'x', 'artist': 'y', 'duration_ms': 0}]
    # an album download never goes looking for a same-named mirror
    db = _MirrorDb(_ROWS, resolved={'id': 3})
    assert playlist_m3u_tracks({'playlist_id': 'album_1', 'playlist_name': 'Mix'}, own, db) is own
    assert db.asked == []
    # a playlist batch that resolves to nothing keeps its own list too
    assert playlist_m3u_tracks({'is_playlist_batch': True, 'playlist_id': '123'}, own, _MirrorDb(_ROWS)) is own
    # a wishlist batch is not a playlist
    assert playlist_m3u_tracks({'is_playlist_batch': True, 'playlist_id': 'wishlist'}, own, db) is own


# ── 8: standalone incremental update indexes new files ─────────────────────

def test_standalone_incremental_indexes_only_new_files(tmp_path, monkeypatch):
    """#2111: incremental update skipped the run and still said complete."""
    from api import database_admin as db_admin

    transfer = tmp_path / 'Transfer'
    transfer.mkdir()
    (transfer / 'new.flac').write_bytes(b'x')
    (transfer / 'known.flac').write_bytes(b'x')
    db = MusicDatabase(str(tmp_path / 'music.db'))
    values = {'soulseek.transfer_path': str(transfer)}

    class _Cfg:
        def get(self, key, default=None):
            return values.get(key, default)

    monkeypatch.setattr(db_admin, 'config_manager', _Cfg())
    monkeypatch.setattr(db_admin, 'db_update_lock', __import__('threading').Lock())
    monkeypatch.setattr(db_admin, 'db_update_state', {'run_epoch': 0})
    monkeypatch.setattr(db_admin, 'docker_resolve_path', lambda p: p)
    monkeypatch.setattr(db_admin, 'get_database', lambda: db)
    monkeypatch.setattr(db_admin, '_known_library_files',
                        lambda _db: {db_admin._file_key(str(transfer / 'known.flac')): (1, 'active')})
    imports = []
    monkeypatch.setattr('core.library2.autolink.link_download_into_library_v2',
                        lambda ctx, *, raise_on_error=False: imports.append(ctx['_final_processed_path']) or 17)
    rescans = []
    monkeypatch.setattr('core.library2.scan.rescan_files', lambda *a, **k: rescans.append(k))
    finished, errors = [], []
    monkeypatch.setattr(db_admin, '_db_update_phase_callback', lambda *_: None)
    monkeypatch.setattr(db_admin, 'add_activity_item', lambda *_: None)
    monkeypatch.setattr(db_admin, '_db_update_finished_callback', lambda *a: finished.append(a))
    monkeypatch.setattr(db_admin, '_db_update_error_callback', errors.append)

    db_admin._run_db_update_task(False, 'soulsync')

    assert errors == []
    assert imports == [str(transfer / 'new.flac')]
    assert rescans == []  # the known file is not re-read
    assert finished == [(0, 0, 1, 2, 0)]


# ── 7: a podcast folder that can't be written says so ──────────────────────

def test_unwritable_podcast_folder_says_so(tmp_path):
    """#2111: the download only said "Download failed or file was invalid"."""
    from core.podcast_download_client import PodcastDownloadClient, PodcastFolderNotWritable
    from tests.test_podcast_client import _make_episode

    blocker = tmp_path / 'blocker'
    blocker.write_bytes(b'')  # a file where the folder should go
    client = PodcastDownloadClient(download_path=str(tmp_path))
    with pytest.raises(PodcastFolderNotWritable, match='not writable'):
        client.download_episode(_make_episode(), dest_dir=str(blocker / 'podcasts'))
