"""Per-provider gaps get filled, not just rows with no provider at all.

The user's `Sawano Hiroyuki` had Spotify, Deezer, iTunes and Last.fm ids a day
after import — and no MusicBrainz id, which is the one the AcoustID verifier's
alias bridge is built from. Nothing was ever going to fill it: the scheduled
native sweep asks `_pending_unmapped_artists`, whose predicate is "no catalog
provider id at ALL", so an artist Spotify had matched counted as done. The
per-service enrich endpoint could have filled it, but only if someone clicked
it for that one artist and that one service.

The gap is per-provider, so the backlog query has to be too. This drains the
same ledger the twelve enrichment workers use, which is what makes the pass
safe to run alongside them: whoever reaches a row first records the attempt
and the other skips it.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.native_enrich import backfill_missing_provider_ids
from core.library2.schema import ensure_library_v2_schema


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "backfill.db"))
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    c.execute(
        "INSERT INTO lib2_artists (id, name, spotify_id, musicbrainz_id) "
        "VALUES (1, 'Sawano Hiroyuki', 'sp-1', NULL)")
    c.execute(
        "INSERT INTO lib2_albums (id, primary_artist_id, title) "
        "VALUES (1, 1, 'Attack on Titan Season 2 OST')")
    c.execute(
        "INSERT INTO lib2_tracks (id, album_id, title) VALUES (1, 1, 'Apetitan')")
    c.execute(
        "INSERT INTO lib2_track_files (track_id, path, format, is_primary) "
        "VALUES (1, '/music/apetitan.flac', 'flac', 1)")
    c.commit()
    yield c
    c.close()


def _enricher(outcomes):
    """Records every (entity_type, entity_id, service) it is asked about and
    answers from ``outcomes`` keyed by service."""
    seen = []

    def _enrich(_conn, entity_type, entity_id, service):
        seen.append((entity_type, int(entity_id), service))
        outcome = outcomes.get(service, {"success": False, "reason": "not_found"})
        if isinstance(outcome, Exception):
            raise outcome
        return dict(outcome)

    _enrich.seen = seen
    return _enrich


def _attempts(conn, service):
    return {
        (r["entity_type"], r["entity_id"]): r["status"]
        for r in conn.execute(
            "SELECT entity_type, entity_id, status FROM lib2_provider_attempts "
            "WHERE service = ?", (service,))
    }


def test_fills_a_missing_provider_on_an_already_matched_artist(conn):
    enrich = _enricher({"musicbrainz": {
        "success": True, "source": "musicbrainz", "provider_id": "mbid-sawano"}})

    stats = backfill_missing_provider_ids(
        conn, services=["musicbrainz"], limit=10, enricher=enrich)

    assert ("artist", 1, "musicbrainz") in enrich.seen
    assert stats["matched"] >= 1
    assert _attempts(conn, "musicbrainz")[("artist", 1)] == "matched"


def test_it_reaches_albums_and_tracks_too(conn):
    enrich = _enricher({"musicbrainz": {
        "success": True, "source": "musicbrainz", "provider_id": "mb-x"}})

    backfill_missing_provider_ids(
        conn, services=["musicbrainz"], limit=10, enricher=enrich)

    kinds = {entity_type for entity_type, _id, _svc in enrich.seen}
    assert kinds == {"artist", "album", "track"}


def test_a_miss_is_recorded_so_the_row_is_not_handed_out_again(conn):
    enrich = _enricher({"musicbrainz": {"success": False, "reason": "not_found"}})

    backfill_missing_provider_ids(
        conn, services=["musicbrainz"], limit=10, enricher=enrich)

    # Three entities, asked once each — not one entity asked ten times.
    assert len(enrich.seen) == 3
    assert set(_attempts(conn, "musicbrainz").values()) == {"not_found"}


def test_one_service_failing_does_not_stop_the_others(conn):
    enrich = _enricher({
        "musicbrainz": RuntimeError("musicbrainz is down"),
        "deezer": {"success": True, "source": "deezer", "provider_id": "dz-1"},
    })

    stats = backfill_missing_provider_ids(
        conn, services=["musicbrainz", "deezer"], limit=10, enricher=enrich)

    assert stats["errors"] >= 1
    assert any(svc == "deezer" for _t, _i, svc in enrich.seen)
    assert _attempts(conn, "musicbrainz")[("artist", 1)] == "error"


def test_the_run_budget_is_respected(conn):
    enrich = _enricher({"musicbrainz": {"success": False, "reason": "not_found"}})

    stats = backfill_missing_provider_ids(
        conn, services=["musicbrainz"], limit=2, enricher=enrich)

    assert len(enrich.seen) == 2
    assert stats["scanned"] == 2


def test_the_budget_is_shared_round_robin_across_services(conn):
    enrich = _enricher({})  # everything misses

    backfill_missing_provider_ids(
        conn, services=["musicbrainz", "deezer"], limit=2, enricher=enrich)

    # One each, rather than one service consuming the whole run.
    assert {svc for _t, _i, svc in enrich.seen} == {"musicbrainz", "deezer"}


def test_no_services_configured_is_a_no_op(conn):
    enrich = _enricher({})

    stats = backfill_missing_provider_ids(
        conn, services=[], limit=10, enricher=enrich)

    assert enrich.seen == []
    assert stats["scanned"] == 0


def test_a_stop_is_honoured_between_items(conn):
    """A full budget against MusicBrainz's one-per-second limiter is minutes.

    The job only checked for a stop before the phase began, so pressing stop
    meant "stop eventually" — and there is no smaller unit of work to fall back
    on, because each item is a blocking provider call.
    """
    enrich = _enricher({"musicbrainz": {
        "success": True, "source": "musicbrainz", "provider_id": "mbid-1"}})
    calls = {"n": 0}

    def _stop():
        calls["n"] += 1
        return calls["n"] > 1

    stats = backfill_missing_provider_ids(
        conn, services=["musicbrainz"], limit=50, enricher=enrich,
        should_stop=_stop)

    assert stats["scanned"] == 1
    assert len(enrich.seen) == 1


def test_without_a_stop_callback_the_budget_is_spent_as_before(conn):
    enrich = _enricher({"musicbrainz": {
        "success": True, "source": "musicbrainz", "provider_id": "mbid-1"}})

    stats = backfill_missing_provider_ids(
        conn, services=["musicbrainz"], limit=50, enricher=enrich)

    assert stats["scanned"] >= 1
