"""hand-tagged releases stay out of the maintenance jobs.

"tag it yourself" lets a user type every tag for a bootleg or a live set no
service knows. those rows get metadata_locked = 1 and the files land in
manual_metadata_files. every job here would otherwise match the release to
the studio album and renumber it, retag it, recover it or offer to delete it.

each test runs the real scan() against a tmp MusicDatabase, with one ordinary
album that must still be processed next to the hand-tagged one. jobs that read
rows AND paths get two tests: a locked row with no path key (the sql guard) and
a path key on a row that isn't locked yet (the per-file guard), so each guard
can fail on its own.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from core.repair_jobs import acoustid_scanner as acs
from core.repair_jobs import album_completeness as acj
from core.repair_jobs import library_retag as lrt
from core.repair_jobs import live_commentary_cleaner as lcc
from core.repair_jobs import metadata_gap_filler as mgf
from core.repair_jobs import missing_cover_art as mca
from core.repair_jobs import quality_upgrade_scanner as qus
from core.repair_jobs import single_album_dedup as sad
from core.repair_jobs import track_number_repair as tnr
from core.repair_jobs import duplicate_detector as dd
from core.repair_jobs.base import JobContext, JobResult, hand_tagged_path_keys, is_hand_tagged_path
from database.music_database import MusicDatabase


# ── fixture helpers ──

def _db(tmp_path: Path) -> MusicDatabase:
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO artists (id, name) VALUES ('ar1', 'Pearl Jam')")
        conn.commit()
    return db


def _album(db, album_id, title, record_type='album', track_count=10, **cols):
    names = ['id', 'artist_id', 'title', 'record_type', 'track_count', *cols]
    vals = [album_id, 'ar1', title, record_type, track_count, *cols.values()]
    with db._get_connection() as conn:
        conn.execute(f"INSERT INTO albums ({', '.join(names)}) VALUES ({', '.join('?' * len(vals))})", vals)
        conn.commit()


def _track(db, tid, album_id, title, path, **cols):
    names = ['id', 'artist_id', 'album_id', 'title', 'file_path', *cols]
    vals = [tid, 'ar1', album_id, title, str(path), *cols.values()]
    with db._get_connection() as conn:
        conn.execute(f"INSERT INTO tracks ({', '.join(names)}) VALUES ({', '.join('?' * len(vals))})", vals)
        conn.commit()


def _lock(db, track_id=None, album_id=None):
    """what the manual tagger does to the rows, without a path key"""
    with db._get_connection() as conn:
        if track_id is not None:
            conn.execute("UPDATE tracks SET metadata_locked = 1 WHERE id = ?", (track_id,))
        if album_id is not None:
            conn.execute("UPDATE albums SET metadata_locked = 1 WHERE id = ?", (album_id,))
        conn.commit()


def _hand_tag_file(db, path):
    """the path key only. called before the row exists, so nothing gets
    locked: a media-server row the scan hasn't caught up with yet"""
    db.record_manual_metadata_file(str(path))


def _ctx(db, findings, transfer='/nonexistent', **kw):
    return JobContext(
        db=db, transfer_folder=str(transfer), config_manager=None,
        create_finding=lambda **f: findings.append(f) or True,
        should_stop=lambda: False, is_paused=lambda: False, **kw,
    )


# ── the shared helpers ──

def test_path_keys_survive_odd_dbs(tmp_path):
    assert hand_tagged_path_keys(None) == set()
    assert hand_tagged_path_keys(SimpleNamespace()) == set()  # no method
    db = _db(tmp_path)
    _hand_tag_file(db, '/mnt/music/Pearl Jam/Live 1992/01 - Alive.flac')
    keys = hand_tagged_path_keys(db)
    # a media server mounts the same file somewhere else
    assert is_hand_tagged_path('/data/Pearl Jam/Live 1992/01 - Alive.flac', keys)
    assert not is_hand_tagged_path('/data/Pearl Jam/Ten/01 - Alive.flac', keys)


# ── 1. track number repair ──

def test_track_number_repair_skips_hand_tagged_album_folder(tmp_path, monkeypatch):
    db = _db(tmp_path)
    live = tmp_path / 'lib' / 'Pearl Jam' / 'Live 1992'
    studio = tmp_path / 'lib' / 'Pearl Jam' / 'Ten'
    for d in (live, studio):
        d.mkdir(parents=True)
        (d / '01 - Alive.flac').write_bytes(b'x')
        (d / '02 - Black.flac').write_bytes(b'x')
    _hand_tag_file(db, live / '02 - Black.flac')  # any one file marks the folder

    job = tnr.TrackNumberRepairJob()
    seen = []
    monkeypatch.setattr(job, '_repair_album',
                        lambda folder, names, *a, **k: seen.append(folder) or JobResult(scanned=len(names)))
    result = job.scan(_ctx(db, [], transfer=tmp_path / 'lib'))

    assert seen == [str(studio)]
    assert result.skipped == 2


# ── 2. live/commentary cleaner ──

def _live_fixture(tmp_path):
    db = _db(tmp_path)
    _album(db, 'al_live', 'Live at the Garden')
    _album(db, 'al_other', 'Some Other Live Album')
    return db


def test_live_cleaner_never_flags_a_locked_row(tmp_path):
    db = _live_fixture(tmp_path)
    _track(db, 1, 'al_live', 'Alive (Live)', '/m/Pearl Jam/Live at the Garden/01 - Alive.flac')
    _track(db, 2, 'al_other', 'Black (Live)', '/m/Pearl Jam/Other/01 - Black.flac')
    _lock(db, track_id=1, album_id='al_live')

    findings = []
    lcc.LiveCommentaryCleanerJob().scan(_ctx(db, findings))

    assert [str(f['details']['track']['id']) for f in findings] == ['2']


def test_live_cleaner_never_flags_a_hand_tagged_file(tmp_path):
    db = _live_fixture(tmp_path)
    path = '/m/Pearl Jam/Live at the Garden/01 - Alive.flac'
    _hand_tag_file(db, path)
    _track(db, 1, 'al_live', 'Alive (Live)', path)
    _track(db, 2, 'al_other', 'Black (Live)', '/m/Pearl Jam/Other/01 - Black.flac')

    findings = []
    lcc.LiveCommentaryCleanerJob().scan(_ctx(db, findings))

    assert [str(f['details']['track']['id']) for f in findings] == ['2']


# ── 3. quality upgrade scanner ──

def _quality_run(tmp_path, monkeypatch, *, lock_row, key_file):
    db = _db(tmp_path)
    _album(db, 'al1', 'Live 1992')
    lib = tmp_path / 'lib'
    boot = lib / 'Pearl Jam' / 'Live 1992' / '01 - Alive.mp3'
    plain = lib / 'Pearl Jam' / 'Live 1992' / '02 - Black.mp3'
    boot.parent.mkdir(parents=True)
    boot.write_bytes(b'x')
    plain.write_bytes(b'x')
    if key_file:
        _hand_tag_file(db, boot)
    _track(db, 1, 'al1', 'Alive', boot)
    _track(db, 2, 'al1', 'Black', plain)
    if lock_row:
        _lock(db, track_id=1)

    aq = SimpleNamespace(label=lambda: 'MP3 128', format='mp3', bitrate=128,
                         sample_rate=44100, bit_depth=None)
    monkeypatch.setattr('core.imports.file_ops.probe_audio_quality', lambda _p: aq)
    monkeypatch.setattr(qus, 'retention_meets_profile', lambda *a, **k: False)

    findings = []
    qus.QualityUpgradeScannerJob().scan(_ctx(db, findings, transfer=lib))
    return sorted(os.path.basename(f['file_path']) for f in findings)


def test_quality_scanner_skips_a_locked_row(tmp_path, monkeypatch):
    assert _quality_run(tmp_path, monkeypatch, lock_row=True, key_file=False) == ['02 - Black.mp3']


def test_quality_scanner_skips_a_hand_tagged_file(tmp_path, monkeypatch):
    assert _quality_run(tmp_path, monkeypatch, lock_row=False, key_file=True) == ['02 - Black.mp3']


# ── 4. metadata gap filler ──

def test_gap_filler_never_searches_a_locked_row(tmp_path, monkeypatch):
    db = _db(tmp_path)
    _album(db, 'al_live', 'Live 1992')
    _album(db, 'al_ten', 'Ten')
    _track(db, 1, 'al_live', 'Alive', '/m/a.flac')
    _track(db, 2, 'al_ten', 'Black', '/m/b.flac')
    _lock(db, track_id=1, album_id='al_live')
    monkeypatch.setattr(mgf, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(mgf, 'get_source_priority', lambda _p: ['deezer'])

    searched = []
    mb = SimpleNamespace(search_recording=lambda title, **k: searched.append(title) or [{'id': 'mb-studio'}])
    findings = []
    mgf.MetadataGapFillerJob().scan(_ctx(db, findings, mb_client=mb))

    assert searched == ['Black']
    assert len(findings) == 1


# ── 5. acoustid scanner ──

class _RecordingClient:
    def __init__(self):
        self.paths = []

    def fingerprint_and_lookup(self, path):
        self.paths.append(os.path.basename(path))
        return {'best_score': 1.0, 'recordings': [
            {'mbid': 'mb-1', 'title': 'Something Else', 'artist': 'Someone', 'score': 1.0}]}


def _acoustid_run(tmp_path, monkeypatch, *, lock_row, key_file):
    db = _db(tmp_path)
    _album(db, 'al1', 'Live 1992')
    folder = tmp_path / 'lib' / 'Pearl Jam' / 'Live 1992'
    folder.mkdir(parents=True)
    boot, plain = folder / '01 - Alive.flac', folder / '02 - Black.flac'
    boot.write_bytes(b'x')
    plain.write_bytes(b'x')
    if key_file:
        _hand_tag_file(db, boot)
    _track(db, 't1', 'al1', 'Alive', boot, duration=300000)
    _track(db, 't2', 'al1', 'Black', plain, duration=300000)
    if lock_row:
        _lock(db, track_id='t1')

    monkeypatch.setattr('core.tag_writer.read_file_tags', lambda _p: {})
    monkeypatch.setattr('core.tag_writer.write_verification_status', lambda *a, **k: True)
    monkeypatch.setattr('core.acoustid_verification._resolve_expected_artist_aliases', lambda _n: [])
    client = _RecordingClient()
    ctx = SimpleNamespace(
        db=db, transfer_folder=str(tmp_path / 'lib'),
        config_manager=SimpleNamespace(get=lambda k, d=None: d, set=lambda *a, **k: None),
        acoustid_client=client, create_finding=lambda **kw: True,
        report_progress=lambda **kw: None, update_progress=lambda *a, **kw: None,
        check_stop=lambda: False, wait_if_paused=lambda: False,
        sleep_or_stop=lambda *a, **kw: False,
    )
    acs.AcoustIDScannerJob().scan(ctx)
    return client.paths


def test_acoustid_scan_skips_a_locked_row(tmp_path, monkeypatch):
    assert _acoustid_run(tmp_path, monkeypatch, lock_row=True, key_file=False) == ['02 - Black.flac']


def test_acoustid_scan_skips_a_hand_tagged_file(tmp_path, monkeypatch):
    assert _acoustid_run(tmp_path, monkeypatch, lock_row=False, key_file=True) == ['02 - Black.flac']


# ── 6. duplicate detector + single/album dedup ──

def _dupe_fixture(tmp_path):
    db = _db(tmp_path)
    _album(db, 'al_live', 'Live 1992')
    _album(db, 'al_ten', 'Ten')
    _album(db, 'al_vs', 'Vs.')
    _album(db, 'al_vs2', 'Vs. (Deluxe)')
    return db


def _dupe_ids(findings):
    return sorted(sorted(str(t['id']) for t in f['details']['tracks']) for f in findings)


def test_duplicate_detector_skips_a_locked_row(tmp_path):
    db = _dupe_fixture(tmp_path)
    kw = dict(bitrate=320, duration=300000)
    _track(db, 1, 'al_live', 'Alive', '/m/Pearl Jam/Live 1992/01 - Alive.flac', **kw)
    _track(db, 2, 'al_ten', 'Alive', '/m/Pearl Jam/Ten/03 - Alive.flac', **kw)
    _track(db, 3, 'al_vs', 'Daughter', '/m/Pearl Jam/Vs/04 - Daughter.flac', **kw)
    _track(db, 4, 'al_vs2', 'Daughter', '/m/Pearl Jam/Vs Deluxe/04 - Daughter.flac', **kw)
    _lock(db, track_id=1)

    findings = []
    dd.DuplicateDetectorJob().scan(_ctx(db, findings))

    assert _dupe_ids(findings) == [['3', '4']]


def test_duplicate_detector_skips_a_hand_tagged_file(tmp_path):
    db = _dupe_fixture(tmp_path)
    kw = dict(bitrate=320, duration=300000)
    boot = '/m/Pearl Jam/Live 1992/01 - Alive.flac'
    _hand_tag_file(db, boot)
    _track(db, 1, 'al_live', 'Alive', boot, **kw)
    _track(db, 2, 'al_ten', 'Alive', '/m/Pearl Jam/Ten/03 - Alive.flac', **kw)
    _track(db, 3, 'al_vs', 'Daughter', '/m/Pearl Jam/Vs/04 - Daughter.flac', **kw)
    _track(db, 4, 'al_vs2', 'Daughter', '/m/Pearl Jam/Vs Deluxe/04 - Daughter.flac', **kw)

    findings = []
    dd.DuplicateDetectorJob().scan(_ctx(db, findings))

    assert _dupe_ids(findings) == [['3', '4']]


def _single_fixture(tmp_path):
    db = _db(tmp_path)
    _album(db, 'sg_alive', 'Alive', record_type='single', track_count=1)
    _album(db, 'sg_black', 'Black', record_type='single', track_count=1)
    _album(db, 'al_ten', 'Ten', record_type='album', track_count=11)
    _track(db, 10, 'al_ten', 'Alive', '/m/Pearl Jam/Ten/03 - Alive.flac')
    _track(db, 11, 'al_ten', 'Black', '/m/Pearl Jam/Ten/05 - Black.flac')
    return db


def test_single_album_dedup_skips_a_locked_single(tmp_path):
    db = _single_fixture(tmp_path)
    _track(db, 1, 'sg_alive', 'Alive', '/m/Pearl Jam/Alive/01 - Alive.flac')
    _track(db, 2, 'sg_black', 'Black', '/m/Pearl Jam/Black/01 - Black.flac')
    _lock(db, album_id='sg_alive')  # the album lock alone is enough

    findings = []
    sad.SingleAlbumDedupJob().scan(_ctx(db, findings))

    assert [f['entity_id'] for f in findings] == ['2']


def test_single_album_dedup_skips_a_hand_tagged_file(tmp_path):
    db = _single_fixture(tmp_path)
    boot = '/m/Pearl Jam/Alive/01 - Alive.flac'
    _hand_tag_file(db, boot)
    _track(db, 1, 'sg_alive', 'Alive', boot)
    _track(db, 2, 'sg_black', 'Black', '/m/Pearl Jam/Black/01 - Black.flac')

    findings = []
    sad.SingleAlbumDedupJob().scan(_ctx(db, findings))

    assert [f['entity_id'] for f in findings] == ['2']


# ── 7. missing cover art ──

def _cover_run(tmp_path, monkeypatch, *, lock_album, key_file):
    db = _db(tmp_path)
    _album(db, 'al_live', 'Live 1992')
    _album(db, 'al_ten', 'Ten')
    boot = '/nowhere/Pearl Jam/Live 1992/01 - Alive.flac'
    if key_file:
        _hand_tag_file(db, boot)
    _track(db, 1, 'al_live', 'Alive', boot)
    _track(db, 2, 'al_ten', 'Black', '/nowhere/Pearl Jam/Ten/05 - Black.flac')
    if lock_album:
        _lock(db, album_id='al_live')

    monkeypatch.setattr(mca, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(mca, 'get_source_priority', lambda _p: ['deezer'])
    monkeypatch.setattr(mca, 'resolve_library_file_path', lambda *a, **k: None)
    monkeypatch.setattr('core.metadata.art_lookup.select_preferred_art_url', lambda *a, **k: None)
    job = mca.MissingCoverArtJob()
    searched = []
    monkeypatch.setattr(job, '_try_source',
                        lambda src, sid, title, artist: searched.append(title) or 'http://art/x.jpg')
    monkeypatch.setattr(job, '_find_artist_art', lambda *a, **k: None)
    job.scan(_ctx(db, []))
    return searched


def test_cover_art_skips_a_locked_album(tmp_path, monkeypatch):
    assert _cover_run(tmp_path, monkeypatch, lock_album=True, key_file=False) == ['Ten']


def test_cover_art_skips_a_hand_tagged_file(tmp_path, monkeypatch):
    assert _cover_run(tmp_path, monkeypatch, lock_album=False, key_file=True) == ['Ten']


# ── 8. library re-tag + album completeness (belt and braces) ──

def test_library_retag_skips_a_locked_album(tmp_path, monkeypatch):
    db = _db(tmp_path)
    _album(db, 'al_live', 'Live 1992', spotify_album_id='sp-ten')  # a stray id
    _album(db, 'al_ten', 'Ten', spotify_album_id='sp-ten')
    _lock(db, album_id='al_live')

    job = lrt.LibraryRetagJob()
    seen = []
    monkeypatch.setattr(job, '_source_order', lambda _s: ['spotify'])
    monkeypatch.setattr(job, '_scan_album', lambda ctx, res, album_id, *a, **k: seen.append(album_id))
    job.scan(_ctx(db, []))

    assert seen == ['al_ten']


def test_album_completeness_skips_a_locked_album(tmp_path, monkeypatch):
    db = _db(tmp_path)
    _album(db, 'al_live', 'Live 1992', spotify_album_id='sp-ten')
    _album(db, 'al_ten', 'Ten', spotify_album_id='sp-ten2')
    _lock(db, album_id='al_live')

    job = acj.AlbumCompletenessJob()
    seen = []
    monkeypatch.setattr(job, '_prepare_work_items',
                        lambda ctx, albums: seen.extend(a['album_id'] for a in albums) or [])
    job.scan(_ctx(db, []))

    assert seen == ['al_ten']
