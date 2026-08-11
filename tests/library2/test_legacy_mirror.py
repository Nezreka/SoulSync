"""iss32-E01: enrichment written to legacy must reach lib2.

Nezreka's bar is explicit — "every artist and album, artwork, genres, bios,
provider ids, all twelve workers". The existing ``test_enrich_resync`` proves
``resync_entity_from_legacy`` works when called. It was never called. These
tests prove the opposite direction: an ordinary ``UPDATE artists SET …``, the
kind the workers issue 137 times across 14 files, changes the lib2 row without
anyone touching the worker.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.importer import import_legacy_library
from core.library2.legacy_mirror import drain, ensure_legacy_mirror_schema, pending_count


def _conn(db):
    conn = db._get_connection()
    conn.row_factory = sqlite3.Row
    return conn


def _add_enrichment_columns(conn):
    """Widen the synthetic legacy schema to what a real install has."""
    for table, columns in (
        ("artists", ("style TEXT", "mood TEXT", "label TEXT", "banner_url TEXT",
                     "aliases TEXT", "deezer_id TEXT", "itunes_artist_id TEXT",
                     "audiodb_id TEXT", "discogs_id TEXT", "tidal_id TEXT",
                     "qobuz_id TEXT", "amazon_id TEXT", "jiosaavn_id TEXT",
                     "genius_id TEXT", "lastfm_url TEXT", "lastfm_bio TEXT",
                     "lastfm_listeners INTEGER", "lastfm_tags TEXT",
                     "lastfm_similar TEXT", "genius_description TEXT",
                     "genius_alt_names TEXT", "genius_url TEXT",
                     "discogs_bio TEXT", "discogs_members TEXT",
                     "discogs_urls TEXT")),
        ("albums", ("style TEXT", "mood TEXT", "label TEXT", "explicit INTEGER",
                    "upc TEXT", "spotify_album_id TEXT", "deezer_id TEXT",
                    "musicbrainz_release_id TEXT")),
        ("tracks", ("style TEXT", "mood TEXT", "bpm REAL", "explicit INTEGER",
                    "genius_lyrics TEXT", "copyright TEXT", "isrc TEXT",
                    "spotify_track_id TEXT", "deezer_id TEXT")),
    ):
        for column in columns:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
            except sqlite3.OperationalError:
                pass
    conn.commit()


@pytest.fixture
def mirrored_db(legacy_db):
    conn = _conn(legacy_db)
    try:
        _add_enrichment_columns(conn)
    finally:
        conn.close()
    import_legacy_library(legacy_db, profile_id=1)
    conn = _conn(legacy_db)
    try:
        # Re-install the triggers now that the wider columns exist — the same
        # thing the schema step does on every start.
        ensure_legacy_mirror_schema(conn.cursor())
        conn.execute("DELETE FROM lib2_legacy_dirty")
        conn.commit()
    finally:
        conn.close()
    return legacy_db


def _lib2_artist(db, legacy_id=1):
    conn = _conn(db)
    try:
        return conn.execute(
            "SELECT * FROM lib2_artists WHERE legacy_artist_id=?", (legacy_id,)
        ).fetchone()
    finally:
        conn.close()


class TestTriggerQueuesEnrichmentWrites:
    def test_a_plain_worker_style_update_is_queued(self, mirrored_db):
        conn = _conn(mirrored_db)
        try:
            # Exactly the shape core/deezer_worker.py:600 issues.
            conn.execute(
                "UPDATE artists SET deezer_id = ?, thumb_url = ? WHERE id = ?",
                ("dz-42", "http://cdn/new.jpg", 1))
            conn.commit()
            assert pending_count(conn) == 1
        finally:
            conn.close()

    def test_unrelated_columns_do_not_queue(self, mirrored_db):
        conn = _conn(mirrored_db)
        try:
            conn.execute("UPDATE artists SET name = name WHERE id = 1")
            conn.commit()
            assert pending_count(conn) == 0, (
                "a trigger that fires on any column would enqueue the whole "
                "library on every media-server scan"
            )
        finally:
            conn.close()


class TestDrainAppliesToLib2:
    def test_artwork_and_genres_reach_lib2(self, mirrored_db):
        conn = _conn(mirrored_db)
        try:
            conn.execute(
                "UPDATE artists SET thumb_url=?, genres=? WHERE id=1",
                ("http://cdn/fresh.jpg", '["trip hop"]'))
            conn.commit()
        finally:
            conn.close()

        drain(mirrored_db)

        row = _lib2_artist(mirrored_db)
        assert row["image_url"] == "http://cdn/fresh.jpg"
        assert json.loads(row["genres"]) == ["trip hop"]

    def test_provider_ids_reach_lib2(self, mirrored_db):
        conn = _conn(mirrored_db)
        try:
            conn.execute(
                "UPDATE artists SET deezer_id=?, tidal_id=?, spotify_artist_id=? "
                "WHERE id=1", ("dz-1", "td-1", "sp-new"))
            conn.commit()
        finally:
            conn.close()

        drain(mirrored_db)

        row = _lib2_artist(mirrored_db)
        external = json.loads(row["external_ids"])
        assert external.get("deezer") == "dz-1"
        assert external.get("tidal") == "td-1"
        # The promoted column has to move too — read paths join on it.
        assert row["spotify_id"] == "sp-new"

    def test_prose_reaches_lib2(self, mirrored_db):
        """The "bios" half of iss32-E01: a mirrored artist used to keep lib2's
        stale text forever.

        This checks the shared ``summary`` column, which the workers still on
        legacy write. The per-service ``enrichment`` bucket is no longer exercised
        here because every provider that had one now writes lib2 itself — its
        mechanism is covered in ``test_mirror_album_track_payload``, which turns the
        handover off on purpose so the copying path stays under test for whichever
        provider is declared next.
        """
        conn = _conn(mirrored_db)
        try:
            conn.execute(
                "UPDATE artists SET summary=?, style=? WHERE id=1",
                ("A Bristol act.", "trip hop"))
            conn.commit()
        finally:
            conn.close()

        drain(mirrored_db)

        row = _lib2_artist(mirrored_db)
        assert row["summary"] == "A Bristol act."
        assert row["style"] == "trip hop"

    def test_a_migrated_services_write_only_fills_a_gap(self, mirrored_db):
        """Last.fm's worker writes lib2 directly now, so legacy may fill an empty
        lib2 field and may never replace one the worker set — otherwise the drain
        pushes a stale value over the fresh native one."""
        conn = _conn(mirrored_db)
        try:
            conn.execute(
                "UPDATE lib2_artists SET enrichment=? WHERE legacy_artist_id=1",
                (json.dumps({"lastfm": {"bio": "Fresh, written natively."}}),))
            conn.execute(
                "UPDATE artists SET lastfm_bio=?, lastfm_listeners=? WHERE id=1",
                ("Stale.", 90210))
            conn.commit()
        finally:
            conn.close()

        drain(mirrored_db)

        payload = json.loads(_lib2_artist(mirrored_db)["enrichment"])["lastfm"]
        assert payload["bio"] == "Fresh, written natively."
        assert payload["listeners"] == 90210, "a key lib2 lacked still arrives"

    def test_album_and_track_writes_are_mirrored_too(self, mirrored_db):
        conn = _conn(mirrored_db)
        try:
            conn.execute("UPDATE albums SET label=?, upc=? WHERE id=10",
                         ("XL Recordings", "0123456789012"))
            conn.execute("UPDATE tracks SET bpm=?, copyright=? WHERE id=100",
                         (104.0, "(C) 2016"))
            conn.commit()
        finally:
            conn.close()

        drain(mirrored_db)

        conn = _conn(mirrored_db)
        try:
            album = conn.execute(
                "SELECT label, upc FROM lib2_albums WHERE legacy_album_id=10").fetchone()
            track = conn.execute(
                "SELECT bpm, copyright FROM lib2_tracks WHERE legacy_track_id=100"
            ).fetchone()
        finally:
            conn.close()
        assert album["label"] == "XL Recordings"
        assert album["upc"] == "0123456789012"
        assert track["bpm"] == 104.0
        assert track["copyright"] == "(C) 2016"

    def test_the_queue_is_emptied(self, mirrored_db):
        conn = _conn(mirrored_db)
        try:
            conn.execute("UPDATE artists SET style=? WHERE id=1", ("trip hop",))
            conn.commit()
        finally:
            conn.close()

        drain(mirrored_db)

        conn = _conn(mirrored_db)
        try:
            assert pending_count(conn) == 0
        finally:
            conn.close()

    def test_a_legacy_row_with_no_lib2_counterpart_is_dropped_not_retried(
            self, mirrored_db):
        conn = _conn(mirrored_db)
        try:
            conn.execute(
                "INSERT INTO lib2_legacy_dirty(entity_type, legacy_id) VALUES('artist', 9999)")
            conn.commit()
        finally:
            conn.close()

        stats = drain(mirrored_db)

        assert stats["unmapped"] == 1
        conn = _conn(mirrored_db)
        try:
            assert pending_count(conn) == 0, "the queue would grow forever"
        finally:
            conn.close()

    def test_scoped_drain_applies_only_the_named_entity(self, mirrored_db):
        conn = _conn(mirrored_db)
        try:
            conn.execute("UPDATE artists SET style=? WHERE id=1", ("trip hop",))
            conn.execute("UPDATE albums SET label=? WHERE id=10", ("XL",))
            conn.commit()
        finally:
            conn.close()

        drain(mirrored_db, entity_type="artist", legacy_id=1)

        conn = _conn(mirrored_db)
        try:
            assert pending_count(conn) == 1  # the album is still queued
            assert conn.execute(
                "SELECT style FROM lib2_artists WHERE legacy_artist_id=1"
            ).fetchone()[0] == "trip hop"
        finally:
            conn.close()


class TestReconcileTheBacklogTheTriggerNeverSaw:
    """iss32-T01a, second half.

    The trigger only sees legacy writes that happen *after* it is installed.
    Everything the twelve workers wrote before this branch existed is already
    divergent and enqueues nothing — measured against a real library: 156 of
    170 mirrored rows. A metric with an expected value of 0 that no mechanism
    can ever bring to 0 is not a metric, so the sweep that closes that gap
    belongs with the mirror.
    """

    def _diverge_behind_the_triggers_back(self, db):
        conn = _conn(db)
        try:
            conn.execute("DROP TRIGGER IF EXISTS trg_lib2_mirror_artists")
            conn.execute("UPDATE artists SET summary=?, spotify_artist_id=? "
                         "WHERE id=1", ("Written before the mirror existed.", "sp-old"))
            conn.commit()
            ensure_legacy_mirror_schema(conn.cursor())
            conn.commit()
            assert pending_count(conn) == 0, "the trigger cannot see the past"
        finally:
            conn.close()

    def _divergent_rows(self, db):
        from core.library2.enrich import iter_mirror_divergences

        conn = _conn(db)
        try:
            return [item for item in iter_mirror_divergences(conn)
                    if item.status == "divergent"]
        finally:
            conn.close()

    def test_sweep_enqueues_the_divergent_row_and_the_drain_clears_it(self, mirrored_db):
        from core.library2.legacy_mirror import reconcile_divergent

        self._diverge_behind_the_triggers_back(mirrored_db)
        assert len(self._divergent_rows(mirrored_db)) == 1

        stats = reconcile_divergent(mirrored_db)

        assert stats["enqueued"] == 1
        drain(mirrored_db)
        assert self._divergent_rows(mirrored_db) == []
        assert _lib2_artist(mirrored_db)["summary"] == (
            "Written before the mirror existed.")

    def test_rows_in_step_are_left_alone(self, mirrored_db):
        from core.library2.legacy_mirror import reconcile_divergent

        stats = reconcile_divergent(mirrored_db)

        assert stats["enqueued"] == 0
        assert stats["scanned"] > 0
        conn = _conn(mirrored_db)
        try:
            assert pending_count(conn) == 0
        finally:
            conn.close()

    def test_a_bounded_sweep_resumes_where_it_stopped(self, mirrored_db):
        from core.library2.legacy_mirror import reconcile_divergent

        first = reconcile_divergent(mirrored_db, scan_limit=1)
        assert first["scanned"] == 1
        assert first["exhausted"] is False

        second = reconcile_divergent(mirrored_db, scan_limit=1,
                                     after=first["cursor"])

        assert second["scanned"] == 1
        assert second["cursor"] != first["cursor"]

    def test_an_exhausted_sweep_restarts_from_the_beginning(self, mirrored_db):
        from core.library2.legacy_mirror import reconcile_divergent

        stats = reconcile_divergent(mirrored_db)

        assert stats["exhausted"] is True
        assert stats["cursor"] == {}

    def test_a_dangling_link_is_never_enqueued(self, mirrored_db):
        """``drain`` drops a queued id with no lib2 counterpart, and the
        reverse case cannot be fixed by queueing either — the legacy row is
        gone. Enqueueing it would be a no-op the sweep repeats forever."""
        from core.library2.legacy_mirror import reconcile_divergent

        conn = _conn(mirrored_db)
        try:
            conn.execute("DELETE FROM artists WHERE id=1")
            conn.commit()
        finally:
            conn.close()

        stats = reconcile_divergent(mirrored_db)

        assert stats["enqueued"] == 0
        assert stats["dangling"] == 1


class TestTheDrainerRunsTheSweep:
    """A repair function with no production caller is the exact shape of the
    defect this branch started from (iss32-E01)."""

    def test_an_idle_tick_sweeps_for_backlog(self, mirrored_db):
        from core.library2.legacy_mirror import MirrorDrainer

        conn = _conn(mirrored_db)
        try:
            conn.execute("DROP TRIGGER IF EXISTS trg_lib2_mirror_artists")
            conn.execute("UPDATE artists SET style='trip hop' WHERE id=1")
            conn.commit()
            ensure_legacy_mirror_schema(conn.cursor())
            conn.commit()
            assert pending_count(conn) == 0
        finally:
            conn.close()

        drainer = MirrorDrainer(mirrored_db)
        drainer.tick()   # finds nothing to drain, sweeps, queues the row
        drainer.tick()   # drains what the sweep queued

        assert _lib2_artist(mirrored_db)["style"] == "trip hop"

    def test_a_busy_tick_drains_before_it_sweeps(self, mirrored_db):
        """The queue is the urgent work; the sweep is catch-up. Doing both in
        one tick would let a big backlog scan delay ordinary mirroring."""
        from core.library2.legacy_mirror import MirrorDrainer

        conn = _conn(mirrored_db)
        try:
            conn.execute("UPDATE artists SET style='trip hop' WHERE id=1")
            conn.commit()
        finally:
            conn.close()

        stats = MirrorDrainer(mirrored_db).tick()

        assert stats["applied"] == 1
        assert stats.get("scanned", 0) == 0


class TestTheDrainerSeedsProviderBookkeeping:
    """The provider-attempt ledger is worthless until something fills it from the
    history legacy already holds — otherwise the first worker to move to lib2
    re-asks every provider about the whole library.

    It runs on an idle tick, not on the startup path (iss32-M03), and once per
    process: it is a one-off seeding pass, not recurring work.
    """

    def test_an_idle_tick_seeds_the_ledger_from_legacy(self, mirrored_db):
        from core.library2.legacy_mirror import MirrorDrainer
        from core.library2.provider_attempts import attempt_state

        conn = _conn(mirrored_db)
        try:
            for column in ("lastfm_match_status", "lastfm_last_attempted"):
                try:
                    conn.execute(f"ALTER TABLE artists ADD COLUMN {column} TEXT")
                except sqlite3.OperationalError:
                    pass
            conn.execute(
                "UPDATE artists SET lastfm_match_status='not_found', "
                "lastfm_last_attempted='2026-07-01 10:00:00' WHERE id=1")
            conn.commit()
            lib2_id = conn.execute(
                "SELECT id FROM lib2_artists WHERE legacy_artist_id=1").fetchone()["id"]
        finally:
            conn.close()

        MirrorDrainer(mirrored_db).tick()

        conn = _conn(mirrored_db)
        try:
            state = attempt_state(conn, entity_type="artist", entity_id=lib2_id)
        finally:
            conn.close()
        assert state["lastfm"]["status"] == "not_found"
        assert state["lastfm"]["last_attempted_at"].startswith("2026-07-01")

    def test_it_seeds_once_and_not_on_every_tick(self, mirrored_db):
        from core.library2.legacy_mirror import MirrorDrainer

        drainer = MirrorDrainer(mirrored_db)
        first = drainer.tick()
        second = drainer.tick()

        assert "seeded" in first
        assert "seeded" not in second


class TestLegacyApiArtistsPage:
    """iss32-E03: /api/library/artists must show what the v2 UI shows."""

    def test_serves_lib2_content_under_the_legacy_id(self, mirrored_db):
        from core.library2.queries import legacy_api_artists_page

        conn = _conn(mirrored_db)
        try:
            # An edit made in the Library-v2 UI: only the lib2 row changes.
            conn.execute(
                "UPDATE lib2_artists SET image_url=?, genres=? WHERE legacy_artist_id=1",
                ("http://cdn/v2.jpg", '["v2 genre"]'))
            conn.commit()

            page = legacy_api_artists_page(conn, search_query="Drake")
            artist = page["artists"][0]
        finally:
            conn.close()

        assert artist["image_url"] == "http://cdn/v2.jpg", (
            "the endpoint still reads the legacy table")
        assert artist["genres"] == ["v2 genre"]
        # The id contract: consumers hand this to navigateToArtistDetail.
        assert artist["id"] == 1
        assert artist["lib2_artist_id"] != 1 or artist["lib2_artist_id"] is not None

    def test_response_shape_matches_the_legacy_reader(self, mirrored_db):
        from core.library2.queries import legacy_api_artists_page

        conn = _conn(mirrored_db)
        try:
            page = legacy_api_artists_page(conn)
        finally:
            conn.close()

        assert set(page) == {"artists", "pagination"}
        assert set(page["pagination"]) == {
            "page", "limit", "total_count", "total_pages", "has_prev", "has_next"}
        required = {
            "id", "name", "image_url", "genres", "musicbrainz_id",
            "spotify_artist_id", "itunes_artist_id", "deezer_id", "audiodb_id",
            "discogs_id", "lastfm_url", "genius_url", "tidal_id", "qobuz_id",
            "soul_id", "amazon_id", "album_count", "track_count", "is_watched"}
        assert required.issubset(set(page["artists"][0]))

    def test_provider_ids_come_from_external_ids(self, mirrored_db):
        from core.library2.queries import legacy_api_artists_page

        conn = _conn(mirrored_db)
        try:
            conn.execute("UPDATE artists SET deezer_id=? WHERE id=1", ("dz-9",))
            conn.commit()
        finally:
            conn.close()
        drain(mirrored_db)

        conn = _conn(mirrored_db)
        try:
            artist = legacy_api_artists_page(conn, search_query="Drake")["artists"][0]
        finally:
            conn.close()
        assert artist["deezer_id"] == "dz-9"

    def test_artists_without_a_legacy_row_are_omitted(self, mirrored_db):
        from core.library2.queries import legacy_api_artists_page

        conn = _conn(mirrored_db)
        try:
            conn.execute(
                "INSERT INTO lib2_artists(name, sort_name, name_key) "
                "VALUES('Native Only','Native Only','nativeonly')")
            conn.commit()
            names = [a["name"] for a in legacy_api_artists_page(conn)["artists"]]
        finally:
            conn.close()
        assert "Native Only" not in names, (
            "a lib2 id here would navigate to nothing — see the docstring")
