"""SeadogsBooty, Nov 5: wanted tracks downloaded into a yearless folder.

the Library v2 wishlist mirror handed the download an album with no release
date, so the [$year] in the folder template came out empty and the track
landed beside the album's [year] folder instead of in it.
"""

from __future__ import annotations

import json

from core.library2.release_dates import album_release_date
from core.library2.wishlist_mirror import track_wishlist_payload


def test_the_date_follows_the_library_year():
    assert album_release_date("2017-08-25", 2017) == "2017-08-25"
    # no full date: the year
    assert album_release_date(None, 2017) == "2017"
    assert album_release_date("", "2017") == "2017"
    # another edition's date: the year the library shows wins
    assert album_release_date("2021-03-05", 2017) == "2017"
    # no year: the date stands
    assert album_release_date("2017-08-25", None) == "2017-08-25"
    assert album_release_date(None, None) == ""
    assert album_release_date("garbage", 0) == ""


def _views(conn):
    album_id = conn.execute("SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0]
    track_id = conn.execute(
        "SELECT id FROM lib2_tracks WHERE album_id=? AND title='One Dance'", (album_id,)
    ).fetchone()[0]
    return album_id, track_id


def test_the_mirrored_wishlist_row_carries_the_albums_date(imported_conn):
    album_id, track_id = _views(imported_conn)
    imported_conn.execute(
        "UPDATE lib2_albums SET release_date=NULL, year=2016 WHERE id=?", (album_id,))
    payload = track_wishlist_payload(imported_conn, track_id)
    assert payload["album"]["release_date"] == "2016"


def test_queued_rows_get_their_albums_date_once(tmp_path):
    from database.music_database import MusicDatabase
    from tests import lib2_seed

    db = MusicDatabase(str(tmp_path / "m.db"))
    conn = lib2_seed.row_conn(str(tmp_path / "m.db"))
    album_id = lib2_seed.album(conn, "Priscilla Block", "Different Route", year=2017)
    rows = [
        # the mirror's shape: no date, its library album in _source_info
        {"name": "Thick Thighs", "album": {"name": "Different Route"},
         "_source_info": {"source": "library_v2", "lib2_album_id": album_id}},
        # a provider row with its own date is left alone
        {"name": "Other", "album": {"name": "X", "release_date": "2020-01-01"}},
    ]
    for i, data in enumerate(rows):
        conn.execute(
            "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, profile_id) "
            "VALUES (?, ?, 1)", (f"t{i}", json.dumps(data)))
    conn.execute("DROP TABLE IF EXISTS _wishlist_lib2_album_dates")
    conn.commit()
    db._wishlist_lib2_album_dates(conn.cursor())
    conn.commit()
    got = [json.loads(r[0])["album"].get("release_date")
           for r in conn.execute("SELECT spotify_data FROM wishlist_tracks ORDER BY id")]
    conn.close()
    assert got == ["2017", "2020-01-01"]


def test_the_mirrored_album_names_its_own_artists(imported_conn):
    """the download picks the album folder (and spots a various artists
    release) from the album's artists. the mirror sent none, so a wanted
    track was filed under the track's own artist: a split single's B side
    under its singer, a compilation track under its performer."""
    from tests import lib2_seed

    lead = lib2_seed.artist(imported_conn, "3OH!3")
    other = lib2_seed.artist(imported_conn, "Innerpartysystem")
    guest = lib2_seed.artist(imported_conn, "A Guest")
    album_id = lib2_seed.album(imported_conn, "3OH!3", "3OH!3 versus Innerpartysystem")
    imported_conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES (?, ?, 'primary')",
        (album_id, other))
    imported_conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES (?, ?, 'featured')",
        (album_id, guest))
    track_id = lib2_seed.track(imported_conn, "3OH!3", "3OH!3 versus Innerpartysystem",
                               "Don't Stop (3OH!3 remix)", owned=False)
    imported_conn.execute(
        "INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
        "VALUES (?, ?, 'primary', 0)", (track_id, other))
    assert lead

    payload = track_wishlist_payload(imported_conn, track_id)

    assert payload["album"]["artists"] == [{"name": "3OH!3"}, {"name": "Innerpartysystem"}]
    # the track still credits who recorded it
    assert payload["artists"][0]["name"] == "Innerpartysystem"


def test_the_mirrored_row_carries_the_explicit_flags(imported_conn):
    """$explicit / $contentrating in the naming template read these (#1860)."""
    album_id, track_id = _views(imported_conn)
    imported_conn.execute("UPDATE lib2_tracks SET explicit=1 WHERE id=?", (track_id,))
    imported_conn.execute("UPDATE lib2_albums SET explicit=0 WHERE id=?", (album_id,))
    payload = track_wishlist_payload(imported_conn, track_id)
    assert payload["explicit"] is True
    assert payload["album"]["explicit"] is False


def test_the_mirrored_album_carries_its_release_identity(imported_conn):
    """the folder lookup checks the musicbrainz release id and #1299 puts the
    edition's disambiguation on the folder, so two same-named releases stay
    apart. the mirror sent neither where the pipeline reads them."""
    album_id, track_id = _views(imported_conn)
    release = "f6b47f3a-53a9-4d09-9c87-18a2b63d8b48"
    imported_conn.execute("UPDATE lib2_albums SET musicbrainz_id=? WHERE id=?", (release, album_id))
    imported_conn.execute("DELETE FROM lib2_release_editions WHERE release_group_id=?", (album_id,))
    imported_conn.execute(
        "INSERT INTO lib2_release_editions(release_group_id, is_default, title, disambiguation, disc_count) "
        "VALUES (?, 1, 'Views', 'deluxe', 2)", (album_id,))
    album = track_wishlist_payload(imported_conn, track_id)["album"]
    assert album["musicbrainz_release_id"] == release
    assert album["disambiguation"] == "deluxe"
    assert album["total_discs"] == 2


def test_the_mirrored_track_carries_its_recording_identity(imported_conn):
    """#1536 puts the recording comment on a single's filename; the match
    checks read the recording id and isrc."""
    _album_id, track_id = _views(imported_conn)
    imported_conn.execute(
        "UPDATE lib2_tracks SET musicbrainz_id='rec-1', isrc='USUM71601014', "
        "recording_disambiguation='acoustic' WHERE id=?", (track_id,))
    payload = track_wishlist_payload(imported_conn, track_id)
    assert payload["disambiguation"] == "acoustic"
    assert payload["musicbrainz_recording_id"] == "rec-1"
    assert payload["isrc"] == "USUM71601014"
