"""#1308 / #1309 (olad32).

#1308: artists on the watchlist page read "Add to watchlist" on their own
page. without a spotify link the page asks with the LIBRARY row id; add
translated that id into the provider id it stores, check and remove never
did. clicking add changed nothing, a refresh showed "Add" again.

#1309: some new releases never reached the wishlist though the artist page
listed them. an incremental scan only looked at releases dated after the
last scan: a release a provider lists a few days late (or dated today at
midnight utc, before a scan that ran this morning) was behind the cutoff by
the time it appeared, and was never looked at again.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "m.db"))


def _library_artist(db, lib_id, **ids):
    conn = db._get_connection()
    cols = ["id", "name"] + list(ids)
    conn.execute(f"INSERT INTO artists ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 [lib_id, "Destiny's Child", *ids.values()])
    conn.commit()
    conn.close()


def _watch(db, **ids):
    conn = db._get_connection()
    cols = ["artist_name", "profile_id"] + list(ids)
    conn.execute(f"INSERT INTO watchlist_artists ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 ["Destiny's Child", 1, *ids.values()])
    conn.commit()
    conn.close()


def test_a_library_id_finds_the_artist_watched_by_its_provider_id(db):
    _library_artist(db, 77, deezer_id="3456")
    _watch(db, deezer_artist_id="3456")
    assert db.is_artist_in_watchlist("3456", profile_id=1)
    assert db.is_artist_in_watchlist("77", profile_id=1)          # was False
    assert not db.is_artist_in_watchlist("78", profile_id=1)


def test_a_library_id_can_remove_it_too(db):
    _library_artist(db, 77, deezer_id="3456")
    _watch(db, deezer_artist_id="3456")
    assert db.remove_artist_from_watchlist("77", profile_id=1)
    assert not db.is_artist_in_watchlist("3456", profile_id=1)


def test_a_raw_provider_id_wins_over_a_library_row_with_the_same_number(db):
    """numeric deezer ids and library ids can collide: the raw id is tried
    first, so an existing caller's meaning never changes."""
    _library_artist(db, 3456, deezer_id="999")
    _watch(db, deezer_artist_id="3456")
    _watch(db, deezer_artist_id="999", spotify_artist_id="sp999")
    assert db.remove_artist_from_watchlist("3456", profile_id=1)
    assert db.is_artist_in_watchlist("999", profile_id=1)          # the other one is untouched


# ── #1309 ────────────────────────────────────────────────────────────────────

def test_incremental_cutoff_overlaps_the_last_scan():
    from core.watchlist_scanner import INCREMENTAL_OVERLAP_DAYS, incremental_cutoff
    last = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)
    assert incremental_cutoff(last, "30") == last - timedelta(days=INCREMENTAL_OVERLAP_DAYS)
    assert incremental_cutoff(last, "7") == last - timedelta(days=7)
    assert incremental_cutoff(last, "all") == last - timedelta(days=INCREMENTAL_OVERLAP_DAYS)
    assert incremental_cutoff(None, "30") is None


def test_a_release_listed_after_its_date_is_still_found():
    """the provider added a friday release on monday; the scan ran on
    saturday. it must be picked up on the next scan, not skipped forever."""
    from core.watchlist_scanner import WatchlistScanner
    scanner = WatchlistScanner.__new__(WatchlistScanner)
    scanner._rescan_cutoff_log_marker = None
    scanner._get_lookback_period_setting = lambda: "30"
    scanner._get_rescan_cutoff = lambda: None
    today = datetime.now(timezone.utc)
    friday = (today - timedelta(days=4)).strftime("%Y-%m-%d")
    album = SimpleNamespace(name="New One", release_date=friday, album_type="album")
    old = SimpleNamespace(name="Old One", release_date="2019-01-01", album_type="album")
    client = SimpleNamespace(get_artist_albums=lambda *a, **k: [album, old])
    last_scan = today - timedelta(days=3)          # saturday
    import core.watchlist_scanner as ws
    real_sleep = ws.time.sleep
    ws.time.sleep = lambda s: None
    try:
        got = scanner._get_artist_discography_with_client(client, "a1", last_scan_timestamp=last_scan)
    finally:
        ws.time.sleep = real_sleep
    assert [a.name for a in got] == ["New One"]
