"""Regression tests for the orphan file detector."""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

import pytest

from core.repair_jobs import orphan_file_detector as _detector
from core.repair_jobs.base import JobContext
from core.repair_jobs.orphan_file_detector import (
    OrphanFileDetectorJob,
    _basename_like_pattern,
    is_tracked_path,
    known_file_suffixes,
    tracked_suffixes_for_file,
)


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


def test_appledouble_sidecars_are_not_scanned(tmp_path: Path) -> None:
    """macOS AppleDouble sidecars ('._Track.flac') carry an audio extension but no
    audio, so every match tier misses and each one used to land as a finding —
    always right next to the real track, which matched fine.
    """
    db_path = tmp_path / "library.sqlite"
    _seed_library(db_path)

    music = tmp_path / "Stray" / "Files"
    music.mkdir(parents=True)
    (music / "00 - Stray 0.mp3").write_bytes(b"no DB match")
    (music / "._00 - Stray 0.mp3").write_bytes(b"resource fork")

    findings = []
    context = JobContext(
        db=_DB(db_path),
        transfer_folder=str(tmp_path),
        config_manager=None,
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    job = OrphanFileDetectorJob()
    result = job.scan(context)

    assert result.scanned == 1                 # only the real stray file
    assert result.findings_created == 1
    assert [f['file_path'] for f in findings] == [str(music / "00 - Stray 0.mp3")]
    assert job.estimate_scope(context) == 1    # progress total agrees with the scan


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


def test_deprecated_false_flag_cannot_silence_the_native_scan(tmp_path: Path) -> None:
    """H-18: the native catalogue is the cutover, not an optional feature.

    ``features.library_v2=false`` used to hand native jobs an empty subject set,
    so a disabled catalogue reported zero findings and looked exactly like a
    clean library. The flag is deprecated and ignored now — this file is known to
    the v2 catalogue, so the scan must see it and clear it, both with the flag on
    (``test_library_v2_only_file_is_not_reported_as_orphan``) and off.
    """
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

    assert result.scanned == 1
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


def test_materialized_simple_download_is_no_longer_an_orphan(tmp_path: Path,
                                                             monkeypatch) -> None:
    """issues §7 end-to-end: a Simple Download used to import fine, never reach
    lib2, and then get flagged as an orphan on the next scan. With the file
    materialized by autolink (status §18 decision), the very same scan
    recognizes it as owned."""
    from core.library2 import autolink

    db_path = tmp_path / "library.sqlite"
    _seed_library(db_path)

    downloaded = tmp_path / "Stray" / "Files" / "Some Artist - Some Song.mp3"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(b"no DB match")

    from core.settings import config_manager
    real_get = config_manager.get
    monkeypatch.setattr(
        config_manager, "get",
        lambda key, default=None: (True if key == "features.library_v2"
                                   else real_get(key, default)))
    monkeypatch.setattr("database.music_database.get_database",
                        lambda: _DB(db_path))

    assert autolink.link_download_into_library_v2({
        "_final_processed_path": str(downloaded),
        "track_info": {},
        "search_result": {"is_simple_download": True,
                          "filename": downloaded.name},
    }) is not None

    findings = []
    context = JobContext(
        db=_DB(db_path),
        transfer_folder=str(tmp_path),
        config_manager=_Config(True),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    result = OrphanFileDetectorJob().scan(context)

    assert result.scanned == 1
    assert findings == []
    assert result.findings_created == 0


def test_track_imported_during_scan_does_not_become_orphan(tmp_path: Path) -> None:
    """The first DB snapshot can predate an import during the file walk."""
    db_path = tmp_path / "library.sqlite"
    _seed_library(db_path)
    track = tmp_path / "Artist" / "Album" / "01 - New Song.flac"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"audio")

    def report_progress(**progress):
        if progress.get('scanned') == 1:
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "INSERT INTO lib2_tracks (id, album_id, title) VALUES (101, 10, 'New Song')")
                conn.execute(
                    "INSERT INTO lib2_track_files (track_id, path, file_state) "
                    "VALUES (101, ?, 'active')",
                    ('/music/Artist/Album/01 - New Song.flac',),
                )
                conn.commit()
            finally:
                conn.close()

    findings = []
    context = JobContext(
        db=_DB(db_path),
        transfer_folder=str(tmp_path),
        config_manager=None,
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
        report_progress=report_progress,
    )

    result = OrphanFileDetectorJob().scan(context)

    assert result.scanned == 1
    assert result.findings_created == 0
    assert findings == []


def test_same_filename_in_another_album_does_not_hide_orphan(tmp_path: Path) -> None:
    db_path = tmp_path / "library.sqlite"
    _seed_library(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO lib2_tracks (id, album_id, title) VALUES (101, 10, 'Other Intro')")
        conn.execute(
            "INSERT INTO lib2_track_files (track_id, path, file_state) "
            "VALUES (101, '/music/Other Artist/Other Album/01 - Intro.flac', 'active')"
        )
        conn.commit()
    finally:
        conn.close()
    track = tmp_path / "Artist" / "Album" / "01 - Intro.flac"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"audio with unreadable tags")
    findings = []
    context = JobContext(
        db=_DB(db_path), transfer_folder=str(tmp_path), config_manager=None,
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    result = OrphanFileDetectorJob().scan(context)

    assert result.findings_created == 1
    assert [finding['file_path'] for finding in findings] == [str(track)]


def test_a_loose_file_matching_only_a_wanted_track_is_still_an_orphan(tmp_path: Path) -> None:
    """lib2_tracks also holds wanted and discography rows with no file; the
    title/artist fallback counted those as known, so a download that never
    imported (its tags naming the wanted song) was never reported."""
    import struct
    from mutagen.flac import FLAC

    db_path = tmp_path / "library.sqlite"
    _seed_library(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO lib2_tracks (id, album_id, title) VALUES (200, 10, 'Wanted Song')")
    conn.commit()
    conn.close()

    stray = tmp_path / "Stray"
    stray.mkdir()
    path = stray / "never imported.flac"
    block = bytearray(34)
    block[0:2] = struct.pack('>H', 4096)
    block[2:4] = struct.pack('>H', 4096)
    block[10] = 0x0A
    block[12] = 0x70
    path.write_bytes(b'fLaC' + bytes([0x80, 0x00, 0x00, 0x22]) + bytes(block))
    audio = FLAC(str(path))
    audio['title'] = ['Wanted Song']
    audio['artist'] = ['Clouddead89']
    audio.save()

    findings = []
    context = JobContext(db=_DB(db_path), transfer_folder=str(tmp_path),
                         config_manager=_Config(True),
                         create_finding=lambda **kwargs: findings.append(kwargs) or True)
    OrphanFileDetectorJob().scan(context)
    assert [f['finding_type'] for f in findings] == ['orphan_file']


# ---------------------------------------------------------------------------
# tracked_suffixes_for_file (#2095): the per-finding re-check must give the
# same answers as the full known_file_suffixes snapshot without building it.
# ---------------------------------------------------------------------------

def _seed_track_files(db_path: Path, rows) -> _DB:
    conn = sqlite3.connect(db_path)
    try:
        from core.library2.schema import ensure_library_v2_schema

        ensure_library_v2_schema(conn)
        conn.executemany(
            "INSERT INTO lib2_track_files (track_id, path, file_state) "
            "VALUES (NULL, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return _DB(db_path)


_EQUIVALENCE_CASES = [
    # a different mount prefix on the same suffix
    (["/mnt/musicBackup/Artist/Album/01 - Song.flac"],
     "/home/user/Music/Artist/Album/01 - Song.flac"),
    # case differences everywhere
    (["/music/ARTIST/ALBUM/01 - SONG.FLAC"],
     "/x/Artist/Album/01 - Song.flac"),
    # DB path stored with backslashes
    (["C:\\Music\\Artist\\Album\\01 - Song.flac"],
     "/mnt/Artist/Album/01 - Song.flac"),
    # same filename in another album: depth-1 only
    (["/music/Other Artist/Other Album/01 - Intro.flac"],
     "/transfer/Artist/Album/01 - Intro.flac"),
    # LIKE wildcards as literal filename characters
    (["/music/Artist/Album/100%_Sure.flac"],
     "/elsewhere/Artist/Album/100%_Sure.flac"),
    # a '_' in the probe must not wildcard-match a different name
    (["/music/Artist/Album/aXb.flac"],
     "/elsewhere/Artist/Album/a_b.flac"),
    (["/music/Artist/Album/a_b.flac"],
     "/elsewhere/Artist/Album/aXb.flac"),
    # k / i heavy name, folded case
    (["/music/Kiss/Album/I Was Made.flac"],
     "/x/kiss/album/i was made.flac"),
    # Kelvin sign in the DB folds to 'k'; and the reverse direction
    (["/music/Artist/Album/Kelvin \u212a.flac"],
     "/x/Artist/Album/Kelvin k.flac"),
    (["/music/Artist/Album/Song k.flac"],
     "/x/Artist/Album/Song \u212a.flac"),
    # U+0130 in the DB folds to 'i' + combining dot in the probe
    (["/music/Artist/Album/\u0130.flac"],
     "/x/Artist/Album/i\u0307.flac"),
    # non-ASCII basename, upper-cased on the probe side
    (["/music/Artist/Album/Bj\u00f6rk - Hunter.flac"],
     "/x/Artist/Album/BJ\u00d6RK - Hunter.flac"),
    # CJK basename and folders
    (["/music/\u30a2\u30fc\u30c6\u30a3\u30b9\u30c8/\u30a2\u30eb\u30d0\u30e0/\u65e5\u672c\u8a9e.flac"],
     "/t/\u30a2\u30fc\u30c6\u30a3\u30b9\u30c8/\u30a2\u30eb\u30d0\u30e0/\u65e5\u672c\u8a9e.flac"),
    # bare-filename DB path: depth 1 only
    (["song.flac"], "/a/b/song.flac"),
    # no match at all
    (["/music/Artist/Album/01 - Song.flac"],
     "/transfer/Other/Place/99 - Different.flac"),
    # many same-name rows, only one of them a suffix match
    (["/music/A1/B1/track.flac", "/music/A2/B2/track.flac",
      "/music/Artist/Album/track.flac"],
     "/mnt/Artist/Album/track.flac"),
    # a name STARTING with a LIKE wildcard character
    (["/music/Artist/Album/%Song.flac"],
     "/elsewhere/Artist/Album/%Song.flac"),
    # probe path with backslash separators
    (["/music/Artist/Album/01 - Song.flac"],
     "C:\\Transfer\\Artist\\Album\\01 - Song.flac"),
    # Greek final sigma: DB title-case vs probe with a final sigma
    (["/music/Artist/Album/\u039f\u03a3.flac"],
     "/x/Artist/Album/\u03bf\u03c2.flac"),
    # capital sharp s in the DB folds to ß in the probe
    (["/music/Artist/Album/\u1e9e.flac"],
     "/x/Artist/Album/\u00df.flac"),
    # dotless i is a different letter from I in Python's folding: no match
    (["/music/Artist/Album/Song I.flac"],
     "/x/Artist/Album/Song \u0131.flac"),
    # astral-plane case pair (Deseret capital vs small)
    (["/music/Artist/Album/\U00010400.flac"],
     "/x/Artist/Album/\U00010428.flac"),
]


@pytest.mark.parametrize("db_paths,probe", _EQUIVALENCE_CASES)
def test_tracked_suffixes_for_file_matches_full_snapshot(tmp_path, db_paths, probe):
    db = _seed_track_files(
        tmp_path / "library.sqlite", [(p, "active") for p in db_paths])

    targeted = tracked_suffixes_for_file(db, probe)
    full = known_file_suffixes(db)

    # Exactly the full snapshot's suffixes that end in this file's name.
    name_lower = probe.replace("\\", "/").split("/")[-1].lower()
    assert targeted == {s for s in full if s.split("/")[-1] == name_lower}
    for min_depth in (1, 2):
        assert is_tracked_path(probe, targeted, min_depth=min_depth) == \
            is_tracked_path(probe, full, min_depth=min_depth)


def test_tracked_suffixes_for_file_file_state_matrix(tmp_path):
    """Same filter as known_file_suffixes: only 'deleted' rows stop counting
    as tracked; a missing/quarantined file is still somebody's. Seeded on a
    minimal legacy-shaped table because the current schema makes file_state
    NOT NULL — the COALESCE in the query guards tables where it can be."""
    db_path = tmp_path / "library.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE lib2_track_files (id INTEGER PRIMARY KEY, "
            "track_id INTEGER, path TEXT, file_state TEXT)")
        conn.execute(
            "CREATE INDEX idx_lib2_track_files_path ON lib2_track_files(path)")
        conn.executemany(
            "INSERT INTO lib2_track_files (track_id, path, file_state) "
            "VALUES (NULL, ?, ?)",
            [
                ("/music/Artist/Album/gone.flac", "deleted"),
                ("/music/Artist/Album/back.flac", "missing_confirmed"),
                ("/music/Artist/Album/held.flac", "quarantined"),
                ("/music/Artist/Album/plain.flac", None),
                ("/music/Artist/Album/live.flac", "active"),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    db = _DB(db_path)
    expected = {
        "gone.flac": False, "back.flac": True, "held.flac": True,
        "plain.flac": True, "live.flac": True,
    }
    for name, tracked in expected.items():
        probe = f"/elsewhere/Artist/Album/{name}"
        assert is_tracked_path(probe, tracked_suffixes_for_file(db, probe)) is tracked
        assert is_tracked_path(probe, known_file_suffixes(db)) is tracked


def test_basename_like_pattern_is_ascii_and_superset_shaped():
    assert _basename_like_pattern("Song.flac") == "%Song.flac"
    # k/K/i/I become single-char wildcards (Kelvin / U+0130 folds), and a
    # literal % or _ in the name must not survive as a wildcard either.
    assert _basename_like_pattern("Kiss.flac") == "%__ss.flac"
    assert _basename_like_pattern("100%.flac") == "%100_.flac"
    # Non-ASCII runs collapse into a single %.
    assert _basename_like_pattern("\u65e5\u672c\u8a9e.flac") == "%.flac"
    pattern = _basename_like_pattern("Bj\u00f6rk_100% - \u0130K.flac")
    assert all(ord(c) < 128 for c in pattern)


def test_only_two_non_ascii_codepoints_lower_to_ascii():
    """Pins the brute-forced fact _basename_like_pattern relies on: U+0130
    and U+212A are the only non-ASCII code points whose .lower() contains
    an ASCII character. If a Python/Unicode bump adds one, this fails
    loudly instead of the pattern silently missing rows. (Context-sensitive
    lowering — the final sigma — never yields ASCII either; that half is
    covered by the sigma case in the equivalence matrix above.)"""
    found = {
        cp for cp in range(0x80, 0x110000)
        if any(ord(c) < 0x80 for c in chr(cp).lower())
    }
    assert found == {0x130, 0x212A}


def test_tracked_suffixes_empty_basename_falls_back(tmp_path, monkeypatch):
    db = _seed_track_files(
        tmp_path / "library.sqlite",
        [("/music/Artist/Album/01 - Song.flac", "active")])
    calls = []
    real = _detector.known_file_suffixes
    monkeypatch.setattr(
        _detector, "known_file_suffixes",
        lambda d: calls.append(1) or real(d))
    assert tracked_suffixes_for_file(db, "/some/dir/") == real(db)
    assert calls == [1]


def test_tracked_suffixes_propagates_db_errors(tmp_path):
    """The orphan fixer's fail-closed behaviour relies on the helper
    raising, never on it guessing 'not tracked'."""
    class _BrokenDB:
        def _get_connection(self):
            raise RuntimeError("database is locked")

    with pytest.raises(RuntimeError):
        tracked_suffixes_for_file(_BrokenDB(), "/x/Artist/Album/song.flac")


def test_tracked_suffixes_phase1_error_propagates(tmp_path):
    """An error INSIDE phase 1 (here: the table is missing entirely) must
    raise, not read as 'not tracked' — that direction moves/deletes files."""
    db = _DB(tmp_path / "empty.sqlite")  # no schema at all
    with pytest.raises(sqlite3.OperationalError):
        tracked_suffixes_for_file(db, "/x/Artist/Album/song.flac")


def test_tracked_suffixes_phase2_error_propagates_and_closes(tmp_path):
    """Same for phase 2: the candidate fetch failing (e.g. the DB locks
    between the phases) must raise, and the connection must still close."""
    db_path = tmp_path / "library.sqlite"
    _seed_track_files(db_path, [("/music/Artist/Album/song.flac", "active")])

    class _Phase2BoomConn:
        def __init__(self, real):
            self._real = real
            self.closed = False

        def execute(self, sql, params=()):
            if "WHERE id IN" in sql:
                raise sqlite3.OperationalError("database is locked")
            return self._real.execute(sql, params)

        def close(self):
            self.closed = True
            self._real.close()

    holder = {}

    class _WrapDB:
        def _get_connection(self):
            conn = _Phase2BoomConn(sqlite3.connect(db_path))
            holder["conn"] = conn
            return conn

    with pytest.raises(sqlite3.OperationalError):
        tracked_suffixes_for_file(_WrapDB(), "/x/Artist/Album/song.flac")
    assert holder["conn"].closed is True


def test_tracked_suffixes_chunks_large_same_name_sets(tmp_path):
    """Libraries hold thousands of '01 - Intro.flac' rows; the candidate
    fetch must chunk its IN list instead of truncating or blowing the
    bound-parameter limit, and still find the one suffix match."""
    rows = [(f"/music/Artist{i}/Album{i}/01 - Intro.flac", "active")
            for i in range(1200)]
    rows.append(("/music/The Artist/The Album/01 - Intro.flac", "active"))
    db = _seed_track_files(tmp_path / "library.sqlite", rows)

    probe = "/mnt/The Artist/The Album/01 - Intro.flac"
    targeted = tracked_suffixes_for_file(db, probe)
    full = known_file_suffixes(db)
    assert targeted == {s for s in full if s.split("/")[-1] == "01 - intro.flac"}
    assert is_tracked_path(probe, targeted) is True
    # A same-name file from an album nobody has stays depth-2 untracked.
    stranger = "/mnt/Nobody/Nothing/01 - Intro.flac"
    assert is_tracked_path(stranger, tracked_suffixes_for_file(db, stranger)) is False
    assert is_tracked_path(stranger, tracked_suffixes_for_file(db, stranger),
                           min_depth=1) is True


def test_tracked_suffixes_same_name_deleted_and_active_rows(tmp_path):
    """Same basename, two rows: the probe's own album row is 'deleted', a
    different album's row is active. Depth 2 must say untracked (the
    deleted row does not count) while depth 1 still sees the name."""
    db = _seed_track_files(tmp_path / "library.sqlite", [
        ("/music/Artist/Album/track.flac", "deleted"),
        ("/music/Other/Other/track.flac", "active"),
    ])
    probe = "/elsewhere/Artist/Album/track.flac"
    targeted = tracked_suffixes_for_file(db, probe)
    full = known_file_suffixes(db)
    assert targeted == {s for s in full if s.split("/")[-1] == "track.flac"}
    assert is_tracked_path(probe, targeted, min_depth=2) is False
    assert is_tracked_path(probe, targeted, min_depth=1) is True


def test_tracked_suffixes_tolerates_null_and_empty_db_paths(tmp_path):
    """Legacy tables can hold NULL/'' paths (the full snapshot's query
    filters them out). The targeted lookup must skip them the same way,
    never crash on them."""
    db_path = tmp_path / "library.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE lib2_track_files (id INTEGER PRIMARY KEY, "
            "track_id INTEGER, path TEXT, file_state TEXT)")
        conn.execute(
            "CREATE INDEX idx_lib2_track_files_path ON lib2_track_files(path)")
        conn.executemany(
            "INSERT INTO lib2_track_files (track_id, path, file_state) "
            "VALUES (NULL, ?, 'active')",
            [(None,), ("",), ("/music/Artist/Album/01 - Song.flac",)],
        )
        conn.commit()
    finally:
        conn.close()
    db = _DB(db_path)
    probe = "/elsewhere/Artist/Album/01 - Song.flac"
    assert is_tracked_path(probe, tracked_suffixes_for_file(db, probe)) is True
    assert is_tracked_path(probe, known_file_suffixes(db)) is True


def test_tracked_suffixes_phase2_recheck_drops_rows_renamed_between_phases(tmp_path):
    """Phase 2 re-verifies the basename: a row renamed after phase 1
    picked it no longer belongs to this file's name, and its suffixes
    must not leak into the returned set."""
    db_path = tmp_path / "library.sqlite"
    _seed_track_files(db_path, [("/music/Artist/Album/song.flac", "active")])

    class _RenamingConn:
        def __init__(self, real):
            self._real = real

        def execute(self, sql, params=()):
            rows = self._real.execute(sql, params).fetchall()
            if "WHERE id IN" in sql:
                return [(path.replace("song.flac", "renamed.flac"),)
                        for (path,) in rows]
            return rows

        def close(self):
            self._real.close()

    class _WrapDB:
        def _get_connection(self):
            return _RenamingConn(sqlite3.connect(db_path))

    assert tracked_suffixes_for_file(_WrapDB(), "/x/Artist/Album/song.flac") == set()


def test_tracked_suffixes_phase2_chunks_at_500(tmp_path):
    """Pins the chunk size itself, not just 'no truncation': every
    phase-2 call must stay far under SQLite's bound-parameter limit."""
    rows = [(f"/music/Artist{i}/Album{i}/01 - Intro.flac", "active")
            for i in range(1200)]
    db_path = tmp_path / "library.sqlite"
    _seed_track_files(db_path, rows)
    recorded = []

    class _RecordingConn:
        def __init__(self, real):
            self._real = real

        def execute(self, sql, params=()):
            recorded.append((sql, params))
            return self._real.execute(sql, params)

        def close(self):
            self._real.close()

    class _WrapDB:
        def _get_connection(self):
            return _RecordingConn(sqlite3.connect(db_path))

    tracked_suffixes_for_file(_WrapDB(), "/x/A/B/01 - Intro.flac")
    phase2_param_counts = [len(params) for sql, params in recorded
                           if "WHERE id IN" in sql]
    assert phase2_param_counts == [500, 500, 200]


def test_tracked_suffixes_real_phase1_query_uses_covering_index(tmp_path):
    """Intercepts the query the function actually runs — a hand-retyped
    EXPLAIN would not catch the implementation drifting back to a
    wide-row scan — and checks ITS plan."""
    db_path = tmp_path / "library.sqlite"
    _seed_track_files(db_path, [("/music/Artist/Album/01 - Song.flac", "active")])
    recorded = []

    class _RecordingConn:
        def __init__(self, real):
            self._real = real

        def execute(self, sql, params=()):
            recorded.append((sql, params))
            return self._real.execute(sql, params)

        def close(self):
            self._real.close()

    class _WrapDB:
        def _get_connection(self):
            return _RecordingConn(sqlite3.connect(db_path))

    tracked_suffixes_for_file(_WrapDB(), "/x/Artist/Album/01 - Song.flac")
    phase1_sql, phase1_params = recorded[0]
    assert "file_state" not in phase1_sql  # the wide-row trap, by construction

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "EXPLAIN QUERY PLAN " + phase1_sql, phase1_params).fetchall()
    finally:
        conn.close()
    plan = " ".join(str(row[-1]) for row in rows)
    assert "COVERING INDEX" in plan
    assert "idx_lib2_track_files_path" in plan


def test_tracked_suffixes_randomized_equivalence(tmp_path):
    """Seeded fuzz over a nasty alphabet: random DB names and probes must
    all give the full snapshot's answers. Catches a future pattern edit
    that the hand-picked matrix happens to miss."""
    rng = random.Random(2095)
    alphabet = list("aAbBkKiI%_-. 013") + [
        "é", "ß", "ẞ", "İ", "ı", "\u212a", "ör", "日本", "Σ", "ς", "ﬀ"]

    def rand_name():
        return "".join(rng.choice(alphabet)
                       for _ in range(rng.randint(1, 14))) + ".flac"

    db_names = [rand_name() for _ in range(120)]
    rows = [(f"/music/Artist{i % 7}/Album{i % 5}/{name}", "active")
            for i, name in enumerate(db_names)]
    db = _seed_track_files(tmp_path / "library.sqlite", rows)
    full = known_file_suffixes(db)

    probes = [f"/elsewhere/Artist{i % 7}/Album{i % 5}/{name}"
              for i, name in enumerate(db_names)]
    probes += [f"/elsewhere/X/Y/{rand_name()}" for _ in range(120)]
    # Case-mangled variants of real rows: the fold must agree too. The
    # .lower() variants are the ones that turn a stored Kelvin sign / İ
    # into an ASCII k / i on the probe side — without them this fuzz is
    # blind to the pattern's k/i fold (verified by mutation).
    probes += [p.upper().replace("/ELSEWHERE/", "/mnt/") for p in probes[:40]]
    probes += [p.lower() for p in probes[:120]]

    for probe in probes:
        targeted = tracked_suffixes_for_file(db, probe)
        name_lower = probe.replace("\\", "/").split("/")[-1].lower()
        assert targeted == {s for s in full if s.split("/")[-1] == name_lower}
        for min_depth in (1, 2):
            assert is_tracked_path(probe, targeted, min_depth=min_depth) == \
                is_tracked_path(probe, full, min_depth=min_depth)
