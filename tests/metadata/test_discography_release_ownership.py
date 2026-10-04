"""download discography decides "owned" per release, like the artist page.

discord (SeadogsBooty): after downloading Yellowcard's discography, the
artist page showed most singles missing, but Download Discography filtered to
singles said every one was already owned and finished "0 tracks added". the
download checked each song against everything the artist owns, so a single
whose song is also on an album counted as owned. the artist page asks whether
the release itself is in the library.
"""

import json
from types import SimpleNamespace

import pytest
from flask import Flask

from core.metadata import discography_filters as df


def _track(tid, title, album_id, album_title):
    return SimpleNamespace(id=tid, title=title, album_id=album_id,
                           album_title=album_title, artist_name="Yellowcard")


OCEAN_AVENUE = SimpleNamespace(id=10, title="Ocean Avenue")
LIBRARY_TRACKS = [
    _track(1, "Hear You Me", 10, "Ocean Avenue"),
    _track(2, "Ocean Avenue", 10, "Ocean Avenue"),
]


class _FakeDB:
    """an artist who owns one album. the release matcher answers by title,
    the track matcher scores whatever candidates it's handed."""

    def __init__(self):
        self.wishlist = []
        self.album_checks = []
        self.albums = []

    def get_candidate_albums_for_artist(self, artist, server_source=None):
        return [OCEAN_AVENUE]

    def get_candidate_tracks_for_albums(self, album_ids):
        return [t for t in LIBRARY_TRACKS if t.album_id in album_ids]

    def check_album_exists_with_completeness(self, title, artist, **kw):
        self.album_checks.append((title, kw.get("strict_discography_match"), kw.get("expected_year")))
        found = OCEAN_AVENUE if title == "Ocean Avenue" else None
        return found, (1.0 if found else 0.0), 0, 0, False, []

    def check_track_exists(self, title, artist, confidence_threshold=0.7, server_source=None,
                           album=None, candidate_tracks=None):
        pool = LIBRARY_TRACKS if candidate_tracks is None else candidate_tracks
        for t in pool:
            if t.title == title:
                return t, 1.0
        return None, 0.0

    def add_to_wishlist(self, spotify_track_data, **kw):
        self.wishlist.append((spotify_track_data["album"]["name"], spotify_track_data["name"]))
        self.albums.append(spotify_track_data["album"])
        return True


RELEASES = {
    "rel-single": ({"name": "Hear You Me", "album_type": "single", "release_date": "2004-05-01"},
                   ["Hear You Me"]),
    "rel-album": ({"name": "Ocean Avenue", "album_type": "album", "release_date": "2003-07-22"},
                  ["Hear You Me", "Ocean Avenue", "Breathing"]),
}


@pytest.fixture
def run(monkeypatch):
    from api import artist_detail as ad
    import database.music_database as mdb
    from core.metadata import album_tracks

    db = _FakeDB()
    monkeypatch.setattr(mdb, "MusicDatabase", lambda: db)
    monkeypatch.setattr(ad, "get_current_profile_id", lambda: 1, raising=False)

    def fake_tracks(album_id, artist_name="", album_name="", source_override=None):
        album, names = RELEASES[album_id]
        return {"success": True, "source": "deezer",
                "album": dict(album, id=album_id, artists=[{"name": "Yellowcard"}]),
                "tracks": [{"id": f"{album_id}-{i}", "name": n, "artists": [{"name": "Yellowcard"}],
                            "track_number": i + 1} for i, n in enumerate(names)]}

    monkeypatch.setattr(album_tracks, "get_artist_album_tracks", fake_tracks)
    monkeypatch.setattr(df, "load_global_content_filter_settings", lambda _cm: {}, raising=False)

    app = Flask(__name__)
    app.register_blueprint(ad.bp)

    def go(release_ids, section=None):
        client = app.test_client()
        resp = client.post("/api/artist/yc/download-discography", json={
            "artist_name": "Yellowcard",
            "albums": [dict({"id": rid, "name": RELEASES[rid][0]["name"], "source": "deezer"},
                            **({"album_type": section} if section else {}))
                       for rid in release_ids],
        })
        lines = [json.loads(line) for line in resp.get_data(as_text=True).splitlines() if line.strip()]
        return db, lines

    return go


def test_a_single_whose_song_is_on_an_owned_album_is_still_queued(run):
    db, _lines = run(["rel-single"])
    assert db.wishlist == [("Hear You Me", "Hear You Me")]


def test_an_owned_release_still_skips_the_songs_it_has(run):
    db, _lines = run(["rel-album"])
    # the owned album's own tracks are skipped, the one it's missing is queued
    assert db.wishlist == [("Ocean Avenue", "Breathing")]


def test_the_release_is_found_the_way_the_artist_page_finds_it(run):
    db, _lines = run(["rel-single"])
    assert ("Hear You Me", True, 2004) in db.album_checks


def test_a_failed_release_lookup_falls_back_to_the_artist_wide_check(monkeypatch):
    class _Broken(_FakeDB):
        def check_album_exists_with_completeness(self, *a, **k):
            raise RuntimeError("db busy")

    assert df.owned_release_tracks(_Broken(), "Hear You Me", "Yellowcard", 1, "2004", "plex") is None
    assert df.owned_release_tracks(_FakeDB(), "Hear You Me", "Yellowcard", 1, "2004", "plex") == []
    owned = df.owned_release_tracks(_FakeDB(), "Ocean Avenue", "Yellowcard", 3, "2003", "plex",
                                    candidate_tracks=LIBRARY_TRACKS)
    assert [t.title for t in owned] == ["Hear You Me", "Ocean Avenue"]


def test_the_artist_page_section_is_locked_onto_what_gets_queued(run):
    # the page showed the release under Albums; the source album lookup calls
    # it a single. the section wins and is locked, so it files under Album/
    db, _lines = run(["rel-single"], section="album")
    assert db.albums and all(a["album_type"] == "album" for a in db.albums)
    assert all(a["album_type_locked"] is True for a in db.albums)


def test_without_a_section_nothing_is_locked(run):
    db, _lines = run(["rel-single"])
    assert db.albums[0]["album_type"] == "single"
    assert db.albums[0]["album_type_locked"] is False



# ── #1289: the downloader's ownership check agrees with the artist page ──


def _yellowcard_library(tmp_path):
    """A scanned library owning Ocean Avenue (filed 2003), with no stored
    Deezer ids — the SeadogsBooty shape."""
    from database.music_database import MusicDatabase
    from tests import lib2_seed
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        album_cols = {"year": 2003, "track_count": 2, "server_source": "test"}
        lib2_seed.track(conn, "Yellowcard", "Ocean Avenue", "Ocean Avenue", track_number=1,
                        path="/m/t1.flac", album_cols=album_cols)
        lib2_seed.track(conn, "Yellowcard", "Ocean Avenue", "Breathing", track_number=2,
                        path="/m/t2.flac")
        conn.commit()
    return db


def test_owned_release_tracks_deezer_reissue_date_exempt(tmp_path):
    """#1289: owned_release_tracks applies the same Deezer exemption as the
    artist page — a Deezer card dated 2006-05-31 for the owned 2003 album
    returns its tracks instead of []."""
    db = _yellowcard_library(tmp_path)
    candidates = db.get_candidate_albums_for_artist('Yellowcard', server_source='test')
    tracks = df.owned_release_tracks(
        db, 'Ocean Avenue', 'Yellowcard', 2, '2006-05-31', 'test',
        candidate_albums=candidates,
        metadata_source='deezer', card_source_id='DZ-375062')
    assert tracks is not None and len(tracks) == 2


def test_owned_release_tracks_default_still_year_gated(tmp_path):
    """Without metadata_source the old gate holds — the same card returns []."""
    db = _yellowcard_library(tmp_path)
    candidates = db.get_candidate_albums_for_artist('Yellowcard', server_source='test')
    tracks = df.owned_release_tracks(
        db, 'Ocean Avenue', 'Yellowcard', 2, '2006-05-31', 'test',
        candidate_albums=candidates)
    assert tracks == []


def test_owned_release_tracks_deezer_conflicting_id_still_missing(tmp_path):
    """#1289 tighter rule: a Deezer card whose id conflicts with the
    candidate's stored id is a different release — the exemption does not
    fire and the downloader's check agrees with the page ([])."""
    db = _yellowcard_library(tmp_path)
    with db._get_connection() as conn:
        conn.execute("""UPDATE lib2_albums SET external_ids = '{"deezer": "DZ-9"}'""")
        conn.commit()
    candidates = db.get_candidate_albums_for_artist('Yellowcard', server_source='test')
    tracks = df.owned_release_tracks(
        db, 'Ocean Avenue', 'Yellowcard', 2, '2006-05-31', 'test',
        candidate_albums=candidates,
        metadata_source='deezer', card_source_id='DZ-375062')
    assert tracks == []
