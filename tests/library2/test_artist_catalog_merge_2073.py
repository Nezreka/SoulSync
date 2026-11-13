"""#2073: two artists that share one catalogue id are not one artist on that
alone. enrichment stamps ids from a name search, so near-named bands end up
holding the same id; the startup repair folded Attack Attack! (US) into
Attack! Attack! (Wales) and twenty albums changed hands."""

import json

from core.library2.dedup_repair import repair_duplicate_artists
from tests.library2.test_dedup_repair import _album, _artist, repair_db  # noqa: F401


def _owned_album(conn, artist_id, title, year):
    album_id = _album(conn, artist_id, title, with_track=True, release_date=f"{year}-01-01")
    track_id = conn.execute(
        "SELECT id FROM lib2_tracks WHERE album_id=?", (album_id,)).fetchone()[0]
    conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?, ?)",
                 (track_id, f"/music/{artist_id}/{title}.flac"))
    return album_id


def _alive(conn, *ids):
    marks = ",".join("?" for _ in ids)
    return conn.execute(
        f"SELECT COUNT(*) FROM lib2_artists WHERE id IN ({marks})", ids).fetchone()[0]


def test_two_bands_with_their_own_records_stay_two(repair_db):  # noqa: F811
    legacy_db, conn = repair_db
    welsh = _artist(conn, "Attack! Attack!", external_ids=json.dumps({"deezer": "150915"}))
    american = _artist(conn, "Attack Attack!", external_ids=json.dumps({"deezer": "150915"}))
    _owned_album(conn, welsh, "Attack! Attack!", 2008)
    _owned_album(conn, welsh, "The Latest Fashion", 2010)
    theirs = [_owned_album(conn, american, "Someday Came Suddenly", 2008),
              _owned_album(conn, american, "Attack Attack!", 2010),
              _owned_album(conn, american, "This Means War", 2012)]
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["artists_merged"] == 0
    assert stats["artists_left_apart"] == 1
    assert _alive(conn, welsh, american) == 2
    for album_id in theirs:
        assert conn.execute("SELECT primary_artist_id FROM lib2_albums WHERE id=?",
                            (album_id,)).fetchone()[0] == american


def test_two_bands_stay_two_even_when_two_ids_were_stamped_alike(repair_db):  # noqa: F811
    legacy_db, conn = repair_db
    ids = json.dumps({"deezer": "150915"})
    first = _artist(conn, "Attack! Attack!", spotify_id="7ITMCzIU9uII8gwRg8JAhc",
                    external_ids=ids)
    second = _artist(conn, "Attack Attack!", spotify_id="7ITMCzIU9uII8gwRg8JAhc",
                     external_ids=ids)
    _owned_album(conn, first, "The Latest Fashion", 2010)
    _owned_album(conn, second, "This Means War", 2012)
    conn.commit()

    assert repair_duplicate_artists(legacy_db)["artists_merged"] == 0
    assert _alive(conn, first, second) == 2


def test_a_shared_id_with_nothing_else_is_not_a_merge(repair_db):  # noqa: F811
    legacy_db, conn = repair_db
    one = _artist(conn, "Nirvana", external_ids=json.dumps({"deezer": "415"}))
    two = _artist(conn, "Approaching Nirvana", external_ids=json.dumps({"deezer": "415"}))
    conn.commit()

    assert repair_duplicate_artists(legacy_db)["artists_merged"] == 0
    assert _alive(conn, one, two) == 2


def test_the_same_artist_two_ways_still_folds_on_a_shared_release(repair_db):  # noqa: F811
    legacy_db, conn = repair_db
    one = _artist(conn, "Tyler, The Creator", external_ids=json.dumps({"deezer": "77"}))
    two = _artist(conn, "Tyler the Creator & Friends", external_ids=json.dumps({"deezer": "77"}))
    _owned_album(conn, one, "IGOR", 2019)
    _owned_album(conn, two, "IGOR", 2019)
    conn.commit()

    assert repair_duplicate_artists(legacy_db)["artists_merged"] == 1
    assert _alive(conn, one, two) == 1


def test_a_stub_that_agrees_on_two_strong_ids_folds(repair_db):  # noqa: F811
    legacy_db, conn = repair_db
    ids = json.dumps({"deezer": "77"})
    real = _artist(conn, "Sigur Rós", spotify_id="6UUrUCIZtQeOf8tC0WuzRy", external_ids=ids)
    stub = _artist(conn, "Sigur Ros Band", spotify_id="6UUrUCIZtQeOf8tC0WuzRy", external_ids=ids)
    _owned_album(conn, real, "Takk", 2005)
    conn.commit()

    assert repair_duplicate_artists(legacy_db)["artists_merged"] == 1
    assert _alive(conn, real, stub) == 1
