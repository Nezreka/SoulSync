"""The dashboard's Recently Added fold (get_recently_added_albums).

library_history is per-TRACK; the rail wants per-ALBUM cards. Pinned here: the
fold key, the single-with-no-album fallback, the track counting past the card
cap, and the art backfill from the library's own albums/artists rows — most
history rows carry no thumb_url, which is exactly the "no images for many
items" report this method exists to fix.
"""

import sqlite3

import pytest

from database.music_database import MusicDatabase


def _db(tmp_path):
    return MusicDatabase(str(tmp_path / 'm.db'))


def _land(db, title, artist, album, thumb='', quality='flac', source='soulseek',
          created='2026-08-10 12:00:00', file_path='/m/x.flac'):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO library_history (event_type, title, artist_name, album_name,"
            " quality, thumb_url, download_source, file_path, created_at)"
            " VALUES ('download', ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, artist, album, quality, thumb, source, file_path, created))
        # #1453: get_recently_added_albums drops cards whose play target has
        # no matching tracks row (a rebuild wipes tracks but keeps
        # library_history), so the helper lands a tracks row for every
        # history row it inserts. The fixture artist/album names are
        # deliberately unlike any test artist/album so the art-backfill
        # lookups can never match them.
        conn.execute(
            "INSERT OR IGNORE INTO artists (id, name)"
            " VALUES ('ra-fixture-artist', 'Recently Added Fixture Artist')")
        conn.execute(
            "INSERT OR IGNORE INTO albums (id, artist_id, title)"
            " VALUES ('ra-fixture-album', 'ra-fixture-artist',"
            " 'Recently Added Fixture Album')")
        conn.execute(
            "INSERT INTO tracks (album_id, artist_id, title, file_path)"
            " VALUES ('ra-fixture-album', 'ra-fixture-artist', ?, ?)",
            (title, file_path))
        conn.commit()


def test_per_track_rows_fold_to_one_card_per_album(tmp_path):
    db = _db(tmp_path)
    _land(db, 'Track 1', 'Camellia', 'U.U.F.O.', thumb='u.jpg', created='2026-08-10 12:00:00')
    _land(db, 'Track 2', 'Camellia', 'U.U.F.O.', created='2026-08-10 11:59:00')
    _land(db, 'Solo', 'Ado', 'Kyougen', thumb='k.jpg', created='2026-08-10 11:00:00')

    cards = db.get_recently_added_albums(limit=20)
    assert [c['album_name'] for c in cards] == ['U.U.F.O.', 'Kyougen']
    assert cards[0]['track_count'] == 2
    assert cards[0]['thumb_url'] == 'u.jpg'
    # The newest row supplies the play target and file facts.
    assert cards[0]['play_title'] == 'Track 1'
    assert cards[0]['quality'] == 'FLAC'
    assert cards[0]['download_source'] == 'soulseek'


def test_single_with_no_album_uses_its_title(tmp_path):
    db = _db(tmp_path)
    _land(db, 'Vivarium', 'Ado', '')
    cards = db.get_recently_added_albums(limit=20)
    assert cards[0]['album_name'] == 'Vivarium'


def test_cap_keeps_counting_tracks_for_kept_cards(tmp_path):
    db = _db(tmp_path)
    _land(db, 't1', 'A', 'One', created='2026-08-10 12:00:00')
    _land(db, 't2', 'B', 'Two', created='2026-08-10 11:00:00')
    _land(db, 't3', 'A', 'One', created='2026-08-10 10:00:00')  # dup of a kept card
    _land(db, 't4', 'C', 'Three', created='2026-08-10 09:00:00')  # over the cap
    cards = db.get_recently_added_albums(limit=2)
    assert [c['album_name'] for c in cards] == ['One', 'Two']
    assert cards[0]['track_count'] == 2


def test_missing_art_backfills_from_the_library_album_row(tmp_path):
    db = _db(tmp_path)
    with db._get_connection() as conn:
        # Explicit ids: the soulid_v2 migration rebuilds artists/albums with
        # TEXT primary keys, so lastrowid-style implicit ids leave id NULL and
        # the albums FK rejects the row.
        conn.execute(
            "INSERT INTO artists (id, name, thumb_url) VALUES ('ar1', 'Ado', 'artist.jpg')")
        conn.execute(
            "INSERT INTO albums (id, artist_id, title, thumb_url)"
            " VALUES ('al1', 'ar1', 'Kyougen', 'album.jpg')")
        conn.commit()
    # History row lands with NO art — the common case.
    _land(db, 'Vivarium', 'Ado', 'Kyougen', thumb='')
    cards = db.get_recently_added_albums(limit=20)
    assert cards[0]['thumb_url'] == 'album.jpg'


def test_feat_credited_history_row_still_finds_the_primary_artists_art(tmp_path):
    """History often lands 'A feat. B' while the library row is just 'A'.
    An exact-only name match left those cards artless — the primary-artist
    retry is what closes it."""
    db = _db(tmp_path)
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, thumb_url) VALUES ('ar1', 'Camellia', 'cam.jpg')")
        conn.commit()
    _land(db, 'crystallized', 'Camellia feat. Nanahira', 'no such album', thumb='')
    cards = db.get_recently_added_albums(limit=20)
    assert cards[0]['thumb_url'] == 'cam.jpg'
    assert cards[0]['artist_thumb_url'] == 'cam.jpg'


def test_art_falls_back_to_the_artist_thumb_when_no_album_row_matches(tmp_path):
    db = _db(tmp_path)
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, thumb_url) VALUES ('ar1', 'Ado', 'artist.jpg')")
        conn.commit()
    _land(db, 'Loose Single', 'Ado', 'Not In Library', thumb='')
    cards = db.get_recently_added_albums(limit=20)
    assert cards[0]['thumb_url'] == 'artist.jpg'


def test_rebuild_wiped_tracks_produces_no_stale_cards(tmp_path):
    """#1453: a Full Refresh wipes tracks/albums/artists but KEEPS
    library_history (the Expired Download Cleaner grandfathers pre-rebuild
    downloads off it). The rail must not surface cards whose play target no
    longer exists in the tracks table — here the tracks table is empty."""
    db = _db(tmp_path)
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO library_history (event_type, title, artist_name, album_name,"
            " quality, thumb_url, download_source, file_path, created_at)"
            " VALUES ('download', 'Ghost Track', 'Ghost Artist', 'Ghost Album',"
            " 'flac', '', 'soulseek', '/gone/ghost/ghost.flac', '2026-08-10 12:00:00')")
        conn.commit()
    assert db.get_recently_added_albums(limit=20) == []


def test_mixed_live_and_stale_rows_keep_only_the_live_card(tmp_path):
    """#1453: one history row still has its track in the library, another
    was wiped by a rebuild — only the surviving card is shown."""
    db = _db(tmp_path)
    _land(db, 'Live Track', 'Live Artist', 'Live Album', file_path='/m/live.flac')
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO library_history (event_type, title, artist_name, album_name,"
            " quality, thumb_url, download_source, file_path, created_at)"
            " VALUES ('download', 'Stale Track', 'Stale Artist', 'Stale Album',"
            " 'flac', '', 'soulseek', '/gone/stale/stale.flac', '2026-08-10 12:00:00')")
        conn.commit()
    cards = db.get_recently_added_albums(limit=20)
    assert [c['album_name'] for c in cards] == ['Live Album']


# ---------------------------------------------------------------------------
# the rail loaded every track's path (300k) and keyed each one in python on
# every dashboard load, even when every card's file was right there on disk:
# 18 s cold, 1.3 s warm. only cards whose file is gone ask the library now,
# and only about their own path. the answer has to stay exactly the same.
# ---------------------------------------------------------------------------


def _history_only(db, title, album, file_path, created='2026-08-10 12:00:00'):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO library_history (event_type, title, artist_name, album_name,"
            " quality, thumb_url, download_source, file_path, created_at)"
            " VALUES ('download', ?, 'Some Artist', ?, 'flac', '', 'soulseek', ?, ?)",
            (title, album, file_path, created))
        conn.commit()


def _track_at(db, file_path):
    with db._get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO artists (id, name) VALUES ('ra-x', 'RA X')")
        conn.execute("INSERT OR IGNORE INTO albums (id, artist_id, title) VALUES ('ra-xa', 'ra-x', 'RA XA')")
        conn.execute("INSERT INTO tracks (album_id, artist_id, title, file_path) VALUES ('ra-xa', 'ra-x', 't', ?)",
                     (file_path,))
        conn.commit()


POINT_LOOKUP = 'SELECT album_id, artist_id FROM tracks WHERE file_path = ?'


def _track_path_queries(monkeypatch, point_lookups=None):
    """the path-matching passes over tracks. the album-id lookup is one
    indexed row per card, not a pass, so it goes in point_lookups instead."""
    seen = []

    class Cursor(sqlite3.Cursor):
        def execute(self, sql, params=()):
            flat = ' '.join(sql.split())
            if flat.startswith(POINT_LOOKUP):
                if point_lookups is not None:
                    point_lookups.append(flat)
            elif 'FROM tracks WHERE file_path' in flat:
                seen.append(flat)
            return super().execute(sql, params)

    class Conn(sqlite3.Connection):
        def cursor(self, *a, **k):
            return super().cursor(Cursor)

    def connect(self):
        c = sqlite3.connect(str(self.database_path), factory=Conn)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(MusicDatabase, '_get_connection', connect)
    return seen


def test_files_on_disk_never_ask_the_library(tmp_path, monkeypatch):
    db = _db(tmp_path)
    for i in range(3):
        f = tmp_path / f'song{i}.flac'
        f.write_bytes(b'x')
        _history_only(db, f'Song {i}', f'Album {i}', str(f))
    points = []
    seen = _track_path_queries(monkeypatch, points)
    cards = db.get_recently_added_albums(limit=20)
    assert len(cards) == 3
    assert seen == []
    # the album-id lookup: one indexed row per card, never more
    assert len(points) <= len(cards)


def test_gone_from_disk_but_in_the_library_under_another_root_is_kept(tmp_path, monkeypatch):
    db = _db(tmp_path)
    _history_only(db, 'Moved', 'Moved Album', '/downloads/staging/Artist - Album/01 - Moved.flac')
    _track_at(db, r'\\nas\music\Artist - Album\01 - MOVED.flac')
    seen = _track_path_queries(monkeypatch)
    cards = db.get_recently_added_albums(limit=20)
    assert [c['album_name'] for c in cards] == ['Moved Album']
    # one prefilter query answered it, no full pass
    assert len(seen) == 1 and 'LIKE' in seen[0], seen


def test_a_casefold_the_prefilter_cannot_see_still_keeps_the_card(tmp_path):
    # 'STRASSE' and 'Straße' casefold to the same key, but sqlite's LIKE
    # can't see that, so the full check has to catch it
    db = _db(tmp_path)
    _history_only(db, 'S', 'Strasse Album', '/gone/Album/STRASSE.flac')
    _track_at(db, '/music/Album/Straße.flac')
    assert [c['album_name'] for c in db.get_recently_added_albums(limit=20)] == ['Strasse Album']


def test_wildcards_in_a_filename_never_match_the_wrong_file(tmp_path):
    db = _db(tmp_path)
    _history_only(db, 'Pct', 'Pct Album', '/gone/Album/100% _live_.flac')
    _track_at(db, '/music/Album/100X XliveX.flac')
    assert db.get_recently_added_albums(limit=20) == []


@pytest.mark.parametrize('n_gone', [1, 5, 6, 12])
def test_many_gone_cards_match_keying_every_path(tmp_path, n_gone):
    from core.library import live_paths

    db = _db(tmp_path)
    for i in range(n_gone):
        _history_only(db, f'T{i}', f'Album {i}', f'/gone/Album {i}/{i:02d} - Track.flac',
                      created=f'2026-08-10 12:{i:02d}:00')
        if i % 2 == 0:
            _track_at(db, f'D:/Music/album {i}/{i:02d} - TRACK.flac')
    cards = db.get_recently_added_albums(limit=50)
    assert sorted(c['album_name'] for c in cards) == sorted(
        f'Album {i}' for i in range(n_gone) if i % 2 == 0)
    assert live_paths.PREFILTER_MAX == 5


# ---------------------------------------------------------------------------
# the card's play / queue / add-to-playlist buttons act on the whole album, so
# each card carries the library album it landed in.
# ---------------------------------------------------------------------------


def test_card_carries_the_library_album_its_newest_track_is_in(tmp_path):
    db = _db(tmp_path)
    _land(db, 'Track 1', 'Camellia', 'U.U.F.O.', file_path='/m/ufo/1.flac')
    cards = db.get_recently_added_albums(limit=20)
    # _land files every track under the fixture album
    assert cards[0]['album_id'] == 'ra-fixture-album'
    assert cards[0]['artist_id'] == 'ra-fixture-artist'


def test_a_path_that_moved_roots_finds_the_album_by_name(tmp_path):
    db = _db(tmp_path)
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES ('ar1', 'Some Artist')")
        conn.execute("INSERT INTO albums (id, artist_id, title) VALUES ('al1', 'ar1', 'Moved Album')")
        conn.commit()
    _history_only(db, 'Moved', 'Moved Album', '/downloads/staging/Artist - Album/01 - Moved.flac')
    _track_at(db, r'\\nas\music\Artist - Album\01 - MOVED.flac')
    cards = db.get_recently_added_albums(limit=20)
    assert cards[0]['album_id'] == 'al1'
    assert cards[0]['artist_id'] == 'ar1'


def test_no_library_album_leaves_the_card_single_track(tmp_path):
    db = _db(tmp_path)
    f = tmp_path / 'loose.flac'
    f.write_bytes(b'x')
    _history_only(db, 'Loose', 'Not In Library', str(f))
    cards = db.get_recently_added_albums(limit=20)
    assert cards[0]['album_id'] is None
    assert cards[0]['artist_id'] is None
