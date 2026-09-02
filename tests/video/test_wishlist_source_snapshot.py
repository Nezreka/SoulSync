"""Per-source search outcome snapshots for wanted items.

Roadmap: "store per-source search outcome snapshots for wanted items: result
count, rejected count, accepted count, and top refusal reason."

A wishlist row carried ONE attempt counter and ONE refusal line for a search
that may have asked three different sources. "Stuck on 40 attempts" could mean
Prowlarr has never been configured, or slskd returns nothing, or every source
finds it and the profile refuses them all - three different problems collapsed
into one number. The snapshot keeps them apart, and it is what the stuck-row
diagnostics drawer reads.
"""

from __future__ import annotations

import json

import pytest

from core.automation.handlers.video_process_wishlist import source_outcome
from database.video_database import VideoDatabase


# ── the pure per-source fact ─────────────────────────────────────────────────

def test_a_source_that_could_not_search_is_not_an_empty_result():
    """The distinction the whole thing rests on: "Prowlarr isn't configured" is
    not evidence about the release, and must never read as "found nothing"."""
    out = source_outcome(None, "Prowlarr not configured")
    assert out["ran"] is False
    assert out["reason"] == "Prowlarr not configured"
    assert out["results"] == 0


def test_a_source_that_ran_and_found_nothing_says_so():
    out = source_outcome([])
    assert out["ran"] is True and out["results"] == 0 and out["reason"] is None


def test_counts_split_accepted_from_rejected():
    out = source_outcome([
        {"accepted": True},
        {"accepted": False, "rejected": "SD isn't in your enabled tiers", "quality_label": "SD"},
        {"accepted": False, "rejected": "Wrong season"},
    ])
    assert out["results"] == 3 and out["accepted"] == 1 and out["rejected"] == 2


def test_the_reason_names_the_rule_that_refused_the_best_one():
    out = source_outcome([
        {"accepted": False, "rejected": "Wrong season"},
        {"accepted": False, "rejected": "SD isn't in your enabled tiers", "quality_label": "SD"},
    ])
    assert out["reason"] == "Best found: SD — SD isn't in your enabled tiers"


def test_a_broken_summariser_costs_the_reason_not_the_snapshot(monkeypatch):
    import core.video.wishlist_evidence as ev
    monkeypatch.setattr(ev, "summarize_search",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    out = source_outcome([{"accepted": False, "rejected": "x"}])
    assert out["rejected"] == 1 and out["reason"] is None


def test_missing_error_text_still_reads_as_did_not_run():
    assert source_outcome(None)["reason"] == "search didn't run"


# ── persistence + read-back ──────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "v.db"))


def _wish_movie(db, tmdb_id=1):
    db.add_movie_to_wishlist(tmdb_id, "A Movie", year=2024)


SNAP = {"chain": ["torrent", "soulseek"],
        "sources": {"torrent": {"ran": False, "reason": "Prowlarr not configured",
                                "results": 0, "accepted": 0, "rejected": 0},
                    "soulseek": {"ran": True, "results": 4, "accepted": 0, "rejected": 4,
                                 "reason": "Best found: SD — SD isn't in your enabled tiers"}}}


def test_a_fruitless_search_stores_the_snapshot(db):
    _wish_movie(db)
    db.record_wishlist_search_outcome("movie", 1, False, refusal="x", snapshot=SNAP)
    row = db.query_wishlist(kind="movie")["items"][0]
    assert row["search_snapshot"] == SNAP
    assert row["search_snapshot"]["sources"]["torrent"]["ran"] is False


def test_a_grab_keeps_the_receipt_of_what_worked(db):
    """Clearing it on success would throw away the only record of WHICH source
    delivered — the thing you want most when tuning the chain."""
    _wish_movie(db)
    db.record_wishlist_search_outcome("movie", 1, True, snapshot=SNAP)
    row = db.query_wishlist(kind="movie")["items"][0]
    assert row["search_snapshot"] == SNAP
    assert row["search_attempts"] == 0          # the grab still reset the counter


def test_passing_no_snapshot_leaves_the_last_one_standing(db):
    """A caller that has nothing to say must not erase what the last search knew."""
    _wish_movie(db)
    db.record_wishlist_search_outcome("movie", 1, False, snapshot=SNAP)
    db.record_wishlist_search_outcome("movie", 1, False)
    assert db.query_wishlist(kind="movie")["items"][0]["search_snapshot"] == SNAP


def test_a_row_that_has_never_searched_has_no_snapshot(db):
    _wish_movie(db)
    assert db.query_wishlist(kind="movie")["items"][0]["search_snapshot"] is None


def test_an_unserialisable_snapshot_is_dropped_not_raised(db):
    """Diagnostics must never break the drain's own bookkeeping."""
    _wish_movie(db)
    db.record_wishlist_search_outcome("movie", 1, False, snapshot={"bad": {1, 2}})
    row = db.query_wishlist(kind="movie")["items"][0]
    assert row["search_snapshot"] is None
    assert row["search_attempts"] == 1          # the outcome itself still landed


def test_a_corrupt_stored_blob_does_not_break_the_page(db):
    _wish_movie(db)
    conn = db._get_connection()
    try:
        conn.execute("UPDATE video_wishlist SET search_snapshot='{not json'")
        conn.commit()
    finally:
        conn.close()
    assert db.query_wishlist(kind="movie")["items"][0]["search_snapshot"] is None


def test_episodes_carry_it_too(db):
    db.add_episodes_to_wishlist(99, "A Show", [{"season_number": 1, "episode_number": 2}])
    db.record_wishlist_search_outcome("episode", 99, False, season_number=1, episode_number=2,
                                      snapshot=SNAP)
    ep = db.query_wishlist(kind="show")["items"][0]["seasons"][0]["episodes"][0]
    assert ep["search_snapshot"] == SNAP


def test_an_existing_database_gains_the_column(tmp_path):
    """It has to ride _COLUMN_MIGRATIONS or every install that predates it breaks
    on the first write. Exercised through the migration step itself: reopening
    in-process can't show this, because _initialize_once guards per path.
    """
    import sqlite3

    path = str(tmp_path / "old.db")
    VideoDatabase(database_path=path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("ALTER TABLE video_wishlist DROP COLUMN search_snapshot")
        conn.commit()
        assert "search_snapshot" not in {
            r[1] for r in conn.execute("PRAGMA table_info(video_wishlist)")}

        VideoDatabase._ensure_columns(conn)      # what a real restart runs
        conn.commit()
        assert "search_snapshot" in {
            r[1] for r in conn.execute("PRAGMA table_info(video_wishlist)")}
    finally:
        conn.close()


def test_the_migration_is_idempotent(tmp_path):
    """It runs on every open, so running it twice must be a no-op, not a
    duplicate-column error."""
    import sqlite3

    path = str(tmp_path / "v.db")
    VideoDatabase(database_path=path)
    conn = sqlite3.connect(path)
    try:
        VideoDatabase._ensure_columns(conn)
        VideoDatabase._ensure_columns(conn)
    finally:
        conn.close()
