"""Seam tests for the Library Re-tag job scan + the apply handler.

Injected fakes only — no metadata APIs, no real tag writes. A temp sqlite db
+ real (empty) track files exercise the orchestration: scan -> detailed
finding, and finding -> per-track write payload.
"""

import sqlite3
import sys
import types
from types import SimpleNamespace

# Stub optional deps so the modules import in the test env.
if 'spotipy' not in sys.modules:
    sp = types.ModuleType('spotipy'); oa = types.ModuleType('spotipy.oauth2')
    sp.Spotify = type('S', (), {}); oa.SpotifyOAuth = oa.SpotifyClientCredentials = type('O', (), {})
    sp.oauth2 = oa; sys.modules['spotipy'] = sp; sys.modules['spotipy.oauth2'] = oa
if 'config.settings' not in sys.modules:
    cm = types.ModuleType('config'); sm = types.ModuleType('config.settings')

    class _Cfg:
        def get(self, k, d=None): return d
        def get_active_media_server(self): return 'plex'
    sm.config_manager = _Cfg(); cm.settings = sm
    sys.modules['config'] = cm; sys.modules['config.settings'] = sm

from core.repair_jobs import library_retag as lr


def _db_with_album(path, track_file, current_title='Old Title'):
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT)")
    c.execute("""CREATE TABLE albums (id INTEGER PRIMARY KEY, title TEXT, artist_id INTEGER,
                 spotify_album_id TEXT, itunes_album_id TEXT, deezer_id TEXT, musicbrainz_release_id TEXT)""")
    c.execute("""CREATE TABLE tracks (id INTEGER PRIMARY KEY, album_id INTEGER, title TEXT,
                 track_number INTEGER, disc_number INTEGER, file_path TEXT)""")
    c.execute("INSERT INTO artists (id, name) VALUES (1, 'Real Artist')")
    c.execute("INSERT INTO albums (id, title, artist_id, spotify_album_id) VALUES (1, 'Real Album', 1, 'sp_alb')")
    c.execute("INSERT INTO tracks (id, album_id, title, track_number, disc_number, file_path) VALUES (1, 1, ?, 1, 1, ?)",
              (current_title, track_file))
    conn.commit()
    return conn


def _context(conn, settings):
    findings = []
    return SimpleNamespace(
        db=SimpleNamespace(_get_connection=lambda: conn),
        config_manager=SimpleNamespace(get=lambda k, d=None: {f'repair.jobs.library_retag.settings': settings}.get(k, d)),
        check_stop=lambda: False, wait_if_paused=lambda: False,
        update_progress=lambda *a, **k: None, report_progress=lambda *a, **k: None,
        create_finding=lambda **kw: (findings.append(kw) or True),
        findings=findings,
    )


_ALBUM_META = {'name': 'Real Album', 'artists': [{'name': 'Real Artist'}],
               'year': '2021', 'genres': ['Rock'], 'total_tracks': 1,
               'image_url': 'http://art/cover.jpg'}
_SRC_TRACKS = [{'name': 'Real Title', 'track_number': 1, 'disc_number': 1, 'id': 'sp_trk'}]


def _patch_source(monkeypatch, current_tags):
    monkeypatch.setattr(lr, 'get_album_for_source', lambda s, i: _ALBUM_META)
    monkeypatch.setattr(lr, 'get_album_tracks_for_source', lambda s, i: list(_SRC_TRACKS))
    monkeypatch.setattr(lr, '_read_current_tags', lambda p: dict(current_tags))


def test_scan_creates_detailed_finding(tmp_path, monkeypatch):
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    conn = _db_with_album(str(tmp_path / 'm.db'), str(track), current_title='Old Title')
    ctx = _context(conn, {'mode': 'overwrite', 'cover_art': 'replace', 'source': 'spotify'})
    _patch_source(monkeypatch, {
        'title': 'Old Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
        'year': '2021', 'genre': 'Rock', 'track_number': 1, 'disc_number': 1,
    })

    result = lr.LibraryRetagJob().scan(ctx)

    assert result.findings_created == 1
    d = ctx.findings[0]['details']
    assert ctx.findings[0]['finding_type'] == 'library_retag'
    assert d['source'] == 'spotify'
    assert d['cover_action'] == 'replace'
    tp = d['tracks'][0]
    assert tp['changes']['title'] == {'old': 'Old Title', 'new': 'Real Title'}
    # source ids stamped onto the write payload
    assert tp['db_data']['spotify_album_id'] == 'sp_alb'
    assert tp['db_data']['spotify_track_id'] == 'sp_trk'
    assert tp['db_data']['title'] == 'Real Title'


def test_scan_dry_run_off_auto_applies_no_finding(tmp_path, monkeypatch):
    """dry_run=False: scan applies in place (auto_fixed) and creates NO finding."""
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    conn = _db_with_album(str(tmp_path / 'm.db'), str(track), current_title='Old Title')
    ctx = _context(conn, {'mode': 'overwrite', 'cover_art': 'skip', 'source': 'spotify', 'dry_run': False})
    _patch_source(monkeypatch, {
        'title': 'Old Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
        'year': '2021', 'genre': 'Rock', 'track_number': 1, 'disc_number': 1,
    })
    writes = []
    monkeypatch.setattr('core.tag_writer.write_tags_to_file',
                        lambda fp, db_data, **k: writes.append(db_data) or {'success': True})

    result = lr.LibraryRetagJob().scan(ctx)

    assert ctx.findings == []                 # no finding in apply mode
    assert result.auto_fixed == 1
    assert writes and writes[0]['title'] == 'Real Title'   # actually wrote


def test_scan_full_depth_attaches_full_meta_to_finding(tmp_path, monkeypatch):
    """depth=full: each track plan carries a full_meta dict (title/album/artist +
    source ids) for the enrichment cascade, and details record the depth."""
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    conn = _db_with_album(str(tmp_path / 'm.db'), str(track), current_title='Old Title')
    ctx = _context(conn, {'mode': 'overwrite', 'cover_art': 'skip', 'source': 'spotify', 'depth': 'full'})
    _patch_source(monkeypatch, {
        'title': 'Old Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
        'year': '2021', 'genre': 'Rock', 'track_number': 1, 'disc_number': 1,
    })

    result = lr.LibraryRetagJob().scan(ctx)

    assert result.findings_created == 1
    d = ctx.findings[0]['details']
    assert d['depth'] == 'full'
    fm = d['tracks'][0]['full_meta']
    assert fm['title'] == 'Real Title'
    assert fm['album'] == 'Real Album'
    assert fm['album_artist'] == 'Real Artist'
    assert fm['spotify_album_id'] == 'sp_alb'
    assert fm['spotify_track_id'] == 'sp_trk'


def test_scan_full_depth_auto_apply_runs_enrich(tmp_path, monkeypatch):
    """depth=full + dry_run off: after the light write, the full enrichment
    cascade runs once per written track."""
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    conn = _db_with_album(str(tmp_path / 'm.db'), str(track), current_title='Old Title')
    ctx = _context(conn, {'mode': 'overwrite', 'cover_art': 'skip', 'source': 'spotify',
                          'depth': 'full', 'dry_run': False})
    _patch_source(monkeypatch, {
        'title': 'Old Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
        'year': '2021', 'genre': 'Rock', 'track_number': 1, 'disc_number': 1,
    })
    monkeypatch.setattr('core.tag_writer.write_tags_to_file',
                        lambda fp, db_data, **k: {'success': True})
    enriched = []
    monkeypatch.setattr(lr, '_run_full_enrich',
                        lambda fp, meta: enriched.append((fp, meta)) or True)

    result = lr.LibraryRetagJob().scan(ctx)

    assert result.auto_fixed == 1
    assert len(enriched) == 1
    assert enriched[0][1]['spotify_track_id'] == 'sp_trk'


def test_scan_light_depth_does_not_run_enrich(tmp_path, monkeypatch):
    """depth=light (default): no full_meta, enrichment cascade never invoked."""
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    conn = _db_with_album(str(tmp_path / 'm.db'), str(track), current_title='Old Title')
    ctx = _context(conn, {'mode': 'overwrite', 'cover_art': 'skip', 'source': 'spotify',
                          'dry_run': False})  # depth defaults to light
    _patch_source(monkeypatch, {
        'title': 'Old Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
        'year': '2021', 'genre': 'Rock', 'track_number': 1, 'disc_number': 1,
    })
    monkeypatch.setattr('core.tag_writer.write_tags_to_file',
                        lambda fp, db_data, **k: {'success': True})
    enriched = []
    monkeypatch.setattr(lr, '_run_full_enrich',
                        lambda fp, meta: enriched.append(fp) or True)

    lr.LibraryRetagJob().scan(ctx)
    assert enriched == []


def test_scan_skips_album_already_correct(tmp_path, monkeypatch):
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    conn = _db_with_album(str(tmp_path / 'm.db'), str(track), current_title='Real Title')
    # cover skipped + tags already match → nothing to do
    ctx = _context(conn, {'mode': 'overwrite', 'cover_art': 'skip', 'source': 'spotify'})
    _patch_source(monkeypatch, {
        'title': 'Real Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
        'year': '2021', 'genre': 'Rock', 'track_number': 1, 'disc_number': 1,
    })

    result = lr.LibraryRetagJob().scan(ctx)
    assert result.findings_created == 0
    assert ctx.findings == []
    assert result.skipped >= 1


def test_add_source_ids_maps_per_source():
    db = {}
    lr._add_source_ids(db, 'spotify', 'AL', {'id': 'TR'})
    assert db == {'spotify_album_id': 'AL', 'spotify_track_id': 'TR'}
    db2 = {}
    lr._add_source_ids(db2, 'musicbrainz', 'REL', {'id': 'REC'})
    assert db2 == {'musicbrainz_release_id': 'REL', 'musicbrainz_recording_id': 'REC'}


# ── apply handler ──

def test_fix_library_retag_writes_each_track(tmp_path, monkeypatch):
    import core.repair_worker as rw
    track = tmp_path / 'a.flac'; track.write_bytes(b'')

    worker = rw.RepairWorker.__new__(rw.RepairWorker)
    worker.db = SimpleNamespace()
    worker._config_manager = SimpleNamespace(get=lambda k, d=None: d)
    worker.transfer_folder = str(tmp_path)

    monkeypatch.setattr(rw, '_resolve_file_path', lambda p, *a, **k: p)
    writes = []
    monkeypatch.setattr('core.tag_writer.write_tags_to_file',
                        lambda fp, db_data, **k: writes.append((fp, db_data)) or {'success': True})

    details = {
        'tracks': [{'file_path': str(track), 'db_data': {'title': 'Real Title', 'spotify_track_id': 'sp_trk'}}],
        'cover_action': None, 'cover_url': None,
    }
    res = worker._fix_library_retag('album', '1', None, details)
    assert res['success'] is True
    assert res['written'] == 1
    assert writes[0][1]['title'] == 'Real Title'
    assert writes[0][1]['spotify_track_id'] == 'sp_trk'


def test_fix_library_retag_counts_unreachable(tmp_path, monkeypatch):
    import core.repair_worker as rw
    worker = rw.RepairWorker.__new__(rw.RepairWorker)
    worker.db = SimpleNamespace()
    worker._config_manager = SimpleNamespace(get=lambda k, d=None: d)
    worker.transfer_folder = str(tmp_path)
    monkeypatch.setattr(rw, '_resolve_file_path', lambda p, *a, **k: p)
    monkeypatch.setattr('core.tag_writer.write_tags_to_file', lambda *a, **k: {'success': True})

    details = {'tracks': [{'file_path': str(tmp_path / 'missing.flac'), 'db_data': {'title': 'x'}}],
               'cover_action': None, 'cover_url': None}
    res = worker._fix_library_retag('album', '1', None, details)
    assert res['success'] is False        # nothing written (file missing)
