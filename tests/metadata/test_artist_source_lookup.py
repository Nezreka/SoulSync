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
from core.library2.provider_ids import provider_id_sql
from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_artist


EXPECTED_SOURCE_ID_FIELD = {
    "spotify": "spotify_artist_id",
    "itunes": "itunes_artist_id",
    "deezer": "deezer_id",
    "discogs": "discogs_id",
    "hydrabase": "soul_id",
    "musicbrainz": "musicbrainz_id",
    "amazon": "amazon_id",
    "jiosaavn": "jiosaavn_id",
}


@pytest.fixture
def db(tmp_path):
    """Fresh MusicDatabase — runs all migrations so source-id columns exist."""
    return MusicDatabase(str(tmp_path / "music.db"))


def _insert_artist(db, *, artist_id, name, server_source="plex", source=None,
                   source_id=None):
    """A catalogue artist row, optionally carrying one source's id wherever
    the catalogue keeps it. Returns the row's catalogue id."""
    with db._get_connection() as conn:
        row_id = seed_artist(conn, server_id=artist_id, name=name,
                             server_source=server_source)
        if source:
            conn.execute(
                f"UPDATE lib2_artists SET {_write_target(source)} WHERE id = ?",
                (str(source_id), row_id),
            )
        conn.commit()
        return row_id


def _write_target(source):
    """The SET clause that stores ``source``'s id — a column of its own, or a
    key in the ``external_ids`` bucket."""
    expression = provider_id_sql(source)
    if expression.startswith("json_extract"):
        return f"external_ids = json_set(external_ids, '$.{source}', ?)"
    return f"{expression} = ?"


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

    def test_source_only_set_includes_all_library_lookup_sources(self):
        """Sources eligible for the source-only fallback must include every
        source that has a library ID column — plus any source-only providers
        (e.g. JioSaavn) that don't persist IDs in the library yet."""
        assert SOURCE_ID_FIELD.keys() <= SOURCE_ONLY_ARTIST_SOURCES

    def test_every_mapped_source_resolves_to_a_real_catalogue_location(self, db):
        """Regression for the 2026-04 ``deezer_artist_id`` typo, in v2 terms:
        every source in the map must name a place the catalogue actually keeps
        an id — a column of its own, or a key in ``external_ids``. A typo now
        surfaces as ``no such column`` on the very first lookup."""
        with db._get_connection() as conn:
            for source in SOURCE_ID_FIELD:
                expression = provider_id_sql(source)
                assert expression, source
                conn.execute(
                    f"SELECT id FROM lib2_artists WHERE {expression} = ? LIMIT 1",
                    ("probe",),
                ).fetchall()


# ===========================================================================
# Group B — find_library_artist_for_source behaviour
# ===========================================================================

class TestFindLibraryArtistForSource:
    """Behavioural tests against a real (in-memory) MusicDatabase."""

    @pytest.mark.parametrize("source", list(EXPECTED_SOURCE_ID_FIELD))
    def test_lookup_by_source_id(self, db, source):
        source_value = f"{source}-test-artist-123"
        row_id = _insert_artist(
            db,
            artist_id=f"pk-{source}",
            name=f"{source.title()} Test Artist",
            source=source, source_id=source_value,
        )

        result = find_library_artist_for_source(db, source, source_value)
        assert result == row_id

    def test_unknown_source_returns_none(self, db):
        assert find_library_artist_for_source(
            db, "made-up-source", "anything", artist_name="Anything"
        ) is None

    def test_lookup_misses_when_source_id_unknown(self, db):
        _insert_artist(db, artist_id="pk-real", name="Real Artist",
                       source="deezer", source_id="dz-real")
        assert find_library_artist_for_source(db, "deezer", "dz-not-real") is None

    def test_artist_name_is_optional(self, db):
        """Callers that don't have a name handy should be able to omit it
        without falling through to the name-fallback branch."""
        _insert_artist(db, artist_id="pk-q", name="Some Artist", server_source="plex")
        # No source-id match, no name passed → must return None even when
        # active_server is set (otherwise we'd risk matching by None name).
        assert find_library_artist_for_source(
            db, "deezer", "no-id-match", active_server="plex"
        ) is None

    def test_name_fallback_matches_within_active_server(self, db):
        plex = _insert_artist(db, artist_id="pk-a", name="Kendrick Lamar",
                              server_source="plex")
        _insert_artist(db, artist_id="pk-b", name="KENDRICK LAMAR",
                       server_source="jellyfin")

        result = find_library_artist_for_source(
            db, "deezer", "no-id-match", artist_name="kendrick lamar",
            active_server="plex",
        )
        assert result == plex

    def test_name_fallback_folds_beyond_ascii(self, db):
        """SQLite's LOWER() left "BJÖRK" and "Björk" as different artists."""
        row_id = _insert_artist(db, artist_id="pk-bjork", name="Björk",
                                server_source="plex")

        result = find_library_artist_for_source(
            db, "deezer", "no-id-match", artist_name="BJÖRK", active_server="plex",
        )
        assert result == row_id

    def test_name_fallback_uses_scoped_mapping_not_last_server_projection(self, db):
        row_id = _insert_artist(
            db, artist_id="j-id", name="Mapped Artist", server_source="jellyfin",
        )
        with db._get_connection() as conn:
            conn.execute(
                "INSERT INTO lib2_media_server_mappings "
                "(entity_type,entity_id,server_source,server_id) "
                "VALUES('artist',?,'plex','p-id')", (row_id,),
            )

        result = find_library_artist_for_source(
            db, "deezer", "no-id-match", artist_name="Mapped Artist",
            active_server="plex",
        )

        assert result == row_id

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

    def test_ambiguous_source_id_skips_id_upgrade(self, db):
        """Regression for the Kendrick/Jorja bug: when one Deezer id is
        stamped on several library artists (enrichment corruption), the id
        match is ambiguous and must NOT pick an arbitrary row — it returns
        None so the caller falls back to showing the source artist."""
        _insert_artist(db, artist_id="pk-kendrick", name="Kendrick Lamar",
                       source="deezer", source_id="525046", server_source="plex")
        _insert_artist(db, artist_id="pk-jorja", name="Jorja Smith",
                       source="deezer", source_id="525046", server_source="plex")
        _insert_artist(db, artist_id="pk-vince", name="Vince Staples",
                       source="deezer", source_id="525046", server_source="plex")

        # No name hint (the URL-driven path) → no id guess, no name fallback.
        assert find_library_artist_for_source(
            db, "deezer", "525046", active_server="plex"
        ) is None

    def test_ambiguous_source_id_still_allows_name_fallback(self, db):
        """An ambiguous id shouldn't block a correct name match when the
        caller does have the name."""
        kendrick = _insert_artist(db, artist_id="pk-kendrick", name="Kendrick Lamar",
                                  source="deezer", source_id="525046",
                                  server_source="plex")
        _insert_artist(db, artist_id="pk-jorja", name="Jorja Smith",
                       source="deezer", source_id="525046", server_source="plex")

        result = find_library_artist_for_source(
            db, "deezer", "525046", artist_name="Kendrick Lamar",
            active_server="plex",
        )
        assert result == kendrick

    def test_unique_source_id_still_matches(self, db):
        """Positive control: a non-duplicated id still upgrades as before."""
        solo = _insert_artist(db, artist_id="pk-solo", name="Solo Artist",
                              source="deezer", source_id="999999",
                              server_source="plex")
        assert find_library_artist_for_source(db, "deezer", "999999") == solo

    def test_id_match_wins_over_name_match(self, db):
        """If both a source-id match and a name match exist, the id match
        should take priority — it's the more reliable signal."""
        by_id = _insert_artist(
            db, artist_id="pk-id-match", name="Different Name",
            source="deezer", source_id="dz-shared", server_source="plex",
        )
        _insert_artist(
            db, artist_id="pk-name-match", name="The Searched Artist",
            server_source="plex",
        )

        result = find_library_artist_for_source(
            db, "deezer", "dz-shared", artist_name="The Searched Artist",
            active_server="plex",
        )
        assert result == by_id


# ===========================================================================
# Group C — Watchlist-config enrichment query schema contract
# ===========================================================================

class TestWatchlistConfigEnrichmentQueries:
    """The watchlist-config GET joins ``watchlist_artists`` against the
    catalogue. The two speak different vocabularies for the same external IDs
    (``deezer_artist_id`` on watchlist_artists; a key in ``external_ids`` on
    the catalogue). Each query must use its own table's."""

    def test_artists_enrichment_query_executes(self, db):
        """Run the exact SELECT from web_server.py verbatim — must not raise
        ``no such column``."""
        with db._get_connection() as conn:
            conn.execute(
                """
                SELECT banner_url, summary, style, mood, label, genres
                FROM lib2_artists
                WHERE spotify_id = ?
                   OR json_extract(external_ids, '$.itunes') = ?
                   OR json_extract(external_ids, '$.deezer') = ?
                   OR json_extract(external_ids, '$.discogs') = ?
                   OR musicbrainz_id = ?
                LIMIT 1
                """,
                ("x", "x", "x", "x", "x"),
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

    def test_catalogue_does_not_have_watchlist_column_names(self, db):
        """Document the schema split that caused the original bug: these
        suffixed names only exist on ``watchlist_artists``. The catalogue has
        no per-provider columns at all beyond Spotify and MusicBrainz."""
        with db._get_connection() as conn:
            cursor = conn.execute("PRAGMA table_info(lib2_artists)")
            catalogue_cols = {row[1] for row in cursor.fetchall()}

        assert "deezer_artist_id" not in catalogue_cols
        assert "discogs_artist_id" not in catalogue_cols
        assert "deezer_id" not in catalogue_cols
