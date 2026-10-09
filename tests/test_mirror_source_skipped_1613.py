"""#1613 follow-up: the skipped count has to live on the mirror.

3.5.3 showed "29 tracks couldn't be loaded from Tidal" only in the identify
window, from in-memory state. a playlist mirrored in the background never
opens that window, and a restart wiped the count, so the user saw 365 of
395 tracks and nothing saying why.

these pin the count onto mirrored_playlists: written by a mirror that
reports it, kept by one that doesn't, cleared by one that skipped nothing,
and carried by the tidal refresh adapter and the refresh automation.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.tidal_client import Playlist, Track


@pytest.fixture
def db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_music.db")
    monkeypatch.setenv("DATABASE_PATH", test_db)
    from database.music_database import MusicDatabase
    return MusicDatabase(test_db)


_TRACKS = [{'track_name': 't1', 'artist_name': 'a', 'source_track_id': '1'}]


def _mirror(db, profile_id=1, **kwargs):
    return db.mirror_playlist(
        source='tidal', source_playlist_id='PL1', name="Cam's playlist",
        tracks=_TRACKS, profile_id=profile_id, **kwargs,
    )


def test_mirror_stores_the_counts(db):
    pl_id = _mirror(db, source_skipped={'videos': 1, 'unavailable': 29})
    assert db.get_mirrored_playlist(pl_id)['source_skipped'] == {'videos': 1, 'unavailable': 29}
    # the list the sync page reads carries it too
    assert db.get_mirrored_playlists(1)[0]['source_skipped'] == {'videos': 1, 'unavailable': 29}


def test_a_mirror_that_does_not_report_keeps_the_counts(db):
    pl_id = _mirror(db, source_skipped={'unavailable': 29})
    _mirror(db)  # a re-mirror from a path that knows nothing about skips
    assert db.get_mirrored_playlist(pl_id)['source_skipped'] == {'unavailable': 29}


def test_a_mirror_that_skipped_nothing_clears_the_counts(db):
    pl_id = _mirror(db, source_skipped={'unavailable': 29})
    _mirror(db, source_skipped=None)
    assert db.get_mirrored_playlist(pl_id)['source_skipped'] is None
    _mirror(db, source_skipped={'videos': 0, 'unavailable': 0})
    assert db.get_mirrored_playlist(pl_id)['source_skipped'] is None


def test_junk_counts_are_not_stored(db):
    pl_id = _mirror(db, source_skipped={'videos': 'lots', 'unavailable': -3})
    assert db.get_mirrored_playlist(pl_id)['source_skipped'] is None
    pl_id = _mirror(db, source_skipped='29')
    assert db.get_mirrored_playlist(pl_id)['source_skipped'] is None


def test_set_counts_updates_only_that_profiles_mirror(db):
    mine = _mirror(db, profile_id=1)
    theirs = _mirror(db, profile_id=2)
    assert db.set_mirrored_playlist_source_skipped('tidal', 'PL1', {'unavailable': 5}, profile_id=1)
    assert db.get_mirrored_playlist(mine)['source_skipped'] == {'unavailable': 5}
    assert db.get_mirrored_playlist(theirs)['source_skipped'] is None
    # no mirror yet, nothing to write
    assert not db.set_mirrored_playlist_source_skipped('tidal', 'nope', {'unavailable': 5}, profile_id=1)


def test_an_old_table_gets_the_column(db, tmp_path):
    conn = sqlite3.connect(str(tmp_path / "old.db"))
    conn.execute("CREATE TABLE mirrored_playlists (id INTEGER PRIMARY KEY, name TEXT)")
    cur = conn.cursor()
    db._add_mirrored_playlist_source_skipped_column(cur)
    db._add_mirrored_playlist_source_skipped_column(cur)  # twice is fine
    cols = [c[1] for c in conn.execute("PRAGMA table_info(mirrored_playlists)")]
    assert cols.count('source_skipped') == 1


def test_counts_read_off_a_playlist_object_or_a_dict():
    from core.discovery.endpoints import playlist_skipped_counts

    assert playlist_skipped_counts(Playlist(id="p", name="p", unavailable_tracks=29)) == {
        'videos': 0, 'unavailable': 29}
    assert playlist_skipped_counts({'skipped_videos': 1, 'unavailable_tracks': 0}) == {
        'videos': 1, 'unavailable': 0}
    assert playlist_skipped_counts({'name': 'x'}) is None
    assert playlist_skipped_counts(None) is None


class _Tidal:
    def __init__(self, playlist):
        self.playlist = playlist

    def is_authenticated(self):
        return True

    def get_playlist(self, playlist_id):
        return self.playlist


def _tidal_playlist(**counts):
    return Playlist(
        id="PL1", name="Cam's playlist",
        tracks=[Track(id="1", name="t1", artists=["a"], album="al", duration_ms=1000)],
        **counts,
    )


def test_tidal_adapter_reports_the_counts():
    from core.playlists.sources.tidal import TidalPlaylistSource

    detail = TidalPlaylistSource(lambda: _Tidal(_tidal_playlist(skipped_videos=1, unavailable_tracks=29))).refresh_playlist("PL1")
    assert detail.meta.extra['source_skipped'] == {'videos': 1, 'unavailable': 29}

    # nothing skipped still sets the key, so a refresh clears an old count
    detail = TidalPlaylistSource(lambda: _Tidal(_tidal_playlist())).refresh_playlist("PL1")
    assert 'source_skipped' in detail.meta.extra
    assert detail.meta.extra['source_skipped'] is None


def test_refresh_automation_writes_the_counts_to_the_mirror():
    from tests.automation.test_handlers_playlist import _StubDB, _build_deps
    from core.automation.handlers.refresh_mirrored import auto_refresh_mirrored

    stub = _StubDB(playlists=[{'id': 7, 'name': "Cam's playlist", 'source': 'tidal',
                                'source_playlist_id': 'PL1', 'profile_id': 1}])
    deps = _build_deps(
        get_database=lambda: stub,
        tidal_client=_Tidal(_tidal_playlist(unavailable_tracks=29)),
    )
    result = auto_refresh_mirrored({'playlist_id': '7', 'skip_discovery': True}, deps)
    assert result['refreshed'] == '1'
    assert stub.mirror_calls[0]['source_skipped'] == {'videos': 0, 'unavailable': 29}


def test_refresh_of_a_source_without_counts_leaves_them_alone():
    from tests.automation.test_handlers_playlist import _StubDB, _build_deps
    from core.automation.handlers.refresh_mirrored import _commit_refresh

    stub = _StubDB()
    deps = _build_deps(get_database=lambda: stub)
    pl = {'id': None, 'name': 'x', 'profile_id': 1}
    _commit_refresh(pl, 'deezer', 'D1', [], stub, deps, None)
    assert 'source_skipped' not in stub.mirror_calls[0]
