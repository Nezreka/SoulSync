"""Preview-clip cleanup job (#937-adjacent): flag ~30s preview clips whose source says the
real track is much longer, then on approval delete the file + drop the row + re-wishlist."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.repair_jobs.base import JobContext
from core.repair_jobs.short_preview_track import ShortPreviewTrackJob
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


class _EnabledConfig:
    def get(self, key, default=None):
        return True if key == "features.library_v2" else default


def _seed(db: MusicDatabase):
    conn = db._get_connection()
    from core.library2.schema import ensure_library_v2_schema

    ensure_library_v2_schema(conn)
    conn.execute(
        "INSERT INTO lib2_artists(id, name, sort_name) VALUES(1, 'A-ha', 'A-ha')"
    )
    conn.execute(
        "INSERT INTO lib2_albums(id, primary_artist_id, title) "
        "VALUES(1, 1, 'Hunting High and Low')"
    )
    conn.commit()
    conn.close()


def _track(db, tid: int, duration_ms, path, spotify_id=None):
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO lib2_tracks(id, album_id, title, duration, spotify_id) "
        "VALUES(?, 1, ?, ?, ?)",
        (tid, f"Track {tid}", duration_ms, spotify_id),
    )
    conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, format, is_primary) "
        "VALUES(?, ?, ?, 1)",
        (tid, path, Path(path).suffix.lstrip('.').lower()),
    )
    conn.commit()
    conn.close()


class _FakeSpotify:
    """get_track_details(id) -> {'duration_ms': N}. 'sp_long' is a full song; else short."""
    def get_track_details(self, track_id, **_):
        return {'duration_ms': 200_000} if track_id == 'sp_long' else {'duration_ms': 28_000}


def _ctx(db, findings, spotify=None):
    return JobContext(
        db=db, transfer_folder='/tmp', config_manager=_EnabledConfig(),
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
    assert f['entity_id'] == 'lib2:1'
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

def test_fix_deletes_the_preview_and_asks_for_the_full_track(tmp_path: Path):
    """The subject is ``lib2:1`` — the only kind the scan above produces.

    The native fix does not build a wishlist payload here and does not drop a
    row: it deletes the file and reports ``repair_intent='redownload'``, and the
    maintenance bridge turns that into "wanted" against the real track
    (``tests/library2/test_maintenance_sync.py``). Written against a bare ``'1'``
    this test drove the legacy branch instead, which is why the two could drift
    without anything failing."""
    db = MusicDatabase(str(tmp_path / 'm.db'))
    _seed(db)
    preview = tmp_path / 'preview.flac'
    preview.write_bytes(b'fake audio bytes')
    _track(db, 1, 28_000, str(preview), spotify_id='sp1')

    w = RepairWorker.__new__(RepairWorker)
    w.db = db
    w.transfer_folder = str(tmp_path)
    w._config_manager = None

    res = w._fix_short_preview_track(
        'track', 'lib2:1', str(preview),
        {'expected_duration_s': 225.0, 'original_path': str(preview)})

    assert res['success'] is True
    assert not preview.exists()                                  # preview file deleted
    assert res['repair_intent'] == 'redownload'
    assert res['library_v2_file_deleted'] is True
    assert 'Track 1' in res['message']


# ── album art capture (so the re-wishlisted item isn't art-less) ──

def test_scan_captures_source_album_art_into_finding(tmp_path: Path):
    """The duration lookup's raw_data carries the source CDN album art — capture it so the
    re-wishlist isn't art-less when the library thumb is empty (the reported bug)."""
    db = MusicDatabase(str(tmp_path / 'm.db'))
    _seed(db)
    _track(db, 1, 28_000, '/m/p.flac', spotify_id='sp_long')

    class _SpWithArt:
        def get_track_details(self, track_id, **_):
            return {'duration_ms': 200_000,
                    'raw_data': {'album': {'images': [{'url': 'https://cdn/cover.jpg'}]}}}

    findings = []
    ShortPreviewTrackJob().scan(_ctx(db, findings, _SpWithArt()))
    assert len(findings) == 1
    assert findings[0]['details']['album_thumb_url'] == 'https://cdn/cover.jpg'


def test_art_from_itunes_artwork_is_upscaled():
    from core.repair_jobs.short_preview_track import _art_from_details
    d = {'raw_data': {'artworkUrl100': 'https://is1.mzstatic.com/a/100x100bb.jpg'}}
    assert _art_from_details(d) == 'https://is1.mzstatic.com/a/600x600bb.jpg'


# The fix no longer builds a wishlist payload, so there is nothing left to put
# the captured art into: it marks the native track wanted and the acquisition
# path takes the album's own `image_url`. The capture is still asserted above —
# `finding-detail.tsx` shows it on the finding — but the payload assertion that
# used to live here only ever ran against the legacy branch.


def test_fix_of_an_already_gone_preview_still_asks_for_the_full_track(tmp_path: Path):
    """Idempotent-ish: the file being gone is not a failure, the track is still
    the wrong one to be holding a 30-second clip of."""
    db = MusicDatabase(str(tmp_path / 'm.db'))
    _seed(db)
    _track(db, 1, 28_000, str(tmp_path / 'gone.flac'), spotify_id='sp2')

    w = RepairWorker.__new__(RepairWorker)
    w.db = db
    w.transfer_folder = str(tmp_path)
    w._config_manager = None

    res = w._fix_short_preview_track('track', 'lib2:1', str(tmp_path / 'gone.flac'), {})
    assert res['success'] is True
    assert res['repair_intent'] == 'redownload'
    assert 'already gone' in res['message']


# ── HiFi fragmented-FLAC previews (sella): stored duration 0, decode to find them ──

def test_scan_decodes_zero_duration_hifi_previews(tmp_path: Path, monkeypatch):
    # HiFi HLS-assembled FLAC stores duration 0 (total_samples=0). The old query
    # (duration > 0) missed these entirely — the exact clips that replaced
    # sella's tracks. verify_zero_length (default on) decodes them.
    db = MusicDatabase(str(tmp_path / 'm.db'))
    _seed(db)
    clip = tmp_path / 'hifi_preview.flac'
    clip.write_bytes(b'fake flac')
    full = tmp_path / 'hifi_full.flac'
    full.write_bytes(b'fake flac')
    _track(db, 1, 0, str(clip), spotify_id='sp_long')   # decodes to 30s, source 200s → FLAG
    _track(db, 2, 0, str(full), spotify_id='sp_long')   # decodes to 199s, source 200s → skip

    import core.repair_jobs.short_preview_track as spt
    monkeypatch.setattr(
        spt, 'resolve_library_file_path',
        lambda p, **_k: p, raising=False)
    monkeypatch.setattr(
        'core.imports.file_integrity.probe_decoded_duration',
        lambda p, *a, **k: 30.0 if 'preview' in str(p) else 199.0)

    findings = []
    result = ShortPreviewTrackJob().scan(_ctx(db, findings, _FakeSpotify()))
    assert len(findings) == 1
    assert findings[0]['entity_id'] == 'lib2:1'
    assert findings[0]['details']['file_duration_s'] == pytest.approx(30.0)
    assert result.scanned == 2


def test_zero_duration_that_cannot_be_decoded_is_skipped(tmp_path: Path, monkeypatch):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    _seed(db)
    f = tmp_path / 'x.flac'
    f.write_bytes(b'fake')
    _track(db, 1, 0, str(f), spotify_id='sp_long')

    import core.repair_jobs.short_preview_track as spt
    monkeypatch.setattr(spt, 'resolve_library_file_path', lambda p, **_k: p, raising=False)
    monkeypatch.setattr('core.imports.file_integrity.probe_decoded_duration',
                        lambda *a, **k: 0.0)   # ffmpeg unavailable / undecodable

    findings = []
    res = ShortPreviewTrackJob().scan(_ctx(db, findings, _FakeSpotify()))
    assert findings == [] and res.skipped == 1


def test_verify_zero_off_keeps_the_old_stored_duration_behavior(tmp_path: Path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    _seed(db)
    _track(db, 1, 0, '/m/zero.flac', spotify_id='sp_long')     # zero-duration → NOT scanned when off
    _track(db, 2, 28_000, '/m/short.flac', spotify_id='sp_long')

    class _CM:
        def get(self, key, default=None):
            return False if key.endswith('verify_zero_length') else default

    ctx = JobContext(
        db=db, transfer_folder='/tmp', config_manager=_CM(),
        spotify_client=_FakeSpotify(),
        create_finding=lambda **kw: True,
        should_stop=lambda: False, is_paused=lambda: False,
    )
    res = ShortPreviewTrackJob().scan(ctx)
    assert res.scanned == 1     # only the 28s row; the zero-duration row is excluded
