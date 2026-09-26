"""Genres soulsync's genre jobs settled survive a media server scan (Cremonies).

genre enrichment and the whitelist cleanup only write soulsync's db, never the
files. the next scan of that artist or album then set genres to whatever the
server read from the files, so a cleanup quietly undid itself the next time
the artist got scanned. users had no way to tell which tool touched what.

when a genre job applies a fix it now marks the row ``genres_locked``, and the
scan upserts keep a locked row's genres. everything else still follows the
server exactly like before. write tags is how the files catch up.

real upserts, real database: the bug was in the scan's SQL.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _artist(rating_key="ar-1", name="Radiohead", genres=("Alternative", "Britpop")):
    return _Obj(ratingKey=rating_key, title=name, thumb=None, genres=list(genres), summary="")


def _album(rating_key="al-1", title="OK Computer", genres=("Alternative", "Britpop")):
    return _Obj(ratingKey=rating_key, title=title, year=1997, thumb=None,
                genres=list(genres), leafCount=12, duration=3200)


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "genres.db"))


def _genres(db, table, row_id):
    conn = db._get_connection()
    try:
        row = conn.execute(f"SELECT genres, genres_locked FROM {table} WHERE id = ?", (row_id,)).fetchone()
        return (json.loads(row[0]) if row and row[0] else None), (row[1] if row else None)
    finally:
        conn.close()


def _scan(db, *, artist_key="ar-1", album_key="al-1", genres=("Alternative", "Britpop")):
    db.insert_or_update_media_artist(_artist(rating_key=artist_key, genres=genres), server_source="navidrome")
    db.insert_or_update_media_album(_album(rating_key=album_key, genres=genres), artist_key, server_source="navidrome")


def _cleanup(db, entity_type, entity_id, kept):
    """the whitelist cleanup job's real fix."""
    worker = SimpleNamespace(db=db)
    return RepairWorker._fix_genre_cleanup(worker, entity_type, entity_id, None, {"kept_genres": kept})


def test_both_tables_have_the_column_and_nothing_is_locked_by_default(db):
    _scan(db)
    assert _genres(db, "artists", "ar-1") == (["Alternative", "Britpop"], 0)
    assert _genres(db, "albums", "al-1") == (["Alternative", "Britpop"], 0)


def test_the_cleanup_survives_the_next_scan(db):
    _scan(db)
    assert _cleanup(db, "artist", "ar-1", ["Alternative"])["success"]
    assert _cleanup(db, "album", "al-1", ["Alternative"])["success"]
    assert _genres(db, "artists", "ar-1") == (["Alternative"], 1)

    # the files still say Britpop, the server says so on the next scan
    _scan(db)

    assert _genres(db, "artists", "ar-1")[0] == ["Alternative"]
    assert _genres(db, "albums", "al-1")[0] == ["Alternative"]


def test_enrichment_additions_survive_the_next_scan(db):
    _scan(db, genres=("Alternative",))
    worker = SimpleNamespace(db=db)
    result = RepairWorker._fix_genre_enrichment(worker, "album", "al-1", None,
                                                {"added_genres": ["Art Rock"]})
    assert result["success"]
    assert _genres(db, "albums", "al-1") == (["Alternative", "Art Rock"], 1)

    _scan(db, genres=("Alternative",))

    assert _genres(db, "albums", "al-1")[0] == ["Alternative", "Art Rock"]


def test_rows_the_genre_jobs_never_touched_still_follow_the_server(db):
    _scan(db)
    _scan(db, genres=("Electronic",))
    assert _genres(db, "artists", "ar-1") == (["Electronic"], 0)
    assert _genres(db, "albums", "al-1") == (["Electronic"], 0)


def test_the_lock_rides_through_a_server_id_change(db):
    """the server handed the same artist and album new ids after a rescan. the
    rows are rebuilt under the new ids, the settled genres and the lock go with
    them."""
    _scan(db)
    _cleanup(db, "artist", "ar-1", ["Alternative"])
    _cleanup(db, "album", "al-1", ["Alternative"])

    _scan(db, artist_key="ar-2", album_key="al-2")

    assert _genres(db, "artists", "ar-2") == (["Alternative"], 1)
    assert _genres(db, "albums", "al-2") == (["Alternative"], 1)


def test_a_cleanup_to_nothing_stays_nothing(db):
    """strict means strict: an all-off-whitelist entity keeps no genres, the
    scan doesn't refill it."""
    _scan(db)
    _cleanup(db, "artist", "ar-1", [])
    _scan(db)
    assert _genres(db, "artists", "ar-1") == (None, 1)
