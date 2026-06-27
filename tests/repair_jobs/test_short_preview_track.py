"""Preview-clip cleanup job (#937-adjacent): flag ~30s preview clips whose source says the
real track is much longer, then on approval delete the file + drop the row + re-wishlist."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.repair_jobs.base import JobContext
from core.repair_jobs.short_preview_track import ShortPreviewTrackJob
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


def _seed(db: MusicDatabase):
    conn = db._get_connection()
    conn.execute("INSERT OR IGNORE INTO artists (id, name) VALUES ('ar1', 'A-ha')")
    conn.execute("INSERT INTO albums (id, artist_id, title) VALUES ('al1', 'ar1', 'Hunting High and Low')")
    conn.commit()
    conn.close()


def _track(db, tid: int, duration_ms, path, spotify_id=None):
    # tid is an INTEGER id, exactly like production (tracks.id is INTEGER PRIMARY KEY) — so the
    # test exercises the real round-trip: the finding stores str(id) and the fix queries WHERE id=?.
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO tracks (id, artist_id, album_id, title, duration, file_path, spotify_track_id) "
        "VALUES (?, 'ar1', 'al1', ?, ?, ?, ?)",
        (tid, f"Track {tid}", duration_ms, path, spotify_id),
    )
    conn.commit()
    conn.close()


class _FakeSpotify:
    """get_track_details(id) -> {'duration_ms': N}. 'sp_long' is a full song; else short."""
    def get_track_details(self, track_id, **_):
        return {'duration_ms': 200_000} if track_id == 'sp_long' else {'duration_ms': 28_000}


def _ctx(db, findings, spotify=None):
    return JobContext(
        db=db, transfer_folder='/tmp', config_manager=None,
        spotify_client=spotify,
        create_finding=lambda **kw: findings.append(kw) or True,
        should_stop=lambda: False, is_paused=lambda: False,
    )


# ── scan ──

def test_scan_flags_preview_skips_genuine_short_and_unverifiable(tmp_path: Path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    _seed(db)
    _track(db, 1, 28_000, '/m/p.flac', spotify_id='sp_long')   # id 1: 28s file, source 200s → FLAG
    _track(db, 2, 28_000, '/m/i.flac', spotify_id='sp_short')  # id 2: 28s file, source 28s  → skip (genuine)
    _track(db, 3, 28_000, '/m/m.flac', spotify_id=None)        # id 3: 28s, no source id     → skip (unverifiable)
    _track(db, 4, 200_000, '/m/l.flac', spotify_id='sp_long')  # id 4: 200s                  → not scanned (>30s)

    findings = []
    result = ShortPreviewTrackJob().scan(_ctx(db, findings, _FakeSpotify()))

    assert len(findings) == 1
    f = findings[0]
    assert f['finding_type'] == 'short_preview_track'
    assert f['entity_id'] == '1'          # str(int id), as create_finding stores it
    assert f['entity_type'] == 'track'
    assert f['details']['expected_duration_s'] == pytest.approx(200.0)
    assert result.findings_created == 1
    assert result.scanned == 3            # the 200s track is excluded by the query, not scanned
    assert result.skipped == 2            # skit + noid


def test_scan_creates_no_finding_when_source_agrees_short(tmp_path: Path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    _seed(db)
    _track(db, 1, 28_000, '/m/i.flac', spotify_id='sp_short')  # source also says 28s
    findings = []
    ShortPreviewTrackJob().scan(_ctx(db, findings, _FakeSpotify()))
    assert findings == []


def test_estimate_scope_counts_short_tracks(tmp_path: Path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    _seed(db)
    _track(db, 1, 28_000, '/m/a.flac', spotify_id='sp_long')
    _track(db, 2, 10_000, '/m/b.flac', spotify_id='sp_short')
    _track(db, 3, 200_000, '/m/c.flac', spotify_id='sp_long')  # >30s, excluded
    assert ShortPreviewTrackJob().estimate_scope(_ctx(db, [], _FakeSpotify())) == 2


# ── fix (approval) ──

def test_fix_deletes_file_removes_row_and_wishlists(tmp_path: Path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    _seed(db)
    preview = tmp_path / 'preview.flac'
    preview.write_bytes(b'fake audio bytes')
    _track(db, 1, 28_000, str(preview), spotify_id='sp1')

    captured = {}
    db.add_to_wishlist = lambda spotify_track_data, **kw: captured.update(
        {'data': spotify_track_data, 'kw': kw}) or True

    w = RepairWorker.__new__(RepairWorker)
    w.db = db
    w.transfer_folder = str(tmp_path)
    w._config_manager = None

    res = w._fix_short_preview_track(
        'track', '1', str(preview),    # entity_id is the string the finding stored
        {'expected_duration_s': 225.0, 'original_path': str(preview)})

    assert res['success'] is True
    assert not preview.exists()                                  # preview file deleted
    assert captured['data']['name'] == 'Track 1'                 # re-wishlisted with payload
    assert captured['data']['duration_ms'] == 225_000           # uses the real (expected) length
    assert captured['kw'].get('source_type') == 'redownload'
    conn = db._get_connection()
    remaining = conn.execute("SELECT COUNT(*) FROM tracks WHERE id=1").fetchone()[0]
    conn.close()
    assert remaining == 0                                        # DB row dropped → track missing again


def test_fix_missing_file_still_wishlists_and_drops_row(tmp_path: Path):
    """If the preview file is already gone, still re-wishlist + drop the row (idempotent-ish)."""
    db = MusicDatabase(str(tmp_path / 'm.db'))
    _seed(db)
    _track(db, 1, 28_000, str(tmp_path / 'gone.flac'), spotify_id='sp2')
    db.add_to_wishlist = lambda spotify_track_data, **kw: True

    w = RepairWorker.__new__(RepairWorker)
    w.db = db
    w.transfer_folder = str(tmp_path)
    w._config_manager = None

    res = w._fix_short_preview_track('track', '1', str(tmp_path / 'gone.flac'), {})
    assert res['success'] is True
    conn = db._get_connection()
    assert conn.execute("SELECT COUNT(*) FROM tracks WHERE id=1").fetchone()[0] == 0
    conn.close()
