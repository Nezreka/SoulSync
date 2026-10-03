"""H8: cancelling a watchlist scan mid-artist must not stamp that artist.

Current bug: `scan_watchlist_artists` calls `update_artist_scan_timestamp`
unconditionally after the album loop — even when the loop was broken out of
by a cancel — and records `success=True, albums_checked=<full count>`. The
cancelled artist then looks fully scanned and is skipped next run.

These tests drive the REAL `WatchlistScanner.scan_watchlist_artists` with a
minimally stubbed scanner object (no network, no real database).
"""
from datetime import datetime
from types import SimpleNamespace

import pytest

import core.watchlist_scanner as ws_mod
from database.music_database import WatchlistArtist


def _make_scanner(stamp_log):
    scanner = ws_mod.WatchlistScanner.__new__(ws_mod.WatchlistScanner)
    scanner._apply_global_watchlist_overrides = lambda artists: None
    scanner._watchlist_source_priority = lambda: []
    scanner._get_lookback_period_setting = lambda: 'recent'
    scanner.get_artist_discography_for_watchlist = lambda artist, ts: [
        SimpleNamespace(id=f'album-{n}', name=f'Album {n}') for n in range(5)
    ]
    scanner.get_artist_image_url = lambda artist: ''
    # metadata_service is a read-only lazy property on the real class — stub
    # the backing attribute instead.
    scanner._metadata_service = SimpleNamespace(
        get_album=lambda album_id: {'name': album_id, 'tracks': {}},
    )
    scanner._extract_track_items = lambda album_data: []
    scanner._has_placeholder_tracks = lambda tracks: False
    scanner._should_include_release = lambda n, artist: True
    scanner._should_include_track = lambda track, album_data, artist: True
    scanner.is_track_missing_from_library = lambda *a, **k: False
    scanner.update_artist_scan_timestamp = lambda artist: stamp_log.append(artist.artist_name)
    scanner.update_similar_artists = lambda *a, **k: None
    scanner._backfill_similar_artists_fallback_ids = lambda *a, **k: None
    scanner._database = SimpleNamespace(
        has_fresh_similar_artists=lambda *a, **k: True,
    )
    return scanner


def _make_artist():
    return WatchlistArtist(
        id=1,
        spotify_artist_id='spotify:artist:1',
        artist_name='Cancel Test Artist',
        date_added=datetime.now(),
    )


def _cancel_on_call(target_call):
    """cancel_check that starts returning True on the Nth invocation.

    For one artist with five albums the calls are: artist-loop head (1),
    then the album-loop head for album 0..4 (2..6). Cancelling on call 4
    lands mid-artist, at the head of album index 2 — i.e. after albums
    0 and 1 were fully processed.
    """
    calls = [0]

    def _check():
        calls[0] += 1
        return calls[0] >= target_call

    return _check


def test_h8_cancel_mid_artist_does_not_stamp_timestamp():
    stamp_log = []
    scanner = _make_scanner(stamp_log)
    results = scanner.scan_watchlist_artists(
        [_make_artist()],
        profile_id=1,
        scan_state={},
        cancel_check=_cancel_on_call(4),
    )
    assert len(results) == 1
    assert stamp_log == [], "cancelled artist's scan timestamp was stamped"


def test_h8_cancel_mid_artist_reports_real_counts_and_not_success():
    stamp_log = []
    scanner = _make_scanner(stamp_log)
    results = scanner.scan_watchlist_artists(
        [_make_artist()],
        profile_id=1,
        scan_state={},
        cancel_check=_cancel_on_call(4),
    )
    assert len(results) == 1
    result = results[0]
    assert result.success is False, "cancelled artist must not report success"
    assert result.albums_checked == 2, (
        f"expected the 2 actually-processed albums, got {result.albums_checked}"
    )


def test_h8_completed_artist_still_stamps_and_succeeds():
    """Control: without a cancel the timestamp, success and full count stand."""
    stamp_log = []
    scanner = _make_scanner(stamp_log)
    results = scanner.scan_watchlist_artists(
        [_make_artist()],
        profile_id=1,
        scan_state={},
        cancel_check=lambda: False,
    )
    assert len(results) == 1
    assert stamp_log == ['Cancel Test Artist']
    assert results[0].success is True
    assert results[0].albums_checked == 5
