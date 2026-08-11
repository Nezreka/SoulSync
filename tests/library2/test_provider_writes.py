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


class TestTheMirrorHandover:
    """Once a worker writes lib2, legacy stops being the authority for its fields.

    The mirror does not stop carrying them, though. Dropping them would lose a
    legacy value the lib2 twin never received on a row the ledger already calls
    matched — nothing would ever carry it across afterwards. So a migrated service
    is mirrored backfill-only: it fills what lib2 lacks and never overwrites what
    the worker wrote.
    """

    def test_its_columns_stay_declared(self):
        from core.library2.enrich import MIGRATED_SERVICES, enrichment_columns

        assert "lastfm" in MIGRATED_SERVICES
        assert [c for c in enrichment_columns("artist") if c.startswith("lastfm_")]

    def test_its_scalars_move_to_backfill_only(self):
        from core.library2.enrich import (
            MIRROR_SPECS, active_scalars, handover_scalars,
        )

        spec = MIRROR_SPECS["track"]
        assert "genius_lyrics" in {field[1] for field in handover_scalars(spec)}
        assert "genius_lyrics" not in {field[1] for field in active_scalars(spec)}

    def test_a_key_lib2_already_has_is_left_alone(self, conn):
        """The hazard the handover exists for: the drain must not push a stale
        legacy value over the worker's fresh native one."""
        from core.library2.enrich import _merge_json_column

        write_provider_enrichment(
            conn, entity_type="artist", entity_id=1, service="lastfm",
            payload={"bio": "fresh"})

        _merge_json_column(conn, "lib2_artists", 1, "enrichment",
                           {"lastfm": {"bio": "stale", "listeners": 5}})

        payload = json.loads(_row(conn, "lib2_artists")["enrichment"])["lastfm"]
        assert payload["bio"] == "fresh"
        assert payload["listeners"] == 5, "a key lib2 lacks still arrives"

    def test_an_unmigrated_service_still_overwrites(self, conn, monkeypatch):
        """Legacy stays authoritative where it is still the only writer, or a
        corrected value could never reach lib2.

        Every provider the mirror knows about has since migrated, so the handover is
        switched off here to keep the overwrite branch exercised rather than letting
        it rot.
        """
        import core.library2.enrich as enrich

        monkeypatch.setattr(enrich, "MIGRATED_SERVICES", frozenset())
        from core.library2.enrich import _merge_json_column

        write_provider_enrichment(
            conn, entity_type="artist", entity_id=1, service="deezer",
            provider_id="dz-old")

        _merge_json_column(conn, "lib2_artists", 1, "external_ids",
                           {"deezer": "dz-new"})

        assert json.loads(
            _row(conn, "lib2_artists")["external_ids"])["deezer"] == "dz-new"

    def test_the_trigger_still_watches_its_columns(self):
        """The resync reads them (to backfill), so the trigger has to fire on them
        — a column read but unwatched is the silent half of the declaration pair."""
        from core.library2.legacy_mirror import watched_columns

        assert [c for c in watched_columns("artists") if c.startswith("lastfm_")]
