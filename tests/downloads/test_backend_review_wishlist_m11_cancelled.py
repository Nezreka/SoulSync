"""M11 companion: the cancelled-track promotion cap.

``add_cancelled_tracks_to_failed_tracks`` only scanned the first 100 queue
entries (``max_process=100``). Cancelled tracks past the cap were never
promoted into ``permanently_failed_tracks``, so they missed the M11 attempt
stamp — a track cancelled in a big batch never escalated backoff and burned
a fresh search every cycle. (The wishlist row itself survives; this is about
the retry clock, not data loss.)
"""
import logging

import core.wishlist.processing as processing

logger = logging.getLogger("tests")


class _FakeLogger:
    def error(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass


def _cancelled_batch(n):
    queue = [f"t{i}" for i in range(n)]
    tasks = {
        f"t{i}": {
            "status": "cancelled",
            "track_index": i,
            "track_info": {"name": f"Song {i}", "artist": "Artist A",
                           "artists": [{"name": "Artist A"}]},
            "cached_candidates": [],
        }
        for i in range(n)
    }
    return {"queue": queue, "cancelled_tracks": set(range(n))}, tasks


def test_m11_cancelled_promotion_has_no_100_cap():
    batch, tasks = _cancelled_batch(120)
    failed = []
    processed = processing.add_cancelled_tracks_to_failed_tracks(
        batch, tasks, failed, logger=_FakeLogger())
    assert processed == 120, (
        f"only {processed}/120 cancelled tracks promoted — cap dropped them")
    assert len(failed) == 120
    assert {f["track_name"] for f in failed} == {f"Song {i}" for i in range(120)}
    assert all(f["failure_reason"] == "Download cancelled" for f in failed)


def test_m11_cancelled_promotion_small_batch_unchanged():
    batch, tasks = _cancelled_batch(3)
    failed = []
    processed = processing.add_cancelled_tracks_to_failed_tracks(
        batch, tasks, failed, logger=_FakeLogger())
    assert processed == 3
    assert [f["track_name"] for f in failed] == ["Song 0", "Song 1", "Song 2"]
