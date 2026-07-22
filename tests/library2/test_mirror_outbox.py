"""Transactional mirror outbox (audit P0-04 / ADR-02 option 3).

The lib2 monitor write and the mirror intent commit atomically; a failing
legacy write keeps its outbox row pending (error recorded) and a later
drain completes it — no more silent split-brain between lib2 flags and
the wishlist the pipeline actually reads.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2 import mirror_outbox as MO
from core.library2.schema import ensure_library_v2_schema


class FlakyDB:
    """Legacy-DB stand-in whose wishlist writes can be told to fail."""

    def __init__(self, path: str):
        self.path = path
        self.fail_adds = False
        self.adds = []
        self.removes = []
        self.watchlist_adds = []
        self.watchlist_removes = []

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_to_wishlist(self, payload, source_type="unknown", source_info=None,
                        user_initiated=False, profile_id=1, quality_profile_id=None,
                        raise_on_error=False):
        if self.fail_adds:
            if raise_on_error:
                raise RuntimeError("legacy db locked")
            return False
        self.adds.append({"id": payload.get("id"), "profile_id": profile_id,
                          "user_initiated": user_initiated,
                          "quality_profile_id": quality_profile_id})
        return True

    def remove_from_wishlist(self, track_id, profile_id=1, raise_on_error=False):
        self.removes.append({"id": track_id, "profile_id": profile_id})
        return True

    def add_artist_to_watchlist(self, ext, name, profile_id, source,
                                raise_on_error=False):
        self.watchlist_adds.append({"ext": ext, "profile_id": profile_id})
        return True

    def remove_artist_from_watchlist(self, ext, profile_id, raise_on_error=False):
        self.watchlist_removes.append({"ext": ext, "profile_id": profile_id})
        return True


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "lib2.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name, spotify_id) VALUES('A','sp-a')")
    artist_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Alb')",
                (artist_id,))
    album_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
                (album_id, artist_id))
    cur.execute("INSERT INTO lib2_tracks(album_id, title, track_number, spotify_id) "
                "VALUES(?, 'T', 1, 'sp-t')", (album_id,))
    track_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_track_artists(track_id, artist_id) VALUES(?,?)",
                (track_id, artist_id))
    conn.commit()
    flaky = FlakyDB(path)
    flaky.ids = {"artist": artist_id, "album": album_id, "track": track_id}
    yield flaky, conn
    conn.close()


def _outbox_rows(conn):
    return conn.execute(
        "SELECT id, op, status, attempts, last_error FROM lib2_mirror_outbox ORDER BY id"
    ).fetchall()


def test_enqueue_and_drain_happy_path(db):
    flaky, conn = db
    ids = MO.enqueue_tracks(conn, [flaky.ids["track"]], True, profile_id=7,
                            user_initiated=True)
    assert len(ids) == 1
    conn.commit()
    result = MO.drain(flaky)
    assert result == {"done": 1, "failed": 0}
    assert flaky.adds and flaky.adds[0]["profile_id"] == 7
    assert flaky.adds[0]["user_initiated"] is True
    assert _outbox_rows(conn)[0]["status"] == "done"


def test_projected_enqueue_uses_wanted_state_and_rejects_projection_gaps(db):
    flaky, conn = db
    track_id = flaky.ids["track"]
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule
    from core.library2.wanted import recompute_wanted
    record_rule(conn, "track", track_id, True, PROVENANCE_USER)
    recompute_wanted(conn, track_ids=[track_id])
    outbox_ids = MO.enqueue_projected_tracks(conn, [track_id])
    assert outbox_ids
    assert _outbox_rows(conn)[-1]["op"] == "wishlist_add"

    record_rule(conn, "track", track_id, False, PROVENANCE_USER)
    recompute_wanted(conn, track_ids=[track_id])
    MO.enqueue_projected_tracks(conn, [track_id])
    assert _outbox_rows(conn)[-1]["op"] == "wishlist_remove"

    conn.execute("DELETE FROM lib2_wanted_tracks WHERE track_id=?", (track_id,))
    with pytest.raises(RuntimeError, match="missing or stale"):
        MO.enqueue_projected_tracks(conn, [track_id])


def test_failed_mirror_stays_pending_and_later_drain_completes(db):
    """The audit's injected-failure scenario: the legacy write fails, the lib2
    command remains traceable as pending, and a later drain reconciles."""
    flaky, conn = db
    MO.enqueue_tracks(conn, [flaky.ids["track"]], True)
    conn.commit()

    flaky.fail_adds = True
    result = MO.drain(flaky)
    assert result == {"done": 0, "failed": 1}
    row = _outbox_rows(conn)[0]
    assert row["status"] == "pending"
    assert row["attempts"] == 1
    assert "locked" in row["last_error"]
    assert flaky.adds == []

    flaky.fail_adds = False
    result = MO.drain(flaky)
    assert result == {"done": 1, "failed": 0}
    assert flaky.adds
    assert _outbox_rows(conn)[0]["status"] == "done"


def test_outbox_uses_strict_legacy_write_mode(db):
    """The real legacy helpers normally convert DB errors to False. The outbox
    must request their strict mode or it would mark that silent failure done."""
    flaky, conn = db
    MO.enqueue_tracks(conn, [flaky.ids["track"]], True)
    conn.commit()
    flaky.fail_adds = True

    result = MO.drain(flaky)

    assert result == {"done": 0, "failed": 1}
    row = _outbox_rows(conn)[0]
    assert row["status"] == "pending"
    assert "legacy db locked" in row["last_error"]


def test_row_flips_to_failed_after_max_attempts_and_retry_resets(db):
    flaky, conn = db
    MO.enqueue_tracks(conn, [flaky.ids["track"]], True)
    conn.commit()
    flaky.fail_adds = True
    for _ in range(MO.MAX_ATTEMPTS):
        MO.drain(flaky)
    row = _outbox_rows(conn)[0]
    assert row["status"] == "failed"
    assert row["attempts"] == MO.MAX_ATTEMPTS
    # A further drain no longer touches it.
    assert MO.drain(flaky) == {"done": 0, "failed": 0}
    # Manual retry re-arms it.
    assert MO.retry_failed(conn) == 1
    conn.commit()
    flaky.fail_adds = False
    assert MO.drain(flaky)["done"] == 1


def test_unmonitor_enqueues_remove_that_survives_row_deletion(db):
    """Deletes enqueue their un-mirrors in the same transaction as the row
    deletion; the drain replays them from the stored payload afterwards."""
    flaky, conn = db
    MO.enqueue_tracks(conn, [flaky.ids["track"]], False, profile_id=3)
    conn.execute("DELETE FROM lib2_track_files WHERE track_id=?", (flaky.ids["track"],))
    conn.execute("DELETE FROM lib2_tracks WHERE id=?", (flaky.ids["track"],))
    conn.commit()
    result = MO.drain(flaky)
    assert result == {"done": 1, "failed": 0}
    assert flaky.removes == [{"id": "sp-t", "profile_id": 3}]


def test_artist_watchlist_ops(db):
    flaky, conn = db
    assert MO.enqueue_artist_watchlist(conn, flaky.ids["artist"], True, profile_id=2)
    assert MO.enqueue_artist_watchlist(conn, flaky.ids["artist"], False, profile_id=2)
    conn.commit()
    result = MO.drain(flaky)
    assert result == {"done": 2, "failed": 0}
    assert flaky.watchlist_adds == [{"ext": "sp-a", "profile_id": 2}]
    assert flaky.watchlist_removes == [{"ext": "sp-a", "profile_id": 2}]


def test_status_and_prune(db):
    flaky, conn = db
    MO.enqueue_tracks(conn, [flaky.ids["track"]], True)
    conn.commit()
    status = MO.outbox_status(conn)
    assert status["pending"] == 1 and status["failed"] == 0
    MO.drain(flaky)
    status = MO.outbox_status(conn)
    assert status["pending"] == 0 and status["done"] == 1
    assert MO.prune_done(conn, keep=0) == 1
    conn.commit()
    assert _outbox_rows(conn) == []


def test_replay_is_idempotent_when_marking_crashes(db):
    """If the process dies between executing an op and marking it done, the
    replay must not corrupt state — wishlist add is an upsert (P1-09/P1-10)."""
    flaky, conn = db
    MO.enqueue_tracks(conn, [flaky.ids["track"]], True)
    conn.commit()
    MO.drain(flaky)
    # Simulate the crash: row back to pending although the op already ran.
    conn.execute("UPDATE lib2_mirror_outbox SET status='pending'")
    conn.commit()
    result = MO.drain(flaky)
    assert result["done"] == 1
    assert len(flaky.adds) == 2  # replayed — the real DB upserts in place
