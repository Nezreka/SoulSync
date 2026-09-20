"""#1257: every album listed each track twice after a reorganize (plex, 230k files).

the chain: reorganize moves the file and repoints its row (plex id X) at the
new path. plex does not track moves: it trashes X and mints a new item Y for
the same file. the next scan inserts Y beside X. only the deep scan ever
removed X, and its 50% safety skipped exactly the libraries that had this
the worst (a reorganized library is nearly half superseded rows).

now: a trashed plex item is never written, and every scan ends by folding a
row the server no longer lists into the live row for the same file, carrying
its enrichment and play history. driven through the deep scan here: a full
refresh wipes the server's rows first (and the enrichment with them), so
the deep scan is the one a reorganized library should run.
"""

from __future__ import annotations

from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

pytestmark = pytest.mark.skip(reason=(
    "the legacy absorb pass this pins is gone: it folds a re-ided file's old `tracks` row into its live one, and this catalogue cannot take that shape -- a file is a lib2_track_files row keyed by path and content hash, and a server re-id moves a mapping in lib2_media_server_mappings instead of minting a second track. OPEN with it: the pass also kept superseded rows out of the deep scan's 50% shrink guard, and whether that guard can still trip here after a reorganize is unverified (docs/library-v2-dir-ownership.md)"
))

from core.database_update_worker import DatabaseUpdateWorker
from database.music_database import MusicDatabase


def _track(key, title, path, duration=274000, size=54_000_000, trashed=False, disc=1):
    part = SimpleNamespace(file=path, size=size)
    # plexapi keeps the raw xml element on _data; deletedAt only lives there
    data = ET.Element('Track', {'deletedAt': '1756500000'} if trashed else {})
    return SimpleNamespace(
        ratingKey=key, title=title, trackNumber=1, parentIndex=disc, duration=duration,
        media=[SimpleNamespace(parts=[part], bitrate=1657)],
        _data=data,
    )


def _album(key, title, tracks):
    return SimpleNamespace(ratingKey=key, title=title, year=2022, thumb=None,
                           tracks=lambda: list(tracks))


def _artist(key, name, albums):
    return SimpleNamespace(ratingKey=key, title=name, thumb=None, genres=[],
                           albums=lambda: list(albums))


class _Client:
    def __init__(self, artists):
        self.artists = artists
        self.last_fetch_failed = False

    def ensure_connection(self):
        return True

    def get_all_artists(self):
        return list(self.artists)

    def set_progress_callback(self, cb):
        pass

    def clear_cache(self):
        pass


@pytest.fixture()
def db(tmp_path, monkeypatch):
    database = MusicDatabase(str(tmp_path / "music.db"))
    monkeypatch.setattr("core.database_update_worker.get_database", lambda path=None: database)
    return database


def _worker(db, client):
    w = DatabaseUpdateWorker(media_client=client, database_path=db.database_path,
                             full_refresh=False, server_type="plex", force_sequential=True)
    w.events = []
    w.callbacks["error"].append(lambda *a: w.events.append(("error", a)))
    return w


def _rows(db):
    with db._get_connection() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, file_path, spotify_track_id, isrc FROM tracks ORDER BY id").fetchall()]


OLD_PATH = "/plex/music/Deborah de Luca/Children/Children.flac"
LOCAL_NEW = "/local/music/Deborah de Luca/Deborah de Luca - Children/01 - Children.flac"
PLEX_NEW = "/plex/music/Deborah de Luca/Deborah de Luca - Children/01 - Children.flac"


def _first_scan(db):
    """the library before the reorganize: one row, plex id t-old."""
    artist = _artist('ar1', 'Deborah de Luca', [_album('al1', 'Children', [_track('t-old', 'Children', OLD_PATH)])])
    _worker(db, _Client([artist])).run_deep_scan()
    with db._get_connection() as c:
        # enrichment and a play the old row picked up in the meantime
        c.execute("UPDATE tracks SET spotify_track_id = 'sp1', isrc = 'ISRC1' WHERE id = 't-old'")
        c.execute("INSERT INTO listening_history (track_id, title, artist, album, played_at, db_track_id) "
                  "VALUES ('x', 'Children', 'Deborah de Luca', 'Children', '2026-09-01', 't-old')")
        # reorganize repoints the row at the LOCAL form of the new path
        c.execute("UPDATE tracks SET file_path = ? WHERE id = 't-old'", (LOCAL_NEW,))
        c.commit()
    assert [r['id'] for r in _rows(db)] == ['t-old']


def test_the_reported_sequence_ends_with_one_row(db):
    _first_scan(db)
    # plex after its own scan: the old item is in the trash, the new one is live
    artist = _artist('ar1', 'Deborah de Luca', [_album('al1', 'Children', [
        _track('t-old', 'Children', OLD_PATH, trashed=True),
        _track('t-new', 'Children', PLEX_NEW),
    ])])
    w = _worker(db, _Client([artist]))
    w.run_deep_scan()
    assert not w.events
    rows = _rows(db)
    assert [r['id'] for r in rows] == ['t-new'], rows
    # the live row inherits what the old one knew
    assert rows[0]['spotify_track_id'] == 'sp1'
    assert rows[0]['isrc'] == 'ISRC1'
    with db._get_connection() as c:
        assert c.execute("SELECT db_track_id FROM listening_history").fetchone()[0] == 't-new'
        assert c.execute("SELECT COUNT(*) FROM track_credits WHERE track_id = 't-old'").fetchone()[0] == 0


def test_after_plex_emptied_its_trash_the_old_row_still_folds(db):
    # the old item is gone from the api entirely (trash emptied) but the
    # row is still in our table: this is the state a full refresh finds on
    # a library that was reorganized weeks ago
    _first_scan(db)
    artist = _artist('ar1', 'Deborah de Luca', [_album('al1', 'Children', [_track('t-new', 'Children', PLEX_NEW)])])
    _worker(db, _Client([artist])).run_deep_scan()
    assert [r['id'] for r in _rows(db)] == ['t-new']


def test_the_pass_is_fingerprint_not_path(db):
    # local vs plex path forms differ on every mapped setup; the fold keys on
    # album + disc + file name + length. a different length is a different
    # file, so the fold leaves it alone (the deep scan's stale phase is what
    # judges a vanished file, not this)
    _first_scan(db)
    artist = _artist('ar1', 'Deborah de Luca', [_album('al1', 'Children', [
        _track('t-new', 'Children', PLEX_NEW, duration=206000),
    ])])
    w = _worker(db, _Client([artist]))
    w.database = db
    w._process_artist_with_content(artist)
    assert w._absorb_superseded_tracks() == 0
    assert [r['id'] for r in _rows(db)] == ['t-new', 't-old']


def test_two_items_the_server_still_lists_are_never_merged(db):
    # overlapping library sections can legitimately list one file twice; both
    # ids are seen, so neither is "superseded" and nothing is folded
    tracks = [_track('t-a', 'Children', PLEX_NEW), _track('t-b', 'Children', PLEX_NEW)]
    artist = _artist('ar1', 'Deborah de Luca', [_album('al1', 'Children', tracks)])
    _worker(db, _Client([artist])).run_deep_scan()
    assert [r['id'] for r in _rows(db)] == ['t-a', 't-b']
    _worker(db, _Client([artist])).run_deep_scan()
    assert [r['id'] for r in _rows(db)] == ['t-a', 't-b']


def test_a_trashed_item_is_never_written(db):
    artist = _artist('ar1', 'Deborah de Luca', [_album('al1', 'Children', [
        _track('t-gone', 'Children', OLD_PATH, trashed=True),
        _track('t-live', 'Children', PLEX_NEW),
    ])])
    w = _worker(db, _Client([artist]))
    w.run_deep_scan()
    assert [r['id'] for r in _rows(db)] == ['t-live']
    assert w._trashed_skipped == 1


def test_an_incremental_scan_folds_the_albums_it_touched(db):
    # the incremental path has no stale phase at all; the fold runs at the
    # end of every scan over the albums that scan wrote
    _first_scan(db)
    artist = _artist('ar1', 'Deborah de Luca', [_album('al1', 'Children', [_track('t-new', 'Children', PLEX_NEW)])])
    w = _worker(db, _Client([artist]))
    w.database = db
    w._process_artist_with_content(artist)
    assert sorted(r['id'] for r in _rows(db)) == ['t-new', 't-old']
    assert w._absorb_superseded_tracks() == 1
    assert [r['id'] for r in _rows(db)] == ['t-new']


def test_deep_scan_folds_before_the_stale_safety_can_trip(db):
    # a whole reorganized library: 4 files, 8 rows. stale = 50% would have
    # been the guard's territory; superseded rows are removed before it looks
    tracks_old = [_track(f't-old{i}', f'T{i}', f'/plex/old/{i}.flac', duration=100000 + i) for i in range(4)]
    artist = _artist('ar1', 'Artist', [_album('al1', 'Album', tracks_old)])
    _worker(db, _Client([artist])).run_deep_scan()
    with db._get_connection() as c:
        for i in range(4):
            c.execute("UPDATE tracks SET file_path = ? WHERE id = ?", (f'/local/new/{i}.flac', f't-old{i}'))
        c.commit()
    tracks_new = [_track(f't-new{i}', f'T{i}', f'/plex/new/{i}.flac', duration=100000 + i) for i in range(4)]
    w = _worker(db, _Client([_artist('ar1', 'Artist', [_album('al1', 'Album', tracks_new)])]))
    w.run_deep_scan()
    assert sorted(r['id'] for r in _rows(db)) == sorted(f't-new{i}' for i in range(4))
