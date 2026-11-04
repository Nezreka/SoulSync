"""The track search stays inside the artist it was given.

The catalogue rewrite dropped dev's artist scope: every miss ran a word-or
match over every track in the library with a python fold per row, a few
seconds a query on a 310k library and minutes for one playlist track. And the
only extra rows it found were other bands' songs that the scorer rejects.
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _insert(db, tid, title, artist_name, album="Alb"):
    from tests.support.catalogue_seed import seed_library_track

    with db._get_connection() as conn:
        seed_library_track(
            conn, artist=artist_name, album=album, title=title,
            artist_server_id=f"ar-{artist_name}", album_server_id=f"al-{artist_name}-{album}",
            track_server_id=str(tid), track_number=tid, duration=180,
            file_path=f"/m/{tid}.flac")
        conn.commit()


def test_the_fuzzy_pass_stays_inside_the_artist(db):
    _insert(db, 1, "Celeste Mirage", "Aether")
    _insert(db, 2, "Mirage Song", "Someone Else")

    results = db.search_tracks(title="Mirage Celeste (2024 Remaster)", artist="Aether")

    assert [t.title for t in results] == ["Celeste Mirage"]


def test_an_artist_the_library_does_not_have_finds_nothing(db):
    _insert(db, 1, "Mirage Song", "Someone Else")

    assert db.search_tracks(title="Mirage", artist="Totally Unknown Band") == []


def test_an_accented_name_still_finds_the_artist(db):
    _insert(db, 1, "Halo", "Beyoncé")

    assert [t.title for t in db.search_tracks(title="Halo", artist="Beyonce")] == ["Halo"]


def test_a_combined_credit_still_finds_the_track(db):
    _insert(db, 1, "Get Lucky", "Daft Punk & Pharrell Williams")

    assert [t.title for t in db.search_tracks(title="Get Lucky", artist="Pharrell Williams")] == ["Get Lucky"]


def test_the_api_search_uses_the_same_scope(db):
    _insert(db, 1, "Celeste Mirage", "Aether")
    _insert(db, 2, "Mirage Song", "Someone Else")

    rows = db.api_search_tracks(title="Mirage Celeste (2024 Remaster)", artist="Aether")

    assert [r["title"] for r in rows] == ["Celeste Mirage"]
