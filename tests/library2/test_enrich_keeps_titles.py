"""a background enrich fills a track's title and length, it doesn't rename it.

3.6.1's enrichment workers never touched a track's or an album's title. Library
v2's per-service enrich (the daily sweep, a watchlist add, the Enrich button)
wrote the provider's title over the row every time: "Come Together" became
"Come Together - Remastered 2009", and with providers taking turns it could
flip between spellings run to run. Full Retag still takes the provider's
values, that's what it is for.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from core.library2.native_enrich import _apply_descriptive_metadata


def _conn(title, duration):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE lib2_tracks (id INTEGER PRIMARY KEY, title TEXT, duration INTEGER, "
        "bpm REAL, explicit INTEGER, genius_lyrics TEXT, copyright TEXT, style TEXT, "
        "mood TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO lib2_tracks (id, title, duration) VALUES (1, ?, ?)", (title, duration))
    return conn


def _provider():
    return SimpleNamespace(title="Come Together - Remastered 2009", duration_ms=259000,
                           bpm=82.0, explicit=None, lyrics=None, copyright=None,
                           style=None, mood=None, image_url=None)


def _row(conn):
    return conn.execute("SELECT title, duration, bpm FROM lib2_tracks WHERE id=1").fetchone()


def test_a_background_enrich_keeps_the_rows_title_and_length():
    conn = _conn("Come Together", 258000)
    _apply_descriptive_metadata(conn, "track", 1, _provider(), fill_identity_only=True)
    assert _row(conn) == ("Come Together", 258000, 82.0)


def test_a_background_enrich_fills_a_row_with_no_title_or_length():
    conn = _conn("", None)
    _apply_descriptive_metadata(conn, "track", 1, _provider(), fill_identity_only=True)
    assert _row(conn) == ("Come Together - Remastered 2009", 259000, 82.0)


def test_full_retag_still_takes_the_providers_values():
    conn = _conn("Come Together", 258000)
    _apply_descriptive_metadata(conn, "track", 1, _provider())
    assert _row(conn) == ("Come Together - Remastered 2009", 259000, 82.0)
