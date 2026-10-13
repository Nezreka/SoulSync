"""Tests for the existing-library pre-check (core/imports/library_check.py).

The hard safety rule under test: skip ONLY on confident exact-ID pairings
(match_type 'mbid'/'isrc') of the SAME release. Everything uncertain fails
open (proceeds) — a false "already owned" is silent data loss, a needless
import is just wasted work.

"Satisfactory" mirrors the pipeline's own replacement arbiter: with
``replace_lower`` on, the measured ``is_profile_upgrade`` decides; without
it, only a metadata-less library copy is refreshed.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from core.imports.library_check import (
    DECISION_IMPORT,
    DECISION_SKIP_OWNED,
    DECISION_UNCERTAIN,
    DECISION_UPGRADE,
    check_track_library_status,
)


class _FakeDB:
    """Minimal stand-in for the music database: fresh connection per call
    (the real lookup closes the connection in a finally block)."""

    def __init__(self, path):
        self._path = path

    def _get_connection(self):
        return sqlite3.connect(self._path)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "library.db")
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE albums (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL
        )"""
    )
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


@pytest.fixture
def db(db_path):
    return _FakeDB(db_path)


def _add_album(db_path, title="Test Album"):
    conn = sqlite3.connect(db_path)
    cur = conn.execute("INSERT INTO albums (title) VALUES (?)", (title,))
    album_id = cur.lastrowid
    conn.commit()
    conn.close()
    return album_id


def _add_track(db_path, mbid=None, isrc=None, file_path="", album_id=None):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO tracks (album_id, musicbrainz_recording_id, isrc, file_path)"
        " VALUES (?, ?, ?, ?)",
        (album_id, mbid, isrc, file_path),
    )
    conn.commit()
    conn.close()


def _track(**overrides):
    payload = {"name": "Test Song", "mbid": "mbid-123"}
    payload.update(overrides)
    return payload


def _staging(tmp_path, name="song.mp3"):
    path = tmp_path / name
    path.write_bytes(b"fake audio")
    return str(path)


def _owned(db_path, tmp_path, album_title="Test Album", **kw):
    """Add a library copy; returns (lib_file_path, album_id)."""
    lib_file = tmp_path / "library" / kw.pop("lib_name", "song.mp3")
    lib_file.parent.mkdir(parents=True, exist_ok=True)
    lib_file.write_bytes(b"library copy")
    album_id = _add_album(db_path, album_title)
    _add_track(db_path, file_path=str(lib_file), album_id=album_id, **kw)
    return str(lib_file), album_id


# ---------------------------------------------------------------------------
# skip_owned: confident pairing + same release + DB hit + satisfactory copy
# ---------------------------------------------------------------------------

def test_skips_on_mbid_match_with_file_present(db, db_path, tmp_path):
    _owned(db_path, tmp_path, mbid="mbid-123")

    with patch(
        "core.imports.library_check._library_file_has_metadata", return_value=True
    ):
        result = check_track_library_status(
            db, track=_track(), staging_file_path=_staging(tmp_path),
            match_type="mbid", confidence=1.0, album_name="Test Album",
        )

    assert result["decision"] == DECISION_SKIP_OWNED
    assert "already in the library" in result["reason"]


def test_skips_on_isrc_match(db, db_path, tmp_path):
    _owned(db_path, tmp_path, isrc="US-ABC-12-34567", lib_name="song.flac")

    with patch(
        "core.imports.library_check._library_file_has_metadata", return_value=True
    ):
        result = check_track_library_status(
            db, track=_track(mbid=None, isrc="US-ABC-12-34567"),
            staging_file_path=_staging(tmp_path, "song.flac"),
            match_type="isrc", confidence=1.0, album_name="Test Album",
        )

    assert result["decision"] == DECISION_SKIP_OWNED


def test_match_type_is_case_insensitive(db, db_path, tmp_path):
    _owned(db_path, tmp_path, mbid="mbid-123")

    with patch(
        "core.imports.library_check._library_file_has_metadata", return_value=True
    ):
        result = check_track_library_status(
            db, track=_track(), staging_file_path=_staging(tmp_path),
            match_type="MBID", confidence=1.0, album_name="Test Album",
        )

    assert result["decision"] == DECISION_SKIP_OWNED


def test_album_title_match_is_case_insensitive(db, db_path, tmp_path):
    _owned(db_path, tmp_path, mbid="mbid-123", album_title="  TEST ALBUM  ")

    with patch(
        "core.imports.library_check._library_file_has_metadata", return_value=True
    ):
        result = check_track_library_status(
            db, track=_track(), staging_file_path=_staging(tmp_path),
            match_type="mbid", confidence=1.0, album_name="test album",
        )

    assert result["decision"] == DECISION_SKIP_OWNED


# ---------------------------------------------------------------------------
# release scoping: a recording owned under a DIFFERENT release never skips
# ---------------------------------------------------------------------------

def test_different_release_never_skips(db, db_path, tmp_path):
    # The recording is owned via the single; importing the album must NOT
    # skip — the album would land incomplete.
    _owned(db_path, tmp_path, mbid="mbid-123", album_title="Test Song (Single)")

    with patch(
        "core.imports.library_check._library_file_has_metadata", return_value=True
    ):
        result = check_track_library_status(
            db, track=_track(), staging_file_path=_staging(tmp_path),
            match_type="mbid", confidence=1.0, album_name="Test Album",
        )

    assert result["decision"] == DECISION_IMPORT
    assert "different release" in result["reason"]


def test_missing_album_name_fails_open(db, db_path, tmp_path):
    # No incoming album title to scope against: cannot prove same release.
    _owned(db_path, tmp_path, mbid="mbid-123")

    with patch(
        "core.imports.library_check._library_file_has_metadata", return_value=True
    ):
        result = check_track_library_status(
            db, track=_track(), staging_file_path=_staging(tmp_path),
            match_type="mbid", confidence=1.0, album_name="",
        )

    assert result["decision"] == DECISION_IMPORT


# ---------------------------------------------------------------------------
# fail open: fuzzy or unprovable pairings never skip
# ---------------------------------------------------------------------------

def test_fuzzy_pairing_never_skips_even_with_db_hit(db, db_path, tmp_path):
    _owned(db_path, tmp_path, mbid="mbid-123")

    for match_type in (None, "", "fuzzy"):
        result = check_track_library_status(
            db, track=_track(), staging_file_path=_staging(tmp_path),
            match_type=match_type, confidence=0.85, album_name="Test Album",
        )
        assert result["decision"] == DECISION_UNCERTAIN, match_type
        assert result["library_path"] is None


def test_no_stable_ids_fails_open(db, tmp_path):
    result = check_track_library_status(
        db, track={"name": "Mystery Song"},
        staging_file_path=_staging(tmp_path),
        match_type="mbid", confidence=1.0, album_name="Test Album",
    )

    assert result["decision"] == DECISION_UNCERTAIN


def test_db_unavailable_fails_open(tmp_path):
    result = check_track_library_status(
        None, track=_track(), staging_file_path=_staging(tmp_path),
        match_type="mbid", confidence=1.0, album_name="Test Album",
    )

    assert result["decision"] == DECISION_UNCERTAIN


# ---------------------------------------------------------------------------
# import: missing, or stale
# ---------------------------------------------------------------------------

def test_imports_when_not_in_library(db, tmp_path):
    result = check_track_library_status(
        db, track=_track(mbid="mbid-999"),
        staging_file_path=_staging(tmp_path),
        match_type="mbid", confidence=1.0, album_name="Test Album",
    )

    assert result["decision"] == DECISION_IMPORT
    assert "not in the library" in result["reason"]


def test_imports_when_library_file_is_gone(db, db_path, tmp_path):
    # The DB row exists but the file was deleted: must NOT block re-import.
    album_id = _add_album(db_path, "Test Album")
    _add_track(db_path, mbid="mbid-123", album_id=album_id,
               file_path=str(tmp_path / "library" / "deleted.mp3"))

    result = check_track_library_status(
        db, track=_track(), staging_file_path=_staging(tmp_path),
        match_type="mbid", confidence=1.0, album_name="Test Album",
    )

    assert result["decision"] == DECISION_IMPORT
    assert "file is gone" in result["reason"]


# ---------------------------------------------------------------------------
# the pipeline's arbiter, mirrored: replace_lower + measured quality
# ---------------------------------------------------------------------------

def test_upgrade_when_arbiter_says_upgrade(db, db_path, tmp_path):
    # Same-format upgrade the old extension-rank heuristic killed: 128k MP3
    # -> 320k MP3 with replace_lower on. The measured arbiter decides.
    _owned(db_path, tmp_path, mbid="mbid-123")

    with patch(
        "core.imports.quality_replace.is_profile_upgrade", return_value=True
    ):
        result = check_track_library_status(
            db, track=_track(), staging_file_path=_staging(tmp_path, "song.mp3"),
            match_type="mbid", confidence=1.0, album_name="Test Album",
            quality_profile={"replace_lower_quality": True},
        )

    assert result["decision"] == DECISION_UPGRADE
    assert "upgrade" in result["reason"]


def test_skip_when_arbiter_declines(db, db_path, tmp_path):
    # replace_lower on, but the measured check says the incoming file is
    # NOT an upgrade: the pipeline's protection would decline, so skip the
    # wasted work instead of miscounting it as "upgraded".
    _owned(db_path, tmp_path, mbid="mbid-123")

    with patch(
        "core.imports.quality_replace.is_profile_upgrade", return_value=False
    ):
        result = check_track_library_status(
            db, track=_track(), staging_file_path=_staging(tmp_path, "song.flac"),
            match_type="mbid", confidence=1.0, album_name="Test Album",
            quality_profile={"replace_lower_quality": True},
        )

    assert result["decision"] == DECISION_SKIP_OWNED


def test_arbiter_error_fails_open(db, db_path, tmp_path):
    _owned(db_path, tmp_path, mbid="mbid-123")

    with patch(
        "core.imports.quality_replace.is_profile_upgrade",
        side_effect=RuntimeError("probe failed"),
    ):
        result = check_track_library_status(
            db, track=_track(), staging_file_path=_staging(tmp_path),
            match_type="mbid", confidence=1.0, album_name="Test Album",
            quality_profile={"replace_lower_quality": True},
        )

    assert result["decision"] == DECISION_UNCERTAIN


def test_skip_when_no_replace_lower_and_library_has_metadata(
    db, db_path, tmp_path
):
    # Default settings: the pipeline never quality-replaces, so a library
    # copy with metadata is satisfactory — skip it.
    _owned(db_path, tmp_path, mbid="mbid-123")

    with patch(
        "core.imports.library_check._library_file_has_metadata", return_value=True
    ):
        result = check_track_library_status(
            db, track=_track(), staging_file_path=_staging(tmp_path),
            match_type="mbid", confidence=1.0, album_name="Test Album",
            quality_profile={},
        )

    assert result["decision"] == DECISION_SKIP_OWNED


def test_import_when_library_copy_lacks_metadata(db, db_path, tmp_path):
    # The pipeline overwrites metadata-less files ("safe to overwrite"),
    # so the gate lets it through instead of skipping.
    _owned(db_path, tmp_path, mbid="mbid-123")

    with patch(
        "core.imports.library_check._library_file_has_metadata", return_value=False
    ):
        result = check_track_library_status(
            db, track=_track(), staging_file_path=_staging(tmp_path),
            match_type="mbid", confidence=1.0, album_name="Test Album",
            quality_profile={},
        )

    assert result["decision"] == DECISION_IMPORT
    assert "metadata" in result["reason"]


def test_unreadable_library_file_fails_open(db, db_path, tmp_path):
    _owned(db_path, tmp_path, mbid="mbid-123")

    with patch(
        "core.imports.library_check._library_file_has_metadata", return_value=None
    ):
        result = check_track_library_status(
            db, track=_track(), staging_file_path=_staging(tmp_path),
            match_type="mbid", confidence=1.0, album_name="Test Album",
            quality_profile={},
        )

    assert result["decision"] == DECISION_UNCERTAIN


def test_result_carries_audit_fields(db, db_path, tmp_path):
    _owned(db_path, tmp_path, mbid="mbid-123")

    with patch(
        "core.imports.library_check._library_file_has_metadata", return_value=True
    ):
        result = check_track_library_status(
            db, track=_track(), staging_file_path=_staging(tmp_path),
            match_type="mbid", confidence=0.99, album_name="Test Album",
        )

    assert result["match_type"] == "mbid"
    assert result["confidence"] == 0.99
    assert result["reason"]
