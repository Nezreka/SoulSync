"""the background matcher for files that imported without a match"""

import json
import sqlite3

import pytest

from core.library2.library_watch import SettleClock
from core.library2.unmatched_identify import (
    identify_file,
    stale_entries,
    stored_results,
    strong_matches_ready,
)
from core.library2.unmatched_matcher import UnmatchedMatcher
from core.library2.unmatched_review import list_unmatched
from tests import lib2_seed


@pytest.fixture
def db(tmp_path):
    from core.library2.schema import ensure_library_v2_schema
    path = str(tmp_path / "m.db")
    conn = lib2_seed.row_conn(path)
    ensure_library_v2_schema(conn)
    for n in range(5):
        lib2_seed.track(conn, "Unknown Artist", "Unknown Album", f"Song {n}",
                        path=str(tmp_path / f"0{n} - Song {n}.flac"))
    conn.commit()
    conn.close()
    return path


def _connect(path):
    def connect():
        conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    return connect


def _identify(fingerprint="ok", confidence=0.95):
    def identify(conn, file_id, *, refresh=False):
        candidates = [{"source": "deezer", "track_id": f"t{file_id}", "track_title": "x",
                       "artist_name": "a", "album_name": "b", "album_type": "album", "year": None,
                       "total_tracks": None, "image_url": "", "isrc": None,
                       "confidence": confidence, "reasons": []}]
        return identify_file(conn, file_id, lookup=lambda p: {"status": fingerprint},
                             search=lambda *a: [], source=None, resolve=lambda p: p,
                             refresh=refresh) and _fake_store(conn, file_id, candidates, fingerprint)
    return identify


def _fake_store(conn, file_id, candidates, fingerprint):
    # identify_file stored a no_match; give it the candidate a real search would
    row = conn.execute("SELECT result_json FROM lib2_unmatched_matches WHERE file_id=?",
                       (file_id,)).fetchone()
    result = json.loads(row["result_json"])
    result.update(status="matched", candidates=candidates)
    conn.execute("UPDATE lib2_unmatched_matches SET status='matched', best_confidence=?, "
                 "result_json=? WHERE file_id=?",
                 (candidates[0]["confidence"], json.dumps(result), file_id))
    conn.commit()
    return result


def test_a_sweep_matches_every_file_once_and_the_review_reads_it(db):
    calls = []
    inner = _identify()

    def identify(conn, file_id, *, refresh=False):
        calls.append(file_id)
        return inner(conn, file_id, refresh=refresh)

    matcher = UnmatchedMatcher(_connect(db), workers=3, identify=identify,
                               acoustid_ready=lambda: True)
    assert matcher.sweep() == 5
    assert sorted(calls) == sorted(set(calls)) and len(calls) == 5
    assert matcher.status() == {"running": False, "done": 5, "total": 5}
    # nothing left: the next sweep does no work
    assert matcher.sweep() == 0
    conn = _connect(db)()
    entries = list_unmatched(conn)
    assert all(e["identified"]["status"] == "matched" for e in entries)
    assert strong_matches_ready(conn) == 5


def test_a_changed_file_is_matched_again(db):
    matcher = UnmatchedMatcher(_connect(db), identify=_identify(), acoustid_ready=lambda: True)
    matcher.sweep()
    conn = _connect(db)()
    conn.execute("UPDATE lib2_track_files SET size=123 WHERE path LIKE '%Song 2.flac'")
    conn.commit()
    entries = list_unmatched(conn)
    assert [e["file_name"] for e in stale_entries(conn, entries, acoustid_ready=True)] == [
        "02 - Song 2.flac"]
    # and its old answer no longer counts
    assert strong_matches_ready(conn) == 4
    assert len(stored_results(conn, entries)) == 4


def test_no_fingerprint_is_retried_once_acoustid_works(db):
    matcher = UnmatchedMatcher(_connect(db), identify=_identify(fingerprint="unavailable"),
                               acoustid_ready=lambda: False)
    matcher.sweep()
    conn = _connect(db)()
    entries = list_unmatched(conn)
    assert stale_entries(conn, entries, acoustid_ready=False) == []
    assert len(stale_entries(conn, entries, acoustid_ready=True)) == 5


def test_weak_matches_are_not_counted_ready(db):
    UnmatchedMatcher(_connect(db), identify=_identify(confidence=0.6),
                     acoustid_ready=lambda: True).sweep()
    assert strong_matches_ready(_connect(db)()) == 0


def test_the_banner_count_without_any_matches_yet(tmp_path):
    from core.library2.schema import ensure_library_v2_schema
    conn = lib2_seed.row_conn(str(tmp_path / "bare.db"))
    ensure_library_v2_schema(conn)
    # an install from before the table: the server starts before it's made
    conn.execute("DROP TABLE lib2_unmatched_matches")
    lib2_seed.track(conn, "Unknown Artist", "Unknown Album", "x", path="/x.flac")
    # no table yet: a read never creates it
    assert strong_matches_ready(conn) == 0
    assert stored_results(conn, list_unmatched(conn)) == {}


def test_the_watch_fires_once_an_import_settles():
    clock = SettleClock(settle_s=20, max_wait_s=120)
    assert clock.observe(100, 0) is False          # first look sets the baseline
    assert clock.observe(100, 15) is False         # nothing new
    assert clock.observe(101, 30) is False         # a file lands
    assert clock.observe(102, 45) is False         # another
    assert clock.observe(102, 60) is False         # 15s quiet
    assert clock.observe(102, 66) is True          # 21s quiet: settled
    assert clock.observe(102, 90) is False         # and only once


def test_a_long_import_still_fires_every_couple_of_minutes():
    clock = SettleClock(settle_s=20, max_wait_s=120)
    clock.observe(0, 0)
    fired = [t for t in range(15, 400, 15) if clock.observe(t, t)]
    assert fired and fired[0] <= 135
    assert all(b - a >= 120 for a, b in zip(fired, fired[1:]))
