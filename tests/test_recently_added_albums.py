"""The dashboard's Recently Added fold (get_recently_added_albums).

library_history is per-TRACK; the rail wants per-ALBUM cards. Pinned here: the
fold key, the single-with-no-album fallback, the track counting past the card
cap, and the art backfill from the library's own albums/artists rows — most
history rows carry no thumb_url, which is exactly the "no images for many
items" report this method exists to fix.
"""

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
        artist_id = conn.execute(
            "INSERT INTO lib2_artists(name, image_url) VALUES('Ado', 'artist.jpg')"
        ).lastrowid
        conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, image_url, origin)"
            " VALUES(?, 'Kyougen', 'album.jpg', 'library')", (artist_id,))
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
        conn.execute(
            "INSERT INTO lib2_artists(name, image_url) VALUES('Camellia', 'cam.jpg')")
        conn.commit()
    _land(db, 'crystallized', 'Camellia feat. Nanahira', 'no such album', thumb='')
    cards = db.get_recently_added_albums(limit=20)
    assert cards[0]['thumb_url'] == 'cam.jpg'
    assert cards[0]['artist_thumb_url'] == 'cam.jpg'


def test_art_falls_back_to_the_artist_thumb_when_no_album_row_matches(tmp_path):
    db = _db(tmp_path)
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO lib2_artists(name, image_url) VALUES('Ado', 'artist.jpg')")
        conn.commit()
    _land(db, 'Loose Single', 'Ado', 'Not In Library', thumb='')
    cards = db.get_recently_added_albums(limit=20)
    assert cards[0]['thumb_url'] == 'artist.jpg'
