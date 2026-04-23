"""Regression tests for the source-artist → library lookup path and the
source-only artist-detail response shape.

These tests exist to catch the class of bug we hit in April 2026 where the
watchlist-config enrichment query referenced a column name (``deezer_artist_id``)
that lived on ``watchlist_artists`` but NOT on ``artists``, producing a
``no such column`` error on every request.

``web_server.py`` cannot be imported at test time (it initialises Spotify,
Soulseek, Plex, etc.), so the ``_SOURCE_ID_FIELD`` map and the
``_build_source_only_artist_detail`` response contract are verified by reading
``web_server.py`` as source text and parsing the dict literal.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from database.music_database import MusicDatabase


_ROOT = Path(__file__).resolve().parent.parent
_WEB_SERVER = _ROOT / "web_server.py"


# ---------------------------------------------------------------------------
# Expected mapping — must match web_server.py::_SOURCE_ID_FIELD
# ---------------------------------------------------------------------------

EXPECTED_SOURCE_ID_FIELD = {
    "spotify": "spotify_artist_id",
    "itunes": "itunes_artist_id",
    "deezer": "deezer_id",
    "discogs": "discogs_id",
    "hydrabase": "soul_id",
    "musicbrainz": "musicbrainz_id",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_source_id_field_dict() -> dict[str, str]:
    """Parse web_server.py and return the _SOURCE_ID_FIELD dict literal."""
    source = _WEB_SERVER.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_SOURCE_ID_FIELD":
                    return ast.literal_eval(node.value)
    raise AssertionError("_SOURCE_ID_FIELD not found in web_server.py")


def _extract_function_source(fn_name: str) -> str:
    """Return the full source text of a top-level function in web_server.py."""
    source = _WEB_SERVER.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"function {fn_name!r} not found in web_server.py")


@pytest.fixture
def db(tmp_path):
    """Fresh MusicDatabase — runs all migrations so source-id columns exist."""
    return MusicDatabase(str(tmp_path / "music.db"))


def _insert_artist(db, *, artist_id: str, name: str, server_source: str = "plex", **extra):
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
# Group A — _SOURCE_ID_FIELD contract
# ===========================================================================

class TestSourceIdFieldMapping:
    """The mapping web_server.py uses to join source artists back to the
    library ``artists`` table must stay in sync with this test's expectations
    AND with the real column names on the table."""

    def test_mapping_matches_expected(self):
        actual = _extract_source_id_field_dict()
        assert actual == EXPECTED_SOURCE_ID_FIELD, (
            "web_server.py::_SOURCE_ID_FIELD changed; update "
            "EXPECTED_SOURCE_ID_FIELD (and the test body) to match."
        )

    def test_every_mapped_column_exists_on_artists_table(self, db):
        """Regression for the 2026-04 ``deezer_artist_id`` typo: every column
        referenced by _SOURCE_ID_FIELD must exist on the ``artists`` table."""
        with db._get_connection() as conn:
            cursor = conn.execute("PRAGMA table_info(artists)")
            existing = {row[1] for row in cursor.fetchall()}

        missing = {
            source: column
            for source, column in EXPECTED_SOURCE_ID_FIELD.items()
            if column not in existing
        }
        assert not missing, (
            "Columns declared in _SOURCE_ID_FIELD are missing from the "
            f"artists table: {missing}. Available columns: {sorted(existing)}"
        )


# ===========================================================================
# Group B — _find_library_artist_for_source lookup behaviour
# ===========================================================================

class TestLibraryArtistLookup:
    """Replicates the two queries in _find_library_artist_for_source so we
    catch column-name drift or schema regressions immediately."""

    @pytest.mark.parametrize("source,column", list(EXPECTED_SOURCE_ID_FIELD.items()))
    def test_lookup_by_source_id_column(self, db, source, column):
        source_value = f"{source}-test-artist-123"
        _insert_artist(
            db,
            artist_id=f"pk-{source}",
            name=f"{source.title()} Test Artist",
            **{column: source_value},
        )

        with db._get_connection() as conn:
            cursor = conn.execute(
                f"SELECT id, name FROM artists WHERE {column} = ? LIMIT 1",
                (source_value,),
            )
            row = cursor.fetchone()

        assert row is not None, (
            f"Lookup by {column} returned no row — schema/query mismatch?"
        )
        assert row[0] == f"pk-{source}"

    def test_lookup_misses_when_source_id_unknown(self, db):
        with db._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id FROM artists WHERE deezer_id = ? LIMIT 1",
                ("does-not-exist",),
            )
            assert cursor.fetchone() is None

    def test_name_fallback_matches_case_insensitively_within_server(self, db):
        _insert_artist(db, artist_id="pk-a", name="Kendrick Lamar", server_source="plex")
        _insert_artist(db, artist_id="pk-b", name="KENDRICK LAMAR", server_source="jellyfin")

        with db._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id FROM artists "
                "WHERE LOWER(name) = LOWER(?) AND server_source = ? LIMIT 1",
                ("kendrick lamar", "plex"),
            )
            row = cursor.fetchone()

        assert row is not None and row[0] == "pk-a"

    def test_name_fallback_respects_server_scope(self, db):
        """Only the active-server artist should match; the other server's copy
        is deliberately ignored to avoid cross-server context jumps."""
        _insert_artist(db, artist_id="pk-jelly", name="Taylor Swift", server_source="jellyfin")

        with db._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id FROM artists "
                "WHERE LOWER(name) = LOWER(?) AND server_source = ? LIMIT 1",
                ("Taylor Swift", "plex"),
            )
            assert cursor.fetchone() is None


# ===========================================================================
# Group C — Watchlist-config enrichment query schema contract
# ===========================================================================

class TestWatchlistConfigEnrichment:
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


# ===========================================================================
# Group D — _build_source_only_artist_detail response-shape contract
# ===========================================================================

class TestSourceOnlyArtistDetailContract:
    """Contract test for the JSON response produced by
    _build_source_only_artist_detail. We assert the function source contains
    the response-key identifiers we rely on from the frontend. If web_server
    drops or renames one, the test fires before the JS tries to read a
    ``undefined`` field."""

    @pytest.fixture(autouse=True)
    def _load_source(self):
        self.src = _extract_function_source("_build_source_only_artist_detail")

    @pytest.mark.parametrize("key", [
        # Top-level response shape
        "success",
        "artist",
        "discography",
        "enrichment_coverage",
        # artist_info fields
        "image_url",
        "server_source",
        "genres",
        # Last.fm enrichment
        "lastfm_bio",
        "lastfm_listeners",
        "lastfm_playcount",
        "lastfm_url",
    ])
    def test_function_references_response_key(self, key):
        assert f"'{key}'" in self.src or f'"{key}"' in self.src, (
            f"_build_source_only_artist_detail no longer references "
            f"response key {key!r}"
        )

    def test_function_sets_source_specific_id_field(self):
        """The function must look up _SOURCE_ID_FIELD and stamp the
        appropriate column name into artist_info so the correct service
        badge renders. Regression guard for a refactor that drops the
        dynamic assignment."""
        assert "_SOURCE_ID_FIELD" in self.src
        # Should assign via dict-style setattr on artist_info
        assert re.search(
            r"artist_info\[\s*source_id_field\s*\]\s*=\s*artist_id",
            self.src,
        ), "expected dynamic artist_info[source_id_field] = artist_id assignment"

    def test_function_disables_variant_dedup(self):
        """Source-only view must pass ``dedup_variants=False`` so every
        release surfaces — matching the prior inline Artists-page behaviour
        the user explicitly requested."""
        assert "dedup_variants=False" in self.src, (
            "_build_source_only_artist_detail must opt out of variant dedup"
        )
