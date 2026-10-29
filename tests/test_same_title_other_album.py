"""A theme song or intro jingle carries the same title on every album of a series.

Several library or ledger rows then score identically for one request, and the
first one used to win. A caller that compares albums (the watchlist with
allow-duplicates on) was handed the copy on a DIFFERENT album, decided the
wanted album's copy was missing, and fetched it again every scan, forever."""

from __future__ import annotations

import pytest

from core import audiobook_routing as routing
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _record(db, path, album, artist="Bobby & Max", title="Titelsong"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    with db._get_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO track_downloads (file_path, source_service, track_title, "
            "track_artist, track_album, status) VALUES (?, 'youtube', ?, ?, ?, 'completed')",
            (str(path), title, artist, album))
        conn.commit()


def _series(db, tmp_path):
    for n in (92, 94, 95):
        _record(db, tmp_path / f"Folge {n}" / "02 - Titelsong.mp3", f"Folge {n}: Titel")


@pytest.mark.parametrize("wanted", ["Folge 92: Titel", "Folge 94: Titel", "Folge 95: Titel"])
def test_download_ledger_prefers_the_album_asked_about(db, tmp_path, wanted):
    _series(db, tmp_path)
    track, _ = db.check_track_exists("Titelsong", "Bobby & Max", include_downloads=True,
                                     prefer_album=wanted)
    assert track.album_title == wanted


def test_routed_artist_prefers_the_album_asked_about(db, tmp_path, monkeypatch):
    books = tmp_path / "audiobooks"
    books.mkdir()
    monkeypatch.setattr(routing, "audiobook_root", lambda: str(books))
    monkeypatch.setattr(routing, "audiobook_folder_configured", lambda: True)
    routing.add_route("Bobby & Max", db)
    for n in (92, 95):
        _record(db, books / f"Folge {n}" / "02 - Titelsong.mp3", f"Folge {n}: Titel")

    track, _ = db.check_track_exists("Titelsong", "Bobby & Max", include_downloads=True,
                                     prefer_album="Folge 95: Titel")
    assert str(track.id).startswith("routed:")
    assert track.album_title == "Folge 95: Titel"


def test_without_a_preference_the_result_is_unchanged(db, tmp_path):
    _series(db, tmp_path)
    track, confidence = db.check_track_exists("Titelsong", "Bobby & Max", include_downloads=True)
    assert track is not None and confidence >= 0.8


def test_the_preference_never_invents_a_match(db, tmp_path):
    _series(db, tmp_path)
    track, _ = db.check_track_exists("Titelsong", "Bobby & Max", include_downloads=True,
                                     prefer_album="Folge 99: Gibt es nicht")
    # still owned on the other albums; the caller's own album gate decides what that means
    assert track is not None and track.album_title != "Folge 99: Gibt es nicht"


def test_media_server_rows_prefer_the_album_asked_about(db, tmp_path):
    with db._get_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO artists (id, name, server_source) VALUES (1, 'Band', 'plex')")
        for album_id, name in ((1, "First Album"), (2, "Second Album")):
            c.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES (?, 1, ?, 'plex')",
                      (album_id, name))
            c.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path, server_source) "
                      "VALUES (?, ?, 1, 'Intro', ?, 'plex')", (album_id, album_id, f"/m/{album_id}.mp3"))
        conn.commit()
    for wanted in ("First Album", "Second Album"):
        track, _ = db.check_track_exists("Intro", "Band", server_source="plex", prefer_album=wanted)
        assert track.album_title == wanted
