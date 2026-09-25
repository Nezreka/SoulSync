"""Resume contract for the automatic legacy -> ``lib2_*`` migration.

A migration that dies mid-run (container restart, OOM, power cut) used to begin
again at the first artist on the next boot, which on a large library means the
user watches the same work happen twice. These tests pin the checkpoint that
lets the next attempt continue where the last one stopped.

The subtle part is not the row offset, it is the **run id**. The importer is a
snapshot reconciler: at the end of a run it removes or detaches every
legacy-owned row that was not observed during *that* run
(``_reconcile_legacy_snapshot``). A resumed run that skips the artist and album
walks has therefore not "observed" those rows -- unless it continues under the
same run id as the attempt it is resuming. Getting this wrong deletes the part
of the library that was already migrated, so it is tested from both sides.
"""

from __future__ import annotations

import time

import pytest

from core.library2 import bootstrap as lib2_bootstrap
from core.library2.importer import ResumePoint, import_legacy_library


def _enabled(_key, _default=None):
    return True


class _ProgressSpy:
    """A resume-aware progress callback that records every checkpoint."""

    lib2_connection_aware = True
    lib2_resume_aware = True

    def __init__(self):
        self.calls = []

    def __call__(self, stage, current, total, *, connection=None, rowid=None, run_id=None):
        self.calls.append(
            {"stage": stage, "current": current, "total": total,
             "rowid": rowid, "run_id": run_id}
        )

    @property
    def stages(self):
        return [call["stage"] for call in self.calls]

    @property
    def run_id(self):
        for call in reversed(self.calls):
            if call["run_id"]:
                return call["run_id"]
        return None


def _counts(db):
    conn = db._get_connection()
    try:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("lib2_artists", "lib2_albums", "lib2_tracks")
        }
    finally:
        conn.close()


def _duration_of_legacy_track(db, legacy_track_id):
    conn = db._get_connection()
    try:
        row = conn.execute(
            "SELECT duration FROM lib2_tracks WHERE legacy_track_id=?",
            (str(legacy_track_id),),
        ).fetchone()
        return row["duration"] if row else None
    finally:
        conn.close()


def _clear_lib2_durations(db):
    conn = db._get_connection()
    try:
        conn.execute("UPDATE lib2_tracks SET duration=NULL")
        conn.commit()
    finally:
        conn.close()


def _set_legacy_duration(db, track_id, duration):
    conn = db._get_connection()
    try:
        conn.execute("UPDATE tracks SET duration=? WHERE id=?", (duration, track_id))
        conn.commit()
    finally:
        conn.close()


# --- importer ------------------------------------------------------------


def test_importer_publishes_run_id_and_rowid_for_resume_aware_progress(legacy_db):
    spy = _ProgressSpy()

    import_legacy_library(legacy_db, progress=spy)

    assert spy.run_id, "the importer must publish its run id so a crash can resume it"
    walked = [call for call in spy.calls if call["rowid"] is not None]
    assert walked, "row checkpoints must carry the source rowid they reached"


def test_resume_skips_the_stages_that_already_completed(legacy_db):
    first = _ProgressSpy()
    import_legacy_library(legacy_db, progress=first)

    resumed = _ProgressSpy()
    import_legacy_library(
        legacy_db,
        progress=resumed,
        resume=ResumePoint(stage="tracks", rowid=0, run_id=first.run_id),
    )

    assert "artists" not in resumed.stages
    assert "albums" not in resumed.stages
    assert "tracks" in resumed.stages


def test_resume_keeps_the_rows_the_crashed_run_already_wrote(legacy_db):
    first = _ProgressSpy()
    import_legacy_library(legacy_db, progress=first)
    before = _counts(legacy_db)

    import_legacy_library(
        legacy_db,
        resume=ResumePoint(stage="tracks", rowid=0, run_id=first.run_id),
    )

    assert _counts(legacy_db) == before


def _legacy_linked_albums(db):
    conn = db._get_connection()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM lib2_albums WHERE legacy_album_id IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()


def test_resume_under_a_foreign_run_id_would_unlink_the_migrated_rows(legacy_db):
    """Pins *why* the run id is part of the checkpoint.

    Continuing under a fresh run id makes the snapshot reconcile treat
    everything the crashed attempt imported as "no longer in the legacy
    library": rows that are still independently backed get detached from their
    legacy id (the rest is deleted outright). A detached album is re-imported
    as a second copy on the next run — this is the damage ResumePoint.run_id
    exists to prevent.
    """
    import_legacy_library(legacy_db)
    assert _legacy_linked_albums(legacy_db) > 0

    import_legacy_library(
        legacy_db,
        resume=ResumePoint(stage="tracks", rowid=0, run_id="a-different-run"),
    )

    assert _legacy_linked_albums(legacy_db) == 0


def test_resume_keeps_the_legacy_link_of_rows_it_skipped(legacy_db):
    first = _ProgressSpy()
    import_legacy_library(legacy_db, progress=first)
    linked = _legacy_linked_albums(legacy_db)

    import_legacy_library(
        legacy_db,
        resume=ResumePoint(stage="tracks", rowid=0, run_id=first.run_id),
    )

    assert _legacy_linked_albums(legacy_db) == linked


def test_resume_continues_the_walk_after_the_checkpoint_rowid(legacy_db):
    first = _ProgressSpy()
    import_legacy_library(legacy_db, progress=first)

    # Both rows change while we are down; only the one after the checkpoint
    # may be picked up by the resumed walk.
    #
    # The probe is a HOLE, not an overwrite: the legacy catalogue is a frozen
    # upgrade snapshot, so a walk may only fill columns lib2 has no value for
    # (see the UPDATE in importer.py). Clearing both durations first makes
    # "did the walk reach this row?" observable again — it used to be read off
    # a renamed title, which the walk no longer writes over.
    _clear_lib2_durations(legacy_db)
    _set_legacy_duration(legacy_db, 100, 111_000)
    _set_legacy_duration(legacy_db, 101, 222_000)

    import_legacy_library(
        legacy_db,
        resume=ResumePoint(stage="tracks", rowid=100, run_id=first.run_id),
    )

    assert _duration_of_legacy_track(legacy_db, 100) is None
    assert _duration_of_legacy_track(legacy_db, 101) == 222_000


def test_resume_reports_absolute_progress(legacy_db):
    first = _ProgressSpy()
    import_legacy_library(legacy_db, progress=first)

    resumed = _ProgressSpy()
    import_legacy_library(
        legacy_db,
        progress=resumed,
        resume=ResumePoint(stage="tracks", rowid=100, run_id=first.run_id),
    )

    track_calls = [call for call in resumed.calls if call["stage"] == "tracks"]
    assert track_calls
    # The first track checkpoint of a resumed run already counts the rows the
    # crashed attempt walked -- otherwise the UI bar jumps back to zero.
    assert track_calls[0]["current"] >= 1
    assert track_calls[-1]["current"] == track_calls[-1]["total"]


def test_reset_ignores_a_resume_point(legacy_db):
    first = _ProgressSpy()
    import_legacy_library(legacy_db, progress=first)

    rebuilt = _ProgressSpy()
    import_legacy_library(
        legacy_db,
        reset=True,
        progress=rebuilt,
        resume=ResumePoint(stage="tracks", rowid=100, run_id=first.run_id),
    )

    assert "artists" in rebuilt.stages
    assert _counts(legacy_db)["lib2_tracks"] > 0


# --- bootstrap state -----------------------------------------------------


def test_heartbeat_persists_the_resume_checkpoint(legacy_db):
    owner = lib2_bootstrap.try_claim(legacy_db)
    assert owner

    lib2_bootstrap.heartbeat(
        legacy_db, owner, stage="tracks", current=5, total=9,
        rowid=1234, run_id="run-abc",
    )

    state = lib2_bootstrap.get_state(legacy_db)
    assert state["resume_stage"] == "tracks"
    assert state["resume_rowid"] == 1234
    assert state["resume_run_id"] == "run-abc"


def test_heartbeat_without_a_rowid_leaves_the_checkpoint_alone(legacy_db):
    owner = lib2_bootstrap.try_claim(legacy_db)
    lib2_bootstrap.heartbeat(
        legacy_db, owner, stage="tracks", current=5, total=9,
        rowid=1234, run_id="run-abc",
    )

    # The post-import precache stages report progress but have no source rowid;
    # they must not erase the import checkpoint.
    lib2_bootstrap.heartbeat(legacy_db, owner, stage="tracklists", current=1, total=4)

    state = lib2_bootstrap.get_state(legacy_db)
    assert state["resume_stage"] == "tracks"
    assert state["resume_rowid"] == 1234


def test_mark_done_clears_the_checkpoint(legacy_db):
    owner = lib2_bootstrap.try_claim(legacy_db)
    lib2_bootstrap.heartbeat(
        legacy_db, owner, stage="tracks", current=5, total=9,
        rowid=1234, run_id="run-abc",
    )

    lib2_bootstrap.mark_done(
        legacy_db, owner, watermark=lib2_bootstrap.source_watermark(legacy_db)
    )

    state = lib2_bootstrap.get_state(legacy_db)
    assert state["resume_stage"] is None
    assert state["resume_run_id"] is None


def test_mark_failed_keeps_the_checkpoint_for_the_retry(legacy_db):
    owner = lib2_bootstrap.try_claim(legacy_db)
    lib2_bootstrap.heartbeat(
        legacy_db, owner, stage="tracks", current=5, total=9,
        rowid=1234, run_id="run-abc",
    )

    lib2_bootstrap.mark_failed(legacy_db, owner, "boom")

    state = lib2_bootstrap.get_state(legacy_db)
    assert state["resume_stage"] == "tracks"
    assert state["resume_rowid"] == 1234


def test_resume_point_requires_an_unchanged_source(legacy_db):
    owner = lib2_bootstrap.try_claim(legacy_db)
    lib2_bootstrap.heartbeat(
        legacy_db, owner, stage="tracks", current=5, total=9,
        rowid=1234, run_id="run-abc",
    )
    state = lib2_bootstrap.get_state(legacy_db)

    unchanged = lib2_bootstrap.resume_point_for(
        state, lib2_bootstrap.source_watermark(legacy_db)
    )
    assert unchanged == ResumePoint(stage="tracks", rowid=1234, run_id="run-abc")

    assert lib2_bootstrap.resume_point_for(state, "a-different-watermark") is None


def test_run_bootstrap_resumes_a_crashed_attempt(legacy_db, monkeypatch):
    owner = lib2_bootstrap.try_claim(legacy_db)
    assert owner
    lib2_bootstrap.heartbeat(
        legacy_db, owner, stage="tracks", current=5, total=9,
        rowid=101, run_id="run-abc",
    )
    # The process died: the claim stays "running" with a heartbeat nobody extends.
    conn = legacy_db._get_connection()
    try:
        conn.execute(
            "UPDATE lib2_bootstrap_state SET heartbeat_at='2000-01-01T00:00:00+00:00' "
            "WHERE id=1"
        )
        conn.commit()
    finally:
        conn.close()

    seen = {}

    def _spy(database, **kwargs):
        seen.update(kwargs)
        return {"artists": 0}

    monkeypatch.setattr(lib2_bootstrap, "_import_legacy_library", _spy)

    result = lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled)

    assert result["success"] is True
    assert seen["resume"] == ResumePoint(stage="tracks", rowid=101, run_id="run-abc")


def test_run_bootstrap_starts_clean_when_the_source_changed_since_the_crash(
    legacy_db, monkeypatch
):
    owner = lib2_bootstrap.try_claim(legacy_db)
    lib2_bootstrap.heartbeat(
        legacy_db, owner, stage="tracks", current=5, total=9,
        rowid=101, run_id="run-abc",
    )
    conn = legacy_db._get_connection()
    try:
        conn.execute(
            "UPDATE lib2_bootstrap_state SET heartbeat_at='2000-01-01T00:00:00+00:00' "
            "WHERE id=1"
        )
        # A media-server scan added rows while we were down: the checkpoint's
        # row offsets no longer mean what they meant.
        conn.execute("INSERT INTO artists(id, name) VALUES(90002, 'Newcomer')")
        conn.commit()
    finally:
        conn.close()

    seen = {}

    def _spy(database, **kwargs):
        seen.update(kwargs)
        return {"artists": 0}

    monkeypatch.setattr(lib2_bootstrap, "_import_legacy_library", _spy)

    lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled)

    assert seen["resume"] is None


def test_post_import_hook_runs_for_the_automatic_migration(legacy_db):
    """The autostart must finish the same work the manual button does.

    Tracklist/tag/artwork precache used to be wired to the button only, so an
    installation that never opened the page kept an un-enriched catalogue.
    """
    ran = []

    lib2_bootstrap.run_bootstrap_if_needed(
        legacy_db, _enabled, post_import=lambda progress: ran.append(progress)
    )

    assert len(ran) == 1


def test_post_import_failure_does_not_fail_the_migration(legacy_db):
    def _boom(_progress):
        raise RuntimeError("artwork provider down")

    result = lib2_bootstrap.run_bootstrap_if_needed(
        legacy_db, _enabled, post_import=_boom
    )

    assert result["success"] is True
    assert lib2_bootstrap.get_state(legacy_db)["status"] == "done"


def test_finished_walks_move_the_checkpoint_past_them(legacy_db):
    """A crash in the whole-library tail work must not re-walk the source.

    The steps after the three walks (reconcile, wishlist seeding, wanted
    projection, ...) can take a while on a big library. Once the walks are
    committed the checkpoint names the finalize stage instead, so the retry
    goes straight to those steps.
    """
    spy = _ProgressSpy()
    import_legacy_library(legacy_db, progress=spy)

    checkpoints = [call for call in spy.calls if call["rowid"] is not None]
    assert checkpoints[-1]["stage"] == "finalizing"

    resumed = _ProgressSpy()
    import_legacy_library(
        legacy_db,
        progress=resumed,
        resume=ResumePoint(stage="finalizing", rowid=0, run_id=spy.run_id),
    )

    assert "artists" not in resumed.stages
    assert "albums" not in resumed.stages
    assert "tracks" not in resumed.stages
    assert "finalizing" in resumed.stages
    assert _counts(legacy_db)["lib2_tracks"] > 0


# --- restart handling ----------------------------------------------------


def test_reclaim_releases_a_claim_the_previous_process_died_holding(legacy_db):
    owner = lib2_bootstrap.try_claim(legacy_db)
    assert owner
    lib2_bootstrap.heartbeat(
        legacy_db, owner, stage="tracks", current=5, total=9,
        rowid=101, run_id="run-abc",
    )
    assert lib2_bootstrap.try_claim(legacy_db) is None, "fresh claim blocks others"

    # This process booted after that heartbeat, so its owner is gone.
    released = lib2_bootstrap.reclaim_abandoned_claim(
        legacy_db, process_started_at=time.time() + 60
    )

    assert released is True
    state = lib2_bootstrap.get_state(legacy_db)
    assert state["status"] == "failed"
    assert state["resume_stage"] == "tracks", "the checkpoint survives the reclaim"
    assert lib2_bootstrap.try_claim(legacy_db), "the migration may start again at once"


def test_reclaim_leaves_a_live_run_alone(legacy_db):
    owner = lib2_bootstrap.try_claim(legacy_db)
    lib2_bootstrap.heartbeat(legacy_db, owner, stage="tracks", current=5, total=9)

    released = lib2_bootstrap.reclaim_abandoned_claim(
        legacy_db, process_started_at=time.time() - 3600
    )

    assert released is False
    assert lib2_bootstrap.get_state(legacy_db)["status"] == "running"


def test_reclaim_is_a_no_op_when_nothing_is_running(legacy_db):
    assert lib2_bootstrap.reclaim_abandoned_claim(
        legacy_db, process_started_at=time.time()
    ) is False


# --- autostart loop ------------------------------------------------------


@pytest.mark.parametrize(
    "result, expected",
    [
        ({"skipped": "already_done"}, True),
        ({"success": True, "stats": {}}, True),
        ({"success": True, "stats": {}, "waiting_for_source": True}, True),
        ({"skipped": "empty_source"}, True),
        ({"skipped": "already_running"}, False),
        ({"success": False, "error": "boom"}, False),
    ],
)
def test_autostart_stops_once_the_catalogue_is_converged(result, expected):
    assert lib2_bootstrap.should_stop_autostart(result) is expected


# --- the checkpoint/watermark pair (iss29-A01, A02, A04) -----------------
#
# These three defects compose into one production failure: an upgrade that
# lands in a permanently empty Library V2 and reports itself as "done". None of
# them is caught by the tests above, because each is individually survivable —
# it is the pair (revived checkpoint) plus (a stage the walks then skip) that
# is fatal.


def _add_legacy_artist(db, artist_id=2, name="Newcomer"):
    """One more row in the legacy source, i.e. a new source watermark."""
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT INTO artists VALUES(?,?,NULL,NULL,NULL,NULL,NULL)",
            (artist_id, name),
        )
        conn.commit()
    finally:
        conn.close()


def test_claim_does_not_revive_a_checkpoint_from_another_source_snapshot(legacy_db):
    """iss29-A01: the claim must not re-validate the checkpoint it invalidated.

    ``resume_watermark`` is the ONLY invalidation signal a checkpoint has. The
    claim used to stamp the current watermark while leaving the old
    ``resume_stage``/``resume_rowid``/``resume_run_id`` in place — which is
    precisely the act of declaring a stale checkpoint fresh again.

    What that costs: the revived point names ``stage='tracks'``, so ``walk_from``
    returns None for artists and albums, both walks are skipped, the tracks walk
    finds an empty album map and skips every row, and ``mark_done`` stamps the
    run as complete. The user gets an empty library that says it finished.
    """
    first_watermark = lib2_bootstrap.source_watermark(legacy_db)
    token = lib2_bootstrap.try_claim(legacy_db, watermark=first_watermark)
    assert token
    lib2_bootstrap.heartbeat(
        legacy_db, token, stage="tracks", current=1, total=9,
        rowid=60000, run_id="RUN-A",
    )
    # A crash keeps the checkpoint on purpose — that is what resume is for.
    lib2_bootstrap.mark_failed(legacy_db, token, "container restart")

    _add_legacy_artist(legacy_db)
    second_watermark = lib2_bootstrap.source_watermark(legacy_db)
    assert second_watermark != first_watermark

    state = lib2_bootstrap.get_state(legacy_db)
    assert lib2_bootstrap.resume_point_for(state, second_watermark) is None

    assert lib2_bootstrap.try_claim(legacy_db, watermark=second_watermark)

    # The claim has just stamped `second_watermark`. If it kept the row offsets
    # taken against `first_watermark`, they now compare equal and a THIRD run
    # would resume from them.
    revived = lib2_bootstrap.resume_point_for(
        lib2_bootstrap.get_state(legacy_db), second_watermark
    )
    assert revived is None


def test_claim_keeps_a_checkpoint_taken_against_the_same_snapshot(legacy_db):
    """The other side of iss29-A01: a genuine resume must still resume.

    Clearing too eagerly would silently turn every restart into a full
    re-migration, which is the cost the checkpoint exists to avoid.
    """
    watermark = lib2_bootstrap.source_watermark(legacy_db)
    token = lib2_bootstrap.try_claim(legacy_db, watermark=watermark)
    lib2_bootstrap.heartbeat(
        legacy_db, token, stage="albums", current=1, total=9,
        rowid=42, run_id="RUN-A",
    )
    lib2_bootstrap.mark_failed(legacy_db, token, "container restart")

    assert lib2_bootstrap.try_claim(legacy_db, watermark=watermark)

    resumed = lib2_bootstrap.resume_point_for(
        lib2_bootstrap.get_state(legacy_db), watermark
    )
    assert resumed is not None
    assert resumed.stage == "albums"
    assert resumed.rowid == 42
    assert resumed.run_id == "RUN-A"


def test_a_beat_carrying_a_rowid_is_never_throttled(legacy_db, monkeypatch):
    """iss29-A02: the throttle dropped exactly the beats that persist a resume point.

    ``heartbeat`` only writes a checkpoint when the beat carries both a rowid
    and a run id, and those beats are the stage openings and the finalize
    transition. The throttle discarded every beat with ``current != total``
    inside a 5 s window — so on any library whose walks finish quickly, the only
    checkpoint ever persisted was the FIRST one. That is what leaves
    ``resume_stage='tracks', resume_rowid=0`` after a fully successful run, and
    it is the value that makes iss29-A01 fatal rather than merely wasteful.
    """
    delivered = []
    real_heartbeat = lib2_bootstrap.heartbeat

    def _spy(database, owner_token, **kwargs):
        delivered.append(kwargs)
        return real_heartbeat(database, owner_token, **kwargs)

    monkeypatch.setattr(lib2_bootstrap, "heartbeat", _spy)

    result = lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled)
    assert result.get("success") is True

    checkpointed = {
        beat["stage"] for beat in delivered
        if beat.get("rowid") is not None and beat.get("run_id")
    }
    assert {"artists", "albums", "tracks", "finalizing"} <= checkpointed


def test_mark_done_stamps_the_watermark_the_walks_actually_saw(legacy_db):
    """iss29-A04: stamping the POST-run watermark loses concurrent writers.

    The walks are keyset scans taken at three different moments, and the whole
    post-import precache runs after them. Auto-import, wishlist downloads and
    the media-server sync all write to the legacy tables meanwhile. An artist
    that appears after the artists walk is never walked — but a watermark taken
    at the end counts it, so the next tick reports ``already_done`` and the
    artist stays invisible in V2 for the rest of the process lifetime.
    """
    def _post_import(_progress):
        _add_legacy_artist(legacy_db, artist_id=999, name="Arrived Mid-Run")

    result = lib2_bootstrap.run_bootstrap_if_needed(
        legacy_db, _enabled, post_import=_post_import
    )
    assert result.get("success") is True

    # The row that arrived mid-run was never walked, so the migration is not
    # done with respect to the source as it now stands.
    second = lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled)
    assert second.get("skipped") != "already_done"
