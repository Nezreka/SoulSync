"""Route-level tests: the existing-library gate in ``album_process``.

An already-owned track (confident exact-ID pairing + same release + DB hit
+ satisfactory copy) must NOT reach ``post_process_matched_download`` — no
transcoding, tagging or file moves for it. The response carries per-track
dispositions and a summary for the UI.
"""

from __future__ import annotations

import logging
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.imports.routes import album_process


class _FakeDB:
    def __init__(self, path):
        self._path = path

    def _get_connection(self):
        return sqlite3.connect(self._path)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "library.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE albums (id INTEGER PRIMARY KEY, title TEXT NOT NULL)")
    conn.execute(
        """CREATE TABLE tracks (
            id INTEGER PRIMARY KEY,
            album_id INTEGER,
            spotify_track_id TEXT, itunes_track_id TEXT, deezer_id TEXT,
            tidal_id TEXT, qobuz_id TEXT, musicbrainz_recording_id TEXT,
            audiodb_id TEXT, soul_id TEXT, isrc TEXT, file_path TEXT
        )"""
    )
    conn.commit()
    conn.close()
    return path


def _add_owned(db_path, mbid, file_path, album_title="Test Album"):
    conn = sqlite3.connect(db_path)
    cur = conn.execute("INSERT INTO albums (title) VALUES (?)", (album_title,))
    album_id = cur.lastrowid
    conn.execute(
        "INSERT INTO tracks (album_id, musicbrainz_recording_id, file_path)"
        " VALUES (?, ?, ?)",
        (album_id, mbid, file_path),
    )
    conn.commit()
    conn.close()


def _staging_file(tmp_path, name):
    path = tmp_path / "staging" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake audio")
    return {"full_path": str(path), "filename": name}


def _match(track_name, mbid, staging, match_type="mbid", confidence=1.0):
    return {
        "track": {"name": track_name, "mbid": mbid, "track_number": 1,
                  "disc_number": 1},
        "staging_file": staging,
        "confidence": confidence,
        "match_type": match_type,
    }


def _runtime(processed_log, allowed_root):
    return SimpleNamespace(
        post_process_matched_download=lambda key, ctx, path: processed_log.append(path),
        is_active_media_server_ready=lambda: (True, ""),
        resolve_album_artist_context=lambda album, source="": {},
        build_album_import_context=lambda album, track, **kw: {"track": track},
        get_allowed_import_roots=lambda: [allowed_root],
        profile_id=None,
        add_activity_item=lambda *a: None,
        refresh_import_suggestions_cache=lambda: None,
        logger=logging.getLogger("test"),
    )


def _album():
    return {"id": "album-1", "name": "Test Album", "artist": "Test Artist",
            "source": "deezer"}


def _has_metadata():
    # Library copies in these tests are treated as satisfactory (tagged).
    return patch(
        "core.imports.library_check._library_file_has_metadata", return_value=True
    )


def test_owned_track_skipped_before_processing(db_path, tmp_path):
    lib_file = tmp_path / "library" / "owned.mp3"
    lib_file.parent.mkdir(parents=True)
    lib_file.write_bytes(b"library copy")
    _add_owned(db_path, "mbid-owned", str(lib_file))

    owned = _match("Owned Song", "mbid-owned", _staging_file(tmp_path, "owned.mp3"))
    new = _match("New Song", "mbid-new", _staging_file(tmp_path, "new.mp3"))
    processed_log = []
    data = {"album": _album(), "matches": [owned, new]}

    with patch("core.imports.routes._get_import_db",
               return_value=_FakeDB(db_path)), _has_metadata():
        payload, status = album_process(_runtime(processed_log, str(tmp_path)), data)

    assert status == 200
    assert payload["success"] is True
    # Only the new track reached the pipeline.
    assert len(processed_log) == 1
    assert processed_log[0].endswith("new.mp3")
    # Dispositions + summary for the UI.
    decisions = {d["track_name"]: d["decision"] for d in payload["dispositions"]}
    assert decisions == {"Owned Song": "skipped_owned", "New Song": "imported"}
    assert payload["summary"]["skipped_owned"] == 1
    assert payload["summary"]["imported"] == 1
    assert payload["processed"] == 1


def test_fuzzy_match_is_never_skipped(db_path, tmp_path):
    lib_file = tmp_path / "library" / "maybe.mp3"
    lib_file.parent.mkdir(parents=True)
    lib_file.write_bytes(b"library copy")
    _add_owned(db_path, "mbid-maybe", str(lib_file))

    fuzzy = _match("Maybe Song", "mbid-maybe",
                   _staging_file(tmp_path, "maybe.mp3"),
                   match_type=None, confidence=0.72)
    processed_log = []

    with patch("core.imports.routes._get_import_db",
               return_value=_FakeDB(db_path)), _has_metadata():
        payload, status = album_process(
            _runtime(processed_log, str(tmp_path)),
            {"album": _album(), "matches": [fuzzy]},
        )

    assert status == 200
    assert len(processed_log) == 1  # proceeded: fail open
    assert payload["dispositions"][0]["decision"] == "imported"


def test_different_release_is_not_skipped(db_path, tmp_path):
    # Recording owned via the single; the album import must NOT skip it.
    lib_file = tmp_path / "library" / "single.mp3"
    lib_file.parent.mkdir(parents=True)
    lib_file.write_bytes(b"library copy")
    _add_owned(db_path, "mbid-song", str(lib_file),
               album_title="Song (Single)")

    single_track = _match("Song", "mbid-song", _staging_file(tmp_path, "song.mp3"))
    processed_log = []

    with patch("core.imports.routes._get_import_db",
               return_value=_FakeDB(db_path)), _has_metadata():
        payload, status = album_process(
            _runtime(processed_log, str(tmp_path)),
            {"album": _album(), "matches": [single_track]},
        )

    assert status == 200
    assert len(processed_log) == 1  # proceeded: the album needs its own copy
    assert payload["dispositions"][0]["decision"] == "imported"
    assert "different release" in payload["dispositions"][0]["reason"]


def test_upgrade_routing_counts_as_upgraded(db_path, tmp_path):
    # replace_lower on + the measured arbiter approves: the better file
    # proceeds and counts as upgraded.
    lib_file = tmp_path / "library" / "song.mp3"
    lib_file.parent.mkdir(parents=True)
    lib_file.write_bytes(b"library mp3")
    _add_owned(db_path, "mbid-song", str(lib_file))

    better = _match("Song", "mbid-song", _staging_file(tmp_path, "song.flac"))
    processed_log = []

    with patch("core.imports.routes._get_import_db",
               return_value=_FakeDB(db_path)), \
         patch("core.imports.quality_replace.is_profile_upgrade",
               return_value=True), \
         patch("core.quality.selection.load_profile_by_id",
               return_value={"replace_lower_quality": True}):
        payload, status = album_process(
            _runtime(processed_log, str(tmp_path)),
            {"album": _album(), "matches": [better]},
        )

    assert status == 200
    assert len(processed_log) == 1  # proceeded to the upgrade path
    assert payload["dispositions"][0]["decision"] == "upgraded"
    assert payload["summary"]["upgraded"] == 1


def test_arbiter_decline_counts_as_skipped_not_upgraded(db_path, tmp_path):
    # replace_lower on, but the measured arbiter declines: the pipeline's
    # protection would decline too, so this is a skip — never miscounted
    # as "upgraded".
    lib_file = tmp_path / "library" / "song.mp3"
    lib_file.parent.mkdir(parents=True)
    lib_file.write_bytes(b"library mp3")
    _add_owned(db_path, "mbid-song", str(lib_file))

    incoming = _match("Song", "mbid-song", _staging_file(tmp_path, "song.flac"))
    processed_log = []

    with patch("core.imports.routes._get_import_db",
               return_value=_FakeDB(db_path)), \
         patch("core.imports.quality_replace.is_profile_upgrade",
               return_value=False), \
         patch("core.quality.selection.load_profile_by_id",
               return_value={"replace_lower_quality": True}):
        payload, status = album_process(
            _runtime(processed_log, str(tmp_path)),
            {"album": _album(), "matches": [incoming]},
        )

    assert status == 200
    assert len(processed_log) == 0  # never reached the pipeline
    assert payload["dispositions"][0]["decision"] == "skipped_owned"
    assert payload["summary"]["skipped_owned"] == 1
    assert payload["summary"]["upgraded"] == 0


def test_manually_reassigned_file_cannot_skip(db_path, tmp_path):
    # A manual re-assignment nulls match_type (human pairing, not exact-ID
    # proof): even with a DB hit on the same release, it must fail open.
    lib_file = tmp_path / "library" / "song.mp3"
    lib_file.parent.mkdir(parents=True)
    lib_file.write_bytes(b"library copy")
    _add_owned(db_path, "mbid-song", str(lib_file))

    reassigned = _match("Song", "mbid-song", _staging_file(tmp_path, "other.mp3"))
    reassigned["match_type"] = None  # what the UI now sends on re-assign
    reassigned["confidence"] = 0
    processed_log = []

    with patch("core.imports.routes._get_import_db",
               return_value=_FakeDB(db_path)), _has_metadata():
        payload, status = album_process(
            _runtime(processed_log, str(tmp_path)),
            {"album": _album(), "matches": [reassigned]},
        )

    assert status == 200
    assert len(processed_log) == 1  # proceeded: fail open
    assert payload["dispositions"][0]["decision"] == "imported"


def _cleanup_setting(on):
    return patch("core.imports.routes._clear_finished_enabled", return_value=on)


def _owned_album_in_staging(db_path, tmp_path):
    lib_file = tmp_path / "library" / "owned.mp3"
    lib_file.parent.mkdir(parents=True)
    lib_file.write_bytes(b"library copy")
    _add_owned(db_path, "mbid-owned", str(lib_file))
    folder = tmp_path / "staging" / "Test Artist" / "Test Album"
    folder.mkdir(parents=True)
    (folder / "owned.mp3").write_bytes(b"fake audio")
    (folder / "cover.jpg").write_bytes(b"jpg")
    return folder, {"full_path": str(folder / "owned.mp3"), "filename": "owned.mp3"}


def _runtime_with_staging(processed_log, tmp_path):
    runtime = _runtime(processed_log, str(tmp_path))
    runtime.get_staging_path = lambda: str(tmp_path / "staging")
    return runtime


def test_clear_finished_removes_the_owned_album_folder(db_path, tmp_path, monkeypatch):
    from core.imports import file_ops
    monkeypatch.setattr(file_ops, "protected_root_dirs", lambda: set())
    folder, staged = _owned_album_in_staging(db_path, tmp_path)
    data = {"album": _album(), "matches": [_match("Owned Song", "mbid-owned", staged)]}

    with patch("core.imports.routes._get_import_db", return_value=_FakeDB(db_path)), \
            _has_metadata(), _cleanup_setting(True):
        payload, _ = album_process(_runtime_with_staging([], tmp_path), data)

    assert payload["summary"]["skipped_owned"] == 1
    assert not (tmp_path / "staging" / "Test Artist").exists()
    assert (tmp_path / "staging").is_dir()


def test_owned_album_stays_in_staging_by_default(db_path, tmp_path):
    folder, staged = _owned_album_in_staging(db_path, tmp_path)
    data = {"album": _album(), "matches": [_match("Owned Song", "mbid-owned", staged)]}

    with patch("core.imports.routes._get_import_db", return_value=_FakeDB(db_path)), \
            _has_metadata(), _cleanup_setting(False):
        album_process(_runtime_with_staging([], tmp_path), data)

    assert (folder / "owned.mp3").exists()


def test_library_copy_the_pipeline_kept_is_not_called_imported(db_path, tmp_path):
    # fuzzy pair, so the gate lets it through; the pipeline then finds the
    # track already at its library path and keeps that copy. nothing was
    # imported, so the summary can't say it was.
    fuzzy = _match("Maybe Song", "mbid-x", _staging_file(tmp_path, "maybe.mp3"),
                   match_type=None, confidence=0.72)
    processed_log = []
    runtime = _runtime(processed_log, str(tmp_path))

    def _keeps(_key, ctx, path):
        processed_log.append(path)
        ctx["_context_failure_msg"] = "Incoming file is not a verified improvement under the quality profile"
        ctx["_kept_existing_library_file"] = True

    runtime.post_process_matched_download = _keeps
    with patch("core.imports.routes._get_import_db", return_value=_FakeDB(db_path)), _has_metadata():
        payload, status = album_process(runtime, {"album": _album(), "matches": [fuzzy]})

    assert status == 200
    assert payload["dispositions"][0]["decision"] == "skipped_owned"
    assert payload["summary"]["skipped_owned"] == 1
    assert payload["summary"]["imported"] == 0
    assert payload["processed"] == 0
