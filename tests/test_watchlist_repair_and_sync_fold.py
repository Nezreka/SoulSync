"""Regression tests for #2254 (playlist sync folding different songs with shared suffix)
and #2274/#2275 (watchlist duplicate artists, bogus deezer==itunes IDs, and stale artwork)."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

from core.matching_engine import MusicMatchingEngine
from core.watchlist_scanner import WatchlistScanner


def test_shared_suffix_does_not_fold_different_songs_2254():
    engine = MusicMatchingEngine()
    conf, _ = engine.score_track_match(
        source_title="Mele Kalikimaka - Piano Version",
        source_artists=["David Schultz"],
        source_duration_ms=180_000,
        candidate_title="Someday At Christmas - Piano Version",
        candidate_artists=["David Schultz"],
        candidate_duration_ms=181_000,
    )
    assert conf < 0.70

    conf2, _ = engine.score_track_match(
        source_title="All I Want For Christmas Is You - Piano Version",
        source_artists=["David Schultz"],
        source_duration_ms=195_000,
        candidate_title="Make It To Christmas - Piano Version",
        candidate_artists=["David Schultz"],
        candidate_duration_ms=194_000,
    )
    assert conf2 < 0.70

    # Same song with same suffix still matches cleanly
    exact, _ = engine.score_track_match(
        source_title="Mele Kalikimaka - Piano Version",
        source_artists=["David Schultz"],
        source_duration_ms=180_000,
        candidate_title="Mele Kalikimaka - Piano Version",
        candidate_artists=["David Schultz"],
        candidate_duration_ms=180_500,
    )
    assert exact >= 0.95


def test_watchlist_repair_fixes_bogus_deezer_ids_artwork_and_duplicates_2274_2275():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE watchlist_artists (
            id INTEGER PRIMARY KEY,
            profile_id INTEGER,
            artist_name TEXT,
            spotify_artist_id TEXT,
            itunes_artist_id TEXT,
            deezer_artist_id TEXT,
            discogs_artist_id TEXT,
            musicbrainz_artist_id TEXT,
            image_url TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE lib2_artists (
            id INTEGER PRIMARY KEY,
            name TEXT,
            external_ids TEXT,
            image_url TEXT
        )
    """)
    conn.execute(
        "INSERT INTO lib2_artists VALUES (1, 'Radiohead', ?, 'https://cdn.example/rh.jpg')",
        (json.dumps({"itunes": "657515", "deezer": "399"}),),
    )
    # Row 1: bogus deezer_artist_id == itunes_artist_id with wrong Deezer image
    conn.execute("""
        INSERT INTO watchlist_artists VALUES
        (1, 1, 'Radiohead', 'sp1', '657515', '657515', NULL, NULL, 'https://api.deezer.com/artist/657515/image?size=big')
    """)
    # Row 2 & 3: duplicate artist name with split IDs
    conn.execute("""
        INSERT INTO watchlist_artists VALUES
        (2, 1, 'Daft Punk', 'sp_dp', NULL, NULL, NULL, NULL, NULL),
        (3, 1, ' daft punk ', NULL, '5468295', '27', NULL, 'mb_dp', 'https://cdn.example/dp.jpg')
    """)
    conn.commit()

    class _FakeScanner(WatchlistScanner):
        def __init__(self, db):
            self._db = db

        @property
        def database(self):
            return self._db

    scanner = _FakeScanner(SimpleNamespace(_get_connection=lambda: conn))

    stats = scanner.repair_watchlist_duplicates_and_ids(profile_id=1)
    assert stats["fixed_ids"] == 1
    assert stats["cleared_artwork"] == 1
    assert stats["merged_duplicates"] == 1

    rh = conn.execute("SELECT * FROM watchlist_artists WHERE artist_name = 'Radiohead'").fetchone()
    assert rh["deezer_artist_id"] == "399"
    assert rh["image_url"] is None

    dp_rows = conn.execute("SELECT * FROM watchlist_artists WHERE LOWER(TRIM(artist_name)) = 'daft punk'").fetchall()
    assert len(dp_rows) == 1
    assert dp_rows[0]["spotify_artist_id"] == "sp_dp"
    assert dp_rows[0]["deezer_artist_id"] == "27"
    assert dp_rows[0]["musicbrainz_artist_id"] == "mb_dp"
    assert dp_rows[0]["image_url"] == "https://cdn.example/dp.jpg"


# the first cut of #2254 capped every title that scored under 0.65 on its bare
# letters, which undid the suffix cleaning: a track with a guest or a remaster
# note in its title stopped matching the same track without one, fell under the
# 0.70 a library match needs, and read as missing.
import pytest as _pytest


@_pytest.mark.parametrize("a,b,least", [
    ("Stay (feat. Justin Bieber)", "Stay", 0.95),
    ("Song 2", "Song 2 - 2012 Remaster", 0.80),
    ("Yesterday (Remastered 2009)", "Yesterday", 0.80),
    ("Hurt", "Hurt (Quiet)", 0.70),
    ("Fade Into You", "Fade Into You Lyrics Video HD", 0.70),
])
def test_a_suffix_on_one_side_only_still_matches(a, b, least):
    from core.matching_engine import MusicMatchingEngine
    score, _ = MusicMatchingEngine().score_track_match(
        source_title=a, source_artists=["X"], source_duration_ms=200000,
        candidate_title=b, candidate_artists=["X"], candidate_duration_ms=200000)
    assert score >= least


def test_a_suffix_both_share_still_cannot_join_two_songs():
    from core.matching_engine import MusicMatchingEngine
    score, _ = MusicMatchingEngine().score_track_match(
        source_title="Mele Kalikimaka - Piano Version", source_artists=["X"], source_duration_ms=200000,
        candidate_title="Someday At Christmas - Piano Version", candidate_artists=["X"],
        candidate_duration_ms=200000)
    assert score < 0.70
