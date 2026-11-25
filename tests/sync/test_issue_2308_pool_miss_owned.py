"""#2308: a song the library has stayed off the playlist and was never
downloaded. the sync's per-artist pool missed it, and the wishlist step,
which asks the library without a pool, found it at 1.0 and called it owned.
the sync now asks the same way before calling a track missing."""

from __future__ import annotations

from unittest.mock import patch

from tests.sync.test_issue_1721_stale_sync_match_cache import _media_match, _track
from tests.sync.test_issue_1715_album_fallback_artist_gate import _make_db


def _db(tmp_path):
    return _make_db(
        tmp_path,
        artists=[('100', 'Alex Grey', 'navidrome'), ('200', 'Someone Else', 'navidrome')],
        albums=[('al-1', '100', 'Alex Grey - Chasing Sunsets', 'navidrome'),
                ('al-2', '200', 'Other', 'navidrome')],
        tracks=[('1001', 'al-1', '100', 'Chasing Sunsets', None, 'navidrome'),
                ('2001', 'al-2', '200', 'Unrelated Song', None, 'navidrome')],
    )


def _pool_without_it(db):
    """the artist's pool came back with rows, none of them this song"""
    other = db.check_track_exists('Unrelated Song', 'Someone Else', confidence_threshold=0.7,
                                  server_source='navidrome')[0]
    return [other] if other is not None else []


def test_a_pool_that_misses_still_finds_the_owned_track(tmp_path):
    db = _db(tmp_path)
    pool = _pool_without_it(db)
    assert pool, "the fixture's pool must be non-empty, or the old path already asked without one"
    track = _track(id='sp-cs', name='Chasing Sunsets', artists=['Alex Grey'], album='Chasing Sunsets',
                   source_track_id='sp-cs')
    with patch('services.sync_service.PlaylistSyncService._get_or_fetch_artist_candidates',
               lambda self, *a, **k: pool):
        found, conf = _media_match(db, track)
    assert found is not None and str(found.id) == '1001'
    assert conf >= 0.7


def test_a_song_the_library_does_not_have_is_still_missing(tmp_path):
    db = _db(tmp_path)
    pool = _pool_without_it(db)
    track = _track(id='sp-x', name='Never Recorded', artists=['Alex Grey'], album='Nothing',
                   source_track_id='sp-x')
    with patch('services.sync_service.PlaylistSyncService._get_or_fetch_artist_candidates',
               lambda self, *a, **k: pool):
        found, conf = _media_match(db, track)
    assert found is None and conf < 0.7
