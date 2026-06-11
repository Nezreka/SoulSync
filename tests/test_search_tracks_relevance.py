"""Find & Add library search relevance (Billie Eilish 'bad guy' report).

Root cause proven against the real DB: `ORDER BY tracks.title` is case-SENSITIVE
(SQLite BINARY sorts 'B' before 'b'), so a lowercase exact title like Billie
Eilish's "bad guy" sorted BELOW every capitalised "Bad Guy" and fell past the
result LIMIT — it never showed in the modal even though it was in the library.

Fix: rank by relevance (exact title first, case-insensitive), and accept a
rank-only artist hint so an exact title+artist match wins — without FILTERING
(filtering would re-hide the track if it's tagged under a slightly different
artist on the server).
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _insert(db, tid, title, artist_id, artist_name):
    with db._get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO artists (id, name) VALUES (?, ?)", (artist_id, artist_name))
        conn.execute("INSERT OR IGNORE INTO albums (id, title, artist_id) VALUES (?, ?, ?)", (artist_id, "Alb", artist_id))
        conn.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration, file_path) "
            "VALUES (?, ?, ?, ?, 1, 180, ?)",
            (tid, artist_id, artist_id, title, f"/m/{tid}.mp3"),
        )
        conn.commit()


def test_lowercase_exact_title_not_buried_by_case(db):
    # Capitalised "Bad Guy" tracks would (pre-fix) fill the LIMIT and sort
    # the lowercase "bad guy" below them, cutting it off.
    _insert(db, 1, "Bad Guy", 1, "Yara")
    _insert(db, 2, "Bad Guy", 2, "Zelda")
    _insert(db, 3, "bad guy", 3, "Billie Eilish")

    names = [t.artist_name for t in db.search_tracks(title="bad guy", limit=2)]
    assert "Billie Eilish" in names, "lowercase exact title must not be sorted past the limit"


def test_rank_artist_hint_floats_match_to_top_without_filtering(db):
    _insert(db, 1, "Bad Guy", 1, "Aaa Artist")
    _insert(db, 2, "Bad Guy", 2, "Bbb Artist")
    _insert(db, 3, "bad guy", 3, "Billie Eilish")

    results = db.search_tracks(title="bad guy", limit=10, rank_artist="Billie Eilish")
    names = [t.artist_name for t in results]
    assert names[0] == "Billie Eilish", "the hinted artist's exact match should rank first"
    # …but it must NOT filter — the other artists' versions are still there.
    assert len(results) == 3
    assert {"Aaa Artist", "Bbb Artist"} <= set(names)


def test_exact_title_outranks_superstring_title(db):
    # "bad guy" should beat "Bad Guy Necessity" / "Bad Guys" for the query.
    _insert(db, 1, "Bad Guy Necessity", 1, "Aardvark")  # would sort first alphabetically
    _insert(db, 2, "bad guy", 2, "Billie Eilish")

    top = db.search_tracks(title="bad guy", limit=5)[0]
    assert top.title.lower() == "bad guy" and top.artist_name == "Billie Eilish"
