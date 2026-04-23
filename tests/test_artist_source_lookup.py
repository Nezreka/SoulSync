"""Tests for the source-artist → library lookup helpers in
``core/artist_source_lookup.py``.

These exist to catch the class of bug we hit in April 2026 where the
watchlist-config enrichment query referenced a column name (``deezer_artist_id``)
that lived on ``watchlist_artists`` but NOT on ``artists``, producing a
``no such column`` error on every request.

The earlier version of this file AST-parsed ``web_server.py`` because the
logic lived inline there and could not be imported at test time. The logic
has since been extracted to a side-effect-free module, so we can just import
and call it directly.
"""

from __future__ import annotations

import pytest

from core.artist_source_lookup import (
    SOURCE_ID_FIELD,
    SOURCE_ONLY_ARTIST_SOURCES,
    find_library_artist_for_source,
)
from database.music_database import MusicDatabase


EXPECTED_SOURCE_ID_FIELD = {
    "spotify": "spotify_artist_id",
    "itunes": "itunes_artist_id",
    "deezer": "deezer_id",
    "discogs": "discogs_id",
    "hydrabase": "soul_id",
    "musicbrainz": "musicbrainz_id",
}


@pytest.fixture
def db(tmp_path):
    """Fresh MusicDatabase — runs all migrations so source-id columns exist."""
    return MusicDatabase(str(tmp_path / "music.db"))


def _insert_artist(db, *, artist_id, name, server_source="plex", **extra):
    """Insert a row into the artists table with the given extra columns."""
    cols = ["id", "name", "server_source"] + list(extra.keys())
    vals = [artist_id, name, server_source] + list(extra.values())
    placeholders = ",".join("?" for _ in cols)
    with db._get_connection() as conn:
        conn.execute(
            f"INSERT INTO artists ({','.join(cols)}) VALUES ({placeholders})",
            vals,
        )
        conn.commit()


# ===========================================================================
# Group A — SOURCE_ID_FIELD constants
# ===========================================================================

class TestSourceIdFieldMapping:
    """The mapping the lookup uses to join source artists back to the library
    ``artists`` table must stay in sync with this test's expectations AND with
    the real column names on the table."""

    def test_mapping_matches_expected(self):
        assert SOURCE_ID_FIELD == EXPECTED_SOURCE_ID_FIELD, (
            "SOURCE_ID_FIELD changed; update EXPECTED_SOURCE_ID_FIELD "
            "(and the test body) to match."
        )

    def test_source_only_set_matches_mapping_keys(self):
        """Sources eligible for the source-only fallback must all have a
        column to look them up by — otherwise the upgrade path silently
        returns None."""
        assert SOURCE_ONLY_ARTIST_SOURCES == frozenset(SOURCE_ID_FIELD.keys())

    def test_every_mapped_column_exists_on_artists_table(self, db):
        """Regression for the 2026-04 ``deezer_artist_id`` typo: every column
        referenced by SOURCE_ID_FIELD must exist on the ``artists`` table."""
        with db._get_connection() as conn:
            cursor = conn.execute("PRAGMA table_info(artists)")
            existing = {row[1] for row in cursor.fetchall()}

        missing = {
            source: column
            for source, column in SOURCE_ID_FIELD.items()
            if column not in existing
        }
        assert not missing, (
            "Columns declared in SOURCE_ID_FIELD are missing from the "
            f"artists table: {missing}. Available columns: {sorted(existing)}"
        )


# ===========================================================================
# Group B — find_library_artist_for_source behaviour
# ===========================================================================

class TestFindLibraryArtistForSource:
    """Behavioural tests against a real (in-memory) MusicDatabase."""

    @pytest.mark.parametrize("source,column", list(EXPECTED_SOURCE_ID_FIELD.items()))
    def test_lookup_by_source_id_column(self, db, source, column):
        source_value = f"{source}-test-artist-123"
        _insert_artist(
            db,
            artist_id=f"pk-{source}",
            name=f"{source.title()} Test Artist",
            **{column: source_value},
        )

        result = find_library_artist_for_source(
            db, source, source_value, artist_name=""
        )
        assert result == f"pk-{source}"

    def test_unknown_source_returns_none(self, db):
        assert find_library_artist_for_source(
            db, "made-up-source", "anything", artist_name="Anything"
        ) is None

    def test_lookup_misses_when_source_id_unknown(self, db):
        _insert_artist(db, artist_id="pk-real", name="Real Artist", deezer_id="dz-real")
        assert find_library_artist_for_source(
            db, "deezer", "dz-not-real", artist_name=""
        ) is None

    def test_name_fallback_matches_within_active_server(self, db):
        _insert_artist(db, artist_id="pk-a", name="Kendrick Lamar", server_source="plex")
        _insert_artist(db, artist_id="pk-b", name="KENDRICK LAMAR", server_source="jellyfin")

        result = find_library_artist_for_source(
            db, "deezer", "no-id-match", artist_name="kendrick lamar",
            active_server="plex",
        )
        assert result == "pk-a"

    def test_name_fallback_skips_other_servers(self, db):
        """Active-server scope is required so we don't jump the user across
        server contexts on a name collision."""
        _insert_artist(db, artist_id="pk-jelly", name="Taylor Swift", server_source="jellyfin")

        result = find_library_artist_for_source(
            db, "deezer", "no-id-match", artist_name="Taylor Swift",
            active_server="plex",
        )
        assert result is None

    def test_name_fallback_requires_active_server(self, db):
        """Without an active_server we shouldn't fall through to a global
        name match — too easy to land the user on the wrong record."""
        _insert_artist(db, artist_id="pk-x", name="Some Artist", server_source="plex")

        result = find_library_artist_for_source(
            db, "deezer", "no-id-match", artist_name="Some Artist",
            active_server=None,
        )
        assert result is None

    def test_id_match_wins_over_name_match(self, db):
        """If both a source-id match and a name match exist, the id match
        should take priority — it's the more reliable signal."""
        _insert_artist(
            db, artist_id="pk-id-match", name="Different Name",
            deezer_id="dz-shared", server_source="plex",
        )
        _insert_artist(
            db, artist_id="pk-name-match", name="The Searched Artist",
            server_source="plex",
        )

        result = find_library_artist_for_source(
            db, "deezer", "dz-shared", artist_name="The Searched Artist",
            active_server="plex",
        )
        assert result == "pk-id-match"


# ===========================================================================
# Group C — Watchlist-config enrichment query schema contract
# ===========================================================================

class TestWatchlistConfigEnrichmentQueries:
    """The watchlist-config GET (web_server.py ~line 42196) joins
    ``watchlist_artists`` against ``artists``. Both tables use different
    column names for the same external IDs (``deezer_id`` on artists,
    ``deezer_artist_id`` on watchlist_artists). The queries must use the
    correct column per table."""

    def test_artists_enrichment_query_executes(self, db):
        """Run the exact SELECT from web_server.py verbatim — must not raise
        ``no such column``."""
        with db._get_connection() as conn:
            conn.execute(
                """
                SELECT banner_url, summary, style, mood, label, genres
                FROM artists
                WHERE spotify_artist_id = ?
                   OR itunes_artist_id = ?
                   OR deezer_id = ?
                   OR discogs_id = ?
                LIMIT 1
                """,
                ("x", "x", "x", "x"),
            )

    def test_watchlist_join_query_executes(self, db):
        """The paired query hits ``watchlist_artists`` where the Deezer column
        is ``deezer_artist_id`` — confirm that shape works too."""
        with db._get_connection() as conn:
            conn.execute(
                """
                SELECT rr.album_name, rr.release_date, rr.album_cover_url, rr.track_count
                FROM recent_releases rr
                JOIN watchlist_artists wa ON rr.watchlist_artist_id = wa.id
                WHERE wa.spotify_artist_id = ?
                   OR wa.itunes_artist_id = ?
                   OR wa.deezer_artist_id = ?
                ORDER BY rr.release_date DESC
                LIMIT 6
                """,
                ("x", "x", "x"),
            )

    def test_artists_table_does_not_have_watchlist_column_names(self, db):
        """Document the schema split that caused the original bug: these
        suffixed names only exist on ``watchlist_artists``, never ``artists``."""
        with db._get_connection() as conn:
            cursor = conn.execute("PRAGMA table_info(artists)")
            artists_cols = {row[1] for row in cursor.fetchall()}

        assert "deezer_artist_id" not in artists_cols
        assert "discogs_artist_id" not in artists_cols
