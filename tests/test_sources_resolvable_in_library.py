"""sources_resolvable_in_library(): which watchlist ids land on a library artist.

Second half of the watchlist "provider is unavailable" fix. The panel's
discography link is safe on a source when the provider is alive OR when its id
resolves to a library artist by the ID COLUMN alone — the artist-detail route
upgrades to the library view off that column with no provider call, and its
name-retry fallback needs the (dead) provider's client, so ids that only match
by name or match ambiguously genuinely 503 and must NOT be vouched for.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest

from core.artist_source_lookup import sources_resolvable_in_library
from core.library2.schema import ensure_library_v2_schema


class _FakeDb:
    """The one seam find_library_artist_for_source uses: _get_connection()."""

    def __init__(self, path):
        self._path = str(path)

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self._path)
        try:
            yield conn
        finally:
            conn.close()


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "music.db"
    conn = sqlite3.connect(str(path))
    ensure_library_v2_schema(conn)
    conn.executemany(
        "INSERT INTO lib2_artists"
        " (name, name_key, server_source, spotify_id, external_ids)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            ("Owned Artist", "owned artist", "plex", "sp-owned", "{}"),
            # One Deezer id smeared onto two rows — the enrichment-corruption
            # shape. The route refuses to upgrade on an ambiguous id, so the
            # helper must refuse to vouch for it too.
            ("Smeared A", "smeared a", "plex", None, '{"deezer":"dz-dupe"}'),
            ("Smeared B", "smeared b", "plex", None, '{"deezer":"dz-dupe"}'),
        ],
    )
    conn.commit()
    conn.close()
    return _FakeDb(path)


def test_unique_id_match_is_vouched_for(db):
    assert sources_resolvable_in_library(db, {"spotify": "sp-owned"}) == ["spotify"]


def test_unmatched_and_blank_ids_are_not(db):
    assert sources_resolvable_in_library(
        db, {"spotify": "sp-elsewhere", "deezer": None, "itunes": ""}
    ) == []


def test_ambiguous_id_is_not_vouched_for(db):
    # The route falls through to the name retry on ambiguity, and that retry
    # needs the provider's client — exactly what a dead provider lacks.
    assert sources_resolvable_in_library(db, {"deezer": "dz-dupe"}) == []


def test_mixed_map_keeps_only_real_hits(db):
    assert sources_resolvable_in_library(
        db, {"spotify": "sp-owned", "deezer": "dz-dupe", "musicbrainz": "mb-x"}
    ) == ["spotify"]


def test_empty_and_none_maps(db):
    assert sources_resolvable_in_library(db, {}) == []
    assert sources_resolvable_in_library(db, None) == []
