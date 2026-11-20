"""playlist sync matchers and exact soundcloud uploads.

an exact upload (``soundcloud_exact_<sc id>``) is that one file. the library
owning a song with the same title and artist (the original the upload is a
flip of) must not satisfy it: only the id-keyed legs (sync-match cache by
id, durable manual match by id, provider rows) may answer, the name matcher
never runs, and no cache row is written from a name hit. a catalogue track of
the same names still matches as before.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.sync.test_issue_1715_album_fallback_artist_gate import _make_db
from tests.sync.test_issue_1721_stale_sync_match_cache import (
    _config, _db_only_match, _media_match, _same_db, _track)

EXACT = 'soundcloud_exact_123'


def _owned_original_db(tmp_path):
    """the library owns the original 'Blue Sky' by Metallica."""
    return _make_db(
        tmp_path,
        artists=[('a-met', 'Metallica', 'navidrome')],
        albums=[('al-gh', 'a-met', 'Greatest Hits', 'navidrome')],
        tracks=[('t-met', 'al-gh', 'a-met', 'Blue Sky', None, 'navidrome')],
    )


def _exact_track():
    return _track(id=EXACT, source_track_id=EXACT, artists=['Metallica'])


def _catalogue_track():
    return _track(artists=['Metallica'])


class TestMediaServerMatcher:
    def test_exact_upload_never_matches_the_owned_original(self, tmp_path):
        db = _owned_original_db(tmp_path)
        match, conf = _media_match(db, _exact_track())
        assert match is None
        assert conf == 0.0
        assert db.read_sync_match_cache(EXACT, 'navidrome') is None

    def test_catalogue_track_of_the_same_names_still_matches(self, tmp_path):
        db = _owned_original_db(tmp_path)
        match, conf = _media_match(db, _catalogue_track())
        assert match is not None and str(match.id) == 't-met'
        assert conf >= 0.7
        assert db.read_sync_match_cache('dz-123', 'navidrome') is not None

    def test_exact_upload_is_found_by_its_own_id_match(self, tmp_path):
        db = _owned_original_db(tmp_path)
        db.save_sync_match_cache(EXACT, 'blue sky', 'metallica', 'navidrome',
                                 't-met', 'Blue Sky', 1.0)
        match, conf = _media_match(db, _exact_track())
        assert match is not None and str(match.id) == 't-met'
        assert conf == 1.0


class TestDatabaseOnlyMatcher:
    def test_exact_upload_never_matches_the_owned_original(self, tmp_path):
        db = _owned_original_db(tmp_path)
        match, conf = _db_only_match(db, _exact_track())
        assert match is None
        assert conf == 0.0
        assert db.read_sync_match_cache(EXACT, 'navidrome') is None

    def test_catalogue_track_of_the_same_names_still_matches(self, tmp_path):
        db = _owned_original_db(tmp_path)
        match, conf = _db_only_match(db, _catalogue_track())
        assert match is not None and str(match.id) == 't-met'
        assert conf >= 0.8
        assert db.read_sync_match_cache('dz-123', 'navidrome') is not None


class TestOwnedCheckBeforeWishlist:
    """the sync's unmatched pass stamps ``owned_in_library`` (which the
    mirror records as library membership) from a name match. an exact
    upload is owned by its own download only."""

    def _unmatched(self, db, track):
        from services.sync_service import PlaylistSyncService
        svc = PlaylistSyncService(spotify_client=MagicMock(), download_orchestrator=MagicMock(),
                                  media_server_engine=MagicMock())
        result = MagicMock()
        result.spotify_track = track
        result.owned_in_library = False
        service = MagicMock()
        service.database = db
        playlist = MagicMock()
        playlist.name = 'p'
        with patch('core.wishlist_service.get_wishlist_service', return_value=service), \
             patch('database.music_database.MusicDatabase', _same_db(db)), \
             patch('core.settings.config_manager', _config()):
            svc._wishlist_unmatched(playlist, [result], add_to_wishlist=False)
        return result

    def test_exact_upload_is_not_owned_by_the_original(self, tmp_path):
        db = _owned_original_db(tmp_path)
        assert self._unmatched(db, _exact_track()).owned_in_library is False

    def test_catalogue_track_is_owned_as_before(self, tmp_path):
        db = _owned_original_db(tmp_path)
        assert self._unmatched(db, _catalogue_track()).owned_in_library is True
