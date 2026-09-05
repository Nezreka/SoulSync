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

    def remove_release_from_wishlist(self, track_id, album_id=None, profile_id=1,
                                     raise_on_error=False):
        self.removes.append({"id": track_id, "album_id": album_id,
                             "profile_id": profile_id})
        return True

    def add_artist_to_watchlist(self, ext, name, profile_id, source,
                                quality_profile_id=None, raise_on_error=False):
        self.watchlist_adds.append({"ext": ext, "profile_id": profile_id,
                                    "quality_profile_id": quality_profile_id})
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
    assert (result["done"], result["failed"]) == (1, 0)
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


def test_payload_failure_rolls_back_monitor_mutation(db, monkeypatch):
    flaky, conn = db
    track_id = flaky.ids["track"]
    conn.execute("UPDATE lib2_tracks SET monitored=0 WHERE id=?", (track_id,))
    conn.commit()

    monkeypatch.setattr(
        "core.library2.wishlist_mirror.track_wishlist_payload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("payload unavailable")
        ),
    )

    conn.execute("UPDATE lib2_tracks SET monitored=1 WHERE id=?", (track_id,))
    with pytest.raises(RuntimeError, match="payload unavailable"):
        MO.enqueue_tracks(conn, [track_id], True)
    conn.rollback()

    assert conn.execute(
        "SELECT monitored FROM lib2_tracks WHERE id=?", (track_id,),
    ).fetchone()["monitored"] == 0
    assert _outbox_rows(conn) == []


def test_failed_mirror_stays_pending_and_later_drain_completes(db):
    """The audit's injected-failure scenario: the legacy write fails, the lib2
    command remains traceable as pending, and a later drain reconciles."""
    flaky, conn = db
    MO.enqueue_tracks(conn, [flaky.ids["track"]], True)
    conn.commit()

    flaky.fail_adds = True
    result = MO.drain(flaky)
    assert (result["done"], result["failed"]) == (0, 1)
    row = _outbox_rows(conn)[0]
    assert row["status"] == "pending"
    assert row["attempts"] == 1
    assert "locked" in row["last_error"]
    assert flaky.adds == []

    flaky.fail_adds = False
    result = MO.drain(flaky)
    assert (result["done"], result["failed"]) == (1, 0)
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

    assert (result["done"], result["failed"]) == (0, 1)
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
    assert (MO.drain(flaky)["done"], MO.drain(flaky)["failed"]) == (0, 0)
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
    assert (result["done"], result["failed"]) == (1, 0)
    assert len(flaky.removes) == 1
    removed = flaky.removes[0]
    assert removed["id"] == "sp-t"
    assert removed["profile_id"] == 3
    # SYNC-02: the withdrawal names the release it withdraws, so a sibling
    # album of the same recording is not swept up with it.
    assert removed["album_id"]


def test_artist_watchlist_ops(db):
    flaky, conn = db
    default_profile = conn.execute(
        "SELECT id FROM quality_profiles WHERE is_default=1 ORDER BY id LIMIT 1"
    ).fetchone()[0]
    assert MO.enqueue_artist_watchlist(conn, flaky.ids["artist"], True, profile_id=2)
    conn.commit()
    first = MO.drain(flaky)
    assert (first["done"], first["failed"]) == (1, 0)
    assert flaky.watchlist_adds == [
        {"ext": "sp-a", "profile_id": 2, "quality_profile_id": default_profile}
    ]

    assert MO.enqueue_artist_watchlist(conn, flaky.ids["artist"], False, profile_id=2)
    conn.commit()
    result = MO.drain(flaky)
    assert (result["done"], result["failed"]) == (1, 0)
    assert flaky.watchlist_removes == [{"ext": "sp-a", "profile_id": 2}]


def test_a_newer_op_supersedes_an_older_pending_one_for_the_same_entity(db):
    """dd28-13: the exact resurrection the finding describes.

    Row N (``wishlist_add`` for T) fails transiently while row N+1
    (``wishlist_remove`` for T) succeeds. Row N stayed pending, so the NEXT
    drain replayed it and brought back a wishlist entry the user had just
    removed. An op is an absolute assertion about one entity, so only the
    newest row for that entity may run.
    """
    flaky, conn = db
    track = flaky.ids["track"]

    flaky.fail_adds = True
    MO.enqueue_tracks(conn, [track], True, profile_id=3)
    conn.commit()
    first = MO.drain(flaky)
    assert (first["done"], first["failed"]) == (0, 1)
    assert conn.execute(
        "SELECT status FROM lib2_mirror_outbox ORDER BY id DESC LIMIT 1"
    ).fetchone()[0] == "pending"

    # The user now removes the track. That op is newer, and it succeeds.
    MO.enqueue_tracks(conn, [track], False, profile_id=3)
    conn.commit()
    flaky.fail_adds = False
    second = MO.drain(flaky)

    assert second["superseded"] == 1, "the stale add must not be replayed"
    assert flaky.adds == [], "a superseded add must never reach the legacy table"
    assert len(flaky.removes) == 1
    assert MO.drain(flaky)["done"] == 0, "nothing may remain queued"


def test_retry_does_not_rearm_a_failed_op_superseded_by_a_later_success(db):
    flaky, conn = db
    track = flaky.ids["track"]
    old = MO.enqueue_tracks(conn, [track], True, profile_id=3)[0]
    conn.execute(
        "UPDATE lib2_mirror_outbox SET status='failed' WHERE id=?", (old,))
    newer = MO.enqueue_tracks(conn, [track], False, profile_id=3)[0]
    conn.execute(
        "UPDATE lib2_mirror_outbox SET status='done' WHERE id=?", (newer,))

    assert MO.retry_failed(conn) == 0
    assert conn.execute(
        "SELECT status FROM lib2_mirror_outbox WHERE id=?", (old,)
    ).fetchone()[0] == "superseded"


def test_artist_watchlist_add_pushes_explicit_catalog_profile(db):
    """Split-doc contract: the native Watchlist add takes one explicit
    quality_profile_id (branch-split/LIBRARY_OVERHAUL.md "Library v2
    integration after rebase"). An artist with an explicit catalog Quality
    Profile override must push THAT profile, not the app-wide default."""
    flaky, conn = db
    conn.execute(
        "INSERT INTO quality_profiles(id, name, is_default) VALUES(9, 'Hi-Res', 0)"
    )
    conn.execute(
        "UPDATE lib2_artists SET quality_profile_id=9, quality_profile_explicit=1 "
        "WHERE id=?",
        (flaky.ids["artist"],),
    )
    assert MO.enqueue_artist_watchlist(conn, flaky.ids["artist"], True, profile_id=2)
    conn.commit()
    result = MO.drain(flaky)
    assert (result["done"], result["failed"]) == (1, 0)
    assert flaky.watchlist_adds == [
        {"ext": "sp-a", "profile_id": 2, "quality_profile_id": 9}
    ]


def test_artist_watchlist_remove_carries_no_quality_profile(db):
    """Un-monitoring is a pure removal — it must not need or send a profile."""
    flaky, conn = db
    assert MO.enqueue_artist_watchlist(conn, flaky.ids["artist"], False, profile_id=2)
    conn.commit()
    result = MO.drain(flaky)
    assert (result["done"], result["failed"]) == (1, 0)
    assert flaky.watchlist_removes == [{"ext": "sp-a", "profile_id": 2}]
    assert flaky.watchlist_adds == []


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


def test_prune_keeps_the_row_that_supersedes_a_stuck_one(db):
    """dd28-13's protection is re-derived from history at drain time, so the
    history has to survive the pruner.

    Row 1 = wishlist_add(T) is stuck failed. Row 2 = wishlist_remove(T)
    succeeded, and is the ONLY evidence that row 1 is obsolete. Pruning row 2
    and then hitting "Retry failed" replays the add and resurrects the wishlist
    entry the user removed.
    """
    flaky, conn = db
    track = flaky.ids["track"]
    MO.enqueue_tracks(conn, [track], True)          # row 1: add
    conn.commit()
    conn.execute("UPDATE lib2_mirror_outbox SET status='failed', attempts=?",
                 (MO.MAX_ATTEMPTS,))
    MO.enqueue_tracks(conn, [track], False)         # row 2: remove
    conn.commit()
    MO.drain(flaky)                                 # row 2 -> done
    conn.commit()

    assert MO.prune_done(conn, keep=0) == 0
    conn.commit()

    statuses = {row["status"] for row in _outbox_rows(conn)}
    assert statuses == {"failed", "done"}

    # And with the stuck row gone, the done row is prunable again.
    conn.execute("DELETE FROM lib2_mirror_outbox WHERE status='failed'")
    conn.commit()
    assert MO.prune_done(conn, keep=0) == 1


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


def test_two_releases_of_one_recording_both_reach_the_wishlist(db):
    """SYNC-03: two wanted releases that share a provider track id are two
    independent intents. Supersession keyed both on the bare track id, so the
    second album looked like a newer assertion about the first and that first
    add was dropped before it was ever persisted — the album silently never
    got queued."""
    flaky, conn = db
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Other')",
                (flaky.ids["artist"],))
    other_album = cur.lastrowid
    cur.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
                (other_album, flaky.ids["artist"]))
    # Same provider track id, different release.
    cur.execute("INSERT INTO lib2_tracks(album_id, title, track_number, spotify_id) "
                "VALUES(?, 'T', 1, 'sp-t')", (other_album,))
    other_track = cur.lastrowid
    cur.execute("INSERT INTO lib2_track_artists(track_id, artist_id) VALUES(?,?)",
                (other_track, flaky.ids["artist"]))
    conn.commit()

    MO.enqueue_tracks(conn, [flaky.ids["track"], other_track], True, profile_id=1)
    conn.commit()
    result = MO.drain(flaky)

    assert (result["done"], result["failed"]) == (2, 0)
    assert [row["status"] for row in _outbox_rows(conn)] == ["done", "done"]
    assert len(flaky.adds) == 2


def test_a_later_op_for_the_same_release_still_supersedes(db):
    """The other half of SYNC-03: narrowing the key must not disable
    supersession for what really is the same release."""
    flaky, conn = db
    MO.enqueue_tracks(conn, [flaky.ids["track"]], True, profile_id=1)
    MO.enqueue_tracks(conn, [flaky.ids["track"]], False, profile_id=1)
    conn.commit()

    MO.drain(flaky)

    statuses = [row["status"] for row in _outbox_rows(conn)]
    assert statuses == ["superseded", "done"]
    assert flaky.adds == []


class TestReleaseScopedWithdrawal:
    """SYNC-02: the wishlist row is a RELEASE, and the mirror has to say which."""

    def test_a_satisfied_release_is_withdrawn_despite_the_composite_key(self, db):
        """Trigger A: the insert stores `<track>::<album>` while the mirror's
        presence probe asked for the bare id, found nothing, and skipped the
        withdrawal — leaving the processor re-downloading a satisfied track."""
        flaky, conn = db
        conn.execute(
            """CREATE TABLE IF NOT EXISTS wishlist_tracks(
                   id INTEGER PRIMARY KEY AUTOINCREMENT, spotify_track_id TEXT,
                   spotify_data TEXT, source_type TEXT, source_info TEXT,
                   profile_id INTEGER)""")
        # A satisfying file makes the track monitored-but-not-queueable.
        conn.execute(
            "INSERT INTO lib2_track_files(track_id, path, format, file_state) "
            "VALUES(?, '/m/t.flac', 'flac', 'active')", (flaky.ids["track"],))
        conn.commit()
        from core.library2.wishlist_mirror import track_wishlist_payload
        payload = track_wishlist_payload(conn, flaky.ids["track"])
        assert payload["_should_queue"] is False
        composite = f"{payload['id']}::{payload['album']['id']}"
        conn.execute(
            "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, "
            "source_type, profile_id) VALUES(?, '{}', 'album', 1)", (composite,))
        conn.commit()

        assert MO.enqueue_tracks(conn, [flaky.ids["track"]], True, profile_id=1)
        conn.commit()
        assert MO.drain(flaky)["done"] == 1
        assert flaky.removes and flaky.removes[0]["id"] == payload["id"]

    def test_unmonitoring_one_release_leaves_the_other_wanted(self, tmp_path):
        """Trigger B: the mirror handed the bare track id to
        `remove_from_wishlist`, whose job is to clear every `<track>::%` row.
        Right for "this recording was downloaded", wrong here — it deleted a
        second album of the same recording that was still wanted."""
        from database.music_database import MusicDatabase

        db = MusicDatabase(str(tmp_path / "music.db"))
        payload = {"id": "sp-t", "name": "T", "artists": [{"name": "A"}],
                   "album": {"id": "alb-1", "name": "One"}}
        other = dict(payload, album={"id": "alb-2", "name": "Two"})
        assert db.add_to_wishlist(payload, source_type="album") is True
        assert db.add_to_wishlist(other, source_type="album") is True
        with db._get_connection() as conn:
            keys = {r[0] for r in conn.execute(
                "SELECT spotify_track_id FROM wishlist_tracks")}
        assert keys == {"sp-t::alb-1", "sp-t::alb-2"}

        assert db.remove_release_from_wishlist("sp-t", "alb-1") is True

        with db._get_connection() as conn:
            keys = {r[0] for r in conn.execute(
                "SELECT spotify_track_id FROM wishlist_tracks")}
        assert keys == {"sp-t::alb-2"}

    def test_a_legacy_bare_row_is_still_withdrawable(self, tmp_path):
        """Rows written before the composite key existed — and by writers
        outside Library v2 — carry the bare id and no album. They stay
        removable; only a row naming a DIFFERENT album is protected."""
        from database.music_database import MusicDatabase

        db = MusicDatabase(str(tmp_path / "music.db"))
        with db._get_connection() as conn:
            conn.execute(
                "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, "
                "source_type, profile_id) VALUES('sp-t', '{}', 'album', 1)")
            conn.commit()

        assert db.remove_release_from_wishlist("sp-t", "alb-1") is True
        with db._get_connection() as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM wishlist_tracks").fetchone()[0] == 0
