"""#2073: every artist merge is written down and can be undone; a merge made
before the journal can be split by what the albums still say; and two artists
someone said are two are never folded again."""

import json

import pytest

from core.library2 import artist_merges
from core.library2.dedup_repair import repair_duplicate_artists
from tests.library2.test_artist_catalog_merge_2073 import _alive, _owned_album
from tests.library2.test_dedup_repair import _album, _artist, repair_db  # noqa: F401


def _merge_fragment(conn, legacy_db):
    """Odetari and the parser fragment "Odetari w": a merge the repair makes"""
    real = _artist(conn, "Odetari", spotify_id="7ITMCzIU9uII8gwRg8JAhc",
                   external_ids=json.dumps({"deezer": "1"}))
    fragment = _artist(conn, "Odetari w", spotify_id="7ITMCzIU9uII8gwRg8JAhc",
                       external_ids=json.dumps({"tidal": "9"}))
    # no files of its own: two artists that both own files and share no release are left apart
    own = _album(conn, real, "XIII SORROWS")
    theirs = _owned_album(conn, fragment, "I LOVE YOU HOE", 2023)
    track = conn.execute("SELECT id FROM lib2_tracks WHERE album_id=?", (theirs,)).fetchone()[0]
    conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id) VALUES(?,?)", (track, fragment))
    conn.commit()
    repair_duplicate_artists(legacy_db)
    return real, fragment, own, theirs, track


def test_a_merge_is_written_down_with_what_moved(repair_db):  # noqa: F811
    legacy_db, conn = repair_db
    real, fragment, _own, theirs, _track = _merge_fragment(conn, legacy_db)
    survivor = real if _alive(conn, real) else fragment
    merges = artist_merges.list_merges(conn)
    assert len(merges) == 1
    entry = merges[0]
    assert {entry["survivor_id"], entry["duplicate_id"]} == {real, fragment}
    assert entry["survivor_id"] == survivor
    assert entry["albums_moved"] == 1
    assert "spotify" in entry["reason"] and "name fragment" in entry["reason"]


def test_undo_brings_the_artist_back_with_its_albums_credits_and_ids(repair_db):  # noqa: F811
    legacy_db, conn = repair_db
    real, fragment, own, theirs, track = _merge_fragment(conn, legacy_db)
    entry = artist_merges.list_merges(conn)[0]
    survivor, gone = entry["survivor_id"], entry["duplicate_id"]
    moved_album = theirs if gone == fragment else own
    kept_album = own if gone == fragment else theirs

    out = artist_merges.undo(conn, entry["id"])
    conn.commit()

    assert out["artist_id"] == gone and out["albums"] == 1
    assert _alive(conn, real, fragment) == 2
    owner = lambda album: conn.execute(  # noqa: E731
        "SELECT primary_artist_id FROM lib2_albums WHERE id=?", (album,)).fetchone()[0]
    assert owner(moved_album) == gone and owner(kept_album) == survivor
    credits = {tuple(r) for r in conn.execute(
        "SELECT album_id, artist_id FROM lib2_album_artists WHERE album_id IN (?,?)", (own, theirs))}
    assert credits == {(own, real), (theirs, fragment)}
    assert conn.execute("SELECT artist_id FROM lib2_track_artists WHERE track_id=?",
                        (track,)).fetchone()[0] == fragment
    # each has the ids it had before, not the other's
    ids = {row[0]: json.loads(row[1]) for row in conn.execute(
        "SELECT id, external_ids FROM lib2_artists WHERE id IN (?,?)", (real, fragment))}
    assert ids[real] == {"deezer": "1"} and ids[fragment] == {"tidal": "9"}
    assert artist_merges.list_merges(conn) == []
    with pytest.raises(artist_merges.MergeUndoError):
        artist_merges.undo(conn, entry["id"])


def test_an_undone_merge_is_not_made_again(repair_db):  # noqa: F811
    legacy_db, conn = repair_db
    real, fragment, *_ = _merge_fragment(conn, legacy_db)
    artist_merges.undo(conn, artist_merges.list_merges(conn)[0]["id"])
    conn.commit()
    stats = repair_duplicate_artists(legacy_db)
    assert stats["artists_merged"] == 0
    assert _alive(conn, real, fragment) == 2
    assert artist_merges.kept_apart(conn, fragment, real)


def test_two_same_named_rows_kept_apart_stay_two(repair_db):  # noqa: F811
    legacy_db, conn = repair_db
    one = _artist(conn, "Nirvana")
    two = _artist(conn, "Nirvana")
    artist_merges.keep_apart(conn, two, one, "the UK band")
    conn.commit()
    assert repair_duplicate_artists(legacy_db)["artists_merged"] == 0
    assert _alive(conn, one, two) == 2


def test_a_pair_left_apart_is_remembered_for_review(repair_db):  # noqa: F811
    legacy_db, conn = repair_db
    welsh = _artist(conn, "Attack! Attack!", external_ids=json.dumps({"deezer": "150915"}))
    american = _artist(conn, "Attack Attack!", external_ids=json.dumps({"deezer": "150915"}))
    _owned_album(conn, welsh, "The Latest Fashion", 2010)
    _owned_album(conn, american, "This Means War", 2012)
    conn.commit()
    repair_duplicate_artists(legacy_db)
    doubts = artist_merges.doubts(conn)
    assert [(d["low_id"], d["high_id"], d["source"], d["value"]) for d in doubts] == [
        (welsh, american, "deezer", "150915")]
    # settled: it is not asked about again
    artist_merges.keep_apart(conn, welsh, american)
    assert artist_merges.doubts(conn) == []


def test_split_moves_the_named_albums_to_an_artist_of_that_name(repair_db):  # noqa: F811
    """a merge from before the journal: nothing to undo, but the albums say whose they are"""
    legacy_db, conn = repair_db
    welsh = _artist(conn, "Attack! Attack!")
    keep = _owned_album(conn, welsh, "The Latest Fashion", 2010)
    wrong = [_owned_album(conn, welsh, "Someday Came Suddenly", 2008),
             _owned_album(conn, welsh, "This Means War", 2012)]
    track = conn.execute("SELECT id FROM lib2_tracks WHERE album_id=?", (wrong[0],)).fetchone()[0]
    conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id) VALUES(?,?)", (track, welsh))
    conn.commit()

    out = artist_merges.split(conn, welsh, "Attack Attack!", wrong)
    conn.commit()

    assert out["albums"] == 2 and out["created"] is True
    american = out["artist_id"]
    owners = dict(conn.execute("SELECT id, primary_artist_id FROM lib2_albums WHERE id IN (?,?,?)",
                               (keep, *wrong)).fetchall())
    assert owners == {keep: welsh, wrong[0]: american, wrong[1]: american}
    assert {r[0] for r in conn.execute(
        "SELECT artist_id FROM lib2_album_artists WHERE album_id=?", (wrong[0],))} == {american}
    assert conn.execute("SELECT artist_id FROM lib2_track_artists WHERE track_id=?",
                        (track,)).fetchone()[0] == american
    assert artist_merges.kept_apart(conn, welsh, american)
    # and the repair leaves the two of them alone afterwards
    assert repair_duplicate_artists(legacy_db)["artists_merged"] == 0
    with pytest.raises(artist_merges.MergeUndoError):
        artist_merges.split(conn, welsh, "Attack Attack!", wrong)    # already moved
