"""A changed rating key must not cost the album its enrichment.

PR #968 review (Nezreka): the legacy scan answered a new rating key by creating
a NEW row and copying a hand-listed set of enrichment columns onto it — and the
list had every source except bandcamp, so a rescan threw the bandcamp match
away. In the catalogue there is nothing to copy: the row has its own identity
and the rating key is just a stamp on it, so a rescan re-stamps and everything
else stays where it was (§50.4.4.26).
"""

from __future__ import annotations

import json

from database.music_database import MusicDatabase


class _Album:
    def __init__(self, rating_key):
        self.ratingKey = rating_key
        self.title = "Episode 1"
        self.year = 2017
        self.leafCount = 3
        self.duration = 600
        self.genres = []
        self.thumb = None


_BANDCAMP = {
    "id": "3317386587",
    "url": "https://fbr.bandcamp.com/album/episode-1",
    "match_status": "matched",
    "tags": "idm,ambient",
    "label": "FBR",
}


def _seed_bandcamp(db, catalogue_album_id):
    conn = db._get_connection()
    conn.execute(
        "UPDATE lib2_albums SET enrichment = ? WHERE id = ?",
        (json.dumps({"bandcamp": _BANDCAMP}), catalogue_album_id))
    conn.commit()


def test_a_new_rating_key_keeps_the_album_and_its_enrichment(tmp_path):
    db = MusicDatabase(database_path=str(tmp_path / "test.db"))
    conn = db._get_connection()
    artist = conn.execute(
        "INSERT INTO lib2_artists (name, name_key, server_source, server_id)"
        " VALUES ('Full Body Recordings', 'full body recordings', 'navidrome', 'artist-1')"
    ).lastrowid
    original = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,origin) "
        "VALUES(?,'Episode 1','library')", (artist,)
    ).lastrowid
    track = conn.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Track')",
                         (original,)).lastrowid
    conn.execute("INSERT INTO lib2_track_files(track_id,path) VALUES(?, '/music/track.flac')",
                 (track,))
    conn.commit()

    assert db.insert_or_update_media_album(_Album("old-key"), "artist-1",
                                           server_source="navidrome")
    _seed_bandcamp(db, original)

    # A rescan re-imports the same album under a new rating key.
    assert db.insert_or_update_media_album(_Album("new-key"), "artist-1",
                                           server_source="navidrome")

    rows = db._get_connection().execute(
        "SELECT id, server_id, enrichment FROM lib2_albums").fetchall()
    assert len(rows) == 1, "a new rating key must not fork the album into two rows"
    assert rows[0]["id"] == original, "the catalogue row keeps its identity"
    assert rows[0]["server_id"] == "new-key", "and takes the new stamp"
    assert json.loads(rows[0]["enrichment"])["bandcamp"] == _BANDCAMP
