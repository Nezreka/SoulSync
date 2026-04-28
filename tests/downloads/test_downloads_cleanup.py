"""Tests for core/downloads/cleanup.py — automatic wishlist cleanup after DB updates."""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeWishlistService:
    def __init__(self, tracks_per_profile=None, mark_results=None):
        # tracks_per_profile: {profile_id: [track_dict, ...]}
        self._tracks = tracks_per_profile or {}
        # mark_results: {spotify_track_id: True/False} — whether mark_track_download_result returns True
        self._mark = mark_results or {}
        self.mark_calls = []

    def get_wishlist_tracks_for_download(self, profile_id):
        return list(self._tracks.get(profile_id, []))

    def mark_track_download_result(self, spotify_track_id, success):
        self.mark_calls.append((spotify_track_id, success))
        return self._mark.get(spotify_track_id, True)


class _FakeMusicDB:
    def __init__(self, hits=None):
        # hits: {(track_name, artist_name): (db_track_obj, confidence)}
        self._hits = hits or {}
        self.check_calls = []

    def check_track_exists(self, track_name, artist_name, confidence_threshold=0.7,
                            server_source=None, album=None):
        self.check_calls.append((track_name, artist_name, server_source, album))
        return self._hits.get((track_name, artist_name), (None, 0.0))


class _FakeProfileDB:
    def __init__(self, profiles):
        self._profiles = profiles

    def get_all_profiles(self):
        return list(self._profiles)


class _FakeConfig:
    def __init__(self, server='plex'):
        self._server = server

    def get_active_media_server(self):
        return self._server


# ---------------------------------------------------------------------------
# monkeypatch helper — wires fakes into core.downloads.cleanup imports
# ---------------------------------------------------------------------------

@pytest.fixture
def install(monkeypatch):
    def _install(profiles, tracks_per_profile, hits, mark_results=None):
        ws = _FakeWishlistService(tracks_per_profile, mark_results)
        mdb = _FakeMusicDB(hits)
        pdb = _FakeProfileDB(profiles)

        # Patch the in-function imports
        import core.wishlist_service as wls_mod
        import database.music_database as mdb_mod
        monkeypatch.setattr(wls_mod, 'get_wishlist_service', lambda: ws)
        monkeypatch.setattr(mdb_mod, 'MusicDatabase', lambda: mdb)
        monkeypatch.setattr(mdb_mod, 'get_database', lambda: pdb)

        return ws, mdb, pdb
    return _install


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_no_wishlist_tracks_returns_early_with_no_marks(install):
    from core.downloads.cleanup import cleanup_wishlist_after_db_update
    ws, _, _ = install(profiles=[{'id': 1}], tracks_per_profile={1: []}, hits={})
    cleanup_wishlist_after_db_update(_FakeConfig())
    assert ws.mark_calls == []


def test_track_found_in_db_gets_removed(install):
    from core.downloads.cleanup import cleanup_wishlist_after_db_update
    track = {
        'spotify_track_id': 'sp-1',
        'name': 'Money',
        'artists': ['Pink Floyd'],
        'album': {'name': 'DSOTM'},
    }
    ws, mdb, _ = install(
        profiles=[{'id': 1}],
        tracks_per_profile={1: [track]},
        hits={('Money', 'Pink Floyd'): (object(), 0.95)},
    )
    cleanup_wishlist_after_db_update(_FakeConfig())
    assert ws.mark_calls == [('sp-1', True)]


def test_track_not_in_db_stays_in_wishlist(install):
    from core.downloads.cleanup import cleanup_wishlist_after_db_update
    track = {
        'spotify_track_id': 'sp-1',
        'name': 'Phantom',
        'artists': ['Nobody'],
        'album': 'NoAlbum',
    }
    ws, _, _ = install(
        profiles=[{'id': 1}],
        tracks_per_profile={1: [track]},
        hits={},
    )
    cleanup_wishlist_after_db_update(_FakeConfig())
    assert ws.mark_calls == []


def test_low_confidence_match_does_not_remove(install):
    from core.downloads.cleanup import cleanup_wishlist_after_db_update
    track = {'spotify_track_id': 'sp-1', 'name': 'Money', 'artists': ['Pink Floyd']}
    # Confidence below 0.7 threshold
    ws, _, _ = install(
        profiles=[{'id': 1}],
        tracks_per_profile={1: [track]},
        hits={('Money', 'Pink Floyd'): (object(), 0.5)},
    )
    cleanup_wishlist_after_db_update(_FakeConfig())
    assert ws.mark_calls == []


def test_skip_when_missing_essential_fields(install):
    from core.downloads.cleanup import cleanup_wishlist_after_db_update
    tracks = [
        {'spotify_track_id': 'sp-1', 'name': '', 'artists': ['A']},  # no name
        {'spotify_track_id': 'sp-2', 'name': 'X', 'artists': []},      # no artists
        {'name': 'Y', 'artists': ['B']},                              # no track_id
    ]
    ws, mdb, _ = install(profiles=[{'id': 1}], tracks_per_profile={1: tracks}, hits={})
    cleanup_wishlist_after_db_update(_FakeConfig())
    assert mdb.check_calls == []
    assert ws.mark_calls == []


def test_artist_dict_format_normalized(install):
    from core.downloads.cleanup import cleanup_wishlist_after_db_update
    track = {
        'spotify_track_id': 'sp-1',
        'name': 'X',
        'artists': [{'name': 'Aretha'}],  # dict format
    }
    ws, mdb, _ = install(
        profiles=[{'id': 1}],
        tracks_per_profile={1: [track]},
        hits={('X', 'Aretha'): (object(), 0.9)},
    )
    cleanup_wishlist_after_db_update(_FakeConfig())
    assert ws.mark_calls == [('sp-1', True)]


def test_breaks_on_first_artist_match(install):
    """Multi-artist track stops checking after first hit."""
    from core.downloads.cleanup import cleanup_wishlist_after_db_update
    track = {
        'spotify_track_id': 'sp-1',
        'name': 'X',
        'artists': ['First', 'Second'],
    }
    ws, mdb, _ = install(
        profiles=[{'id': 1}],
        tracks_per_profile={1: [track]},
        hits={('X', 'First'): (object(), 0.9)},
    )
    cleanup_wishlist_after_db_update(_FakeConfig())
    assert ws.mark_calls == [('sp-1', True)]
    # Only first artist checked
    assert mdb.check_calls == [('X', 'First', 'plex', None)]


def test_walks_all_profiles(install):
    from core.downloads.cleanup import cleanup_wishlist_after_db_update
    track_p1 = {'spotify_track_id': 'sp-p1', 'name': 'A', 'artists': ['X']}
    track_p2 = {'spotify_track_id': 'sp-p2', 'name': 'B', 'artists': ['Y']}
    ws, mdb, _ = install(
        profiles=[{'id': 1}, {'id': 2}],
        tracks_per_profile={1: [track_p1], 2: [track_p2]},
        hits={('A', 'X'): (object(), 0.9), ('B', 'Y'): (object(), 0.9)},
    )
    cleanup_wishlist_after_db_update(_FakeConfig())
    marked = {c[0] for c in ws.mark_calls}
    assert marked == {'sp-p1', 'sp-p2'}


def test_album_string_form_passed_through(install):
    from core.downloads.cleanup import cleanup_wishlist_after_db_update
    track = {
        'spotify_track_id': 'sp-1', 'name': 'X', 'artists': ['A'],
        'album': 'StringAlbumName',  # not a dict
    }
    ws, mdb, _ = install(profiles=[{'id': 1}], tracks_per_profile={1: [track]}, hits={})
    cleanup_wishlist_after_db_update(_FakeConfig())
    # check_track_exists should receive the string album
    assert mdb.check_calls[0] == ('X', 'A', 'plex', 'StringAlbumName')


def test_album_dict_form_uses_name(install):
    from core.downloads.cleanup import cleanup_wishlist_after_db_update
    track = {
        'spotify_track_id': 'sp-1', 'name': 'X', 'artists': ['A'],
        'album': {'name': 'DSOTM'},
    }
    _, mdb, _ = install(profiles=[{'id': 1}], tracks_per_profile={1: [track]}, hits={})
    cleanup_wishlist_after_db_update(_FakeConfig())
    assert mdb.check_calls[0] == ('X', 'A', 'plex', 'DSOTM')


def test_db_check_failure_continues_to_next_artist(install, monkeypatch):
    from core.downloads.cleanup import cleanup_wishlist_after_db_update
    track = {'spotify_track_id': 'sp-1', 'name': 'X', 'artists': ['Bad', 'Good']}

    class _ExplodingDB:
        def __init__(self):
            self.calls = 0

        def check_track_exists(self, track_name, artist_name, **kw):
            self.calls += 1
            if artist_name == 'Bad':
                raise RuntimeError("db boom")
            return (object(), 0.9)

    edb = _ExplodingDB()
    import database.music_database as mdb_mod
    import core.wishlist_service as wls_mod
    ws = _FakeWishlistService({1: [track]})
    monkeypatch.setattr(wls_mod, 'get_wishlist_service', lambda: ws)
    monkeypatch.setattr(mdb_mod, 'MusicDatabase', lambda: edb)
    monkeypatch.setattr(mdb_mod, 'get_database', lambda: _FakeProfileDB([{'id': 1}]))

    cleanup_wishlist_after_db_update(_FakeConfig())
    # Both artists tried; second match removed track
    assert edb.calls == 2
    assert ws.mark_calls == [('sp-1', True)]


def test_top_level_exception_swallowed(monkeypatch):
    from core.downloads.cleanup import cleanup_wishlist_after_db_update
    import core.wishlist_service as wls_mod

    def boom():
        raise RuntimeError("service init dead")

    monkeypatch.setattr(wls_mod, 'get_wishlist_service', boom)
    # Must not raise
    cleanup_wishlist_after_db_update(_FakeConfig())


def test_uses_active_server_from_config(install):
    from core.downloads.cleanup import cleanup_wishlist_after_db_update
    track = {'spotify_track_id': 'sp-1', 'name': 'X', 'artists': ['A']}
    _, mdb, _ = install(profiles=[{'id': 1}], tracks_per_profile={1: [track]}, hits={})
    cleanup_wishlist_after_db_update(_FakeConfig(server='jellyfin'))
    assert mdb.check_calls[0][2] == 'jellyfin'
