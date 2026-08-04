"""Post-import scheduling for album-scoped provider identity healing."""

from __future__ import annotations

import threading

import core.library2.track_reconcile_trigger as TRT


class _Config:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_import_burst_coalesces_and_deduplicates_album_ids(legacy_db):
    TRT.reset_for_tests()
    done = threading.Event()
    calls = []

    def runner(database, *, album_ids):
        calls.append((database, album_ids))
        done.set()
        return {}

    config = _Config({
        "library_v2.track_identity_reconcile.debounce_seconds": 0.1,
    })
    armed = [
        TRT.schedule_album_track_reconcile(
            legacy_db,
            album_id,
            config,
            runner=runner,
        )
        for album_id in (10, 10, 11, 10)
    ]

    assert done.wait(5)
    assert TRT.wait_for_idle(5)
    assert calls == [(legacy_db, [10, 11])]
    assert armed.count(True) == 1


def test_fresh_file_resolves_its_album_before_scheduling(legacy_db):
    from core.library2.importer import import_legacy_library

    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    try:
        row = conn.execute(
            """SELECT tf.id AS file_id, t.album_id
                 FROM lib2_track_files tf
                 JOIN lib2_tracks t ON t.id=tf.track_id
                ORDER BY tf.id LIMIT 1"""
        ).fetchone()
    finally:
        conn.close()

    TRT.reset_for_tests()
    done = threading.Event()
    seen = []

    def runner(_database, *, album_ids):
        seen.extend(album_ids)
        done.set()
        return {}

    assert TRT.schedule_file_track_reconcile(
        legacy_db,
        row["file_id"],
        _Config({"library_v2.track_identity_reconcile.debounce_seconds": 0}),
        runner=runner,
    )
    assert done.wait(5)
    assert seen == [row["album_id"]]


def test_trigger_is_fail_open_and_can_be_disabled(legacy_db):
    TRT.reset_for_tests()

    class ExplodingConfig:
        def get(self, _key, _default=None):
            raise RuntimeError("config unavailable")

    assert not TRT.schedule_album_track_reconcile(
        legacy_db,
        10,
        ExplodingConfig(),
    )
    assert not TRT.schedule_album_track_reconcile(
        legacy_db,
        10,
        _Config({
            "library_v2.track_identity_reconcile.auto_after_import": False,
        }),
    )
