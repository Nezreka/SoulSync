"""Seam tests for recommendation explainability — get_recommendation_sources.

The similar-artists worker stores rows keyed by a polymorphic
``source_artist_id`` (one of the user's artists' spotify / itunes / deezer /
musicbrainz ids). The "because you have X, Y, Z" explanation has to resolve
that id back to a display name by matching it against every provider-id column
on BOTH the library (`artists`) and `watchlist_artists` tables.

These tests build a real schema via MusicDatabase(tmp) and insert rows directly
so the SQL join is exercised end to end.
"""

from __future__ import annotations

import sqlite3

from database.music_database import MusicDatabase


def _seed(path):
    """Insert two library artists + one watchlist artist, and similar_artists
    rows that point at them via different provider-id columns."""
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    # Library artists, each matched on a DIFFERENT provider id column
    cur.execute("INSERT INTO artists (name, spotify_artist_id) VALUES ('Radiohead', 'sp_radiohead')")
    cur.execute("INSERT INTO artists (name, musicbrainz_id) VALUES ('Portishead', 'mb_portishead')")
    # A watchlist-only artist (not in library), matched on deezer id
    cur.execute(
        "INSERT INTO watchlist_artists (artist_name, deezer_artist_id, profile_id) "
        "VALUES ('Bjork', 'dz_bjork', 1)"
    )

    # 'Thom Yorke' is listed as similar by Radiohead (spotify id) AND Bjork
    # (watchlist, deezer id) -> two distinct sources.
    cur.execute(
        "INSERT INTO similar_artists (source_artist_id, similar_artist_name, profile_id) "
        "VALUES ('sp_radiohead', 'Thom Yorke', 1)"
    )
    cur.execute(
        "INSERT INTO similar_artists (source_artist_id, similar_artist_name, profile_id) "
        "VALUES ('dz_bjork', 'Thom Yorke', 1)"
    )
    # 'Massive Attack' listed by Portishead (musicbrainz id) only.
    cur.execute(
        "INSERT INTO similar_artists (source_artist_id, similar_artist_name, profile_id) "
        "VALUES ('mb_portishead', 'Massive Attack', 1)"
    )
    # An orphan: source id matches nobody -> must NOT produce a phantom source.
    cur.execute(
        "INSERT INTO similar_artists (source_artist_id, similar_artist_name, profile_id) "
        "VALUES ('sp_ghost', 'Nobody Knows', 1)"
    )
    conn.commit()
    conn.close()


def test_resolves_library_and_watchlist_sources(tmp_path):
    path = str(tmp_path / "m.db")
    MusicDatabase(path)          # build schema via migrations
    _seed(path)
    db = MusicDatabase(path)

    out = db.get_recommendation_sources(["Thom Yorke", "Massive Attack", "Nobody Knows"])

    # Thom Yorke: resolved from a library spotify id AND a watchlist deezer id
    assert out["Thom Yorke"] == ["Bjork", "Radiohead"]   # deduped + name-sorted
    # Massive Attack: resolved via musicbrainz id on a library artist
    assert out["Massive Attack"] == ["Portishead"]
    # Orphan recommendation has no resolvable source -> omitted entirely
    assert "Nobody Knows" not in out


def test_empty_input_returns_empty(tmp_path):
    path = str(tmp_path / "m.db")
    db = MusicDatabase(path)
    assert db.get_recommendation_sources([]) == {}
    assert db.get_recommendation_sources([None, ""]) == {}


def test_max_per_caps_sources(tmp_path):
    path = str(tmp_path / "m.db")
    MusicDatabase(path)
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    # Five library artists all listing the same recommendation
    for i in range(5):
        cur.execute("INSERT INTO artists (name, spotify_artist_id) VALUES (?, ?)",
                    (f"Artist{i}", f"sp_{i}"))
        cur.execute(
            "INSERT INTO similar_artists (source_artist_id, similar_artist_name, profile_id) "
            "VALUES (?, 'Shared Rec', 1)", (f"sp_{i}",))
    conn.commit()
    conn.close()

    db = MusicDatabase(path)
    out = db.get_recommendation_sources(["Shared Rec"], max_per=3)
    assert len(out["Shared Rec"]) == 3                   # capped
    assert out["Shared Rec"] == ["Artist0", "Artist1", "Artist2"]  # name-sorted


def test_profile_scoping(tmp_path):
    """A source artist in another profile must not leak into the explanation."""
    path = str(tmp_path / "m.db")
    MusicDatabase(path)
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("INSERT INTO artists (name, spotify_artist_id) VALUES ('Mine', 'sp_mine')")
    # similar row belongs to profile 2, not 1
    cur.execute(
        "INSERT INTO similar_artists (source_artist_id, similar_artist_name, profile_id) "
        "VALUES ('sp_mine', 'Rec X', 2)"
    )
    conn.commit()
    conn.close()

    db = MusicDatabase(path)
    assert db.get_recommendation_sources(["Rec X"], profile_id=1) == {}
    assert db.get_recommendation_sources(["Rec X"], profile_id=2) == {"Rec X": ["Mine"]}
