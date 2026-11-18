"""#2178: Other versions, when one album sits on two rows.

wolf39us had Nelly's "Country Grammar" split over two library albums: one
called Country Grammar, the other matched to the 5 track "(Hot S**t) Country
Grammar" single, filed as an EP. picking the same 17 track edition on both
never merged them. the switch moved the release id from one row to the other,
and the row it landed on kept the EP's title, type and date, so the picker
marked "Country Grammar" current on a row that still read "(Hot S**t)".

here Drake's Views stands in: the seeded "Views" row plus a second row,
"(Hot) Views", holding the rest of the files.
"""

import json

import pytest

from core.library2 import album_versions, locks
from tests.library2.test_api_routes import api  # noqa: F401 - fixture

FULL = {"title": "Views", "date": "2016-04-29", "album_type": "album", "track_count": 20}


@pytest.fixture(autouse=True)
def _fresh():
    album_versions.clear_cache()
    locks.reset_for_tests()
    yield
    album_versions.clear_cache()
    locks.reset_for_tests()


def _views(conn):
    return conn.execute("SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0]


def _split_copy(conn, *, spotify_id="sp-ep", album_type="ep", title="(Hot) Views"):
    """the second row: the same album's files, matched to a short release"""
    views = conn.execute("SELECT * FROM lib2_albums WHERE title='Views'").fetchone()
    cur = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, album_type_known, release_date,"
        " year, spotify_id, external_ids, filed_release, expected_track_count, track_count)"
        " VALUES(?,?,?,1,'2016-02-29',2016,?,?,?,18,3)",
        (views["primary_artist_id"], title, album_type, spotify_id,
         json.dumps({"spotify": spotify_id}) if spotify_id else "{}",
         json.dumps({"type": album_type, "total_tracks": 5, "source": "spotify", "locked": True})))
    other = int(cur.lastrowid)
    conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?,?,'primary')",
                 (other, views["primary_artist_id"]))
    for number, name in ((1, "One Dance"), (2, "Hotline Bling"), (3, "Bonus Cut")):
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, duration, play_count)"
            " VALUES(?,?,?,1,200000,2)", (other, name, number)).lastrowid
        conn.execute("INSERT INTO lib2_track_files(track_id, path, is_primary, format)"
                     " VALUES(?,?,1,'mp3')", (track, f"/m/hot/{number:02d}.mp3"))
    conn.commit()
    return other


def _album(conn, album_id):
    row = conn.execute("SELECT * FROM lib2_albums WHERE id=?", (album_id,)).fetchone()
    return dict(row) if row else None


def _tracks(conn, album_id):
    return {r["title"]: sorted(p[0] for p in conn.execute(
                "SELECT path FROM lib2_track_files WHERE track_id=?", (r["id"],)))
            for r in conn.execute("SELECT id, title FROM lib2_tracks WHERE album_id=?", (album_id,))}


def test_the_reports_two_titles_are_one_album():
    assert album_versions.same_title("Country Grammar", "(Hot S**t) Country Grammar")
    assert not album_versions.same_title("Country Grammar", "Nellyville")


def test_switching_puts_the_editions_title_type_date_and_count_on_the_row(imported_conn, legacy_db):
    """#2178: the row agrees with the version the picker marks current."""
    other = _split_copy(imported_conn)
    out = album_versions.apply_version(legacy_db, other, "spotify", "sp-full", actor="profile:1",
                                       version=FULL)
    assert out["success"], out
    imported_conn.rollback()
    row = _album(imported_conn, other)
    assert (row["title"], row["album_type"], row["release_date"], row["year"]) == (
        "Views", "album", "2016-04-29", 2016)
    assert row["expected_track_count"] == 20  # was 18
    assert (row["spotify_id"], row["canonical_album_id"]) == ("sp-full", "sp-full")
    # a reorganize no longer files it back under EP/
    assert json.loads(row["filed_release"]) == {"type": "album", "total_tracks": 20,
                                                "source": "spotify", "locked": False}
    facts = album_versions.album_facts(imported_conn, other)
    assert facts["release_ids"]["spotify"] == "sp-full" and facts["title"] == "Views"


def test_spotifys_single_is_split_by_track_count_like_the_download_filing(imported_conn, legacy_db):
    other = _split_copy(imported_conn, album_type="album")
    album_versions.apply_version(legacy_db, other, "spotify", "sp-5", actor="profile:1",
                                 version={"title": "(Hot) Views", "album_type": "single",
                                          "track_count": 5})
    imported_conn.rollback()
    assert _album(imported_conn, other)["album_type"] == "ep"


def test_a_type_set_by_hand_and_a_compilation_keep_their_type(imported_conn, legacy_db):
    other = _split_copy(imported_conn)
    imported_conn.execute(
        "INSERT INTO lib2_metadata_overrides(entity_type, entity_id, field_name, value_json)"
        " VALUES('release_group', ?, 'album_type', '\"ep\"')", (other,))
    imported_conn.commit()
    album_versions.apply_version(legacy_db, other, "spotify", "sp-full", actor="profile:1", version=FULL)
    imported_conn.rollback()
    row = _album(imported_conn, other)
    assert row["album_type"] == "ep" and json.loads(row["filed_release"])["type"] == "ep"
    assert row["title"] == "Views"

    comp = _split_copy(imported_conn, spotify_id=None, album_type="compilation", title="Hits")
    album_versions.apply_version(legacy_db, comp, "spotify", "sp-hits", actor="profile:1",
                                 version={"title": "Hits", "track_count": 17})
    imported_conn.rollback()
    assert _album(imported_conn, comp)["album_type"] == "compilation"


def test_preview_offers_to_merge_the_album_that_already_is_the_edition(imported_conn, legacy_db):
    """#2178: picking the edition another album of the artist holds offers a
    merge, not only "switching moves it here"."""
    views = _views(imported_conn)
    imported_conn.execute("UPDATE lib2_albums SET spotify_id='sp-views' WHERE id=?", (views,))
    imported_conn.commit()
    other = _split_copy(imported_conn)
    out = album_versions.preview_version(legacy_db, other, "spotify", "sp-views", title="Views",
                                         tracklist=lambda *_a: [{"id": "t", "name": "One Dance",
                                                                 "track_number": 1}])
    assert out["success"] and out["held_by"]["id"] == views
    assert [(c["id"], c["holds"], c["owned_count"], c["locked"]) for c in out["merge_with"]] == [
        (views, True, 1, False)]


def test_the_current_version_offers_the_same_title_album_that_lost_its_match(imported_conn, legacy_db):
    """the user's state today: the EP row took the id, the album row has none."""
    views = _views(imported_conn)
    other = _split_copy(imported_conn, spotify_id="sp-views")
    out = album_versions.preview_version(legacy_db, other, "spotify", "sp-views", title="Views",
                                         tracklist=lambda *_a: [{"id": "t", "name": "x"}])
    assert out["success"] and out["already_current"]
    assert [(c["id"], c["holds"]) for c in out["merge_with"]] == [(views, False)]


def test_an_album_holding_another_edition_is_not_offered(imported_conn, legacy_db):
    """deluxe next to standard, kept apart on purpose, stays apart."""
    views = _views(imported_conn)
    imported_conn.execute("UPDATE lib2_albums SET spotify_id='sp-views' WHERE id=?", (views,))
    imported_conn.commit()
    deluxe = _split_copy(imported_conn, spotify_id="sp-deluxe", title="Views (Deluxe)")
    out = album_versions.preview_version(legacy_db, deluxe, "spotify", "sp-deluxe-2",
                                         title="Views (Deluxe)",
                                         tracklist=lambda *_a: [{"id": "t", "name": "x"}])
    assert out["merge_with"] == []
    refused = album_versions.apply_version(legacy_db, deluxe, "spotify", "sp-deluxe-2",
                                           actor="profile:1", version=FULL, merge_album_id=views)
    assert not refused["success"]
    imported_conn.rollback()
    assert _album(imported_conn, deluxe) and _album(imported_conn, views)["spotify_id"] == "sp-views"


def test_merging_folds_the_split_album_into_the_one_that_is_the_edition(imported_conn, legacy_db):
    """#2178: one album, every file, the shared songs as one track each."""
    views = _views(imported_conn)
    imported_conn.execute("UPDATE lib2_albums SET spotify_id='sp-views' WHERE id=?", (views,))
    imported_conn.commit()
    other = _split_copy(imported_conn)
    out = album_versions.apply_version(legacy_db, other, "spotify", "sp-views", actor="profile:1",
                                       version=FULL, merge_album_id=views)
    assert out["success"], out
    assert (out["album_id"], out["merged_album_id"]) == (views, other)
    assert (out["tracks_folded"], out["tracks_moved"]) == (2, 1)
    imported_conn.rollback()

    assert _album(imported_conn, other) is None
    kept = _album(imported_conn, views)
    assert (kept["title"], kept["album_type"], kept["track_count"]) == ("Views", "album", 3)
    assert (kept["spotify_id"], kept["canonical_album_id"], kept["canonical_locked"]) == (
        "sp-views", "sp-views", 1)
    tracks = _tracks(imported_conn, views)
    assert tracks == {"One Dance": ["/m/01.flac", "/m/hot/01.mp3"],
                      "Hotline Bling": ["/m/hot/02.mp3"],   # the gap took the file
                      "Bonus Cut": ["/m/hot/03.mp3"]}
    # one primary per track, and the flac stays it
    for track_id, primaries, best in imported_conn.execute(
            "SELECT t.id, SUM(f.is_primary), MAX(CASE WHEN f.is_primary=1 THEN f.path END)"
            " FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id=t.id"
            " WHERE t.album_id=? GROUP BY t.id", (views,)):
        assert primaries == 1
    one_dance = imported_conn.execute(
        "SELECT f.path FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id=t.id"
        " WHERE t.album_id=? AND t.title='One Dance' AND f.is_primary=1", (views,)).fetchone()[0]
    assert one_dance == "/m/01.flac"
    # the EP's release stays on record as an edition, never a second default
    editions = imported_conn.execute(
        "SELECT spotify_id, is_default FROM lib2_release_editions WHERE release_group_id=?",
        (views,)).fetchall()
    assert ("sp-ep", 0) in [tuple(e) for e in editions]
    assert sum(e[1] for e in editions) == 1
    assert imported_conn.execute("SELECT COUNT(*) FROM lib2_albums WHERE spotify_id='sp-views'"
                                 ).fetchone()[0] == 1
    assert imported_conn.execute("SELECT target_id FROM lib2_catalogue_redirects"
                                 " WHERE entity_type='album' AND old_id=?", (other,)
                                 ).fetchone()[0] == views


def test_merging_from_the_current_row_keeps_that_row(imported_conn, legacy_db):
    views = _views(imported_conn)
    other = _split_copy(imported_conn, spotify_id="sp-views")
    out = album_versions.apply_version(legacy_db, other, "spotify", "sp-views", actor="profile:1",
                                       version=FULL, merge_album_id=views)
    assert out["success"] and (out["album_id"], out["merged_album_id"]) == (other, views)
    imported_conn.rollback()
    assert _album(imported_conn, views) is None
    row = _album(imported_conn, other)
    assert (row["title"], row["album_type"]) == ("Views", "album")
    assert set(_tracks(imported_conn, other)) == {"One Dance", "Hotline Bling", "Bonus Cut"}


def test_a_locked_album_is_not_merged_away(imported_conn, legacy_db):
    views = _views(imported_conn)
    imported_conn.execute("UPDATE lib2_albums SET spotify_id='sp-views' WHERE id=?", (views,))
    other = _split_copy(imported_conn, spotify_id=None)
    locks.set_lock(imported_conn, "album", other, ["match"])
    imported_conn.commit()
    locks.invalidate()
    # from the holder's page the locked row would be the one merged away
    out = album_versions.apply_version(legacy_db, views, "spotify", "sp-views", actor="profile:1",
                                       version=FULL, merge_album_id=other)
    assert not out["success"] and "locked" in out["error"]
    imported_conn.rollback()
    assert _album(imported_conn, other) is not None


def test_routes_preview_and_apply_a_merge(api, monkeypatch):  # noqa: F811
    client, db, ids = api
    monkeypatch.setattr(album_versions, "fetch_release_facts", lambda *_a: pytest.fail("version sent"))
    monkeypatch.setattr("core.imports.reassign_service.target_tracks",
                        lambda *_a: [{"id": "t1", "name": "One Dance", "track_number": 1}])
    monkeypatch.setattr("core.library2.provider_credits.schedule_match_credits",
                        lambda *a, **k: None)
    conn = db._get_connection()
    other = _split_copy(conn, spotify_id="sp-views")
    conn.close()
    body = {"source": "spotify", "release_id": "sp-views", "title": "Views"}
    preview = client.post(f"/api/library/v2/albums/{other}/versions/preview", json=body).get_json()
    assert preview["already_current"] and preview["merge_with"][0]["id"] == ids["views"]
    applied = client.post(f"/api/library/v2/albums/{other}/versions/apply",
                          json={**body, "version": FULL, "merge_album_id": ids["views"]})
    assert applied.status_code == 200, applied.get_json()
    assert applied.get_json()["album_id"] == other
    check = db._get_connection()
    try:
        titles = {r[0]: r[1] for r in check.execute("SELECT id, title FROM lib2_albums")}
    finally:
        check.close()
    assert ids["views"] not in titles and titles[other] == "Views"
