"""Find & Add: Spotify "Title - Qualifier" must find the base-titled library track.

wolf's report: Spotify shows "Calma - Remix", Find & Add searches that literal
string, the library stores the track as just "Calma" (only the duration marks it
as the remix) → the literal search misses and the OR-fuzzy fallback floods 20
unrelated "... remix" hits. Dropping "- Remix" (searching "Calma") finds it.

Fix: search_tracks retries on the base title (before Spotify's " - " separator)
before the OR-fuzzy flood.
"""

from __future__ import annotations

import pytest

from core.text.title_match import base_title_before_dash
from database.music_database import MusicDatabase


# --- pure helper -----------------------------------------------------------

def test_base_title_before_dash_strips_spotify_version_suffix():
    assert base_title_before_dash('Calma - Remix') == 'Calma'
    assert base_title_before_dash('Closer - Radio Edit') == 'Closer'
    assert base_title_before_dash('Crocodile Rock - Remastered 2014') == 'Crocodile Rock'


def test_base_title_before_dash_leaves_plain_titles_alone():
    assert base_title_before_dash('Tom Sawyer') == 'Tom Sawyer'
    assert base_title_before_dash('21st Century Schizoid Man') == '21st Century Schizoid Man'
    assert base_title_before_dash('Up-Tight') == 'Up-Tight'   # bare hyphen, not a separator
    assert base_title_before_dash('') == ''


def test_base_title_before_dash_splits_first_separator_only():
    assert base_title_before_dash('A - B - C') == 'A'


# --- integration: the real search path -------------------------------------

@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _insert(db, tid, title, artist_id, artist_name):
    with db._get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO artists (id, name) VALUES (?, ?)", (artist_id, artist_name))
        conn.execute("INSERT OR IGNORE INTO albums (id, title, artist_id) VALUES (?, ?, ?)",
                     (artist_id, "Alb", artist_id))
        conn.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration, file_path) "
            "VALUES (?, ?, ?, ?, 1, 238, ?)",
            (tid, artist_id, artist_id, title, f"/m/{tid}.flac"),
        )
        conn.commit()


def test_spotify_dash_remix_finds_base_titled_track(db):
    # Library stores the remix as just "Calma" (the wolf case).
    _insert(db, 1, "Calma", 1, "Pedro Capó")
    results = db.search_tracks(title="Calma - Remix", rank_artist="Pedro Capó")
    titles = [t.title for t in results]
    assert "Calma" in titles, "base-title fallback should find 'Calma' for 'Calma - Remix'"


def test_spotify_dash_remix_finds_parenthesized_remix(db):
    # …and still matches when the library DID label it "(Remix)".
    _insert(db, 1, "Calma (Remix)", 1, "Pedro Capó")
    results = db.search_tracks(title="Calma - Remix", rank_artist="Pedro Capó")
    assert any("Calma" in t.title for t in results)


def test_plain_title_unaffected_uses_basic_search(db):
    _insert(db, 1, "Tom Sawyer", 1, "Rush")
    results = db.search_tracks(title="Tom Sawyer")
    assert [t.title for t in results] == ["Tom Sawyer"]


def test_dash_query_does_not_flood_when_base_matches(db):
    # The base-title retry must short-circuit BEFORE the OR-fuzzy flood, so an
    # unrelated "... Remix" track doesn't drown the real one.
    _insert(db, 1, "Calma", 1, "Pedro Capó")
    _insert(db, 2, "Some Other Song (KAIZ Remix)", 2, "Someone Else")
    results = db.search_tracks(title="Calma - Remix", rank_artist="Pedro Capó")
    titles = [t.title for t in results]
    assert "Calma" in titles
    assert "Some Other Song (KAIZ Remix)" not in titles
