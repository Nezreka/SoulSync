"""#1567 follow-up: re-identifying the tracks of a poisoned album.

two things went wrong when the reporter re-identified the tracks of the bad
"Music" album one by one from the library:

1. every staged copy still carried the album tag "Music", so the scanner
   grouped them into one 5-file candidate. the hint is only read on a one-file
   candidate, so it was skipped and the bundle landed in the inbox as
   "Needs identifying" with a "Nickelback staging" search.
2. when the chosen release already had that song on disk the pipeline kept
   the library copy, and since 6105a3beb that counts as success. the finalize
   step then ran replace with no landing paths, so the same-home guard never
   saw the kept file and could delete it as "the old track".
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from core.auto_import_worker import AutoImportWorker
from core.imports.rematch_apply import is_reidentify_copy, staged_destination
from core.imports.rematch_hints import RematchHint


class _DB:
    def __init__(self, path):
        self._path = path

    def _get_connection(self):
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn


class _Cfg:
    def get(self, key, default=None):
        return default


def _worker(tmp_path, db_path=None, callback=None):
    w = AutoImportWorker(
        database=_DB(db_path or str(tmp_path / "x.db")),
        staging_path=str(tmp_path / "Staging"),
        transfer_path=str(tmp_path / "Transfer"),
        process_callback=callback or (lambda *a: None),
        config_manager=_Cfg(),
    )
    w._resolve_staging_path = lambda: str(tmp_path / "Staging")
    return w


# ── 1. a staged copy is its own candidate ──────────────────────────────────

def test_the_marker_matches_what_staging_writes():
    staged = staged_destination("/s", "/m/04 - Nickelback - Too Bad.mp3", "2Ri3igz6hljCeQmzEG5nIG")
    assert is_reidentify_copy(staged)
    assert not is_reidentify_copy("/s/04 - Nickelback - Too Bad.mp3")
    assert not is_reidentify_copy("/s/[reid-x] at the front.mp3")
    assert not is_reidentify_copy("")


def test_copies_from_one_bad_album_are_not_bundled(tmp_path, monkeypatch):
    import core.auto_import_worker as aiw

    staging = tmp_path / "Staging"
    staging.mkdir()
    names = [
        "03 - Default - Wasting My Time [reid-0qxQQAC4jkspuqcYcxZlim].mp3",
        "03 - Three Days Grace - Animal I Have Become [reid-1L679Eow0fZx7X2p3RVkVM].mp3",
        "04 - Nickelback - Too Bad [reid-2Ri3igz6hljCeQmzEG5nIG].mp3",
        "01 - Real Album Song.mp3",
        "02 - Real Album Song Two.mp3",
    ]
    for n in names:
        (staging / n).write_bytes(b"x")
    # every file says album "Music", the way the poisoned batch tagged them
    monkeypatch.setattr(aiw, "_read_file_tags", lambda p: {"album": "Music", "artist": "Nickelback"})

    cands = []
    _worker(tmp_path)._scan_directory(str(staging), cands, staging_root=str(staging))

    singles = sorted(os.path.basename(c.audio_files[0]) for c in cands if c.is_single)
    assert singles == sorted(names[:3])
    assert all(len(c.audio_files) == 1 for c in cands if c.is_single)
    # ordinary loose files still group by their tag like before
    groups = [c for c in cands if not c.is_single]
    assert len(groups) == 1
    assert sorted(os.path.basename(f) for f in groups[0].audio_files) == sorted(names[3:])


def test_the_hint_is_read_for_a_split_out_copy(tmp_path, monkeypatch):
    import core.auto_import_worker as aiw

    staging = tmp_path / "Staging"
    staging.mkdir()
    a = staging / "04 - Nickelback - Too Bad [reid-1].mp3"
    b = staging / "02 - Nickelback - How You Remind Me [reid-2].mp3"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    monkeypatch.setattr(aiw, "_read_file_tags", lambda p: {"album": "Music"})
    db = str(tmp_path / "h.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE rematch_hints (id INTEGER PRIMARY KEY, staged_path TEXT,
        content_hash TEXT, source TEXT, isrc TEXT, track_id TEXT, album_id TEXT, artist_id TEXT,
        track_title TEXT, album_name TEXT, artist_name TEXT, album_type TEXT, track_number INT,
        disc_number INT, replace_track_id INT, exempt_dedup INT, status TEXT DEFAULT 'pending',
        consumed_at TEXT)""")
    conn.execute("INSERT INTO rematch_hints (staged_path, source, album_id, album_name, track_title) "
                 "VALUES (?, 'deezer', 'alb', 'Silver Side Up', 'Too Bad')", (str(a),))
    conn.commit()
    conn.close()
    w = _worker(tmp_path, db_path=db)
    cands = []
    w._scan_directory(str(staging), cands, staging_root=str(staging))
    (cand,) = [c for c in cands if c.audio_files == [str(a)]]
    hint, ident = w._resolve_rematch_hint(cand)
    assert hint is not None and ident["album_name"] == "Silver Side Up"


# ── 2. a kept library copy is never deleted as the old track ───────────────

@pytest.fixture
def library(tmp_path):
    path = str(tmp_path / "library.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, file_path TEXT)")
    conn.execute("CREATE TABLE auto_import_history (id INTEGER PRIMARY KEY, folder_path TEXT, "
                 "folder_hash TEXT, status TEXT, match_data TEXT)")
    conn.execute("CREATE TABLE rematch_hints (id INTEGER PRIMARY KEY, status TEXT, consumed_at TEXT)")
    conn.execute("INSERT INTO rematch_hints (id, status) VALUES (1, 'pending')")
    conn.commit()
    conn.close()
    return path


@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    from core.imports import library_check
    from core.quality import selection

    monkeypatch.setattr(selection, "load_profile_by_id", lambda _pid: {})
    monkeypatch.setattr(library_check, "_library_file_has_metadata", lambda _p: True)


def _reidentify_into(tmp_path, library, kept_path, old_path, callback=None):
    """run one staged copy through _process_matches with a pipeline that keeps
    the library copy at ``kept_path``, then finalize the hint like the worker
    does. returns the import status."""
    staged = tmp_path / "Staging" / "02 - Motivation [reid-9].mp3"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"staged copy")
    conn = sqlite3.connect(library)
    conn.execute("INSERT INTO tracks (id, file_path) VALUES (9, ?)", (str(old_path),))
    conn.commit()
    conn.close()

    def _keeps(_key, ctx, _path):
        # what the real pipeline leaves: destination resolved, existing kept
        ctx["_final_processed_path"] = str(kept_path)
        ctx["_context_failure_msg"] = "Incoming file is not a verified improvement under the quality profile"
        ctx["_kept_existing_library_file"] = True

    w = _worker(tmp_path, db_path=library, callback=callback or _keeps)
    from tests.imports.test_auto_import_library_precheck import _Candidate
    cand = _Candidate(path=str(staged), name=staged.name, audio_files=[str(staged)], is_single=True)
    ident = {"source": "deezer", "artist_name": "Canon", "artist_id": "a", "album_name": "Loose Canon, Vol. 2",
             "album_id": "rel", "image_url": "", "release_date": "2014", "method": "rematch_hint"}
    result = {"matches": [{"track": {"id": "t2", "name": "Motivation", "track_number": 2, "disc_number": 1,
                                     "duration_ms": 277000, "artists": [{"name": "Canon"}]},
                           "file": str(staged), "confidence": 1.0, "match_type": None}],
              "unmatched_files": [], "total_tracks": 9, "matched_count": 1, "confidence": 1.0,
              "album_data": {"id": "rel", "total_tracks": 9, "album_type": "album", "images": []}}
    status, _errors = w._process_matches(cand, ident, result)
    hint = RematchHint(staged_path=str(staged), source="deezer", replace_track_id=9, id=1)
    w._finalize_rematch_hint(hint, getattr(cand, "_reid_final_paths", None))
    return status


def _rows(library):
    conn = sqlite3.connect(library)
    try:
        return [r[0] for r in conn.execute("SELECT id FROM tracks")], \
            conn.execute("SELECT status FROM rematch_hints WHERE id = 1").fetchone()[0]
    finally:
        conn.close()


def test_picking_the_release_the_file_already_sits_in_keeps_the_file(tmp_path, library):
    """kevin's Motivation: the chosen release's spot already held the
    original. keeping it is fine, deleting it as 'the old track' is data loss."""
    lib = tmp_path / "Music" / "Canon" / "Canon - Loose Canon, Vol. 2" / "02 - Motivation.mp3"
    lib.parent.mkdir(parents=True)
    lib.write_bytes(b"the only copy")

    assert _reidentify_into(tmp_path, library, kept_path=lib, old_path=lib) == "completed"

    assert lib.read_bytes() == b"the only copy"
    rows, hint_status = _rows(library)
    assert rows == [9]
    assert hint_status == "consumed", "the hint is done, it must not retry every scan"


def test_a_different_copy_already_in_the_release_still_replaces_the_old_one(tmp_path, library):
    """the release already has the song somewhere else: the old misfiled copy
    goes, like any re-identify with replace ticked."""
    kept = tmp_path / "Music" / "Canon" / "Canon - Loose Canon, Vol. 2" / "02 - Motivation.mp3"
    old = tmp_path / "Music" / "Nickelback" / "Music" / "02 - Motivation.mp3"
    for p, data in ((kept, b"already there"), (old, b"misfiled")):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    _reidentify_into(tmp_path, library, kept_path=kept, old_path=old)

    assert kept.read_bytes() == b"already there"
    assert not old.exists()
    rows, _ = _rows(library)
    assert rows == []


def test_an_exact_id_owned_skip_keeps_the_file_too(tmp_path, library, monkeypatch):
    """the library check can skip the copy as owned before the pipeline runs
    (exact id pairing, same release title). the copy it found is the
    original, so replace must not delete it."""
    from core.imports import library_check

    lib = tmp_path / "Music" / "Canon" / "Canon - Loose Canon, Vol. 2" / "02 - Motivation.mp3"
    lib.parent.mkdir(parents=True)
    lib.write_bytes(b"the only copy")
    monkeypatch.setattr(library_check, "check_track_library_status", lambda *a, **k: {
        "decision": library_check.DECISION_SKIP_OWNED, "reason": "already in the library",
        "library_path": str(lib)})

    def _never(*_a):
        raise AssertionError("a skipped track never reaches the pipeline")

    assert _reidentify_into(tmp_path, library, kept_path=lib, old_path=lib, callback=_never) == "completed"

    assert lib.read_bytes() == b"the only copy"
    rows, hint_status = _rows(library)
    assert rows == [9]
    assert hint_status == "consumed"
