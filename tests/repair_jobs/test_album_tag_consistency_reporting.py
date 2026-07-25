"""Album Tag Consistency — eligibility breakdown + unreadable-album reporting.

clouddead89: "4000 albums but it only scans 1300" — the smaller number is the
job's eligibility gate (2+ tracks with stored file paths), not a truncation,
but the job reported the bare number and let users read it as a bug. It now
says what was excluded and why, and separately counts eligible albums whose
files couldn't actually be read from SoulSync's filesystem (Docker mount
mismatch) — those used to be silently indistinguishable from healthy albums.

Everything runs on a temp DB + tmp_path FLAC files. No network, no services.
"""

from __future__ import annotations

import struct

from core.repair_jobs.album_tag_consistency import AlbumTagConsistencyJob
from core.repair_jobs.base import JobContext
from database.music_database import MusicDatabase


def _make_flac(path, tags=None):
    """Minimal but real FLAC (same recipe as test_comma_artist_splitter)."""
    from mutagen.flac import FLAC
    si = bytearray(34)
    si[0:2] = struct.pack(">H", 4096)
    si[2:4] = struct.pack(">H", 4096)
    si[10] = 0x0A
    si[12] = 0x70
    block_header = bytes([0x80, 0x00, 0x00, 0x22])
    path.write_bytes(b"fLaC" + block_header + bytes(si) + bytes(range(256)) * 8)
    audio = FLAC(str(path))
    for k, v in (tags or {}).items():
        audio[k] = v if isinstance(v, list) else [v]
    audio.save()


class _Config:
    def get(self, key, default=None):
        return default


def _add_album(db, album_id, artist_id, tracks):
    """tracks: list of (track_id, file_path_or_None)."""
    with db._get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO artists (id, name, server_source) VALUES (?, ?, 'test')",
            (artist_id, f'Artist {artist_id}'))
        conn.execute(
            "INSERT INTO albums (id, artist_id, title, server_source) VALUES (?, ?, ?, 'test')",
            (album_id, artist_id, f'Album {album_id}'))
        for track_id, file_path in tracks:
            conn.execute(
                "INSERT INTO tracks (id, album_id, artist_id, title, file_path, server_source) "
                "VALUES (?, ?, ?, ?, ?, 'test')",
                (track_id, album_id, artist_id, f'Track {track_id}', file_path))
        conn.commit()


def _run_scan(db, tmp_path):
    progress_calls = []
    findings = []

    def report_progress(**kwargs):
        progress_calls.append(kwargs)

    def create_finding(**kwargs):
        findings.append(kwargs)
        return True

    context = JobContext(
        db=db,
        transfer_folder=str(tmp_path),
        config_manager=_Config(),
        create_finding=create_finding,
        should_stop=lambda: False,
        is_paused=lambda: False,
        report_progress=report_progress,
    )
    result = AlbumTagConsistencyJob().scan(context)
    return result, progress_calls, findings


def test_breakdown_reports_excluded_albums(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))

    # Eligible: 2 tracks with real readable files (consistent tags → no finding)
    f1, f2 = tmp_path / 'a1.flac', tmp_path / 'a2.flac'
    _make_flac(f1, {'album': 'Same', 'albumartist': 'Same Artist'})
    _make_flac(f2, {'album': 'Same', 'albumartist': 'Same Artist'})
    _add_album(db, 'AL_OK', 'AR1', [('T1', str(f1)), ('T2', str(f2))])

    # Excluded: single-track album
    _add_album(db, 'AL_SINGLE', 'AR2', [('T3', str(f1))])

    # Excluded: 2 tracks but no stored file paths (the Navidrome-shaped gap)
    _add_album(db, 'AL_NOPATH', 'AR3', [('T4', None), ('T5', '')])

    result, progress_calls, findings = _run_scan(db, tmp_path)

    assert result.scanned == 1          # only the eligible album entered the loop
    assert findings == []               # consistent tags → nothing flagged

    log_lines = [c.get('log_line', '') for c in progress_calls if c.get('log_line')]
    breakdown = next((l for l in log_lines if 'eligible' in l), '')
    assert '3 albums in the database' in breakdown
    assert '1 eligible' in breakdown
    assert '1 single-track' in breakdown
    assert '1 without stored file paths' in breakdown

    phases = [c.get('phase', '') for c in progress_calls if c.get('phase')]
    assert any('1 of 3 albums' in p for p in phases)


def test_unreadable_albums_get_a_warning(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    # Eligible on paper — 2 tracks with paths — but the files don't exist
    _add_album(db, 'AL_GONE', 'AR1', [
        ('T1', str(tmp_path / 'missing1.flac')),
        ('T2', str(tmp_path / 'missing2.flac')),
    ])

    result, progress_calls, findings = _run_scan(db, tmp_path)

    assert result.scanned == 1
    assert findings == []
    warnings = [c for c in progress_calls
                if c.get('log_type') == 'warning' and 'could not be read' in c.get('log_line', '')]
    assert warnings, 'expected an unreadable-albums warning'
    assert '1 album(s) skipped' in warnings[0]['log_line']


def test_inconsistent_album_still_flagged(tmp_path):
    """The reporting additions must not change detection itself."""
    db = MusicDatabase(str(tmp_path / 'm.db'))
    f1, f2 = tmp_path / 'b1.flac', tmp_path / 'b2.flac'
    _make_flac(f1, {'album': 'Simulation Theory'})
    _make_flac(f2, {'album': 'Simulation Theory (Super Deluxe)'})
    _add_album(db, 'AL_SPLIT', 'AR1', [('T1', str(f1)), ('T2', str(f2))])

    result, _progress, findings = _run_scan(db, tmp_path)

    assert result.findings_created == 1
    assert findings[0]['finding_type'] == 'album_tag_inconsistency'
    assert findings[0]['details']['inconsistencies'][0]['field'] == 'album'
