"""#1572: a track or two grabbed by a Discover playlist is not the user
collecting that artist or album. The discography backfill made every such
artist a full target (2344 findings a week in), and album completeness called
a 1/16 album incomplete. Both now want a stronger sign: a few tracks on one
album, or the artist on the watchlist. 0 turns the gate off.
"""
import types

import pytest

import core.repair_jobs.album_completeness as album_completeness_module
from core.repair_jobs.album_completeness import AlbumCompletenessJob
from core.repair_jobs.discography_backfill import DiscographyBackfillJob
from database.music_database import MusicDatabase
from tests.test_album_completeness_job import _SharedMemoryDB


def _seed_backfill(db):
    with db._get_connection() as conn:
        rows = [
            # three tracks on one album: collected
            (1, 'Collected', [(1, 3)]),
            # two playlist grabs on two albums: incidental
            (2, 'Playlist Grab', [(2, 1), (3, 1)]),
            # one playlist grab, but on the watchlist
            (3, 'Watched', [(4, 1)]),
        ]
        tid = 0
        for artist_id, name, albums in rows:
            conn.execute("INSERT INTO artists (id, name, server_source) VALUES (?, ?, 'test')",
                         (artist_id, name))
            for album_id, count in albums:
                conn.execute("INSERT INTO albums (id, title, artist_id, server_source) "
                             "VALUES (?, 'Alb', ?, 'test')", (album_id, artist_id))
                for _ in range(count):
                    tid += 1
                    conn.execute("INSERT INTO tracks (id, title, file_path, artist_id, album_id, server_source) "
                                 "VALUES (?, 'T', ?, ?, ?, 'test')",
                                 (tid, f'/music/{tid}.flac', artist_id, album_id))
        conn.execute("INSERT INTO watchlist_artists (artist_name) VALUES ('watched')")
        conn.commit()


def _backfill_names(db, minimum):
    job = DiscographyBackfillJob()
    return {a['name'] for a in job._get_library_artists(
        types.SimpleNamespace(db=db), min_album_tracks=minimum)}


def test_backfill_skips_playlist_only_artists(tmp_path):
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _seed_backfill(db)
    assert _backfill_names(db, 3) == {'Collected', 'Watched'}


def test_backfill_gate_off_keeps_everyone(tmp_path):
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _seed_backfill(db)
    assert _backfill_names(db, 0) == {'Collected', 'Playlist Grab', 'Watched'}


def test_backfill_default_is_on():
    assert DiscographyBackfillJob.default_settings['min_album_tracks_owned'] == 3


class _Config:
    def __init__(self, settings=None):
        self.settings = settings

    def get(self, key, default=None):
        if key == 'repair.jobs.album_completeness.settings' and self.settings is not None:
            return self.settings
        return default

    def get_active_media_server(self):
        return 'plex'


def _completeness(monkeypatch, *, owned, watched=False, settings=None):
    db = _SharedMemoryDB()
    db.insert_artist('a1', 'Pentatonix')
    db.insert_album('alb', 'a1', 'A Pentatonix Christmas', spotify_id='sp-1',
                    track_count=owned, api_track_count=16)
    db.insert_tracks('alb', owned)
    if watched:
        db._keepalive.execute("CREATE TABLE watchlist_artists (artist_name TEXT)")
        db._keepalive.execute("INSERT INTO watchlist_artists VALUES ('Pentatonix')")
        db._keepalive.commit()
    monkeypatch.setattr(album_completeness_module, 'get_album_tracks_for_source',
                        lambda source, album_id: {'items': [
                            {'track_number': i, 'name': f'T{i}', 'artists': []}
                            for i in range(1, 17)]})
    monkeypatch.setattr(album_completeness_module, 'get_primary_source', lambda: 'spotify')
    monkeypatch.setattr(album_completeness_module, 'get_source_priority',
                        lambda primary: ['spotify'])
    findings = []
    ctx = types.SimpleNamespace(
        db=db, transfer_folder='', config_manager=_Config(settings), spotify_client=None,
        is_spotify_rate_limited=lambda: False, stop_event=None,
        create_finding=lambda **kw: findings.append(kw) or True,
        should_stop=None, is_paused=None, update_progress=None, report_progress=None,
        check_stop=lambda: False, wait_if_paused=lambda: False,
    )
    AlbumCompletenessJob().scan(ctx)
    return findings


@pytest.mark.parametrize('owned', [1, 2])
def test_completeness_skips_a_couple_of_playlist_tracks(monkeypatch, owned):
    assert _completeness(monkeypatch, owned=owned) == []


def test_completeness_still_flags_a_partial_album(monkeypatch):
    assert len(_completeness(monkeypatch, owned=9)) == 1


def test_completeness_checks_watched_artists_regardless(monkeypatch):
    assert len(_completeness(monkeypatch, owned=1, watched=True)) == 1


def test_completeness_gate_off(monkeypatch):
    assert len(_completeness(monkeypatch, owned=1, settings={'min_owned_tracks': 0})) == 1
