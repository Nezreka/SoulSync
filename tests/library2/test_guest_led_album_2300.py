"""#2300: an album whose every track is led by a guest stays its artist's.

"93 Empire" by 93 Empire credits each track to someone else (Sofiane, Kaaris,
Vald...), never 93 Empire. Download Discography from the artist page put
each track on the wishlist, and the wishlist add filed the release under the
track's lead: one "93 Empire" album per guest, every guest a release artist
on the Library page, the wishlist grouped by guest. On Spotify, where no
track names 93 Empire, the artist filter dropped the whole album instead.
"""

from __future__ import annotations

import json
import threading

import pytest

import core.wishlist.service as wishlist_service
import database.music_database as database
from core.library2.materialize import materialize_from_spotify_track, materialize_track_intent
from core.library2.sql_util import library_index_artist_ids_sql

ALBUM_ARTIST = {"id": "art-93", "name": "93 Empire"}
GUESTS = ["Sofiane", "Kaaris", "Vald"]


def _album(source):
    return {
        "id": "alb-93", "name": "93 Empire", "album_type": "album",
        "artists": [dict(ALBUM_ARTIST)], "images": [], "release_date": "2020-03-06",
        "total_tracks": len(GUESTS), "source": source,
    }


def _tracks():
    return [{"id": f"trk-{n}", "name": f"Song {n}", "track_number": n, "disc_number": 1,
             "artists": [{"id": f"art-{guest.lower()}", "name": guest}],
             "duration_ms": 200000}
            for n, guest in enumerate(GUESTS, start=1)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    from flask import Flask

    from api import artist_detail
    from core.settings import config_manager

    path = str(tmp_path / "music.db")
    monkeypatch.setenv("DATABASE_PATH", path)
    db = database.MusicDatabase(path)
    monkeypatch.setattr(database, "_database_instances", {(threading.get_ident(), path): db})
    monkeypatch.setattr(wishlist_service, "_wishlist_service", None)
    monkeypatch.setattr("database.music_database.MusicDatabase", lambda *a, **k: db)
    monkeypatch.setattr(artist_detail, "get_current_profile_id", lambda: 1)
    monkeypatch.setattr(config_manager, "get_active_media_server", lambda: "soulsync")
    app = Flask(__name__)
    app.register_blueprint(artist_detail.bp)
    return db, app.test_client(), monkeypatch


def _download_discography(env, source):
    db, client, monkeypatch = env
    monkeypatch.setattr(
        "core.metadata.album_tracks.get_artist_album_tracks",
        lambda *a, **k: {"success": True, "album": _album(source), "tracks": _tracks(),
                         "source": source, "resolved_album_id": "alb-93"})
    response = client.post("/api/artist/art-93/download-discography", json={
        "artist_name": "93 Empire", "source": source,
        "albums": [{"id": "alb-93", "name": "93 Empire", "source": source,
                    "artist_name": "93 Empire", "album_type": "album"}],
    })
    lines = [json.loads(line) for line in response.get_data(as_text=True).splitlines() if line]
    return lines[-1]


@pytest.mark.parametrize("source", ["spotify", "itunes"])
def test_discography_download_files_the_album_under_its_artist(env, source):
    db = env[0]
    done = _download_discography(env, source)
    # every track is the artist's own release, none dropped as a mismatch
    assert done["total_added"] == len(GUESTS), done
    assert done["total_skipped_artist"] == 0

    conn = db._get_connection()
    try:
        albums = conn.execute(
            "SELECT al.title, ar.name FROM lib2_albums al "
            "JOIN lib2_artists ar ON ar.id = al.primary_artist_id").fetchall()
        assert [(r["title"], r["name"]) for r in albums] == [("93 Empire", "93 Empire")]
        tracks = conn.execute(
            "SELECT t.title, t.track_artist FROM lib2_tracks t ORDER BY t.track_number").fetchall()
        # each track keeps its own lead as its credit
        assert [(r["title"], r["track_artist"]) for r in tracks] == [
            (f"Song {n}", guest) for n, guest in enumerate(GUESTS, start=1)]
        credited = {r[0] for r in conn.execute(
            "SELECT ar.name FROM lib2_track_artists ta JOIN lib2_artists ar ON ar.id = ta.artist_id")}
        assert credited == set(GUESTS)
        # the Library page lists the album artist, not a guest
        listed = {r[0] for r in conn.execute(
            f"SELECT name FROM lib2_artists WHERE id IN ({library_index_artist_ids_sql()})")}
        assert listed == {"93 Empire"}
        # the wishlist rows point at the one album
        infos = [json.loads(r[0] or "{}") for r in conn.execute(
            "SELECT source_info FROM wishlist_tracks")]
        assert len(infos) == len(GUESTS)
        assert len({info.get("lib2_album_id") for info in infos}) == 1
    finally:
        conn.close()


def test_a_normal_album_stays_its_leads(imported_conn):
    """the lead's own album, credited as such: nothing changes."""
    result = materialize_from_spotify_track(imported_conn, {
        "id": "t-solo", "name": "Solo Song", "artists": [{"id": "a-solo", "name": "Solo Act"}],
        "album": {"id": "al-solo", "name": "Solo Album",
                  "artists": [{"id": "a-solo", "name": "Solo Act"}]},
    })
    row = imported_conn.execute(
        "SELECT ar.name, t.track_artist FROM lib2_tracks t JOIN lib2_albums al ON al.id=t.album_id "
        "JOIN lib2_artists ar ON ar.id=al.primary_artist_id WHERE t.id=?",
        (result["track_id"],)).fetchone()
    assert row["name"] == "Solo Act"
    assert not row["track_artist"]
    assert result["artist_id"] == imported_conn.execute(
        "SELECT primary_artist_id FROM lib2_albums WHERE id=?", (result["album_id"],)).fetchone()[0]


def test_a_collab_led_by_the_album_artist_stays_theirs(imported_conn):
    """"A & B" with the lead listed first is the lead's release, as the folder files it."""
    result = materialize_track_intent(
        imported_conn, artist_name="Lead Act", track_title="Joint Song",
        album_title="Joint Album", album_artist_name="Lead Act & Other Act")
    name = imported_conn.execute(
        "SELECT ar.name FROM lib2_albums al JOIN lib2_artists ar ON ar.id=al.primary_artist_id "
        "WHERE al.id=?", (result["album_id"],)).fetchone()[0]
    assert name == "Lead Act"


def test_a_compilation_track_files_under_various_artists(imported_conn):
    result = materialize_from_spotify_track(imported_conn, {
        "id": "t-comp", "name": "Misirlou", "artists": [{"name": "Dick Dale"}],
        "album": {"id": "al-comp", "name": "Pulp Fiction", "album_type": "compilation",
                  "artists": [{"name": "Various Artists"}]},
    })
    name = imported_conn.execute(
        "SELECT ar.name FROM lib2_albums al JOIN lib2_artists ar ON ar.id=al.primary_artist_id "
        "WHERE al.id=?", (result["album_id"],)).fetchone()[0]
    assert name == "Various Artists"
    credit = imported_conn.execute(
        "SELECT ar.name FROM lib2_track_artists ta JOIN lib2_artists ar ON ar.id=ta.artist_id "
        "WHERE ta.track_id=?", (result["track_id"],)).fetchall()
    assert [r[0] for r in credit] == ["Dick Dale"]


def test_a_search_album_download_keeps_its_album_artist(imported_conn):
    """the search page's confirmed album download takes the same rule."""
    from core.library2.confirmed_intent import _intent_fields

    fields = _intent_fields(
        {"id": "trk-1", "name": "Song 1", "artists": [{"name": "Sofiane"}],
         "album": {"id": "alb-93", "name": "93 Empire", "artists": [dict(ALBUM_ARTIST)]}},
        album_context=None, artist_context=None)
    assert fields["album_artist_name"] == "93 Empire"
    assert fields["album_artist_provider_id"] == "art-93"
    result = materialize_track_intent(imported_conn, **fields)
    name = imported_conn.execute(
        "SELECT ar.name FROM lib2_albums al JOIN lib2_artists ar ON ar.id=al.primary_artist_id "
        "WHERE al.id=?", (result["album_id"],)).fetchone()[0]
    assert name == "93 Empire"
