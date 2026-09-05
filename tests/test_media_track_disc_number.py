"""Multi-disc fix (#927): the media-server scan must store the real disc number.

Every track was stored with disc_number=1 because the Jellyfin/Plex/Navidrome scan never
read the disc field — so multi-disc albums collapsed onto disc 1, mis-filing disc-2+ tracks
and flagging them "missing". insert_or_update_media_track now reads the disc number off the
track object (Jellyfin/Navidrome `.discNumber`, Plex `.parentIndex`), floored to >=1.
"""

from __future__ import annotations

from types import SimpleNamespace

from database.music_database import MusicDatabase


def _db(tmp_path, server_source='jellyfin'):
    """A catalogue that already knows the artist and album, as the scan's own
    passes leave it — both addressed by the SERVER's ids."""
    db = MusicDatabase(database_path=str(tmp_path / "t.db"))
    with db._get_connection() as c:
        artist = c.execute(
            "INSERT INTO lib2_artists (name, name_key, server_source, server_id)"
            " VALUES ('Evan Call', 'evan call', ?, 'art1')", (server_source,)).lastrowid
        c.execute(
            "INSERT INTO lib2_albums (primary_artist_id, title, origin, server_source,"
            "                         server_id)"
            " VALUES (?, 'Frieren OST', 'library', ?, 'alb1')", (artist, server_source))
        c.commit()
    return db


def _track(rating_key, title, track_number, **kw):
    return SimpleNamespace(ratingKey=rating_key, title=title, trackNumber=track_number,
                           duration=200000, path=f"/music/{rating_key}.flac", **kw)


def _imported(db, track):
    with db._get_connection() as c:
        album = c.execute("SELECT id FROM lib2_albums WHERE server_id='alb1'").fetchone()[0]
        track_id = c.execute(
            "INSERT INTO lib2_tracks(album_id,title,track_number,disc_number) VALUES(?,?,?,1)",
            (album, track.title, track.trackNumber),
        ).lastrowid
        c.execute("INSERT INTO lib2_track_files(track_id,path,is_primary) VALUES(?,?,1)",
                  (track_id, track.path))
        c.commit()


def _disc_of(db, track_id):
    with db._get_connection() as c:
        row = c.execute(
            "SELECT disc_number, track_number FROM lib2_tracks WHERE server_id = ?",
            (track_id,)).fetchone()
    return (row['disc_number'], row['track_number'])


def test_jellyfin_navidrome_disc_number_stored(tmp_path):
    db = _db(tmp_path)
    # .discNumber is what the Jellyfin (ParentIndexNumber) + Navidrome (discNumber) wrappers set.
    d2 = _track('t-d2', 'Waltz for Stark and Fern', 34, discNumber=2)
    d1 = _track('t-d1', 'The Magic Within', 32, discNumber=1)
    _imported(db, d2); _imported(db, d1)
    db.insert_or_update_media_track(d2, 'alb1', 'art1', 'jellyfin')
    db.insert_or_update_media_track(d1, 'alb1', 'art1', 'jellyfin')
    assert _disc_of(db, 't-d2') == (2, 34)
    assert _disc_of(db, 't-d1') == (1, 32)


def test_plex_parent_index_used_as_disc(tmp_path):
    db = _db(tmp_path, 'plex')
    # plexapi Track has no .discNumber — disc comes from .parentIndex.
    track = _track('t-plex', 'Disc 2 Track', 5, parentIndex=2)
    _imported(db, track)
    db.insert_or_update_media_track(track, 'alb1', 'art1', 'plex')
    assert _disc_of(db, 't-plex') == (2, 5)


def test_missing_or_bad_disc_floors_to_one(tmp_path):
    db = _db(tmp_path)
    tracks = [_track('t-none', 'No Disc', 1),
              _track('t-zero', 'Zero Disc', 2, discNumber=0),
              _track('t-str', 'Junk Disc', 3, discNumber='x')]
    for track in tracks:
        _imported(db, track)
        db.insert_or_update_media_track(track, 'alb1', 'art1', 'jellyfin')
    assert _disc_of(db, 't-none')[0] == 1
    assert _disc_of(db, 't-zero')[0] == 1
    assert _disc_of(db, 't-str')[0] == 1


def test_update_path_backfills_disc_on_rescan(tmp_path):
    db = _db(tmp_path)
    # First scan (old behavior simulated): no disc -> 1. Re-scan with the real disc -> updated.
    track = _track('t-x', 'Track', 7)
    _imported(db, track)
    db.insert_or_update_media_track(track, 'alb1', 'art1', 'jellyfin')
    assert _disc_of(db, 't-x') == (1, 7)
    db.insert_or_update_media_track(_track('t-x', 'Track', 7, discNumber=3), 'alb1', 'art1', 'jellyfin')
    assert _disc_of(db, 't-x') == (3, 7)
