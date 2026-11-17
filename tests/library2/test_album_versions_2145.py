"""#2145: an album's other versions, and switching the album to one of them.

picard's "other versions" menu on the album page: the musicbrainz release
group's editions plus the primary source's same-title releases, with track
counts, and picking one re-points the album through the manual album match.
"""

import json

import pytest

from core.library2 import album_versions
from tests.library2.test_api_routes import api  # noqa: F401 - fixture


@pytest.fixture(autouse=True)
def _fresh_cache():
    album_versions.clear_cache()
    yield
    album_versions.clear_cache()


def _views(conn, **columns):
    album = conn.execute("SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0]
    sets = ", ".join(f"{k}=?" for k in columns)
    if sets:
        conn.execute(f"UPDATE lib2_albums SET {sets} WHERE id=?", (*columns.values(), album))
        conn.commit()
    return album


EDITIONS = [
    {"id": "mb-us", "name": "Views", "release_date": "2016-04-29", "total_tracks": 20,
     "label": "Young Money", "country": "US", "format": "CD"},
    {"id": "mb-xw", "name": "Views", "release_date": "2016-04-29", "total_tracks": 20,
     "country": "XW", "format": "Digital Media"},
    {"id": "mb-34", "name": "Views", "release_date": "2017-01-01", "total_tracks": 34,
     "disambiguation": "deluxe"},
]

SPOTIFY = [
    {"id": "sp-views", "name": "Views", "release_date": "2016-04-29", "total_tracks": 20},
    {"id": "sp-deluxe", "name": "Views (Deluxe)", "release_date": "2016-05-01", "total_tracks": 22},
    {"id": "sp-other", "name": "Scorpion", "release_date": "2018-06-29", "total_tracks": 25},
]


def test_lists_release_group_editions_and_same_title_releases(imported_conn, legacy_db):
    """#2145: every edition shows with its track count, the matched one marked."""
    album = _views(imported_conn, musicbrainz_release_group_id="rg-views",
                   musicbrainz_id="mb-xw", spotify_id="sp-views")
    calls = []

    def editions(rg, artist):
        calls.append(("mb", rg, artist))
        return EDITIONS

    def artist_albums(source, artist_id, artist_name):
        calls.append((source, artist_id, artist_name))
        return SPOTIFY

    out = album_versions.list_versions(legacy_db, album, source="spotify", editions=editions,
                                       artist_albums=artist_albums, group_of=lambda _r: None)
    spotify, mb = out["groups"]
    assert spotify["source"] == "spotify"
    assert [v["id"] for v in spotify["versions"]] == ["sp-views", "sp-deluxe"]  # not Scorpion
    assert spotify["versions"][0]["current"] is True
    assert [v["track_count"] for v in mb["versions"]] == [20, 20, 34]
    assert mb["versions"][0]["id"] == "mb-xw" and mb["versions"][0]["current"]
    us = next(v for v in mb["versions"] if v["id"] == "mb-us")
    assert (us["label"], us["country"], us["format"], us["year"]) == ("Young Money", "US", "CD", 2016)
    assert ("spotify", "sp1", "Drake") in calls

    # reopening the picker costs nothing
    album_versions.list_versions(legacy_db, album, source="spotify", editions=editions,
                                 artist_albums=artist_albums, group_of=lambda _r: None)
    assert len(calls) == 2


def test_release_group_comes_from_the_release_when_unknown(imported_conn, legacy_db):
    album = _views(imported_conn, musicbrainz_id="mb-us")
    out = album_versions.list_versions(
        legacy_db, album, source="musicbrainz", group_of=lambda rel: "rg-" + rel,
        editions=lambda rg, _a: EDITIONS if rg == "rg-mb-us" else [],
        artist_albums=lambda *_a: pytest.fail("musicbrainz is listed once"))
    assert [g["source"] for g in out["groups"]] == ["musicbrainz"]
    assert len(out["groups"][0]["versions"]) == 3


def test_a_source_that_fails_does_not_sink_the_picker(imported_conn, legacy_db):
    album = _views(imported_conn, musicbrainz_release_group_id="rg-views")

    def boom(*_a):
        raise RuntimeError("down")

    out = album_versions.list_versions(legacy_db, album, source="spotify", editions=lambda *_a: EDITIONS,
                                       artist_albums=boom, group_of=lambda _r: None)
    spotify, mb = out["groups"]
    assert spotify["error"] and spotify["versions"] == []
    assert len(mb["versions"]) == 3


def test_search_keeps_to_the_albums_artist(imported_conn, legacy_db):
    album = _views(imported_conn)
    seen = []

    def search(source, query):
        seen.append((source, query))
        return [{"id": "a", "name": "Views (34 track)", "artists": ["Drake"], "total_tracks": 34},
                {"id": "b", "name": "Views", "artists": [{"name": "Drake Bell"}]},
                {"id": "c", "name": "Views", "artists": ["Not Drake At All"]}]

    out = album_versions.search_versions(legacy_db, album, "34", source="spotify", search=search)
    assert [v["id"] for g in out["groups"] for v in g["versions"]] == ["a", "a"]
    assert seen == [("spotify", "Drake 34"), ("musicbrainz", "Drake - 34")]


def test_same_title_ignores_edition_words_and_keeps_non_latin():
    assert album_versions.same_title("Good Time", "Good Time (Deluxe Edition)")
    assert album_versions.same_title("Good Time", "Good Time - Remastered")
    assert not album_versions.same_title("Good Time", "Good Times")
    assert album_versions.same_title("夜に駆ける", "夜に駆ける (Deluxe)")
    assert not album_versions.same_title("夜に駆ける", "群青")


def _tracklist(_source, release_id):
    base = [{"id": "t1", "name": "One Dance", "track_number": 1, "disc_number": 1},
            {"id": "t2", "name": "Hotline Bling", "track_number": 2, "disc_number": 1}]
    if release_id == "sp-deluxe":
        base.append({"id": "t3", "name": "Bonus", "track_number": 3, "disc_number": 1})
    return base


def test_preview_says_what_changes_before_anything_is_written(imported_conn, legacy_db):
    """#2145: track count before and after, and how the files line up."""
    album = _views(imported_conn, spotify_id="sp-views", expected_track_count=34)
    out = album_versions.preview_version(legacy_db, album, "spotify", "sp-deluxe", tracklist=_tracklist)
    assert out["success"]
    assert (out["track_count_before"], out["track_count_after"]) == (34, 3)
    assert out["owned_count"] >= 1 and out["mapped_count"] == out["owned_count"]
    assert out["held_by"] is None
    row = imported_conn.execute("SELECT spotify_id, canonical_album_id FROM lib2_albums WHERE id=?",
                                (album,)).fetchone()
    assert tuple(row) == ("sp-views", None)


def test_preview_names_the_album_that_already_holds_the_version(imported_conn, legacy_db):
    album = _views(imported_conn)
    other = imported_conn.execute("SELECT id FROM lib2_albums WHERE title='One Dance'").fetchone()[0]
    imported_conn.execute("UPDATE lib2_albums SET spotify_id='sp-deluxe' WHERE id=?", (other,))
    imported_conn.commit()
    out = album_versions.preview_version(legacy_db, album, "spotify", "sp-deluxe", tracklist=_tracklist)
    assert out["held_by"] == {"id": other, "title": "One Dance"}


def test_preview_refuses_the_version_already_in_place_and_unpinnable_sources(imported_conn, legacy_db):
    album = _views(imported_conn, spotify_id="sp-views")
    same = album_versions.preview_version(legacy_db, album, "spotify", "sp-views", tracklist=_tracklist)
    assert not same["success"] and same["already_current"]
    lastfm = album_versions.preview_version(legacy_db, album, "lastfm", "x", tracklist=_tracklist)
    assert not lastfm["success"]


def test_switching_pins_the_picked_version(imported_conn, legacy_db):
    """#2145: the album points at the picked edition and stays there."""
    album = _views(imported_conn, spotify_id="sp-views", tracklist_json=json.dumps([{"title": "x"}] * 34),
                   tracklist_status="ready")
    out = album_versions.apply_version(legacy_db, album, "spotify", "sp-deluxe", actor="profile:1")
    assert out["success"]
    imported_conn.rollback()
    row = imported_conn.execute(
        "SELECT spotify_id, canonical_source, canonical_album_id, canonical_locked, tracklist_json"
        " FROM lib2_albums WHERE id=?", (album,)).fetchone()
    assert (row[0], row[1], row[2], row[3]) == ("sp-deluxe", "spotify", "sp-deluxe", 1)
    assert row[4] is None  # the old 34 track list goes, the picked one is fetched
    listed = album_versions.list_versions(legacy_db, album, source="spotify",
                                          editions=lambda *_a: [],
                                          artist_albums=lambda *_a: SPOTIFY, group_of=lambda _r: None)
    current = [v["id"] for v in listed["groups"][0]["versions"] if v["current"]]
    assert current == ["sp-deluxe"]


def test_routes_list_preview_and_apply(api, monkeypatch):  # noqa: F811
    client, _db, ids = api
    monkeypatch.setattr(album_versions, "primary_source", lambda: "spotify")
    monkeypatch.setattr(album_versions, "fetch_artist_albums", lambda *_a: SPOTIFY)
    monkeypatch.setattr(album_versions, "fetch_release_group_id", lambda _r: None)
    monkeypatch.setattr("core.imports.reassign_service.target_tracks", _tracklist)
    monkeypatch.setattr("core.library2.provider_credits.schedule_match_credits", lambda *a, **k: None)

    listed = client.get(f"/api/library/v2/albums/{ids['views']}/versions").get_json()
    assert listed["success"] and [v["id"] for v in listed["groups"][0]["versions"]] == ["sp-views", "sp-deluxe"]

    body = {"source": "spotify", "release_id": "sp-deluxe"}
    preview = client.post(f"/api/library/v2/albums/{ids['views']}/versions/preview", json=body)
    assert preview.status_code == 200 and preview.get_json()["track_count_after"] == 3

    applied = client.post(f"/api/library/v2/albums/{ids['views']}/versions/apply", json=body)
    assert applied.status_code == 200, applied.get_json()
    again = client.post(f"/api/library/v2/albums/{ids['views']}/versions/preview", json=body)
    assert again.get_json()["already_current"]
    assert client.get("/api/library/v2/albums/999999/versions").status_code == 404
