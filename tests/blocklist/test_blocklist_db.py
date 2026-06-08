"""Blocklist DB layer + the add_to_wishlist enforcement guard.

Verified against a real SQLite DB: CRUD + profile scoping + dedup + backfill
update, the discovery→blocklist migration, and the end-to-end wishlist guard
(a banned artist's track is refused; cascade; profile isolation).
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _track(track_id, name, artist_id, artist_name, album_id="al0", album_name="Al", source="spotify"):
    return {
        "id": track_id, "name": name, "source": source,
        "artists": [{"id": artist_id, "name": artist_name}],
        "album": {"id": album_id, "name": album_name, "artists": [{"id": artist_id, "name": artist_name}]},
    }


# ── CRUD + scoping ───────────────────────────────────────────────────────────

def test_add_list_remove(db):
    eid = db.add_blocklist_entry(1, "artist", "Drake", spotify_id="drake-sp")
    assert eid
    rows = db.get_blocklist(1)
    assert len(rows) == 1 and rows[0]["name"] == "Drake"
    assert db.remove_blocklist_entry(1, eid) is True
    assert db.get_blocklist(1) == []


def test_dedup_by_id_and_name(db):
    a = db.add_blocklist_entry(1, "artist", "Drake", spotify_id="sp")
    b = db.add_blocklist_entry(1, "artist", "Drake", spotify_id="sp")   # same id
    c = db.add_blocklist_entry(1, "artist", "drake")                     # same name (NOCASE)
    assert a == b == c
    assert len(db.get_blocklist(1)) == 1


def test_profile_isolation(db):
    db.add_blocklist_entry(1, "artist", "Drake", spotify_id="sp")
    assert len(db.get_blocklist(1)) == 1
    assert db.get_blocklist(2) == []
    # remove is profile-scoped — profile 2 can't delete profile 1's row
    eid = db.get_blocklist(1)[0]["id"]
    assert db.remove_blocklist_entry(2, eid) is False
    assert len(db.get_blocklist(1)) == 1


def test_match_status_pending_vs_matched(db):
    one = db.add_blocklist_entry(1, "artist", "A", spotify_id="sp")
    two = db.add_blocklist_entry(1, "artist", "B", spotify_id="sp2", deezer_id="dz2")
    rows = {r["id"]: r for r in db.get_blocklist(1)}
    assert rows[one]["match_status"] == "pending"   # single id → needs backfill
    assert rows[two]["match_status"] == "matched"   # 2+ ids → already cross-known


def test_backfill_update_fills_only_nulls(db):
    eid = db.add_blocklist_entry(1, "artist", "A", spotify_id="sp")
    db.update_blocklist_entry_ids(eid, deezer_id="dz", spotify_id="SHOULD-NOT-OVERWRITE")
    row = db.get_blocklist(1)[0]
    assert row["spotify_id"] == "sp"        # COALESCE: existing kept
    assert row["deezer_id"] == "dz"
    assert row["match_status"] == "matched"
    assert db.get_blocklist_entries_needing_backfill() == []


# ── the wishlist guard, end to end ───────────────────────────────────────────

def test_blocked_artist_track_refused_from_wishlist(db):
    db.add_blocklist_entry(1, "artist", "Drake", spotify_id="drake-sp")
    ok = db.add_to_wishlist(
        spotify_track_data=_track("t1", "God's Plan", "drake-sp", "Drake"),
        profile_id=1)
    assert ok is False                       # refused
    # And nothing landed in the wishlist.
    assert db.get_wishlist_count(profile_id=1) == 0


def test_unblocked_artist_track_is_added(db):
    db.add_blocklist_entry(1, "artist", "Drake", spotify_id="drake-sp")
    ok = db.add_to_wishlist(
        spotify_track_data=_track("t2", "Hello", "adele-sp", "Adele"),
        profile_id=1)
    assert ok is True
    assert db.get_wishlist_count(profile_id=1) == 1


def test_block_is_profile_scoped_at_the_guard(db):
    db.add_blocklist_entry(1, "artist", "Drake", spotify_id="drake-sp")
    # Profile 2 has no such ban → the same track is allowed.
    ok = db.add_to_wishlist(
        spotify_track_data=_track("t3", "Nice For What", "drake-sp", "Drake"),
        profile_id=2)
    assert ok is True


def test_album_block_cascades_to_track_at_guard(db):
    db.add_blocklist_entry(1, "album", "Scorpion", spotify_id="scorp-sp")
    ok = db.add_to_wishlist(
        spotify_track_data=_track("t4", "Nonstop", "drake-sp", "Drake",
                                  album_id="scorp-sp", album_name="Scorpion"),
        profile_id=1)
    assert ok is False


def test_discovery_blacklist_migrated_into_blocklist(tmp_path):
    import database.music_database as mdb

    def _reinit(path):
        # An app upgrade = a fresh process with an empty init memo. Clear the
        # per-process "already initialized" set so init (and the migration)
        # actually re-runs against the existing DB file.
        mdb._database_initialized_paths.discard(str(mdb.Path(path).resolve()))
        return MusicDatabase(path)

    path = str(tmp_path / "mig.db")
    db = MusicDatabase(path)
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO discovery_artist_blacklist (artist_name, spotify_artist_id) "
                "VALUES ('Nickelback', 'nb-sp')")
    conn.commit()
    conn.close()

    db2 = _reinit(path)          # upgrade → migration runs (committed)
    rows = db2.get_blocklist(1)
    assert any(r["name"] == "Nickelback" and r["entity_type"] == "artist" for r in rows)

    db3 = _reinit(path)          # idempotent — a second upgrade doesn't duplicate
    nb = [r for r in db3.get_blocklist(1) if r["name"] == "Nickelback"]
    assert len(nb) == 1

    # The migrated ban actually enforces at the wishlist guard.
    ok = db3.add_to_wishlist(
        spotify_track_data=_track("t5", "Photograph", "nb-sp", "Nickelback"),
        profile_id=1)
    assert ok is False


# ── Phase 2a: shared guard with source fallback (download-queue path) ────────

def test_blocklist_reason_source_fallback(db):
    """The download-queue guard passes the batch source explicitly because the
    analysis track dict may not carry a 'provider' field. Uses an ALBUM ban
    (id-only, no name fallback) to isolate the source-driven ID match."""
    db.add_blocklist_entry(1, "album", "Scorpion", deezer_id="scorp-dz")
    track = {"id": "t1", "name": "Nonstop",
             "artists": [{"id": "drake", "name": "Drake"}],
             "album": {"id": "scorp-dz", "name": "Scorpion"}}
    assert db.blocklist_reason_for_track(1, track) is None              # no source → album id can't match
    assert db.blocklist_reason_for_track(1, track, source="spotify") is None  # wrong source
    assert db.blocklist_reason_for_track(1, track, source="deezer")     # right source → match


def test_artist_name_fallback_works_without_source(db):
    # Artists DO fall back to name, so a ban matches even when the source is
    # unknown (covers the cross-source backfill window).
    db.add_blocklist_entry(1, "artist", "Drake", deezer_id="drake-dz")
    track = {"id": "t1", "name": "Track", "artists": [{"id": "x", "name": "Drake"}],
             "album": {"id": "al", "name": "Al"}}
    assert db.blocklist_reason_for_track(1, track) is not None


def test_blocklist_reason_simulates_queue_filter(db):
    """Mirror what master.py does: filter a missing-tracks list by the guard."""
    db.add_blocklist_entry(1, "artist", "Blocked Guy", spotify_id="bg-sp")
    missing = [
        {"track": {"id": "t1", "name": "Keep Me",
                   "artists": [{"id": "ok", "name": "Good Artist"}], "album": {"id": "a1", "name": "A"}}},
        {"track": {"id": "t2", "name": "Drop Me",
                   "artists": [{"id": "bg-sp", "name": "Blocked Guy"}], "album": {"id": "a2", "name": "B"}}},
    ]
    kept = [r for r in missing
            if not db.blocklist_reason_for_track(1, r["track"], source="spotify")]
    assert [r["track"]["id"] for r in kept] == ["t1"]
