"""Writing a provider's result straight onto a lib2 row (docs §32.3.1 stage 2).

A worker that has moved off legacy writes here instead. The shape is not a new
invention: it is exactly what the mirror would have produced from an equivalent
legacy row, because the mirror's declaration is the contract for what a lib2 row
looks like. A worker inventing its own layout would make its own output show up
as divergence in the integrity report.

The second half is the handover. Once a worker writes lib2 directly, legacy is no
longer the only source of truth for its fields, and the one-way mirror promise
(§32.3.1 promise 1) turns from a safeguard into a hazard: the next drain would
push a stale legacy value back over the fresh native one. So a migrated service
leaves the mirror in the same change that moves its worker.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.provider_writes import write_provider_enrichment
from core.library2.schema import ensure_library_v2_schema


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "lib2.db"))
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    c.execute("INSERT INTO lib2_artists(name, sort_name) VALUES('A','A')")
    c.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
        "VALUES(1,'Album','album')")
    c.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(1,'Track')")
    c.commit()
    yield c
    c.close()


def _row(conn, table, entity_id=1):
    return conn.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()


class TestTheEnrichmentBucket:
    def test_the_payload_lands_under_its_service_key(self, conn):
        write_provider_enrichment(
            conn, entity_type="artist", entity_id=1, service="lastfm",
            payload={"listeners": 42, "bio": "A band."})

        payload = json.loads(_row(conn, "lib2_artists")["enrichment"])
        assert payload == {"lastfm": {"listeners": 42, "bio": "A band."}}

    def test_another_service_is_not_disturbed(self, conn):
        write_provider_enrichment(
            conn, entity_type="artist", entity_id=1, service="genius",
            payload={"description": "d"})
        write_provider_enrichment(
            conn, entity_type="artist", entity_id=1, service="lastfm",
            payload={"listeners": 1})

        payload = json.loads(_row(conn, "lib2_artists")["enrichment"])
        assert set(payload) == {"genius", "lastfm"}

    def test_empty_values_are_dropped_rather_than_stored_as_null(self, conn):
        """The mirror never stores an empty key, so neither may a native write —
        otherwise the same data looks different depending on who wrote it."""
        write_provider_enrichment(
            conn, entity_type="artist", entity_id=1, service="lastfm",
            payload={"listeners": 5, "bio": None, "tags": [], "url": ""})

        payload = json.loads(_row(conn, "lib2_artists")["enrichment"])
        assert payload == {"lastfm": {"listeners": 5}}

    def test_a_rewrite_replaces_the_services_own_keys(self, conn):
        write_provider_enrichment(
            conn, entity_type="artist", entity_id=1, service="lastfm",
            payload={"listeners": 1, "bio": "old"})
        write_provider_enrichment(
            conn, entity_type="artist", entity_id=1, service="lastfm",
            payload={"listeners": 2})

        payload = json.loads(_row(conn, "lib2_artists")["enrichment"])
        assert payload["lastfm"]["listeners"] == 2
        assert payload["lastfm"]["bio"] == "old", "keys not in this answer survive"

    def test_albums_and_tracks_work_the_same(self, conn):
        write_provider_enrichment(
            conn, entity_type="album", entity_id=1, service="lastfm",
            payload={"wiki": "w"})
        write_provider_enrichment(
            conn, entity_type="track", entity_id=1, service="lastfm",
            payload={"playcount": 9})

        assert json.loads(_row(conn, "lib2_albums")["enrichment"])["lastfm"]["wiki"] == "w"
        assert json.loads(_row(conn, "lib2_tracks")["enrichment"])["lastfm"]["playcount"] == 9


class TestProviderIdentity:
    def test_the_provider_id_reaches_external_ids(self, conn):
        write_provider_enrichment(
            conn, entity_type="artist", entity_id=1, service="lastfm",
            payload={"listeners": 1}, provider_id="https://last.fm/music/A")

        ids = json.loads(_row(conn, "lib2_artists")["external_ids"])
        assert ids["lastfm"] == "https://last.fm/music/A"

    def test_a_promoted_column_moves_with_it(self, conn):
        """lib2 stores Spotify and MusicBrainz twice — read paths join on the
        column, so the JSON alone would leave them behind."""
        write_provider_enrichment(
            conn, entity_type="artist", entity_id=1, service="spotify",
            payload={}, provider_id="sp-1")

        row = _row(conn, "lib2_artists")
        assert row["spotify_id"] == "sp-1"
        assert json.loads(row["external_ids"])["spotify"] == "sp-1"


class TestBackfillOnlyWhenEmpty:
    def test_an_empty_column_is_filled(self, conn):
        write_provider_enrichment(
            conn, entity_type="artist", entity_id=1, service="lastfm",
            payload={}, backfill={"image_url": "http://img", "style": "trip hop"})

        row = _row(conn, "lib2_artists")
        assert row["image_url"] == "http://img"
        assert row["style"] == "trip hop"

    def test_an_existing_value_is_never_overwritten(self, conn):
        """Last.fm's image is a fallback, not an authority. The legacy worker
        only ever backfilled, and a provider that starts overwriting artwork the
        user or a better source chose is a regression."""
        conn.execute("UPDATE lib2_artists SET image_url='http://chosen' WHERE id=1")
        conn.commit()

        write_provider_enrichment(
            conn, entity_type="artist", entity_id=1, service="lastfm",
            payload={}, backfill={"image_url": "http://lastfm"})

        assert _row(conn, "lib2_artists")["image_url"] == "http://chosen"

    def test_an_empty_json_list_counts_as_empty(self, conn):
        write_provider_enrichment(
            conn, entity_type="album", entity_id=1, service="lastfm",
            payload={}, backfill={"genres": json.dumps(["trip hop"])})

        assert json.loads(_row(conn, "lib2_albums")["genres"]) == ["trip hop"]

    def test_an_unknown_column_is_refused(self, conn):
        with pytest.raises(ValueError):
            write_provider_enrichment(
                conn, entity_type="artist", entity_id=1, service="lastfm",
                payload={}, backfill={"nonexistent": "x"})


class TestOutrightColumnWrites:
    """Some fields are not fallbacks. Genius lyrics replace what is there: a
    fresh fetch is the newer truth, and backfill-only would freeze the first
    version ever stored."""

    def test_a_named_column_is_written_even_when_it_already_has_a_value(self, conn):
        conn.execute("UPDATE lib2_tracks SET genius_lyrics='old words' WHERE id=1")
        conn.commit()

        write_provider_enrichment(
            conn, entity_type="track", entity_id=1, service="genius",
            payload={}, columns={"genius_lyrics": "new words"})

        assert _row(conn, "lib2_tracks")["genius_lyrics"] == "new words"

    def test_a_none_value_does_not_erase_what_is_there(self, conn):
        """A lyrics fetch that failed must not blank lyrics we already have."""
        conn.execute("UPDATE lib2_tracks SET genius_lyrics='keep me' WHERE id=1")
        conn.commit()

        write_provider_enrichment(
            conn, entity_type="track", entity_id=1, service="genius",
            payload={}, columns={"genius_lyrics": None})

        assert _row(conn, "lib2_tracks")["genius_lyrics"] == "keep me"

    def test_an_unknown_column_is_refused(self, conn):
        with pytest.raises(ValueError):
            write_provider_enrichment(
                conn, entity_type="track", entity_id=1, service="genius",
                payload={}, columns={"nope": "x"})
