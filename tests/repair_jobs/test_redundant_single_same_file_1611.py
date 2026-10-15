"""#1611 (kevin2xk): Redundant Singles said "Faint" was a single on "Meteora
(Bonus Edition)" that also sits on album "None", track 7. there's one Faint in
the library. two things lined up: an album with no stored track count read as
0 tracks, so every track on it counted as a single, and a ghost second row for
the same file passed as "the album copy". Remove Single would have deleted the
only Faint.
"""

from __future__ import annotations

from pathlib import Path

from core.repair_jobs import single_album_dedup as sad
from core.repair_jobs.base import JobContext
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


def _db(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES ('lp', 'Linkin Park')")
        conn.commit()
    return db


def _album(db, aid, title, record_type=None, track_count=None):
    with db._get_connection() as conn:
        conn.execute("INSERT INTO albums (id, artist_id, title, record_type, track_count) VALUES (?, 'lp', ?, ?, ?)",
                     (aid, title, record_type, track_count))
        conn.commit()


def _track(db, tid, aid, title, path, track_number=None):
    with db._get_connection() as conn:
        conn.execute("INSERT INTO tracks (id, artist_id, album_id, title, file_path, track_number) "
                     "VALUES (?, 'lp', ?, ?, ?, ?)", (tid, aid, title, str(path), track_number))
        conn.commit()


def _scan(db):
    findings = []
    sad.SingleAlbumDedupJob().scan(JobContext(
        db=db, transfer_folder="/nonexistent", config_manager=None,
        create_finding=lambda **f: findings.append(f) or True,
        should_stop=lambda: False, is_paused=lambda: False))
    return findings


METEORA = ["Foreword", "Don't Stay", "Somewhere I Belong", "Lying from You", "Hit the Floor",
           "Easier to Run", "Faint", "Figure.09"]


def test_an_album_with_no_stored_count_is_not_a_pile_of_singles(tmp_path):
    db = _db(tmp_path)
    _album(db, "met", "Meteora (Bonus Edition)")            # no type, no count
    for i, t in enumerate(METEORA, start=1):
        _track(db, i, "met", t, f"/m/LP/Meteora/{i:02d} - {t}.mp3", i)
    _album(db, "ghost", "None", record_type="album", track_count=12)
    _track(db, 100, "ghost", "Faint", "/m/LP/Elsewhere/07 - Faint.mp3", 7)

    assert _scan(db) == []


def test_the_same_file_twice_is_never_a_redundant_single(tmp_path):
    db = _db(tmp_path)
    _album(db, "sg", "Faint", record_type="single", track_count=1)
    _album(db, "ghost", "None", record_type="album", track_count=12)
    _track(db, 1, "sg", "Faint", "/m/LP/Meteora/07 - Faint.mp3", 7)
    _track(db, 2, "ghost", "Faint", "/m/LP/Meteora/07 - Faint.mp3", 7)

    assert _scan(db) == []


def test_a_real_single_with_a_separate_album_copy_is_still_flagged(tmp_path):
    db = _db(tmp_path)
    _album(db, "sg", "Faint", record_type="single", track_count=1)
    _album(db, "met", "Meteora", record_type="album", track_count=12)
    _track(db, 1, "sg", "Faint", "/m/LP/Faint/01 - Faint.mp3", 1)
    _track(db, 2, "met", "Faint", "/m/LP/Meteora/07 - Faint.mp3", 7)

    found = _scan(db)
    assert [f["entity_id"] for f in found] == ["1"]


def test_an_untyped_lone_track_still_counts_as_a_single(tmp_path):
    db = _db(tmp_path)
    _album(db, "lone", "Faint")                             # no type, no count, one track
    _album(db, "met", "Meteora", record_type="album", track_count=12)
    _track(db, 1, "lone", "Faint", "/m/LP/Faint/01 - Faint.mp3", 1)
    _track(db, 2, "met", "Faint", "/m/LP/Meteora/07 - Faint.mp3", 7)

    assert [f["entity_id"] for f in _scan(db)] == ["1"]


# ── the fix action ──

def _worker(db, tmp_path):
    w = RepairWorker.__new__(RepairWorker)
    w.db = db
    w.transfer_folder = str(tmp_path)
    w._config_manager = None
    return w


def _details(single, album):
    return {"single_track": {"id": 1, "file_path": str(single)},
            "album_track": {"id": 2, "file_path": str(album)}}


def test_remove_single_refuses_when_both_rows_are_one_file(tmp_path: Path):
    db = _db(tmp_path)
    _album(db, "a", "Meteora")
    faint = tmp_path / "07 - Faint.mp3"
    faint.write_bytes(b"only copy")
    _track(db, 1, "a", "Faint", faint)
    _track(db, 2, "a", "Faint", faint)

    res = _worker(db, tmp_path)._fix_single_album_redundant("track", 1, str(faint), _details(faint, faint))

    assert res["success"] is False
    assert faint.exists()


def test_remove_single_refuses_when_the_album_copy_is_gone(tmp_path: Path):
    db = _db(tmp_path)
    _album(db, "a", "Meteora")
    single = tmp_path / "single.mp3"
    single.write_bytes(b"single")
    _track(db, 1, "a", "Faint", single)
    _track(db, 2, "a", "Faint", tmp_path / "missing.mp3")

    res = _worker(db, tmp_path)._fix_single_album_redundant(
        "track", 1, str(single), _details(single, tmp_path / "missing.mp3"))

    assert res["success"] is False and single.exists()


def test_remove_single_still_works_with_a_real_album_copy(tmp_path: Path):
    db = _db(tmp_path)
    _album(db, "a", "Meteora")
    single, album = tmp_path / "single.mp3", tmp_path / "album.mp3"
    single.write_bytes(b"single")
    album.write_bytes(b"album")
    _track(db, 1, "a", "Faint", single)
    _track(db, 2, "a", "Faint", album)

    res = _worker(db, tmp_path)._fix_single_album_redundant("track", 1, str(single), _details(single, album))

    assert res["success"] is True
    assert not single.exists() and album.exists()
