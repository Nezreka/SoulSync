"""Album ratingKey migration must preserve Bandcamp enrichment.

PR #968 review (Nezreka): the album ratingKey-migration path in
insert_or_update_media_album copies a fixed `enrichment_cols` list from the
old album row to the new one, but that list carried jiosaavn and every other
source *except* bandcamp. So when a Plex/Jellyfin album's ratingKey changed
(library rescan), the bandcamp match/url/tags/label were dropped and the
worker had to re-scrape bandcamp.com from scratch.

This pins that the six bandcamp_* album columns survive a ratingKey change.
"""

from __future__ import annotations

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


def _seed_bandcamp(db, album_id):
    conn = db._get_connection()
    conn.execute(
        """UPDATE albums SET
               bandcamp_id = ?, bandcamp_url = ?, bandcamp_match_status = ?,
               bandcamp_tags = ?, bandcamp_label = ?
           WHERE id = ?""",
        ('3317386587', 'https://fbr.bandcamp.com/album/episode-1', 'matched',
         'idm,ambient', 'FBR', album_id),
    )
    conn.commit()


def test_album_ratingkey_migration_preserves_bandcamp_enrichment(tmp_path):
    db = MusicDatabase(database_path=str(tmp_path / "test.db"))

    # Satisfy the albums.artist_id foreign key.
    conn = db._get_connection()
    conn.execute("INSERT INTO artists (id, name) VALUES ('artist-1', 'Full Body Recordings')")
    conn.commit()

    # First import lands the album under its original ratingKey, then it gets
    # matched to a Bandcamp release.
    assert db.insert_or_update_media_album(_Album("old-key"), "artist-1", server_source="navidrome")
    _seed_bandcamp(db, "old-key")

    # A rescan re-imports the same album under a new ratingKey (same title +
    # artist + server_source) — the migration branch fires.
    assert db.insert_or_update_media_album(_Album("new-key"), "artist-1", server_source="navidrome")

    row = db._get_connection().execute(
        """SELECT bandcamp_id, bandcamp_url, bandcamp_match_status, bandcamp_tags, bandcamp_label
           FROM albums WHERE id = 'new-key'"""
    ).fetchone()
    assert row is not None, "migrated album row missing"
    assert row["bandcamp_id"] == "3317386587"
    assert row["bandcamp_url"] == "https://fbr.bandcamp.com/album/episode-1"
    assert row["bandcamp_match_status"] == "matched"
    assert row["bandcamp_tags"] == "idm,ambient"
    assert row["bandcamp_label"] == "FBR"

    # Old row is gone (migrated, not duplicated).
    old = db._get_connection().execute(
        "SELECT id FROM albums WHERE id = 'old-key'"
    ).fetchone()
    assert old is None
