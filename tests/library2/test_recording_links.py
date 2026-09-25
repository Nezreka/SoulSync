"""§49.6(c) — a file belongs to a recording, not to one album position.

The production DB (23 Aug) holds 21 recordings that sit on more than one
release; on 7 of them the file is attached to one position while every other
position renders as missing. "Vogel Im Kafig" is the reported case: the file
lives under the single, and track 7 of the OST reads as a gap.

These tests pin the resolution and — just as importantly — its guard. Spotify
hands live cuts the ISRC of the studio take (``AM``: "Do I Wanna Know?" and
"Do I Wanna Know? (live from the iTunes Festival)" share ``GBCEL1300362``), so
a shared recording alone must never mean "you have this".
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.editions import backfill_editions
from core.library2.recording_links import reference_owners


def _artist(conn, name="Sawano Hiroyuki"):
    return conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES(?,?)", (name, name)
    ).lastrowid


def _album(conn, artist_id, title, *, album_type="album"):
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, origin,"
        "                        monitored, external_ids)"
        " VALUES(?,?,?,'library',1,'{}')",
        (artist_id, title, album_type),
    ).lastrowid
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id, role)"
        " VALUES(?,?,'primary')", (album_id, artist_id))
    return album_id


def _track(conn, album_id, title, *, track_number=1, isrc=None, duration=None):
    return conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number,"
        "                        isrc, duration, monitored)"
        " VALUES(?,?,?,1,?,?,1)",
        (album_id, title, track_number, isrc, duration),
    ).lastrowid


def _file(conn, track_id, path, *, file_state="active"):
    return conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, import_status, file_state,"
        "                             is_primary)"
        " VALUES(?,?,'imported',?,1)",
        (track_id, path, file_state),
    ).lastrowid


@pytest.fixture
def catalogue(imported_conn):
    """The Sawano shape: one file under the single, a gap on the OST."""
    conn = imported_conn
    artist = _artist(conn)
    ost = _album(conn, artist, 'TV Anime "Attack on Titan" OST')
    single = _album(conn, artist, "Vogel Im Kafig", album_type="single")
    ost_track = _track(conn, ost, "Vogel Im Kafig", track_number=7,
                       isrc="JPPC01301136", duration=180000)
    single_track = _track(conn, single, "Vogel Im Kafig", track_number=1,
                          isrc="JPPC01301136", duration=180000)
    _file(conn, single_track, "Sawano Hiroyuki/Vogel Im Kafig/01-07 - Vogel Im Kafig.flac")
    backfill_editions(conn.cursor(), connection=conn)
    conn.commit()
    return conn, {"ost_track": ost_track, "single_track": single_track,
                  "ost": ost, "single": single}


def test_fileless_position_resolves_to_the_sibling_that_owns_the_file(catalogue):
    conn, ids = catalogue

    owners = reference_owners(conn, [ids["ost_track"]])

    assert set(owners) == {ids["ost_track"]}
    assert owners[ids["ost_track"]]["track_id"] == ids["single_track"]
    assert owners[ids["ost_track"]]["album_id"] == ids["single"]
    assert owners[ids["ost_track"]]["path"].endswith("Vogel Im Kafig.flac")


def test_a_live_cut_sharing_the_studio_isrc_is_not_owned(imported_conn):
    """``AM``: the iTunes-Festival takes carry the studio cut's ISRC."""
    conn = imported_conn
    artist = _artist(conn, "Arctic Monkeys")
    album = _album(conn, artist, "AM")
    studio = _track(conn, album, "Do I Wanna Know?", track_number=1,
                    isrc="GBCEL1300362", duration=272000)
    live = _track(conn, album, "Do I Wanna Know? (live from the iTunes Festival)",
                  track_number=13, isrc="GBCEL1300362", duration=289000)
    _file(conn, studio, "Arctic Monkeys/AM/01-01 - Do I Wanna Know?.flac")
    backfill_editions(conn.cursor(), connection=conn)
    conn.commit()

    assert reference_owners(conn, [live]) == {}


def test_two_positions_that_each_own_a_file_borrow_from_nobody(imported_conn):
    """``Moonlight`` sits on disk twice — under the single and under the album.

    Without this, each position would report the other as its owner and the UI
    would draw a borrowed file on a row that has its own.
    """
    conn = imported_conn
    artist = _artist(conn, "Kali Uchis")
    album = _album(conn, artist, "Red Moon In Venus")
    single = _album(conn, artist, "Moonlight", album_type="single")
    album_track = _track(conn, album, "Moonlight", track_number=14,
                         isrc="USUM72219486", duration=187000)
    single_track = _track(conn, single, "Moonlight", track_number=1,
                          isrc="USUM72219486", duration=187000)
    _file(conn, album_track, "Kali Uchis/Red Moon In Venus/14 - Moonlight.flac")
    _file(conn, single_track, "Kali Uchis/Moonlight/01 - Moonlight.flac")
    backfill_editions(conn.cursor(), connection=conn)
    conn.commit()

    assert reference_owners(conn, [album_track, single_track]) == {}


def test_a_deleted_sibling_file_is_not_something_you_own(imported_conn):
    """Deleting the single's file must reopen the gap on the album, not hide it."""
    conn = imported_conn
    artist = _artist(conn)
    ost = _album(conn, artist, 'TV Anime "Attack on Titan" OST')
    single = _album(conn, artist, "Vogel Im Kafig", album_type="single")
    ost_track = _track(conn, ost, "Vogel Im Kafig", track_number=7,
                       isrc="JPPC01301136")
    single_track = _track(conn, single, "Vogel Im Kafig", track_number=1,
                          isrc="JPPC01301136")
    _file(conn, single_track, "Sawano Hiroyuki/Vogel Im Kafig/01-07 - Vogel Im Kafig.flac",
          file_state="deleted")
    backfill_editions(conn.cursor(), connection=conn)
    conn.commit()

    assert reference_owners(conn, [ost_track]) == {}


def test_album_detail_draws_a_borrowed_file_instead_of_a_gap(catalogue):
    """The OST's track 7 is on disk — under the single. It is not a gap."""
    from core.library2.queries import get_album

    conn, ids = catalogue

    album = get_album(conn, ids["ost"])
    row = next(t for t in album["tracks"] if t.get("id") == ids["ost_track"])

    assert row["file_status"] == "linked"
    assert row["linked_from"]["album_id"] == ids["single"]
    assert row["linked_from"]["album_title"] == "Vogel Im Kafig"
    assert row["linked_from"]["track_id"] == ids["single_track"]
    assert row["file"]["path"].endswith("Vogel Im Kafig.flac")


def _want(conn, track_id, *, profile_id=1):
    conn.execute(
        "INSERT INTO lib2_wanted_tracks(profile_id, track_id, wanted, reason,"
        "                               projection_version)"
        " VALUES(?,?,1,'test',2)", (profile_id, track_id))


def test_a_borrowed_position_is_not_listed_as_missing(catalogue):
    """Wanted Views must not nag for a song that is already on the disk."""
    from core.library2.wanted_views import list_missing

    conn, ids = catalogue
    _want(conn, ids["ost_track"])
    conn.commit()

    rows, total = list_missing(conn)

    assert total == 0
    assert rows == []


def test_the_downloader_does_not_target_a_borrowed_position(catalogue):
    """Decision §49.7(3): a position filled by reference is not a download job."""
    from core.acquisition.wanted_adapter import materialize_wanted_requests

    conn, ids = catalogue
    _want(conn, ids["ost_track"])
    conn.commit()

    assert materialize_wanted_requests(conn) == tuple()


def test_the_artist_page_counts_a_borrowed_position_as_owned(catalogue):
    """§44 LV2-CNT-01's rule: two views must not disagree about one number."""
    from core.library2.queries import get_artist

    conn, ids = catalogue
    artist_id = conn.execute(
        "SELECT primary_artist_id FROM lib2_albums WHERE id=?", (ids["ost"],)
    ).fetchone()[0]

    artist = get_artist(conn, artist_id)
    ost = next(a for a in artist["albums"] if a["id"] == ids["ost"])

    assert ost["tracks_present"] == 1
    assert ost["tracks_missing"] == 0


def test_the_album_position_becomes_the_home_of_a_singles_file(catalogue):
    """User decision: a song that is on an album belongs to the album.

    The file physically sits in the single's folder — it stays there. What
    moves is which position the catalogue calls its home, so quality checks,
    upgrades and deletion all act from the album the song really belongs to.
    """
    from core.library2.recording_links import prefer_album_home

    conn, ids = catalogue

    stats = prefer_album_home(conn)
    conn.commit()

    assert stats["rehomed"] == 1
    owner = conn.execute(
        "SELECT track_id FROM lib2_track_files WHERE path LIKE '%Vogel Im Kafig.flac'"
    ).fetchone()[0]
    assert owner == ids["ost_track"]
    # …and the single now borrows from the album, not the other way round.
    from core.library2.recording_links import reference_owners
    owners = reference_owners(conn, [ids["single_track"], ids["ost_track"]])
    assert owners[ids["single_track"]]["album_id"] == ids["ost"]
    assert ids["ost_track"] not in owners


def test_rehome_moves_all_versions_and_elects_the_best_primary(catalogue):
    """Re-homing a recording must not make whichever row moved last primary."""
    from core.library2.recording_links import prefer_album_home

    conn, ids = catalogue
    master_id = conn.execute(
        "SELECT id FROM lib2_track_files WHERE track_id=?",
        (ids["single_track"],),
    ).fetchone()[0]
    conn.execute(
        "UPDATE lib2_track_files SET format='flac', bit_depth=24,"
        " sample_rate=96000, bitrate=3000 WHERE id=?", (master_id,),
    )
    derivative_id = conn.execute(
        "INSERT INTO lib2_track_files(track_id,path,format,bitrate,file_role,"
        " derived_from_file_id,import_status,file_state)"
        " VALUES(?,?,'opus',192,'derivative',?,'imported','active')",
        (ids["single_track"],
         "Sawano Hiroyuki/Vogel Im Kafig/01-07 - Vogel Im Kafig.opus",
         master_id),
    ).lastrowid

    stats = prefer_album_home(conn)

    assert stats["rehomed"] == 2
    rows = conn.execute(
        "SELECT id,track_id,is_primary FROM lib2_track_files"
        " WHERE id IN (?,?) ORDER BY id", (master_id, derivative_id),
    ).fetchall()
    assert {(row[0], row[1]) for row in rows} == {
        (master_id, ids["ost_track"]),
        (derivative_id, ids["ost_track"]),
    }
    assert [row[0] for row in rows if row[2] == 1] == [master_id]


def test_two_real_copies_are_a_duplicate_finding_not_a_rehome(imported_conn):
    """``Moonlight`` is on disk twice. Re-pointing one at the other's position
    would silently orphan a file the user still has."""
    from core.library2.recording_links import prefer_album_home

    conn = imported_conn
    artist = _artist(conn, "Kali Uchis")
    album = _album(conn, artist, "Red Moon In Venus")
    single = _album(conn, artist, "Moonlight", album_type="single")
    album_track = _track(conn, album, "Moonlight", track_number=14, isrc="USUM72219486")
    single_track = _track(conn, single, "Moonlight", track_number=1, isrc="USUM72219486")
    _file(conn, album_track, "Kali Uchis/Red Moon In Venus/14 - Moonlight.flac")
    _file(conn, single_track, "Kali Uchis/Moonlight/01 - Moonlight.flac")
    backfill_editions(conn.cursor(), connection=conn)
    conn.commit()

    assert prefer_album_home(conn)["rehomed"] == 0
    owner = conn.execute(
        "SELECT track_id FROM lib2_track_files WHERE path LIKE '%Moonlight/01 - Moonlight.flac'"
    ).fetchone()[0]
    assert owner == single_track


def test_a_file_already_on_the_album_never_moves_down_to_the_single(imported_conn):
    """The preference has a direction. Without one, the pass would ping-pong."""
    from core.library2.recording_links import prefer_album_home

    conn = imported_conn
    artist = _artist(conn, "The Weeknd")
    album = _album(conn, artist, "Starboy")
    single = _album(conn, artist, "Starboy", album_type="single")
    album_track = _track(conn, album, "Starboy", track_number=1, isrc="USUG11600970")
    _track(conn, single, "Starboy", track_number=1, isrc="USUG11600970")
    _file(conn, album_track, "The Weeknd/Starboy/01 - Starboy.flac")
    backfill_editions(conn.cursor(), connection=conn)
    conn.commit()

    assert prefer_album_home(conn)["rehomed"] == 0
    owner = conn.execute(
        "SELECT track_id FROM lib2_track_files WHERE path LIKE '%Starboy.flac'"
    ).fetchone()[0]
    assert owner == album_track


def test_startup_convergence_moves_the_home_without_being_asked(catalogue):
    """The pass must actually run, or the model converges only in tests."""
    from core.library2.schema import run_library_v2_backfills

    conn, ids = catalogue

    stats = run_library_v2_backfills(conn, commit=True)

    assert stats["recording_home"]["rehomed"] == 1
    owner = conn.execute(
        "SELECT track_id FROM lib2_track_files WHERE path LIKE '%Vogel Im Kafig.flac'"
    ).fetchone()[0]
    assert owner == ids["ost_track"]


def test_a_recording_learns_the_id_its_track_gained_later(imported_conn):
    """``Call of Silence``: the OST row got its Spotify id AFTER its release
    track existed, and ``ensure_release_track`` never revisits one — so the
    recording stayed identifier-less and could never merge with the single's.
    94 recordings in the 23 Aug production DB are in that state."""
    from core.library2.editions import reconcile_recording_identity

    conn = imported_conn
    artist = _artist(conn)
    ost = _album(conn, artist, 'TV Anime "Attack on Titan Season 2" OST')
    single = _album(conn, artist, "Call of Silence", album_type="single")
    ost_track = _track(conn, ost, "Call of Silence", track_number=5)
    single_track = _track(conn, single, "Call of Silence", track_number=1,
                          isrc="JPPC01700991")
    backfill_editions(conn.cursor(), connection=conn)
    # The tracklist fetch arrives afterwards and gives the OST row its id.
    conn.execute("UPDATE lib2_tracks SET spotify_id=? WHERE id=?",
                 ("7k1HoUdskuBhyWvm7hPctM", ost_track))
    conn.execute("UPDATE lib2_tracks SET spotify_id=? WHERE id=?",
                 ("7k1HoUdskuBhyWvm7hPctM", single_track))
    conn.execute("UPDATE lib2_recordings SET spotify_id=? "
                 "WHERE id=(SELECT recording_id FROM lib2_release_tracks "
                 "           WHERE track_id=?)",
                 ("7k1HoUdskuBhyWvm7hPctM", single_track))
    conn.commit()

    stats = reconcile_recording_identity(conn.cursor())
    conn.commit()

    assert stats["merged"] == 1
    recordings = {
        r[0] for r in conn.execute(
            "SELECT recording_id FROM lib2_release_tracks WHERE track_id IN (?,?)",
            (ost_track, single_track))
    }
    assert len(recordings) == 1


def test_the_identity_reconcile_runs_with_the_edition_backfill(imported_conn):
    """It has to converge on its own — 94 stale recordings will not fix
    themselves on a screen nobody opens."""
    conn = imported_conn
    artist = _artist(conn)
    ost = _album(conn, artist, 'TV Anime "Attack on Titan Season 2" OST')
    single = _album(conn, artist, "Call of Silence", album_type="single")
    ost_track = _track(conn, ost, "Call of Silence", track_number=5)
    single_track = _track(conn, single, "Call of Silence", track_number=1,
                          isrc="JPPC01700991", duration=178000)
    backfill_editions(conn.cursor(), connection=conn)
    conn.execute("UPDATE lib2_tracks SET spotify_id='7k1HoUdskuBhyWvm7hPctM'"
                 " WHERE id IN (?,?)", (ost_track, single_track))
    conn.execute("UPDATE lib2_recordings SET spotify_id='7k1HoUdskuBhyWvm7hPctM'"
                 " WHERE id=(SELECT recording_id FROM lib2_release_tracks"
                 "            WHERE track_id=?)", (single_track,))
    conn.commit()

    stats = backfill_editions(conn.cursor(), connection=conn)

    assert stats["identity"]["merged"] == 1
