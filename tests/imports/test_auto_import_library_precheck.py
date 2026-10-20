"""auto-import runs the existing-library pre-check (SeadogsBooty follow-up).

the pre-check shipped on the manual import page only. a folder dropped into
staging went to the auto-import worker, which ran every file through the
pipeline and recorded nothing about what it decided. now the worker asks the
same gate, leaves owned tracks alone, keeps each track's decision on the
history row, and can clear a finished folder out of staging.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List

import pytest


class _DB:
    def __init__(self, path):
        self._path = path

    def _get_connection(self):
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn


@dataclass
class _Candidate:
    path: str
    name: str
    audio_files: List[str] = field(default_factory=list)
    disc_structure: Dict[int, List[str]] = field(default_factory=dict)
    folder_hash: str = "hash-1"
    is_single: bool = False


@pytest.fixture
def library(tmp_path):
    path = str(tmp_path / "library.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE albums (id INTEGER PRIMARY KEY, title TEXT NOT NULL)")
    conn.execute(
        """CREATE TABLE tracks (
            id INTEGER PRIMARY KEY, album_id INTEGER,
            spotify_track_id TEXT, itunes_track_id TEXT, deezer_id TEXT,
            tidal_id TEXT, qobuz_id TEXT, musicbrainz_recording_id TEXT,
            audiodb_id TEXT, soul_id TEXT, isrc TEXT, file_path TEXT)"""
    )
    conn.execute(
        """CREATE TABLE auto_import_history (
            id INTEGER PRIMARY KEY, folder_path TEXT, folder_hash TEXT, status TEXT,
            match_data TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"""
    )
    album_id = conn.execute("INSERT INTO albums (title) VALUES ('Collide With The Sky')").lastrowid
    lib_file = tmp_path / "Music" / "Pierce The Veil" / "Collide With The Sky" / "02 Hell Above.flac"
    lib_file.parent.mkdir(parents=True)
    lib_file.write_bytes(b"library copy")
    conn.execute(
        "INSERT INTO tracks (album_id, musicbrainz_recording_id, file_path) VALUES (?, 'mbid-owned', ?)",
        (album_id, str(lib_file)),
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    from core.imports import library_check
    from core.quality import selection

    monkeypatch.setattr(selection, "load_profile_by_id", lambda _pid: {})
    monkeypatch.setattr(library_check, "_library_file_has_metadata", lambda _p: True)


def _staging(tmp_path):
    album = tmp_path / "Staging" / "Pierce The Veil" / "Collide With The Sky"
    album.mkdir(parents=True)
    owned = album / "02 Hell Above.flac"
    new = album / "03 A Match Into Water.flac"
    owned.write_bytes(b"staged owned")
    new.write_bytes(b"staged new")
    (album / "cover.jpg").write_bytes(b"jpg")
    return album, owned, new


def _worker(tmp_path, library, config=None):
    from core.auto_import_worker import AutoImportWorker

    config = config or {}
    calls = []

    class _Cfg:
        def get(self, key, default=None):
            return config.get(key, default)

    def _pipeline(_key, ctx, path):
        # what the real pipeline reports for a clean import: moved, confirmed
        dest = tmp_path / "Music" / os.path.basename(path)
        shutil.move(path, dest)
        ctx["_pipeline_import_succeeded"] = True
        ctx["_final_processed_path"] = str(dest)
        calls.append(os.path.basename(path))

    worker = AutoImportWorker(
        database=_DB(library),
        staging_path=str(tmp_path / "Staging"),
        transfer_path=str(tmp_path / "Transfer"),
        process_callback=_pipeline,
        config_manager=_Cfg(),
    )
    worker._resolve_staging_path = lambda: str(tmp_path / "Staging")
    return worker, calls


def _match(path, name, number, mbid, match_type="mbid"):
    return {
        "track": {"id": f"t{number}", "name": name, "track_number": number, "disc_number": 1,
                  "duration_ms": 200000, "artists": [{"name": "Pierce The Veil"}], "mbid": mbid},
        "file": str(path), "confidence": 1.0, "match_type": match_type,
    }


IDENT = {"source": "musicbrainz", "artist_name": "Pierce The Veil", "artist_id": "a",
         "album_name": "Collide With The Sky", "album_id": "rel", "image_url": "",
         "release_date": "2012-07-17", "method": "tags"}


def _result(*matches):
    return {"matches": list(matches), "unmatched_files": [], "total_tracks": 12,
            "matched_count": len(matches), "confidence": 1.0,
            "album_data": {"id": "rel", "total_tracks": 12, "album_type": "album", "images": []}}


def test_owned_track_is_left_alone_and_the_rest_imports(tmp_path, library):
    album, owned, new = _staging(tmp_path)
    worker, calls = _worker(tmp_path, library)
    result = _result(_match(owned, "Hell Above", 2, "mbid-owned"),
                     _match(new, "A Match Into Water", 3, "mbid-new"))

    status, errors = worker._process_matches(
        _Candidate(path=str(album), name="Collide With The Sky", audio_files=[str(owned), str(new)]),
        IDENT, result)

    assert (status, errors) == ("completed", [])
    assert calls == ["03 A Match Into Water.flac"], "the owned track never reaches the pipeline"
    assert owned.read_bytes() == b"staged owned", "and its file is not touched"
    assert [m["import_status"] for m in result["matches"]] == ["skipped_owned", "completed"]
    assert "already in the library" in result["matches"][0]["import_reason"]
    assert result["summary"] == {"imported": 1, "upgraded": 0, "skipped_owned": 1, "failed": 0}


def test_a_fuzzy_pairing_is_never_skipped(tmp_path, library):
    # same recording id, but the file was paired by title: the gate fails open
    album, owned, _new = _staging(tmp_path)
    worker, calls = _worker(tmp_path, library)
    result = _result(_match(owned, "Hell Above", 2, "mbid-owned", match_type=None))
    worker._process_matches(_Candidate(path=str(album), name="x", audio_files=[str(owned)]), IDENT, result)
    assert calls == ["02 Hell Above.flac"]
    assert result["matches"][0]["import_status"] == "completed"


def test_the_decisions_survive_onto_the_history_row(tmp_path, library):
    album, owned, new = _staging(tmp_path)
    worker, _ = _worker(tmp_path, library)
    result = _result(_match(owned, "Hell Above", 2, "mbid-owned"),
                     _match(new, "A Match Into Water", 3, "mbid-new"))
    worker._process_matches(_Candidate(path=str(album), name="x", audio_files=[str(owned), str(new)]),
                            IDENT, result)

    stored = json.loads(worker._serialize_match_data(result))
    assert stored["summary"]["skipped_owned"] == 1
    assert stored["matches"][0]["import_status"] == "skipped_owned"
    assert stored["matches"][0]["import_reason"]

    # and the inbox hands them to the page
    from core.imports.inbox import _parse_match
    parsed = _parse_match(json.dumps(stored))
    assert parsed["summary"] == stored["summary"]
    assert parsed["matches"][0]["import_status"] == "skipped_owned"
    assert parsed["matches"][1]["import_status"] == "completed"


def test_skipped_files_left_in_the_folder_do_not_start_a_new_import(tmp_path, library):
    """one track imported, one owned: what's left hashes to a folder the
    history has never seen. it must read as this run's remnant, or the worker
    identifies and 'imports' it again on every scan."""
    album, owned, new = _staging(tmp_path)
    worker, _ = _worker(tmp_path, library)
    result = _result(_match(owned, "Hell Above", 2, "mbid-owned"),
                     _match(new, "A Match Into Water", 3, "mbid-new"))
    worker._process_matches(_Candidate(path=str(album), name="x", audio_files=[str(owned), str(new)]),
                            IDENT, result)
    conn = sqlite3.connect(library)
    conn.execute("INSERT INTO auto_import_history (folder_path, status, match_data) VALUES (?, 'completed', ?)",
                 (str(album), worker._serialize_match_data(result)))
    conn.commit()
    conn.close()

    leftover = _Candidate(path=str(album), name="x", audio_files=[str(owned)], folder_hash="new-hash")
    assert worker._is_already_processed(leftover) is True


def test_finished_folder_is_cleared_when_asked(tmp_path, library):
    album, owned, new = _staging(tmp_path)
    worker, _ = _worker(tmp_path, library, {"import.clear_finished_from_staging": True})
    cand = _Candidate(path=str(album), name="x", audio_files=[str(owned), str(new)])
    worker._process_matches(cand, IDENT, _result(_match(owned, "Hell Above", 2, "mbid-owned"),
                                                 _match(new, "A Match Into Water", 3, "mbid-new")))
    worker._clear_finished_folder(cand)

    assert not (tmp_path / "Staging" / "Pierce The Veil").exists(), "album and artist folders go"
    assert (tmp_path / "Staging").is_dir(), "the staging root never does"


def test_finished_folder_stays_by_default(tmp_path, library):
    album, owned, _new = _staging(tmp_path)
    worker, _ = _worker(tmp_path, library)
    worker._clear_finished_folder(_Candidate(path=str(album), name="x", audio_files=[str(owned)]))
    assert owned.exists() and (album / "cover.jpg").exists()


def _keeps_library_copy(_key, ctx, path):
    # what the real pipeline leaves when the track is already at its library
    # path and the staged file is not an improvement: no success flag, a
    # failure message, and the kept flag. the staged file stays put.
    ctx["_context_failure_msg"] = "Incoming file is not a verified improvement under the quality profile"
    ctx["_kept_existing_library_file"] = True


def test_a_library_copy_the_pipeline_kept_counts_as_owned_not_failed(tmp_path, library):
    """SeadogsBooty: 13 releases they already had all landed in failed /
    needs attention. the gate couldn't prove identity (fuzzy pair), the
    pipeline kept the library copy, and the worker called that a failure."""
    album, owned, _new = _staging(tmp_path)
    worker, _ = _worker(tmp_path, library)
    worker._process_callback = _keeps_library_copy
    result = _result(_match(owned, "Hell Above", 2, "mbid-owned", match_type=None))

    status, errors = worker._process_matches(
        _Candidate(path=str(album), name="x", audio_files=[str(owned)]), IDENT, result)

    assert (status, errors) == ("completed", [])
    assert result["matches"][0]["import_status"] == "skipped_owned"
    assert result["summary"] == {"imported": 0, "upgraded": 0, "skipped_owned": 1, "failed": 0}
    assert owned.read_bytes() == b"staged owned"


def test_a_real_pipeline_failure_says_why(tmp_path, library):
    album, owned, _new = _staging(tmp_path)
    worker, _ = _worker(tmp_path, library)

    def _fails(_key, ctx, _path):
        ctx["_context_failure_msg"] = "Missing artist context"

    worker._process_callback = _fails
    result = _result(_match(owned, "Hell Above", 2, "mbid-owned", match_type=None))
    status, errors = worker._process_matches(
        _Candidate(path=str(album), name="x", audio_files=[str(owned)]), IDENT, result)

    assert status == "failed"
    assert "Missing artist context" in errors[0], "not the generic 'did not confirm'"
