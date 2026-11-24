"""Regression tests for #2257 (canonical MB artist name & alias projection),
#2258 (never write artist name as a genre), and #2259 (release-level album_artist)."""

from __future__ import annotations

import sqlite3

from core.genre_filter import filter_genres, strip_artist_name_genres
from core.library2.metadata_overrides import effective_artist_names
from core.metadata.musicbrainz_tags import credit_tags


def test_artist_name_is_never_applied_as_genre_2258():
    raw = ["2 Chainz", "Hip-Hop", "Trap"]
    assert strip_artist_name_genres(raw, ["2 Chainz"]) == ["Hip-Hop", "Trap"]
    # Works even when strict whitelist is off (config_manager=None)
    assert filter_genres(raw, None, artist_names=["2 Chainz", "2 Chainz feat. Drake"]) == ["Hip-Hop", "Trap"]
    # Case & punctuation insensitive
    assert strip_artist_name_genres(["2-chainz", "Southern Hip Hop"], ["2 Chainz"]) == ["Southern Hip Hop"]


def test_musicbrainz_credit_tags_prefer_canonical_artist_name_2257():
    credits = [
        {
            "name": "16 Bit Lolita's",
            "artist": {
                "id": "11111111-1111-1111-1111-111111111111",
                "name": "16BL",
                "sort-name": "16BL",
            },
        }
    ]
    tags = credit_tags(credits, album=False)
    assert tags["ARTISTS"] == ["16BL"]


def test_effective_artist_names_follows_canonical_artist_id_2257():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE lib2_artists (id INTEGER PRIMARY KEY, name TEXT, canonical_artist_id INTEGER)")
    conn.execute("INSERT INTO lib2_artists VALUES (1, 'GIMS', NULL)")
    conn.execute("INSERT INTO lib2_artists VALUES (2, 'Maître Gims', 1)")
    names = effective_artist_names(conn, [1, 2])
    assert names[1] == "GIMS"
    assert names[2] == "GIMS"
