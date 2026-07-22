"""Regression tests for the orphan file detector."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from core.repair_jobs.base import JobContext
from core.repair_jobs.orphan_file_detector import OrphanFileDetectorJob


class _DB:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


class _Config:
    def __init__(self, library_v2: bool, *, music_paths: list | None = None,
                 scan_library_folder: bool = False) -> None:
        self.library_v2 = library_v2
        self.music_paths = music_paths or []
        self.scan_library_folder = scan_library_folder

    def get(self, key, default=None):
        if key == "features.library_v2":
            return self.library_v2
        if key == "library.music_paths":
            return self.music_paths
        if key == "repair.jobs.orphan_file_detector.settings":
            return {"scan_library_folder": self.scan_library_folder}
        return default


def _seed_library(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from core.library2.schema import ensure_library_v2_schema

        ensure_library_v2_schema(conn)
        conn.executescript(
            """
            INSERT INTO lib2_artists (id, name) VALUES
                (1, 'Clouddead89'),
                (2, 'Featured Artist');
            INSERT INTO lib2_albums (id, primary_artist_id, title) VALUES
                (10, 1, 'Perfect Match Error');
            INSERT INTO lib2_album_artists(album_id, artist_id, role)
                VALUES(10, 1, 'primary');
            INSERT INTO lib2_tracks (id, album_id, title) VALUES
                (100, 10, 'Perfect Match');
            INSERT INTO lib2_track_artists(track_id, artist_id, role, position)
                VALUES(100, 2, 'primary', 0);
            INSERT INTO lib2_track_files(id, track_id, path, file_state)
                VALUES(100, 100, '/old/prefix/elsewhere.mp3', 'active');
            """
        )
        conn.commit()
    finally:
        conn.close()


def _insert_native_file(db_path: Path, audio_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO lib2_artists(id, name) VALUES(3, 'Lib2 Artist')"
        )
        conn.execute(
            "INSERT INTO lib2_albums(id, primary_artist_id, title) "
            "VALUES(11, 3, 'Lib2 Album')"
        )
        conn.execute(
            "INSERT INTO lib2_album_artists(album_id, artist_id, role) "
            "VALUES(11, 3, 'primary')"
        )
        conn.execute(
            "INSERT INTO lib2_tracks(id, album_id, title) "
            "VALUES(101, 11, 'Lib2 Song')"
        )
        conn.execute(
            "INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
            "VALUES(101, 3, 'primary', 0)"
        )
        conn.execute(
            "INSERT INTO lib2_track_files(track_id, path, file_state) "
            "VALUES(101, ?, 'active')",
            (str(audio_path),),
        )
        conn.commit()
    finally:
        conn.close()


def test_mass_orphan_path_mismatch_creates_no_findings(tmp_path: Path) -> None:
    """The "transferred to staging" footgun: when the DB's stored paths no longer
    match the filesystem (remount / Docker volume change) EVERY file looks
    orphaned. The detector must create NO findings then — otherwise a user
    batch-applying "move to staging" relocates their whole library. Mirrors the
    hard skip the stale-removal paths use.
    """
    db_path = tmp_path / "library.sqlite"
    _seed_library(db_path)  # DB tracks live under /old/prefix/... — nothing on disk matches

    # Drop 30 untracked files (> the 20 absolute floor, and 100% > 50%).
    music = tmp_path / "Some Artist" / "Some Album"
    music.mkdir(parents=True)
    for i in range(30):
        (music / f"{i:02d} - Track {i}.mp3").write_bytes(b"unreadable tags; no DB match")

    findings = []
    context = JobContext(
        db=_DB(db_path),
        transfer_folder=str(tmp_path),
        config_manager=_Config(True),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    result = OrphanFileDetectorJob().scan(context)

    assert result.scanned == 30
    assert result.findings_created == 0
    assert findings == []          # hard skip — not even flagged as warnings


def test_small_orphan_set_still_surfaces(tmp_path: Path) -> None:
    """Below the absolute floor, genuine orphans must still be reported — the
    guard only suppresses an implausibly large flood, not normal stray files.
    """
    db_path = tmp_path / "library.sqlite"
    _seed_library(db_path)

    music = tmp_path / "Stray" / "Files"
    music.mkdir(parents=True)
    for i in range(3):             # 3 orphans — under the 20-file floor
        (music / f"{i:02d} - Stray {i}.mp3").write_bytes(b"no DB match")

    findings = []
    context = JobContext(
        db=_DB(db_path),
        transfer_folder=str(tmp_path),
        config_manager=_Config(True),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    result = OrphanFileDetectorJob().scan(context)

    assert result.scanned == 3
    assert result.findings_created == 3
    assert all(f['finding_type'] == 'orphan_file' for f in findings)


def test_orphan_detector_accepts_picard_albumartist_folder_match(tmp_path: Path) -> None:
    """Picard paths use albumartist/album (year)/track - title.

    Even when the DB track artist is a featured artist, the album artist
    folder should be enough to recognize the file as tracked.
    """
    db_path = tmp_path / "library.sqlite"
    _seed_library(db_path)

    transfer = tmp_path / "Clouddead89" / "Perfect Match Error (2026)"
    transfer.mkdir(parents=True)
    audio_path = transfer / "01 - Perfect Match.mp3"
    audio_path.write_bytes(b"not a real mp3; filename fallback handles this")

    findings = []
    context = JobContext(
        db=_DB(db_path),
        transfer_folder=str(tmp_path),
        config_manager=_Config(True),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    result = OrphanFileDetectorJob().scan(context)

    assert result.scanned == 1
    assert result.findings_created == 0
    assert findings == []


def test_native_job_is_gated_when_library_v2_is_disabled(tmp_path: Path) -> None:
    db_path = tmp_path / "library.sqlite"
    _seed_library(db_path)
    audio_path = tmp_path / "Lib2 Artist" / "Lib2 Album" / "01 - Lib2 Song.mp3"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"not a real mp3")

    _insert_native_file(db_path, audio_path)

    findings = []
    context = JobContext(
        db=_DB(db_path),
        transfer_folder=str(tmp_path),
        config_manager=_Config(False),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    result = OrphanFileDetectorJob().scan(context)

    assert result.scanned == 0
    assert result.findings_created == 0
    assert findings == []


def test_library_v2_only_file_is_not_reported_as_orphan(tmp_path: Path) -> None:
    """A lib2-autolinked file may precede legacy media-server sync."""
    db_path = tmp_path / "library.sqlite"
    _seed_library(db_path)
    audio_path = tmp_path / "Lib2 Artist" / "Lib2 Album" / "01 - Lib2 Song.mp3"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"not a real mp3; exact lib2 path is sufficient")

    _insert_native_file(db_path, audio_path)

    findings = []
    context = JobContext(
        db=_DB(db_path),
        transfer_folder=str(tmp_path),
        config_manager=_Config(True),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    result = OrphanFileDetectorJob().scan(context)

    assert result.scanned == 1
    assert result.findings_created == 0
    assert findings == []


def test_library_folder_untouched_by_default(tmp_path: Path) -> None:
    """A4 (library-overhaul-branch-review): scan_library_folder defaults to
    off, so an orphan sitting in the organized library folder must not be
    reported unless the admin opts in — existing installations keep their
    current (transfer-only) scan cost unchanged."""
    db_path = tmp_path / "library.sqlite"
    _seed_library(db_path)

    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    library = tmp_path / "Library"
    (library / "Some Artist" / "Some Album").mkdir(parents=True)
    (library / "Some Artist" / "Some Album" / "01 - Untracked.mp3").write_bytes(b"junk")

    findings = []
    context = JobContext(
        db=_DB(db_path),
        transfer_folder=str(transfer),
        config_manager=_Config(True, music_paths=[str(library)], scan_library_folder=False),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    result = OrphanFileDetectorJob().scan(context)

    assert result.scanned == 0
    assert findings == []


def test_library_folder_scanned_when_opted_in_with_quality_details(tmp_path: Path) -> None:
    """A4: with scan_library_folder on, an untracked file in the organized
    library folder (not just transfer/staging) surfaces as an orphan finding
    carrying its real measured quality — the retired quality_upgrade_scanner
    used to walk the whole music folder for exactly this, the native
    replacement stopped touching the disk at all."""
    db_path = tmp_path / "library.sqlite"
    _seed_library(db_path)

    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    library = tmp_path / "Library"
    album_dir = library / "Some Artist" / "Some Album"
    album_dir.mkdir(parents=True)
    (album_dir / "01 - Untracked.mp3").write_bytes(b"junk; unreadable by mutagen")

    findings = []
    context = JobContext(
        db=_DB(db_path),
        transfer_folder=str(transfer),
        config_manager=_Config(True, music_paths=[str(library)], scan_library_folder=True),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    result = OrphanFileDetectorJob().scan(context)

    assert result.scanned == 1
    assert result.findings_created == 1
    details = findings[0]["details"]
    # Unreadable-by-mutagen still yields a usable finding: format falls back
    # to the extension, quality_tier to 'unknown' — never silently dropped.
    assert details["format"] == "mp3"
    assert details["quality_tier"] == "unknown"
    assert "sample_rate" in details and "bit_depth" in details


def test_transfer_folder_orphan_finding_now_carries_quality_details(tmp_path: Path) -> None:
    """Existing transfer-folder orphan findings gain the same quality facts
    (no behavior change to WHICH files get flagged, only richer details)."""
    db_path = tmp_path / "library.sqlite"
    _seed_library(db_path)

    music = tmp_path / "Stray" / "Files"
    music.mkdir(parents=True)
    (music / "00 - Stray.mp3").write_bytes(b"no DB match")

    findings = []
    context = JobContext(
        db=_DB(db_path),
        transfer_folder=str(tmp_path),
        config_manager=_Config(True),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    result = OrphanFileDetectorJob().scan(context)

    assert result.findings_created == 1
    assert "quality_tier" in findings[0]["details"]
    assert "Measured quality" in findings[0]["description"]
